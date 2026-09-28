"""Interview orchestration service mirroring Go interview/service.go."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from asyncio import Lock
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..knowledge.retriever import Focus as RetrieverFocus
from ..knowledge.retriever import KnowledgeQuery as RetrieverKnowledgeQuery
from .errors import (
    AnswerAlreadyCommitted,
    DomainError,
    ErrorCode,
    IdempotencyConflict,
    PersistenceConflict,
    PersistenceNotFound,
    StoreConflict,
    StoreNotFound,
    conflict,
    internal,
    unavailable,
    validation,
)
from .memory_store import MemoryStore, RandomIDs, SystemClock
from .persistence import (
    AnswerCommitSpec,
    Command,
    CommandSpec,
    CommandStatus,
    CommandTransition,
    SessionEventSpec,
)
from .policy import (
    choose_follow_up_axis,
    coverage_point_by_id,
    derive_policy,
    normalize_assessment,
    question_kind,
    vague_assessment_for_area,
    strong_assessment_for_area,
)
from .profile_builder import ProfileBuilder
from .recovery import (
    ReviewSnapshot,
    ReviewTurn,
    SessionEventPage,
    SessionEventSummary,
    SessionSnapshot,
    SnapshotTurn,
    review_references,
)
from .scoring import score_report
from .sources import (
    all_anchors,
    all_evidence,
    anchors_for_evidence,
    canonical_evidence,
    concise,
    contains_any,
    evidence_from_anchor,
    first_folded_marker,
    knowledge_question_label,
    knowledge_reference_text,
    KNOWLEDGE_REFERENCE_MARKERS,
    merge_knowledge_documents,
    normalize_evidence_text,
    normalize_question_guard_text,
    public_anchor_label,
    public_evidence_quote,
    public_generated_text,
    public_knowledge_question,
    validate_evidence_refs,
)
from .types import (
    Action,
    AnswerFeedback,
    AnswerPayload,
    AnswerRecord,
    AnswerRequest,
    AnswerResponse,
    Assessment,
    AuditTurn,
    ClaimCheck,
    ClaimVerdict,
    CoverageCandidate,
    CoveragePoint,
    CoverageSelection,
    Difficulty,
    EvidenceRef,
    FeedbackMode,
    Focus,
    GenerateQuestionRequest,
    GenerateReportRequest,
    AssessAnswerRequest,
    InputMode,
    InterviewConfig,
    InterviewSession,
    InterviewState,
    PlanCoverageRequest,
    PolicyAction,
    PolicyDecision,
    Progress,
    Question,
    QuestionAdaptation,
    QuestionDraft,
    QuestionKind,
    RepairInstruction,
    Report,
    ReportAudit,
    ReportDraft,
    ReportRequest,
    ReportResponse,
    SourceKind,
    StartRequest,
    StartResponse,
)

MAX_KNOWLEDGE_EVIDENCE_PER_QUESTION = 5

_RETRIEVER_FOCUS = {
    Focus.MIXED: RetrieverFocus.MIXED,
    Focus.KNOWLEDGE: RetrieverFocus.KNOWLEDGE,
    Focus.PROJECTS: RetrieverFocus.PROJECTS,
}


@dataclass
class AnswerExecution:
    command_id: str = ""
    session_id: str = ""
    scope: str = ""
    durable: bool = False


@dataclass
class MemoryAnswerCommand:
    request_hash: str = ""
    status: CommandStatus = CommandStatus.PENDING
    result: str | None = None


class KeyedCoordinator:
    def __init__(self):
        self._locks: dict[str, Lock] = {}
        self._refs: dict[str, int] = {}

    @asynccontextmanager
    async def acquire(self, key: str):
        lock = self._locks.get(key)
        if lock is None:
            lock = Lock()
            self._locks[key] = lock
        self._refs[key] = self._refs.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._refs[key] -= 1
            if self._refs[key] == 0:
                self._locks.pop(key, None)
                self._refs.pop(key, None)


class Service:
    def __init__(
        self,
        agent=None,
        planner=None,
        retriever=None,
        profile_builder=None,
        store=None,
        persistence=None,
        clock=None,
        ids=None,
    ):
        self._agent = agent
        self._planner = planner if planner is not None else (agent if hasattr(agent, "plan_coverage") else None)
        self._retriever = retriever
        self._profile_builder = profile_builder if profile_builder is not None else ProfileBuilder()
        self._store = store if store is not None else MemoryStore()
        self._persistence = persistence
        if self._persistence is None and hasattr(self._store, "commit_answer"):
            self._persistence = self._store
        self._answer_committer = self._persistence if self._persistence is not None and hasattr(self._persistence, "commit_answer") else None
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else RandomIDs()
        self._coordinator = KeyedCoordinator()
        self._memory_answers: dict[str, MemoryAnswerCommand] = {}
        self._memory_answer_topics: dict[str, str] = {}

    # --- start ---

    async def start(self, request: StartRequest) -> StartResponse:
        request.config = with_config_defaults(request.config)
        validate_start(request)

        try:
            profile, sources = self._profile_builder.build(request.materials, [], request.config.focus)
        except Exception as profile_err:
            raise unavailable("profile extraction is temporarily unavailable", profile_err)

        if not profile.coverage and self._retriever is not None:
            seed = self._retrieve_knowledge(self._build_query(
                model=request.model,
                focus=request.config.focus,
                jd=material_text(request.materials.jd),
                resume=material_text(request.materials.resume),
            ))
            try:
                profile, sources = self._profile_builder.build(request.materials, seed, request.config.focus)
            except Exception as profile_err:
                raise unavailable("profile extraction is temporarily unavailable", profile_err)

        if not profile.coverage:
            raise validation("materials", "JD, resume, or retrieved knowledge must contain at least one usable anchor")

        interview_id = self._ids.new_id("interview")
        session = InterviewSession(
            id=interview_id,
            clientSessionId=request.clientSessionId,
            model=request.model,
            config=request.config,
            state=InterviewState.AWAITING_ANSWER,
            profile=profile,
            sources=sources,
            answers=[],
            coverageCursor=0,
            startedAt=self._clock.now(),
            version=1,
        )
        initial_point = self._bind_knowledge_context(session, profile.coverage[0], None, [])
        decision = PolicyDecision(
            action=PolicyAction.INITIAL,
            reason="start with the highest-priority uncovered material anchor",
            difficulty=request.config.difficulty,
            coveragePointId=initial_point.id,
            rootId=self._ids.new_id("root"),
            followUpDepth=0,
        )
        question = await self._generate_question(session, initial_point, decision, "")
        session.currentQuestion = question
        try:
            await self._store.create(session)
        except StoreConflict as err:
            raise conflict("interview identifier already exists", err)
        except Exception as err:
            raise internal("could not persist interview", err)

        return StartResponse(
            interviewId=interview_id,
            state=session.state,
            profile=session.profile,
            question=question,
            progress=progress_for(session),
        )

    # --- answer ---

    async def answer(self, request: AnswerRequest) -> AnswerResponse:
        validate_answer(request)
        loaded = await self._load(request.interviewId)
        async with self._coordinator.acquire(answer_coordination_key(loaded.clientSessionId, loaded.id)):
            session = await self._load(request.interviewId)
            execution, replay = await self._begin_answer_execution(session, request)
            if replay is not None:
                return replay
            try:
                response, updated, expected_version = await self._prepare_answer(session, request)
            except Exception as err:
                await self._fail_answer_execution(execution, request.questionId, err)
                raise
            try:
                await self._commit_answer_execution(execution, updated, expected_version, request.questionId, response)
            except Exception as err:
                replayed = await self._replay_succeeded_answer(execution)
                if replayed is not None:
                    return replayed
                await self._fail_answer_execution(execution, request.questionId, err)
                if isinstance(err, StoreConflict):
                    raise conflict("answer raced with another update; reload the active question", err)
                if isinstance(err, StoreNotFound):
                    raise DomainError(ErrorCode.NOT_FOUND, "interview not found", cause=err)
                raise internal("could not persist answer", err)
            return response

    async def _prepare_answer(self, session, request):
        if session.state == InterviewState.COMPLETED:
            raise conflict("the interview no longer accepts answers", None)
        if session.state != InterviewState.AWAITING_ANSWER or session.currentQuestion is None:
            raise DomainError(ErrorCode.INVALID_STATE, "interview is not awaiting an answer")
        if session.currentQuestion.id != request.questionId:
            raise conflict("questionId is stale or does not match the active question", None)

        expected_version = session.version
        assessment = await self._assess_answer(session, session.currentQuestion, request.answer)
        decision = derive_policy(session, assessment)
        record = AnswerRecord(
            question=session.currentQuestion,
            answer=request.answer,
            assessment=assessment,
            decision=decision,
            answeredAt=self._clock.now(),
        )
        session.answers.append(record)

        next_question = None
        if decision.action == PolicyAction.COMPLETE:
            session.state = InterviewState.COMPLETED
            session.completedAt = self._clock.now()
            session.currentQuestion = None
        else:
            point = coverage_point_by_id(session.profile, decision.coveragePointId)
            if decision.action == PolicyAction.ADVANCE:
                selection = await self._plan_next_coverage(session, record)
                selected_point, position, exists = find_coverage_point(session.profile, selection.coveragePointId)
                if not exists:
                    raise unavailable(
                        "coverage planning returned an invalid target",
                        ValueError(f'unknown coverage point "{selection.coveragePointId}"'),
                    )
                point = selected_point
                session.coverageCursor = position
                decision.coveragePointId = point.id
                decision.rootId = self._ids.new_id("root")
                decision.reason = selection.reason.strip()
                session.answers[-1].decision = decision
            point = self._bind_knowledge_context(session, point, record.question, assessment.gaps)
            question = await self._generate_question(session, point, decision, request.questionId)
            session.currentQuestion = question
            next_question = question

        session.version += 1

        feedback = AnswerFeedback(
            focus=coverage_point_by_id(session.profile, record.question.coveragePointId).area,
        )
        if session.config.feedbackMode == FeedbackMode.DEFERRED:
            feedback.deferred = True
        else:
            feedback.assessment = public_assessment(session.sources, assessment)
            feedback.summary = assessment_summary(feedback.focus, assessment)

        response = AnswerResponse(
            interviewId=session.id,
            state=session.state,
            feedback=feedback,
            nextQuestion=next_question,
            progress=progress_for(session),
            reportReady=session.state == InterviewState.COMPLETED,
        )
        return response, session, expected_version

    # --- answer idempotency ---

    async def _begin_answer_execution(self, session, request):
        request_hash = answer_request_hash(request)
        scope = answer_command_scope(session.clientSessionId, session.id, request.clientAnswerId)
        execution = AnswerExecution(session_id=session.id, scope=scope)
        if self._persistence is None:
            return self._begin_memory_answer(execution, session, request, request_hash)
        if self._answer_committer is None:
            raise internal("answer persistence does not support atomic commits")
        try:
            command, _created = await self._persistence.create_or_get_command(CommandSpec(
                id=self._ids.new_id("command"),
                principal_id=session.clientSessionId,
                session_id=session.id,
                action=Action.ANSWER.value,
                idempotency_key=request.clientAnswerId,
                subject_id=request.questionId,
                request_hash=request_hash,
            ))
        except IdempotencyConflict as err:
            raise conflict("clientAnswerId was already used with a different answer", err)
        except AnswerAlreadyCommitted as err:
            raise conflict("question already has an accepted answer", err)
        except PersistenceConflict as err:
            raise internal("could not persist answer command", err)
        execution.command_id = command.id
        execution.durable = True
        if command.status == CommandStatus.SUCCEEDED:
            return execution, decode_answer_response(command.result)
        if command.status == CommandStatus.RUNNING:
            raise unavailable(
                "answer is already being processed; retry with the same clientAnswerId", PersistenceConflict()
            )
        if command.status not in (CommandStatus.PENDING, CommandStatus.FAILED):
            raise internal("answer command has an invalid state")
        try:
            command = await self._persistence.transition_command(
                command.id, command.status, CommandTransition(status=CommandStatus.RUNNING)
            )
        except Exception as err:
            replay = await self._replay_succeeded_answer(execution)
            if replay is not None:
                return execution, replay
            raise unavailable("answer is already being processed; retry with the same clientAnswerId", err)
        if command.status != CommandStatus.RUNNING:
            raise internal("could not start answer command")
        try:
            await self._append_answer_event(execution, "answer.started", {
                "questionId": request.questionId,
                "status": CommandStatus.RUNNING.value,
            })
        except Exception as err:
            await self._fail_answer_execution(execution, request.questionId, err)
            raise internal("could not record answer start", err)
        return execution, None

    def _begin_memory_answer(self, execution, session, request, request_hash):
        topic = answer_topic_scope(session.clientSessionId, session.id, request.questionId)
        command = self._memory_answers.get(execution.scope)
        if command is not None:
            if command.request_hash != request_hash:
                raise conflict("clientAnswerId was already used with a different answer", IdempotencyConflict())
            if command.status == CommandStatus.SUCCEEDED:
                return execution, decode_answer_response(command.result)
            if command.status == CommandStatus.RUNNING:
                raise unavailable(
                    "answer is already being processed; retry with the same clientAnswerId", PersistenceConflict()
                )
            if command.status in (CommandStatus.FAILED, CommandStatus.PENDING):
                command.status = CommandStatus.RUNNING
                command.result = None
                self._memory_answers[execution.scope] = command
                return execution, None
            raise internal("answer command has an invalid state")
        existing_scope = self._memory_answer_topics.get(topic)
        if existing_scope is not None and existing_scope != execution.scope:
            raise conflict("question already has an accepted answer", AnswerAlreadyCommitted())
        self._memory_answer_topics[topic] = execution.scope
        self._memory_answers[execution.scope] = MemoryAnswerCommand(
            request_hash=request_hash, status=CommandStatus.RUNNING, result=None
        )
        return execution, None

    async def _commit_answer_execution(self, execution, session, expected_version, question_id, response):
        result = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
        if not execution.durable:
            await self._store.save(session, expected_version)
            command = self._memory_answers[execution.scope]
            command.status = CommandStatus.SUCCEEDED
            command.result = result
            self._memory_answers[execution.scope] = command
            return
        event_payload = json.dumps({
            "questionId": question_id,
            "status": CommandStatus.SUCCEEDED.value,
            "state": response.state.value,
            "nextQuestionId": question_id_for(response.nextQuestion),
            "answeredCount": response.progress.answered,
        }, ensure_ascii=False)
        await self._answer_committer.commit_answer(AnswerCommitSpec(
            session=session,
            expected_version=expected_version,
            command_id=execution.command_id,
            result=result,
            event=SessionEventSpec(
                event_id=self._ids.new_id("event"),
                session_id=session.id,
                command_id=execution.command_id,
                type="answer.committed",
                payload=event_payload,
            ),
        ))

    async def _fail_answer_execution(self, execution, question_id, failure):
        code, retryable = safe_answer_failure(failure)
        payload = json.dumps({
            "questionId": question_id,
            "status": CommandStatus.FAILED.value,
            "errorCode": code,
            "retryable": retryable,
        }, ensure_ascii=False)
        if not execution.durable:
            command = self._memory_answers.get(execution.scope)
            if command is not None and command.status == CommandStatus.RUNNING:
                command.status = CommandStatus.FAILED
                command.result = None
                self._memory_answers[execution.scope] = command
            return
        try:
            await self._persistence.transition_command(
                execution.command_id, CommandStatus.RUNNING,
                CommandTransition(status=CommandStatus.FAILED, error=payload),
            )
        except Exception:
            return
        try:
            await self._append_answer_event(execution, "answer.failed", {
                "questionId": question_id,
                "status": CommandStatus.FAILED.value,
                "errorCode": code,
                "retryable": retryable,
            })
        except Exception:
            pass

    async def _replay_succeeded_answer(self, execution):
        if not execution.durable:
            command = self._memory_answers.get(execution.scope)
            if command is None or command.status != CommandStatus.SUCCEEDED:
                return None
            return decode_answer_response(command.result)
        try:
            command = await self._persistence.get_command(execution.command_id)
        except Exception:
            return None
        if command.status != CommandStatus.SUCCEEDED:
            return None
        return decode_answer_response(command.result)

    async def _append_answer_event(self, execution, event_type, payload):
        if not execution.durable:
            return
        encoded = json.dumps(payload, ensure_ascii=False)
        await self._persistence.append_session_event(SessionEventSpec(
            event_id=self._ids.new_id("event"),
            session_id=execution.session_id,
            command_id=execution.command_id,
            type=event_type,
            payload=encoded,
        ))

    # --- report ---

    async def report(self, request: ReportRequest) -> ReportResponse:
        if request.action != Action.REPORT:
            raise validation("action", "must be report")
        if not request.interviewId.strip():
            raise validation("interviewId", "is required")
        session = await self._load(request.interviewId)
        if session.state != InterviewState.COMPLETED:
            raise DomainError(ErrorCode.INVALID_STATE, "report is available only after the interview is completed")
        if session.report is not None:
            return ReportResponse(interviewId=session.id, state=session.state, report=session.report)

        expected_version = session.version
        report = await self._generate_report(session)
        session.report = report
        session.version += 1
        try:
            await self._store.save(session, expected_version)
        except StoreConflict as err:
            latest = None
            try:
                latest = await self._store.load(session.id)
            except Exception:
                latest = None
            if latest is not None and latest.report is not None:
                return ReportResponse(interviewId=latest.id, state=latest.state, report=latest.report)
            raise conflict("report raced with another update", err)
        except Exception as err:
            raise internal("could not persist report", err)
        return ReportResponse(interviewId=session.id, state=session.state, report=report)

    # --- recovery projections ---

    async def snapshot(self, interview_id: str) -> SessionSnapshot:
        if not interview_id.strip():
            raise validation("interviewId", "is required")
        session = await self._load(interview_id)

        public_profile = session.profile.model_copy(deep=True)
        public_profile_evidence(session.sources, public_profile)
        turns: list[SnapshotTurn] = []
        for stored in session.answers:
            record = stored.model_copy(deep=True)
            public_record_evidence(session.sources, record)
            focus = coverage_point_by_id(session.profile, stored.question.coveragePointId).area
            feedback = AnswerFeedback(focus=focus)
            if session.config.feedbackMode == FeedbackMode.DEFERRED and session.state != InterviewState.COMPLETED:
                feedback.deferred = True
            else:
                feedback.assessment = record.assessment
                feedback.summary = assessment_summary(focus, record.assessment)
            turns.append(SnapshotTurn(question=record.question, answer=record.answer, feedback=feedback))

        current = None
        if session.currentQuestion is not None:
            question = session.currentQuestion.model_copy(deep=True)
            question.evidenceRefs = public_question_evidence(session.sources, question.evidenceRefs)
            question.adaptation.reason = public_decision_reason(question.adaptation.trigger)
            if question_leaks_knowledge_reference(session.sources, question.text, evidence_id_set(question.evidenceRefs)):
                question.text = public_question_summary(question.evidenceRefs)
            current = question

        return SessionSnapshot(
            interviewId=session.id,
            state=session.state,
            profile=public_profile,
            currentQuestion=current,
            turns=turns,
            progress=progress_for(session),
            reportReady=session.state == InterviewState.COMPLETED,
        )

    async def review(self, interview_id: str) -> ReviewSnapshot:
        if not interview_id.strip():
            raise validation("interviewId", "is required")
        session = await self._load(interview_id)

        turns: list[ReviewTurn] = []
        for stored in session.answers:
            record = stored.model_copy(deep=True)
            public_record_evidence(session.sources, record)
            focus = coverage_point_by_id(session.profile, stored.question.coveragePointId).area
            feedback = AnswerFeedback(
                focus=focus,
                assessment=record.assessment,
                summary=assessment_summary(focus, record.assessment),
            )
            turns.append(ReviewTurn(
                question=record.question,
                answer=record.answer,
                feedback=feedback,
                references=review_references(stored.question.evidenceRefs),
                answeredAt=stored.answeredAt,
            ))
        return ReviewSnapshot(
            schemaVersion="1.0.0",
            interviewId=session.id,
            state=session.state,
            startedAt=session.startedAt,
            generatedAt=self._clock.now(),
            turns=turns,
        )

    async def session_events(self, interview_id: str, after_sequence: int, limit: int) -> SessionEventPage:
        if not interview_id.strip():
            raise validation("interviewId", "is required")
        if after_sequence < 0:
            raise validation("after", "must not be negative")
        await self._load(interview_id)
        repository = self._persistence
        if repository is None:
            raise unavailable("interview event recovery is not configured", ValueError("persistence repository is unavailable"))
        try:
            events = await repository.list_session_events(interview_id, after_sequence, limit)
        except Exception as err:
            raise unavailable("interview events are temporarily unavailable", err)
        summaries: list[SessionEventSummary] = []
        next_sequence = after_sequence
        for event in events:
            summaries.append(SessionEventSummary(
                eventId=event.event_id, sequence=event.sequence, commandId=event.command_id,
                type=event.type, createdAt=event.created_at,
            ))
            if event.sequence > next_sequence:
                next_sequence = event.sequence
        return SessionEventPage(interviewId=interview_id, events=summaries, nextSequence=next_sequence)

    # --- agent calls ---

    async def _generate_question(self, session, point, decision, based_on):
        if self._agent is None:
            raise unavailable("interview question generation is temporarily unavailable", ValueError("interview agent is not configured"))
        allowed = {ref.anchorId for ref in point.evidenceRefs}
        profile, history = public_question_context(session)
        anchors = public_question_anchors(anchors_for_evidence(session.sources, point.evidenceRefs))
        public_decision = decision.model_copy()
        public_decision.reason = public_decision_reason(decision.action)
        request = GenerateQuestionRequest(profile=profile, decision=public_decision, anchors=anchors, history=history)
        try:
            draft = await self._agent.generate_question(request)
        except Exception as err:
            raise unavailable("interview question generation is temporarily unavailable", _wrap("generate question", err))
        try:
            validate_question_draft(session.sources, draft, allowed, session.answers)
        except ValueError as validation_err:
            request.repair = RepairInstruction(
                reason=str(validation_err),
                allowedEvidence=public_question_evidence(session.sources, point.evidenceRefs),
            )
            try:
                draft = await self._agent.generate_question(request)
            except Exception as err:
                raise unavailable("interview question generation is temporarily unavailable", _wrap(f"repair question after {validation_err}", err))
            try:
                validate_question_draft(session.sources, draft, allowed, session.answers)
            except ValueError as repair_err:
                raise unavailable(
                    "interview question generation could not produce a grounded result",
                    _wrap("question repair validation", repair_err),
                )

        evidence = canonical_evidence(session.sources, point.evidenceRefs)
        return Question(
            id=self._ids.new_id("question"),
            rootId=decision.rootId,
            text=draft.text.strip(),
            kind=question_kind(point, decision),
            difficulty=decision.difficulty,
            coveragePointId=point.id,
            evidenceRefs=evidence,
            adaptation=QuestionAdaptation(
                trigger=decision.action,
                reason=public_decision_reason(decision.action),
                basedOnQuestionId=based_on,
                followUpAxis=decision.followUpAxis,
                depth=decision.followUpDepth,
            ),
        )

    async def _assess_answer(self, session, question, answer):
        if self._agent is None:
            raise unavailable("interview assessment is temporarily unavailable", ValueError("interview agent is not configured"))
        question_anchors = anchors_for_evidence(session.sources, question.evidenceRefs)
        allowed = {anchor.id for anchor in question_anchors}
        request = AssessAnswerRequest(question=question, answer=answer, anchors=question_anchors, history=session.answers)
        try:
            assessment = await self._agent.assess_answer(request)
        except Exception as err:
            raise unavailable("interview assessment is temporarily unavailable", _wrap("assess answer", err))
        try:
            validate_assessment(session.sources, assessment, allowed)
        except ValueError as validation_err:
            request.repair = RepairInstruction(
                reason=str(validation_err),
                allowedEvidence=evidence_for_anchors(question_anchors),
            )
            try:
                assessment = await self._agent.assess_answer(request)
            except Exception as err:
                raise unavailable("interview assessment is temporarily unavailable", _wrap(f"repair assessment after {validation_err}", err))
            try:
                validate_assessment(session.sources, assessment, allowed)
            except ValueError as repair_err:
                raise unavailable(
                    "interview assessment could not produce a valid grounded result",
                    _wrap("assessment repair validation", repair_err),
                )
        assessment = normalize_assessment(assessment)
        if assessment.evidenceRefs:
            assessment.evidenceRefs = canonical_evidence(session.sources, assessment.evidenceRefs)
        for i in range(len(assessment.claimChecks)):
            assessment.claimChecks[i].evidenceRefs = canonical_evidence(
                session.sources, assessment.claimChecks[i].evidenceRefs
            )
        return assessment

    async def _generate_report(self, session):
        if self._agent is None:
            raise unavailable("interview report generation is temporarily unavailable", ValueError("interview agent is not configured"))
        reporter_profile = session.profile.model_copy(deep=True)
        public_profile_evidence(session.sources, reporter_profile)
        reporter_answers = [a.model_copy(deep=True) for a in session.answers]
        for record in reporter_answers:
            public_record_evidence(session.sources, record)
        public_anchors = public_question_anchors(all_anchors(session.sources))
        request = GenerateReportRequest(profile=reporter_profile, answers=reporter_answers, anchors=public_anchors)
        try:
            draft = await self._agent.generate_report(request)
        except Exception as err:
            raise unavailable("interview report generation is temporarily unavailable", _wrap("generate report", err))
        try:
            validate_report_draft(session.sources, draft)
        except ValueError as validation_err:
            request.repair = RepairInstruction(
                reason=str(validation_err),
                allowedEvidence=evidence_for_anchors(public_anchors),
            )
            try:
                draft = await self._agent.generate_report(request)
            except Exception as err:
                raise unavailable("interview report generation is temporarily unavailable", _wrap(f"repair report after {validation_err}", err))
            try:
                validate_report_draft(session.sources, draft)
            except ValueError as repair_err:
                raise unavailable(
                    "interview report generation could not produce a valid grounded result",
                    _wrap("report repair validation", repair_err),
                )

        audit_turns = []
        for record in session.answers:
            audit_turns.append(AuditTurn(
                questionId=record.question.id,
                rootId=record.question.rootId,
                coveragePointId=record.question.coveragePointId,
                evidenceRefs=record.question.evidenceRefs,
                inputMode=record.answer.inputMode,
                durationMs=record.answer.durationMs,
                answeredAt=record.answeredAt,
            ))
        generated_at = self._clock.now()
        private_profile = session.profile.model_copy(deep=True)
        private_answers = [a.model_copy(deep=True) for a in session.answers]
        return Report(
            overallScore=score_report(session.answers),
            summary=draft.summary.strip(),
            strengths=list(draft.strengths),
            gaps=list(draft.gaps),
            evidenceRefs=canonical_evidence(session.sources, draft.evidenceRefs),
            profile=private_profile,
            turns=private_answers,
            audit=ReportAudit(
                clientSessionId=session.clientSessionId,
                startedAt=session.startedAt,
                completedAt=session.completedAt,
                generatedAt=generated_at,
                turns=audit_turns,
            ),
        )

    # --- knowledge ---

    def _build_query(self, *, model="", focus=Focus.MIXED, jd="", resume="", coverage_point_id="",
                     objective="", question="", previous_gaps=None) -> RetrieverKnowledgeQuery:
        return RetrieverKnowledgeQuery(
            model=model,
            focus=_RETRIEVER_FOCUS[focus],
            jd=jd,
            resume=resume,
            coverage_point_id=coverage_point_id,
            objective=objective,
            question=question,
            previous_gaps=list(previous_gaps or []),
        )

    def _retrieve_knowledge(self, query: RetrieverKnowledgeQuery):
        if self._retriever is None:
            return []
        documents = self._retriever.retrieve(query)
        if documents is None:
            return []
        return list(documents)

    def _bind_knowledge_context(self, session, point, previous, gaps):
        if self._retriever is None:
            return point
        documents = self._retrieve_knowledge(self._build_query(
            model=session.model,
            focus=point.area,
            jd=source_content(session.sources, SourceKind.JD),
            resume=source_content(session.sources, SourceKind.RESUME),
            coverage_point_id=point.id,
            objective=point.label,
            question=previous.text if previous is not None else "",
            previous_gaps=gaps,
        ))
        if not documents:
            return point
        knowledge_refs = merge_knowledge_documents(session.sources, documents, MAX_KNOWLEDGE_EVIDENCE_PER_QUESTION)
        if not knowledge_refs:
            return point
        bundle = [ref for ref in point.evidenceRefs if ref.kind != SourceKind.KNOWLEDGE]
        bundle.extend(knowledge_refs)
        point.evidenceRefs = canonical_evidence(session.sources, bundle)
        for i in range(len(session.profile.coverage)):
            if session.profile.coverage[i].id == point.id:
                session.profile.coverage[i] = point
                break
        return point

    async def _plan_next_coverage(self, session, previous):
        candidates = coverage_candidates(session)
        if not candidates:
            raise unavailable("coverage planning has no candidate targets", ValueError("profile coverage is empty"))
        if self._planner is None:
            candidate = least_covered_candidate(candidates, session.currentQuestion.coveragePointId)
            return CoverageSelection(
                coveragePointId=candidate.coveragePointId,
                reason="select the highest-priority least-covered target available to this non-production planner fixture",
                signals=["coverage count", "business priority"],
            )

        question_kind_counts: dict[QuestionKind, int] = {}
        for answer in session.answers:
            question_kind_counts[answer.question.kind] = question_kind_counts.get(answer.question.kind, 0) + 1
        planner_history = public_planner_history(session)
        public_previous = previous
        if planner_history:
            public_previous = planner_history[-1]
        try:
            selection = await self._planner.plan_coverage(PlanCoverageRequest(
                config=session.config,
                currentCoveragePointId=session.currentQuestion.coveragePointId,
                previousQuestion=public_previous.question,
                previousAssessment=public_previous.assessment,
                candidates=candidates,
                questionKindCounts=question_kind_counts,
                history=planner_history,
                remainingQuestions=max(0, session.config.questionCount - len(session.answers)),
            ))
        except Exception as err:
            raise unavailable("coverage planning is temporarily unavailable", _wrap("plan next coverage", err))
        if not selection.reason.strip() or not selection.signals:
            raise unavailable("coverage planning returned an invalid proposal", ValueError("selection reason and signals are required"))
        if not find_coverage_point(session.profile, selection.coveragePointId)[2]:
            raise unavailable("coverage planning returned an invalid target", ValueError(f'unknown coverage point "{selection.coveragePointId}"'))
        if len(candidates) > 1 and selection.coveragePointId == session.currentQuestion.coveragePointId:
            raise unavailable("coverage planning returned an invalid target", ValueError("planner reselected the current coverage point despite alternatives"))
        return selection

    async def _load(self, id):
        try:
            return await self._store.load(id)
        except StoreNotFound as err:
            raise DomainError(ErrorCode.NOT_FOUND, "interview not found", cause=err)
        except Exception as err:
            raise internal("could not load interview", err)


# --- request/validation helpers ---

def with_config_defaults(config: InterviewConfig) -> InterviewConfig:
    if config.questionCount == 0:
        config.questionCount = 6
    if not config.language.strip():
        config.language = "zh-CN"
    return config


def validate_start(request: StartRequest) -> None:
    if request.action != Action.START:
        raise validation("action", "must be start")
    if not request.clientSessionId.strip():
        raise validation("clientSessionId", "is required")
    if request.config.focus not in (Focus.MIXED, Focus.KNOWLEDGE, Focus.PROJECTS):
        raise validation("config.focus", "must be mixed, knowledge, or projects")
    if request.config.difficulty not in (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD):
        raise validation("config.difficulty", "must be easy, medium, or hard")
    if request.config.questionCount < 1 or request.config.questionCount > 20:
        raise validation("config.questionCount", "must be between 1 and 20")
    if request.config.feedbackMode not in (FeedbackMode.IMMEDIATE, FeedbackMode.DEFERRED):
        raise validation("config.feedbackMode", "must be immediate or deferred")


def validate_answer(request: AnswerRequest) -> None:
    if request.action != Action.ANSWER:
        raise validation("action", "must be answer")
    if not request.interviewId.strip():
        raise validation("interviewId", "is required")
    if not request.questionId.strip():
        raise validation("questionId", "is required")
    if not request.clientAnswerId.strip():
        raise validation("clientAnswerId", "is required")
    if not request.answer.text.strip():
        raise validation("answer.text", "is required")
    if request.answer.inputMode not in (InputMode.TEXT, InputMode.VOICE):
        raise validation("answer.inputMode", "must be text or voice")
    if request.answer.durationMs < 0:
        raise validation("answer.durationMs", "must not be negative")


def answer_request_hash(request: AnswerRequest) -> str:
    answer = {"text": request.answer.text, "inputMode": request.answer.inputMode.value}
    if request.answer.durationMs != 0:
        answer["durationMs"] = request.answer.durationMs
    payload = {"questionId": request.questionId, "answer": answer}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decode_answer_response(payload: str | None) -> AnswerResponse | None:
    if not payload:
        raise internal("stored answer result is empty")
    try:
        return AnswerResponse.model_validate(json.loads(payload))
    except Exception as err:
        raise internal("stored answer result is invalid", err)


def safe_answer_failure(err: Exception) -> tuple[str, bool]:
    if isinstance(err, DomainError):
        return err.code, err.code in (ErrorCode.SERVICE_UNAVAILABLE, ErrorCode.INTERNAL)
    return ErrorCode.INTERNAL, True


def answer_command_scope(principal_id: str, session_id: str, client_answer_id: str) -> str:
    return structured_scope(principal_id, session_id, Action.ANSWER.value, client_answer_id)


def answer_topic_scope(principal_id: str, session_id: str, question_id: str) -> str:
    return structured_scope(principal_id, session_id, Action.ANSWER.value, question_id)


def answer_coordination_key(principal_id: str, session_id: str) -> str:
    return structured_scope(principal_id, session_id)


def structured_scope(*parts: str) -> str:
    return json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))


def question_id_for(question: Question | None) -> str:
    if question is None:
        return ""
    return question.id


def material_text(material) -> str:
    if material is None:
        return ""
    return material.text


def source_content(index, kind: SourceKind) -> str:
    parts = []
    for document in index.documents.values():
        if document.kind == kind and document.content.strip():
            parts.append(document.content)
    return "\n".join(parts)


def find_coverage_point(profile, id: str) -> tuple[CoveragePoint | None, int, bool]:
    for index, point in enumerate(profile.coverage):
        if point.id == id:
            return point, index, True
    return None, -1, False


def coverage_candidates(session: InterviewSession) -> list[CoverageCandidate]:
    counts: dict[str, int] = {}
    last_asked: dict[str, int] = {}
    for turn, answer in enumerate(session.answers):
        counts[answer.question.coveragePointId] = counts.get(answer.question.coveragePointId, 0) + 1
        last_asked[answer.question.coveragePointId] = turn + 1
    candidates: list[CoverageCandidate] = []
    for point in session.profile.coverage:
        label = point.label
        evidence = public_question_evidence(session.sources, point.evidenceRefs)
        if evidence and evidence[0].kind == SourceKind.KNOWLEDGE:
            label = concise(knowledge_question_label(evidence[0].quote), 120)
        elif first_folded_marker(label, KNOWLEDGE_REFERENCE_MARKERS) >= 0:
            label = concise(knowledge_question_label(label), 120)
        candidates.append(CoverageCandidate(
            coveragePointId=point.id,
            area=point.area,
            label=label,
            priority=coverage_priority(session.config.focus, point),
            questionCount=counts.get(point.id, 0),
            lastAskedTurn=last_asked.get(point.id, 0),
            evidenceRefs=evidence,
        ))
    return candidates


def coverage_priority(focus: Focus, point: CoveragePoint) -> int:
    priority = 50
    if focus == Focus.MIXED or focus == point.area:
        priority += 20
    for ref in point.evidenceRefs:
        if ref.kind == SourceKind.JD:
            priority += 20
        elif ref.kind == SourceKind.RESUME:
            priority += 15
        elif ref.kind == SourceKind.KNOWLEDGE:
            priority += 10
    return priority


def least_covered_candidate(candidates: list[CoverageCandidate], current_id: str) -> CoverageCandidate:
    selected = candidates[0]
    found_alternative = False
    for candidate in candidates:
        if len(candidates) > 1 and candidate.coveragePointId == current_id:
            continue
        if (
            not found_alternative
            or candidate.questionCount < selected.questionCount
            or (candidate.questionCount == selected.questionCount and candidate.priority > selected.priority)
            or (
                candidate.questionCount == selected.questionCount
                and candidate.priority == selected.priority
                and candidate.lastAskedTurn < selected.lastAskedTurn
            )
        ):
            selected = candidate
            found_alternative = True
    return selected


def progress_for(session: InterviewSession) -> Progress:
    current = 0
    depth = 0
    if session.currentQuestion is not None:
        current = len(session.answers) + 1
        depth = session.currentQuestion.adaptation.depth
    return Progress(
        answered=len(session.answers),
        total=session.config.questionCount,
        current=current,
        followUpDepth=depth,
    )


def assessment_summary(area: Focus, assessment: Assessment) -> str:
    if assessment.factualErrors or assessment.correctness <= 1:
        return "回答存在事实或前置理解问题，下一题先验证基础。"
    if vague_assessment_for_area(area, assessment):
        if area == Focus.KNOWLEDGE:
            return "回答仍缺少关键机制、适用边界或具体场景，下一题会沿知识缺口继续追问。"
        return "回答仍偏空泛，下一题会追问个人职责、量化指标或方案取舍。"
    if strong_assessment_for_area(area, assessment):
        return "回答具体且有深度，下一题提高难度并轮换覆盖点。"
    return "回答达到当前要求，下一题轮换到新的材料覆盖点。"


# --- public projections ---

def public_question_context(session: InterviewSession):
    profile = session.profile.model_copy(deep=True)
    answers = [a.model_copy(deep=True) for a in session.answers]
    public_profile_evidence(session.sources, profile)
    for record in answers:
        public_record_evidence(session.sources, record)
    return profile, answers


def public_planner_history(session: InterviewSession) -> list[AnswerRecord]:
    answers = [a.model_copy(deep=True) for a in session.answers]
    for record in answers:
        public_record_evidence(session.sources, record)
    return answers


def public_record_evidence(index, record: AnswerRecord) -> None:
    record.question.evidenceRefs = public_question_evidence(index, record.question.evidenceRefs)
    record.question.adaptation.reason = public_decision_reason(record.question.adaptation.trigger)
    if question_leaks_knowledge_reference(index, record.question.text, evidence_id_set(record.question.evidenceRefs)):
        record.question.text = public_question_summary(record.question.evidenceRefs)
    record.assessment = public_assessment(index, record.assessment)
    record.decision.reason = public_decision_reason(record.decision.action)


def public_assessment(index, assessment: Assessment) -> Assessment:
    assessment.evidenceRefs = public_question_evidence(index, assessment.evidenceRefs)
    assessment.factualErrors = public_generated_strings(index, assessment.factualErrors)
    assessment.strengths = public_generated_strings(index, assessment.strengths)
    assessment.gaps = public_generated_strings(index, assessment.gaps)
    checks: list[ClaimCheck] = []
    for check in assessment.claimChecks:
        if generated_text_leaks_knowledge_reference(index, check.claim):
            continue
        check.evidenceRefs = public_question_evidence(index, check.evidenceRefs)
        checks.append(check)
    assessment.claimChecks = checks
    return assessment


def public_generated_strings(index, values: list[str]) -> list[str]:
    result = []
    for value in values:
        if not value.strip() or generated_text_leaks_knowledge_reference(index, value):
            continue
        result.append(value)
    return result


def generated_text_leaks_knowledge_reference(index, value: str) -> bool:
    return value.strip() != "" and public_generated_text(value, all_evidence(index)) == ""


def public_profile_evidence(index, profile) -> None:
    for point in profile.jd.requirements:
        point.evidenceRefs = public_question_evidence(index, point.evidenceRefs)
    for point in profile.jd.responsibilities:
        point.evidenceRefs = public_question_evidence(index, point.evidenceRefs)
    for point in profile.resume.projects:
        point.evidenceRefs = public_question_evidence(index, point.evidenceRefs)
    for point in profile.coverage:
        point.evidenceRefs = public_question_evidence(index, point.evidenceRefs)
        if point.evidenceRefs and point.evidenceRefs[0].kind == SourceKind.KNOWLEDGE:
            point.label = concise(knowledge_question_label(point.evidenceRefs[0].quote), 120)


def public_question_anchors(anchors) -> list:
    result = []
    for anchor in anchors:
        if anchor.kind == SourceKind.KNOWLEDGE:
            anchor = anchor.model_copy(deep=True)
            anchor.text = public_knowledge_question(anchor.text)
        result.append(anchor)
    return result


def public_question_evidence(index, refs: list[EvidenceRef]) -> list[EvidenceRef]:
    canonical = canonical_evidence(index, refs)
    for ref in canonical:
        ref.quote = public_evidence_quote(ref)
    return canonical


def evidence_for_anchors(anchors) -> list[EvidenceRef]:
    return [evidence_from_anchor(anchor) for anchor in anchors]


def evidence_id_set(refs: list[EvidenceRef]) -> set[str]:
    return {ref.anchorId for ref in refs}


def public_question_summary(refs: list[EvidenceRef]) -> str:
    for ref in refs:
        if ref.kind == SourceKind.KNOWLEDGE:
            return public_evidence_quote(ref)
    return "此前面试问题"


def public_decision_reason(action: PolicyAction) -> str:
    if action == PolicyAction.INITIAL:
        return "按当前覆盖目标生成首题。"
    if action == PolicyAction.PREREQUISITE:
        return "上一轮存在知识或事实风险，先核对前置理解。"
    if action == PolicyAction.FOLLOW_UP:
        return "根据上一轮评估继续追问当前能力。"
    if action == PolicyAction.ADVANCE:
        return "当前覆盖点已完成，切换到尚未覆盖的能力。"
    if action == PolicyAction.COMPLETE:
        return "达到本场问题上限，完成面试。"
    return "根据本轮评估调整后续问题。"


# --- draft/assessment validation ---

def validate_question_draft(index, draft: QuestionDraft, allowed: set[str], history) -> None:
    if not draft.text.strip():
        raise ValueError("question text is empty")
    validate_question_evidence_refs(index, draft.evidenceRefs, allowed, True)
    if question_leaks_knowledge_reference(index, draft.text, allowed):
        raise ValueError("question text exposes private knowledge reference content")
    if repeats_recent_question(draft.text, history):
        raise ValueError("question text repeats a recent interview question")


def validate_question_evidence_refs(index, refs: list[EvidenceRef], allowed: set[str] | None, require: bool) -> None:
    if require and not refs:
        raise ValueError("at least one evidence reference is required")
    for ref in refs:
        anchor = index.anchors.get(ref.anchorId)
        if anchor is None:
            raise ValueError(f'unknown anchor "{ref.anchorId}"')
        if allowed is not None and ref.anchorId not in allowed:
            raise ValueError(f'anchor "{ref.anchorId}" is outside the allowed evidence set')
        if ref.sourceId != anchor.sourceId or ref.kind != anchor.kind or ref.locator != anchor.locator:
            raise ValueError(f'metadata does not match anchor "{ref.anchorId}"')
        expected = evidence_from_anchor(anchor)
        expected.quote = public_evidence_quote(expected)
        if normalize_evidence_text(ref.quote) != normalize_evidence_text(expected.quote):
            raise ValueError(f'public quote does not match anchor "{ref.anchorId}"')


def question_leaks_knowledge_reference(index, question: str, allowed: set[str] | None) -> bool:
    if first_folded_marker(question, KNOWLEDGE_REFERENCE_MARKERS) >= 0:
        return True
    normalized_question = normalize_question_guard_text(question)
    if not normalized_question:
        return False
    for anchor_id in index.order:
        if allowed is not None and anchor_id not in allowed:
            continue
        anchor = index.anchors.get(anchor_id)
        if anchor is None or anchor.kind != SourceKind.KNOWLEDGE:
            continue
        reference = knowledge_reference_text(anchor.text)
        if not reference:
            continue
        fragments = [reference]
        fragments.extend(_split_on_punct_or_newline(reference))
        for fragment in fragments:
            normalized_fragment = normalize_question_guard_text(fragment)
            if len(normalized_fragment) >= 6 and normalized_fragment in normalized_question:
                return True
    return False


def _split_on_punct_or_newline(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch == "\n" or unicodedata.category(ch).startswith("P"):
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def repeats_recent_question(question: str, history: list[AnswerRecord]) -> bool:
    normalized = normalize_question_guard_text(question)
    if not normalized:
        return False
    start = max(0, len(history) - 6)
    for record in history[start:]:
        previous = normalize_question_guard_text(record.question.text)
        if not previous:
            continue
        if normalized == previous:
            return True
        shorter, longer = normalized, previous
        if len(shorter) > len(longer):
            shorter, longer = longer, shorter
        short_length = len(shorter)
        long_length = len(longer)
        if short_length >= 12 and short_length / long_length >= 0.85 and shorter in longer:
            return True
    return False


def validate_report_draft(index, draft: ReportDraft) -> None:
    if not draft.summary.strip():
        raise ValueError("report summary is empty")
    if generated_text_leaks_knowledge_reference(index, draft.summary):
        raise ValueError("report summary exposes private knowledge reference content")
    for item in list(draft.strengths) + list(draft.gaps):
        if generated_text_leaks_knowledge_reference(index, item):
            raise ValueError("report narrative exposes private knowledge reference content")
    validate_question_evidence_refs(index, draft.evidenceRefs, None, True)


def validate_assessment(index, assessment: Assessment, allowed: set[str]) -> None:
    for name, value in (
        ("correctness", assessment.correctness),
        ("depth", assessment.depth),
        ("specificity", assessment.specificity),
        ("ownership", assessment.ownership),
        ("metrics", assessment.metrics),
        ("tradeoffs", assessment.tradeoffs),
    ):
        if value < 1 or value > 5:
            raise ValueError(f"{name} must be between 1 and 5")
    for item in list(assessment.factualErrors) + list(assessment.strengths) + list(assessment.gaps):
        if generated_text_leaks_knowledge_reference(index, item):
            raise ValueError("assessment narrative exposes private knowledge reference content")
    validate_evidence_refs(index, assessment.evidenceRefs, allowed, True)
    for i, check in enumerate(assessment.claimChecks):
        if not check.claim.strip():
            raise ValueError(f"claimChecks[{i}].claim is empty")
        if generated_text_leaks_knowledge_reference(index, check.claim):
            raise ValueError(f"claimChecks[{i}].claim exposes private knowledge reference content")
        if check.verdict in (ClaimVerdict.SUPPORTED, ClaimVerdict.CONTRADICTED):
            validate_evidence_refs(index, check.evidenceRefs, allowed, True)
        elif check.verdict in (ClaimVerdict.UNVERIFIED, ClaimVerdict.NOT_IN_MATERIAL):
            validate_evidence_refs(index, check.evidenceRefs, allowed, False)
        else:
            raise ValueError(f'claimChecks[{i}].verdict "{check.verdict}" is invalid')


def _wrap(context: str, err: Exception) -> ValueError:
    return ValueError(f"{context}: {err}")
