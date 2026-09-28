"""P4d SQLite store CAS + idempotency tests."""
from datetime import datetime, timezone

import pytest

from app.interview.errors import (
    AnswerAlreadyCommitted,
    IdempotencyConflict,
    PersistenceConflict,
    PersistenceNotFound,
    StoreConflict,
    StoreNotFound,
)
from app.interview.persistence import (
    AnswerCommitSpec,
    CommandSpec,
    CommandStatus,
    CommandTransition,
    SessionEventSpec,
)
from app.interview.sqlite_store import SQLiteStore
from app.interview.types import (
    InterviewConfig,
    InterviewSession,
    InterviewState,
    Profile,
    SourceDocument,
    SourceIndex,
    SourceKind,
)

NOW = datetime.now(timezone.utc)


def make_session(id="interview-1", version=1):
    return InterviewSession(
        id=id, clientSessionId="c1", config=InterviewConfig(),
        state=InterviewState.AWAITING_ANSWER, profile=Profile(),
        sources=SourceIndex(documents={
            "jd": SourceDocument(id="jd", kind=SourceKind.JD, name="jd.md", content="secret jd body"),
        }),
        startedAt=NOW, version=version,
    )


@pytest.fixture
async def store():
    s = await SQLiteStore.open(":memory:")
    yield s
    await s.close()


async def test_create_load_round_trips_source_contents(store):
    await store.create(make_session())
    loaded = await store.load("interview-1")
    assert loaded.version == 1
    assert loaded.sources.documents["jd"].content == "secret jd body"
    # content is excluded from the JSON snapshot but restored separately
    assert "content" not in loaded.sources.documents["jd"].model_dump()


async def test_create_conflict(store):
    await store.create(make_session())
    with pytest.raises(StoreConflict):
        await store.create(make_session())


async def test_load_not_found(store):
    with pytest.raises(StoreNotFound):
        await store.load("nope")


async def test_save_cas_and_conflicts(store):
    await store.create(make_session(version=1))
    session = make_session(version=2)
    await store.save(session, 1)
    loaded = await store.load("interview-1")
    assert loaded.version == 2

    stale = make_session(version=2)
    with pytest.raises(StoreConflict):
        await store.save(stale, 1)

    # version must equal expected+1
    wrong = make_session(version=5)
    with pytest.raises(StoreConflict):
        await store.save(wrong, 1)

    missing = make_session(id="ghost", version=2)
    with pytest.raises(StoreNotFound):
        await store.save(missing, 1)


def command_spec(**kwargs):
    base = dict(
        id="cmd-1", principal_id="client-1", session_id="interview-1",
        action="answer", idempotency_key="answer-key", subject_id="question-1",
        request_hash="sha256:abc",
    )
    base.update(kwargs)
    return CommandSpec(**base)


async def test_create_or_get_command_idempotency(store):
    command, created = await store.create_or_get_command(command_spec())
    assert created is True
    assert command.status == CommandStatus.PENDING

    same, created = await store.create_or_get_command(command_spec())
    assert created is False
    assert same.id == "cmd-1"

    with pytest.raises(IdempotencyConflict):
        await store.create_or_get_command(command_spec(request_hash="sha256:different"))


async def test_create_or_get_command_subject_conflict(store):
    await store.create_or_get_command(command_spec())
    with pytest.raises(AnswerAlreadyCommitted):
        await store.create_or_get_command(command_spec(id="cmd-2", idempotency_key="other-key"))


async def test_transition_command(store):
    command, _ = await store.create_or_get_command(command_spec())
    running = await store.transition_command(command.id, CommandStatus.PENDING, CommandTransition(status=CommandStatus.RUNNING))
    assert running.status == CommandStatus.RUNNING
    with pytest.raises(PersistenceConflict):
        await store.transition_command(command.id, CommandStatus.PENDING, CommandTransition(status=CommandStatus.RUNNING))
    succeeded = await store.transition_command(command.id, CommandStatus.RUNNING, CommandTransition(status=CommandStatus.SUCCEEDED, result='{"x":1}'))
    assert succeeded.status == CommandStatus.SUCCEEDED
    assert succeeded.result == '{"x":1}'


async def test_append_and_list_events(store):
    await store.create(make_session())
    event, created = await store.append_session_event(SessionEventSpec(
        event_id="e1", session_id="interview-1", command_id="cmd-1", type="answer.started", payload='{"a":1}',
    ))
    assert created is True
    assert event.sequence == 1

    event2, _ = await store.append_session_event(SessionEventSpec(
        event_id="e2", session_id="interview-1", command_id="cmd-1", type="answer.committed", payload='{"a":2}',
    ))
    assert event2.sequence == 2

    events = await store.list_session_events("interview-1", 0, 10)
    assert [e.type for e in events] == ["answer.started", "answer.committed"]
    page2 = await store.list_session_events("interview-1", 1, 10)
    assert [e.sequence for e in page2] == [2]


async def test_append_event_idempotent_by_id(store):
    await store.create(make_session())
    spec = SessionEventSpec(event_id="e1", session_id="interview-1", command_id="cmd", type="answer.started", payload='{"a":1}')
    await store.append_session_event(spec)
    event, created = await store.append_session_event(spec)
    assert created is False
    assert event.sequence == 1


async def test_commit_answer_atomic(store):
    await store.create(make_session(version=1))
    command, _ = await store.create_or_get_command(command_spec())
    await store.transition_command(command.id, CommandStatus.PENDING, CommandTransition(status=CommandStatus.RUNNING))

    session = make_session(version=2)
    await store.commit_answer(AnswerCommitSpec(
        session=session,
        expected_version=1,
        command_id=command.id,
        result='{"result":true}',
        event=SessionEventSpec(
            event_id="ev-commit", session_id="interview-1", command_id=command.id,
            type="answer.committed", payload='{"questionId":"question-1"}',
        ),
    ))

    loaded = await store.load("interview-1")
    assert loaded.version == 2
    stored_command = await store.get_command(command.id)
    assert stored_command.status == CommandStatus.SUCCEEDED
    assert stored_command.result == '{"result":true}'
    events = await store.list_session_events("interview-1", 0, 10)
    assert [e.type for e in events] == ["answer.committed"]
    assert events[0].sequence == 1


async def test_commit_answer_version_conflict(store):
    await store.create(make_session(version=1))
    command, _ = await store.create_or_get_command(command_spec())
    await store.transition_command(command.id, CommandStatus.PENDING, CommandTransition(status=CommandStatus.RUNNING))
    session = make_session(version=2)
    with pytest.raises(StoreConflict):
        await store.commit_answer(AnswerCommitSpec(
            session=session, expected_version=0, command_id=command.id, result="{}",
            event=SessionEventSpec(event_id="e", session_id="interview-1", command_id=command.id, type="answer.committed", payload="{}"),
        ))
