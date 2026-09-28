"""Web compatibility projections mirroring Go httpapi/interview.go mapping helpers."""
from __future__ import annotations

import math

from ..interview.sources import (
    public_evidence_quote,
    public_generated_text,
    public_knowledge_question,
)
from ..interview.types import (
    Assessment,
    AnswerRecord,
    ClaimVerdict,
    EvidenceRef,
    Focus,
    InterviewState,
    PolicyAction,
    Progress,
    Profile,
    ProfilePoint,
    Question,
    QuestionKind,
    Report,
    SourceKind,
)


def _round(value: float) -> int:
    return int(math.floor(value + 0.5))


def _non_nil(values) -> list:
    return list(values) if values else []


def _min_int(left: int, right: int) -> int:
    return left if left < right else right


def _truncate_runes(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _append_unique_string(values: list, value: str) -> list:
    value = value.strip()
    if not value or value in values:
        return values
    return values + [value]


def web_state(state: InterviewState) -> str:
    return "completed" if state == InterviewState.COMPLETED else "questioning"


def map_profile(profile: Profile) -> dict:
    topics: list[str] = []
    for point in list(profile.jd.requirements) + list(profile.jd.responsibilities):
        topics = _append_unique_string(topics, point.label)
    for skill in profile.resume.skills:
        topics = _append_unique_string(topics, skill)
    projects: list[str] = []
    for project in profile.resume.projects:
        projects = _append_unique_string(projects, project.label)
    return {"targetRole": profile.jd.title, "topics": topics, "projects": projects}


def map_question(question: Question, index: int, profile: Profile | None = None) -> dict:
    return _map_question_with_privacy(question, index, profile, question.evidenceRefs)


def _map_question_with_privacy(question: Question, index: int, profile: Profile | None, privacy_refs: list[EvidenceRef]) -> dict:
    focus = question_focus(question)
    topic = question.coveragePointId
    if profile is not None:
        for point in profile.coverage:
            if point.id == question.coveragePointId:
                topic = public_coverage_topic(point)
                break
    if (not topic or topic == question.coveragePointId) and question.evidenceRefs:
        topic = public_evidence_quote(question.evidenceRefs[0])
    topic = _truncate_runes(topic, 90)

    kind = "opening"
    if question.adaptation.trigger == PolicyAction.FOLLOW_UP:
        kind = "follow_up"
    elif question.adaptation.trigger == PolicyAction.PREREQUISITE:
        kind = "verification"
    elif question.adaptation.trigger == PolicyAction.ADVANCE:
        kind = "opening"

    adaptation = None
    if question.adaptation.trigger != PolicyAction.INITIAL:
        strategy = "switch_topic"
        if question.adaptation.trigger == PolicyAction.FOLLOW_UP:
            strategy = "deepen"
            if focus == "project" and question.adaptation.followUpAxis == "ownership":
                strategy = "verify_resume"
        elif question.adaptation.trigger == PolicyAction.PREREQUISITE:
            strategy = "challenge"
        elif question.adaptation.trigger == PolicyAction.ADVANCE:
            strategy = "switch_topic"
        adaptation = {"strategy": strategy, "basedOn": adaptation_reason(question.adaptation)}

    if index <= 0:
        index = 1
    return {
        "id": question.id,
        "index": index,
        "text": public_question_text(question, privacy_refs),
        "kind": kind,
        "focus": focus,
        "topic": topic,
        "difficulty": question.difficulty.value,
        "depth": question.adaptation.depth,
        "maxDepth": 2,
        "parentQuestionId": question.adaptation.basedOnQuestionId,
        "evidenceRefs": map_evidence_refs(question.evidenceRefs),
        "adaptation": adaptation,
    }


def public_question_text(question: Question, privacy_refs: list[EvidenceRef]) -> str:
    privacy_refs = merge_privacy_evidence(privacy_refs, question.evidenceRefs)
    if (text := public_generated_text(question.text, privacy_refs)):
        return text
    for ref in question.evidenceRefs:
        if ref.kind == SourceKind.KNOWLEDGE:
            return public_evidence_quote(ref)
    return "当前问题内容已隐藏。"


def question_focus(question: Question) -> str:
    if question.kind == QuestionKind.PROJECT:
        return "project"
    for ref in question.evidenceRefs:
        if ref.kind == SourceKind.RESUME:
            return "project"
    return "knowledge"


def feedback_focus(focus: Focus) -> str:
    return "project" if focus == Focus.PROJECTS else "knowledge"


_REFERENCE_LABELS = {
    SourceKind.JD: "JD 要求",
    SourceKind.RESUME: "简历项目",
    SourceKind.KNOWLEDGE: "知识库",
}


def evidence_label(ref: EvidenceRef) -> str:
    label = _REFERENCE_LABELS.get(ref.kind, ref.kind.value)
    return f"{label} · {ref.sourceId}"


def map_evidence_refs(refs: list[EvidenceRef]) -> list[dict]:
    return [
        {
            "id": ref.anchorId,
            "source": ref.kind.value,
            "label": evidence_label(ref),
            "excerpt": public_evidence_quote(ref),
            "locator": ref.locator,
        }
        for ref in refs
    ]


def knowledge_question_excerpt(value: str) -> str:
    return _truncate_runes(public_knowledge_question(value), 180)


def public_coverage_topic(point) -> str:
    if point.evidenceRefs and point.evidenceRefs[0].kind == SourceKind.KNOWLEDGE:
        return public_evidence_quote(point.evidenceRefs[0])
    lower = point.label.lower()
    if any(marker in lower for marker in ("参考内容", "参考答案", "reference content", "reference answer")):
        return knowledge_question_excerpt(point.label)
    return point.label


def map_progress(progress: Progress) -> dict:
    percent = 0
    if progress.total > 0:
        percent = _round(progress.answered / progress.total * 100)
    return {
        "answered": progress.answered,
        "target": progress.total,
        "current": progress.current,
        "percent": _min_int(percent, 100),
    }


def map_feedback(question_id: str, assessment: Assessment, summary: str, focus: str) -> dict:
    return _map_feedback_with_privacy(question_id, assessment, summary, focus, assessment.evidenceRefs)


def _map_feedback_with_privacy(question_id: str, assessment: Assessment, summary: str, focus: str, privacy_refs: list[EvidenceRef]) -> dict:
    assessment = assessment.model_copy(deep=True)
    privacy_refs = merge_privacy_evidence(privacy_refs, assessment.evidenceRefs)
    assessment.factualErrors = public_narratives(assessment.factualErrors, privacy_refs)
    assessment.strengths = public_narratives(assessment.strengths, privacy_refs)
    assessment.gaps = public_narratives(assessment.gaps, privacy_refs)
    assessment.claimChecks = [c for c in assessment.claimChecks if public_generated_text(c.claim, privacy_refs)]
    summary = public_generated_text(summary, privacy_refs)

    score = assessment_score_for_focus(assessment, focus)
    verdict = "strong" if score >= 80 else ("partial" if score >= 55 else "weak")
    checks = [
        {"claim": check.claim, "verdict": check.verdict.value, "evidenceRefs": [ref.anchorId for ref in check.evidenceRefs]}
        for check in assessment.claimChecks
    ]
    coach_tip = "用结论、证据、个人动作、指标口径和方案取舍重新组织回答。"
    if assessment.gaps:
        coach_tip = assessment.gaps[0]
    feedback = {
        "questionId": question_id,
        "score": score,
        "verdict": verdict,
        "summary": summary.strip(),
        "strengths": _non_nil(assessment.strengths),
        "gaps": _non_nil(assessment.gaps),
        "claimChecks": checks,
        "coachTip": coach_tip,
        "evidenceRefs": map_evidence_refs(assessment.evidenceRefs),
    }
    if focus == "knowledge":
        if assessment.correctness >= 4:
            feedback["knowledgeVerdict"] = "correct"
        elif assessment.correctness >= 2:
            feedback["knowledgeVerdict"] = "partial"
        else:
            feedback["knowledgeVerdict"] = "incorrect"
    else:
        feedback["knowledgeVerdict"] = "not_applicable"
    if assessment.factualErrors:
        feedback["correction"] = "；".join(assessment.factualErrors)
    if not feedback["summary"]:
        feedback["summary"] = feedback_summary(assessment)
    return feedback


def deferred_feedback(question_id: str) -> dict:
    return {
        "questionId": question_id,
        "deferred": True,
        "verdict": "deferred",
        "summary": "本轮反馈已延迟至最终报告。",
        "strengths": [],
        "gaps": [],
        "claimChecks": [],
        "coachTip": "",
        "knowledgeVerdict": "deferred",
    }


def map_report(interview_id: str, report: Report) -> dict:
    privacy_refs = report_privacy_evidence(report)
    turns = []
    for index, record in enumerate(report.turns):
        turns.append({
            "question": _map_question_with_privacy(record.question, index + 1, report.profile, privacy_refs),
            "answer": record.answer.text,
            "feedback": _map_feedback_with_privacy(
                record.question.id, record.assessment, feedback_summary(record.assessment),
                question_focus(record.question), privacy_refs,
            ),
        })
    dimensions = report_dimensions(report.turns)
    for dimension in dimensions:
        dimension["evidence"] = public_narratives(dimension["evidence"], privacy_refs)
    jd_coverage = report_jd_coverage(report.profile, report.turns)
    for item in jd_coverage:
        item["requirement"] = public_generated_text(item["requirement"], privacy_refs) or "JD 要求"
    project_coverage = report_project_coverage(report.profile, report.turns)
    for item in project_coverage:
        item["risks"] = public_narratives(item["risks"], privacy_refs)
    readiness = report_readiness(report.overallScore, report.profile, report.turns, jd_coverage, project_coverage)
    public_strengths = public_narratives(report.strengths, privacy_refs)
    public_gaps = public_narratives(report.gaps, privacy_refs)
    next_drills = _non_nil(public_gaps)
    if not next_drills:
        next_drills = ["针对本轮证据重答一遍，并补足个人职责、测量口径与取舍。"]
    summary = public_generated_text(report.summary, privacy_refs)
    if not summary:
        summary = "本轮面试已完成，报告仅展示可公开且有证据支持的结论。"
    return {
        "interviewId": interview_id,
        "state": "completed",
        "overallScore": report.overallScore,
        "readiness": readiness,
        "summary": summary,
        "dimensions": dimensions,
        "strengths": _non_nil(public_strengths),
        "risks": _non_nil(public_gaps),
        "jdCoverage": jd_coverage,
        "projectCoverage": project_coverage,
        "turns": turns,
        "nextDrills": next_drills,
    }


def public_narratives(values: list[str], privacy_refs: list[EvidenceRef]) -> list[str]:
    result: list[str] = []
    for value in values:
        if (public := public_generated_text(value, privacy_refs)):
            result = _append_unique_string(result, public)
    return result


def report_privacy_evidence(report: Report) -> list[EvidenceRef]:
    refs = list(report.evidenceRefs)
    for point in list(report.profile.jd.requirements) + list(report.profile.jd.responsibilities) + list(report.profile.resume.projects):
        refs = merge_privacy_evidence(refs, point.evidenceRefs)
    for point in report.profile.coverage:
        refs = merge_privacy_evidence(refs, point.evidenceRefs)
    for record in report.turns:
        refs = merge_privacy_evidence(refs, record.question.evidenceRefs)
        refs = merge_privacy_evidence(refs, record.assessment.evidenceRefs)
        for check in record.assessment.claimChecks:
            refs = merge_privacy_evidence(refs, check.evidenceRefs)
    return refs


def merge_privacy_evidence(current: list[EvidenceRef], additional: list[EvidenceRef]) -> list[EvidenceRef]:
    result = list(current)
    positions = {f"{ref.kind.value}\x00{ref.sourceId}\x00{ref.anchorId}": index for index, ref in enumerate(result)}
    for ref in additional:
        key = f"{ref.kind.value}\x00{ref.sourceId}\x00{ref.anchorId}"
        position = positions.get(key)
        if position is not None:
            if len(ref.quote) > len(result[position].quote):
                result[position] = ref
            continue
        positions[key] = len(result)
        result.append(ref)
    return result


_DIMENSION_SPECS = [
    ("knowledge_depth", "知识深度", lambda a: (a.correctness + a.depth) / 2),
    ("project_depth", "项目深度", lambda a: (a.depth + a.specificity) / 2),
    ("ownership", "个人贡献", lambda a: a.ownership),
    ("tradeoffs", "方案取舍", lambda a: a.tradeoffs),
    ("communication", "表达清晰度", lambda a: a.specificity),
    ("jd_fit", "岗位匹配", lambda a: (a.correctness + a.specificity) / 2),
]


def report_dimensions(turns: list[AnswerRecord]) -> list[dict]:
    result = []
    for key, label, value_fn in _DIMENSION_SPECS:
        total = 0.0
        count = 0
        for turn in turns:
            if not dimension_includes_turn(key, turn.question):
                continue
            total += value_fn(turn.assessment)
            count += 1
        score = 0
        summary = "本轮没有覆盖该能力，未评估，不计为 0 分。"
        if count > 0:
            score = _round(total / count * 20)
            summary = f"基于 {count} 轮对应题型的结构化评估聚合，得分 {score}/100。"
        result.append({
            "key": key, "label": label, "score": score, "assessed": count > 0,
            "sampleCount": count, "summary": summary, "evidence": dimension_evidence(turns, key),
        })
    return result


def dimension_evidence(turns: list[AnswerRecord], key: str) -> list[str]:
    result: list[str] = []
    for turn in turns:
        if not dimension_includes_turn(key, turn.question):
            continue
        items = list(turn.assessment.strengths)
        if key in ("jd_fit", "project_depth"):
            items = items + list(turn.assessment.gaps)
        for item in items:
            result = _append_unique_string(result, item)
            if len(result) == 3:
                return result
    return result


def dimension_includes_turn(key: str, question: Question) -> bool:
    if key == "knowledge_depth":
        return question_focus(question) == "knowledge"
    if key in ("project_depth", "ownership"):
        return question_focus(question) == "project"
    if key == "jd_fit":
        return question_has_evidence_kind(question, SourceKind.JD)
    return True


def question_has_evidence_kind(question: Question, kind: SourceKind) -> bool:
    return any(ref.kind == kind for ref in question.evidenceRefs)


def adaptation_reason(adaptation) -> str:
    if adaptation.trigger == PolicyAction.PREREQUISITE:
        return "上一轮存在事实或前置理解问题，先核对基础原理。"
    if adaptation.trigger == PolicyAction.FOLLOW_UP:
        axis_reasons = {
            "ownership": "上一轮没有讲清个人职责边界，继续核对本人贡献。",
            "metrics": "上一轮缺少指标、基线或测量口径，继续追问可验证数据。",
            "tradeoff": "上一轮缺少备选方案与代价分析，继续追问技术取舍。",
            "verification": "上一轮回答与简历材料存在冲突，继续核对陈述和证据边界。",
            "principle": "上一轮没有讲清核心机制，继续追问原理与因果链。",
            "boundary": "上一轮缺少适用前提或失效边界，继续追问约束条件。",
            "example": "上一轮缺少具体场景，继续追问输入、过程、输出与验证方式。",
        }
        return axis_reasons.get(adaptation.followUpAxis, "上一轮回答仍不够具体，继续追问可核验细节。")
    if adaptation.trigger == PolicyAction.ADVANCE:
        return "当前覆盖点已完成，切换到尚未覆盖的岗位或项目能力。"
    return "根据本轮评估调整后续问题。"


def report_jd_coverage(profile: Profile, turns: list[AnswerRecord]) -> list[dict]:
    points = merge_profile_points(list(profile.jd.requirements) + list(profile.jd.responsibilities))
    result = []
    for point in points:
        records = records_for_evidence(turns, point.evidenceRefs)
        status = "missing"
        if records:
            status = "partial"
            if average_record_score(records) >= 70 and records_have_no_knowledge_error(records):
                status = "covered"
        result.append({"requirement": point.label, "status": status, "evidence": record_ids(records)})
    return result


def merge_profile_points(points: list[ProfilePoint]) -> list[ProfilePoint]:
    result: list[ProfilePoint] = []
    positions: dict[str, int] = {}
    for point in points:
        key = " ".join(point.label.lower().split())
        if not key:
            continue
        position = positions.get(key)
        if position is not None:
            merged = list(result[position].evidenceRefs)
            for ref in point.evidenceRefs:
                if all(existing.anchorId != ref.anchorId for existing in merged):
                    merged.append(ref)
            result[position].evidenceRefs = merged
            continue
        positions[key] = len(result)
        result.append(point.model_copy(deep=True))
    return result


def report_readiness(score: int, profile: Profile, turns: list[AnswerRecord], jd: list[dict], projects: list[dict]) -> str:
    if score < 55 or not turns:
        return "not_ready"
    if len(turns) < 4:
        return "borderline"

    has_jd = len(profile.jd.requirements) + len(profile.jd.responsibilities) > 0
    has_projects = len(profile.resume.projects) > 0
    knowledge_turns = 0
    project_turns = 0
    for turn in turns:
        if question_focus(turn.question) == "project":
            project_turns += 1
        else:
            knowledge_turns += 1

    coverage_complete = True
    if has_jd:
        covered = sum(1 for item in jd if item["status"] == "covered")
        required = _min_int(2, len(jd))
        coverage_complete = required > 0 and covered >= required
    if has_projects:
        project_covered = any(item["depth"] >= 2 for item in projects)
        coverage_complete = coverage_complete and project_covered

    if has_jd and has_projects:
        coverage_complete = coverage_complete and knowledge_turns >= 2 and project_turns >= 2
    elif has_jd:
        coverage_complete = coverage_complete and knowledge_turns >= 3
    elif has_projects:
        coverage_complete = coverage_complete and project_turns >= 3
    else:
        coverage_complete = knowledge_turns >= 3

    if score >= 75 and coverage_complete and not has_critical_interview_risk(turns):
        return "ready"
    return "borderline"


def has_critical_interview_risk(turns: list[AnswerRecord]) -> bool:
    for turn in turns:
        if turn.assessment.factualErrors:
            return True
        if any(check.verdict == ClaimVerdict.CONTRADICTED for check in turn.assessment.claimChecks):
            return True
    return False


def report_project_coverage(profile: Profile, turns: list[AnswerRecord]) -> list[dict]:
    result = []
    for project in profile.resume.projects:
        records = records_for_evidence(turns, project.evidenceRefs)
        risks: list[str] = []
        for record in records:
            for gap in record.assessment.gaps:
                risks = _append_unique_string(risks, gap)
        result.append({"project": project.label, "depth": len(records), "risks": risks})
    return result


def records_for_evidence(turns: list[AnswerRecord], refs: list[EvidenceRef]) -> list[AnswerRecord]:
    wanted = {ref.anchorId for ref in refs}
    result = []
    for turn in turns:
        if any(ref.anchorId in wanted for ref in turn.question.evidenceRefs):
            result.append(turn)
    return result


def record_ids(records: list[AnswerRecord]) -> list[str]:
    return [record.question.id for record in records]


def average_record_score(records: list[AnswerRecord]) -> int:
    if not records:
        return 0
    total = sum(assessment_score_for_focus(record.assessment, question_focus(record.question)) for record in records)
    return _round(total / len(records))


def assessment_score(assessment: Assessment) -> int:
    total = (
        assessment.correctness + assessment.depth + assessment.specificity
        + assessment.ownership + assessment.metrics + assessment.tradeoffs
    )
    return _round(total / 30 * 100)


def assessment_score_for_focus(assessment: Assessment, focus: str) -> int:
    if focus == "knowledge":
        total = assessment.correctness + assessment.depth + assessment.specificity + assessment.tradeoffs
        return _round(total / 20 * 100)
    return assessment_score(assessment)


def records_have_no_knowledge_error(records: list[AnswerRecord]) -> bool:
    for record in records:
        if record.assessment.correctness <= 2 or record.assessment.factualErrors:
            return False
    return True


def feedback_summary(assessment: Assessment) -> str:
    if assessment.factualErrors:
        return "本轮存在需要纠正的事实或前置理解。"
    if assessment.gaps:
        return assessment.gaps[0]
    if assessment.strengths:
        return assessment.strengths[0]
    return "本轮评估已完成。"


def map_snapshot(snapshot) -> dict:
    turns = []
    for index, turn in enumerate(snapshot.turns):
        if turn.feedback.deferred:
            feedback = deferred_feedback(turn.question.id)
        else:
            feedback = map_feedback(
                turn.question.id, turn.feedback.assessment, turn.feedback.summary,
                feedback_focus(turn.feedback.focus),
            )
        turns.append({
            "question": map_question(turn.question, index + 1, snapshot.profile),
            "answer": turn.answer.text,
            "feedback": feedback,
        })
    current = None
    if snapshot.currentQuestion is not None:
        current = map_question(snapshot.currentQuestion, snapshot.progress.current, snapshot.profile)
    return {
        "interviewId": snapshot.interviewId,
        "state": web_state(snapshot.state),
        "profile": map_profile(snapshot.profile),
        "currentQuestion": current,
        "turns": turns,
        "progress": map_progress(snapshot.progress),
        "reportReady": snapshot.reportReady,
    }


def map_review(review) -> dict:
    turns = []
    for index, turn in enumerate(review.turns):
        turns.append({
            "question": map_question(turn.question, index + 1, None),
            "answer": turn.answer.text,
            "inputMode": turn.answer.inputMode.value,
            "durationMs": turn.answer.durationMs,
            "feedback": map_feedback(
                turn.question.id, turn.feedback.assessment, turn.feedback.summary,
                feedback_focus(turn.feedback.focus),
            ),
            "references": [reference.model_dump(mode="json") for reference in turn.references],
            "answeredAt": turn.answeredAt.isoformat(),
        })
    return {
        "schemaVersion": review.schemaVersion,
        "interviewId": review.interviewId,
        "state": web_state(review.state),
        "startedAt": review.startedAt.isoformat(),
        "generatedAt": review.generatedAt.isoformat(),
        "turns": turns,
    }
