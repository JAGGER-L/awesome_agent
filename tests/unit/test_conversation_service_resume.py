from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Run, RuntimeEvent
from awesome_agent.domain.threads import Thread


def test_conversation_stream_event_kind_includes_turn_continued() -> None:
    assert ConversationStreamEventKind.TURN_CONTINUED.value == "turn.continued"


class FakeConversationRepository:
    def __init__(self, thread_id: UUID) -> None:
        self.thread = Thread(id=thread_id, title="Chat")
        self.get_thread_calls: list[UUID] = []

    async def get_thread(self, thread_id: UUID) -> Thread:
        self.get_thread_calls.append(thread_id)
        if thread_id != self.thread.id:
            raise KeyError(thread_id)
        return self.thread


class FakeTurnIntake:
    def __init__(self) -> None:
        self.create_turn_run_calls = 0

    async def create_turn_run(self, **kwargs: object) -> Run:
        self.create_turn_run_calls += 1
        raise AssertionError("continue_turn must not create a new Run")


class FakeRuntimeRepository:
    def __init__(self, runs: list[Run], events: dict[UUID, list[RuntimeEvent]]) -> None:
        self.runs = runs
        self.events = events

    async def list_runs(self) -> list[Run]:
        return list(self.runs)

    async def get_run(self, run_id: UUID) -> Run:
        for run in self.runs:
            if run.id == run_id:
                return run
        raise KeyError(run_id)

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
    ) -> list[RuntimeEvent]:
        return [
            event
            for event in self.events.get(run_id, [])
            if event.sequence > after_sequence
        ]


def _service(
    *,
    thread_id: UUID,
    runtime_repository: FakeRuntimeRepository,
    intake: FakeTurnIntake | None = None,
) -> tuple[ConversationService, FakeConversationRepository, FakeTurnIntake]:
    repository = FakeConversationRepository(thread_id)
    turn_intake = intake or FakeTurnIntake()
    return (
        ConversationService(
            repository=repository,  # type: ignore[arg-type]
            runtime_repository=runtime_repository,  # type: ignore[arg-type]
            conversation_run_intake=turn_intake,
            default_model="fake-model",
            event_poll_interval=0,
        ),
        repository,
        turn_intake,
    )


def _run(
    run_id: UUID,
    *,
    status: RunStatus,
    dispatch_status: DispatchStatus,
    created_at: datetime,
) -> Run:
    return Run(
        id=run_id,
        goal="Build",
        status=status,
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route="conversation-turn",
        dispatch_status=dispatch_status,
        created_at=created_at,
    )


def _event(
    run_id: UUID,
    sequence: int,
    event_type: EventType,
    payload: dict[str, object],
    *,
    created_at: datetime,
) -> RuntimeEvent:
    return RuntimeEvent(
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        trace_id=run_id.hex,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_continue_turn_emits_turn_continued_for_latest_resumable_run() -> None:
    thread_id = uuid4()
    other_thread_id = uuid4()
    older = uuid4()
    latest = uuid4()
    other_thread_newest = uuid4()
    runtime = FakeRuntimeRepository(
        [
            _run(
                older,
                status=RunStatus.PAUSED,
                dispatch_status=DispatchStatus.TERMINAL,
                created_at=datetime(2026, 1, 3, tzinfo=UTC),
            ),
            _run(
                latest,
                status=RunStatus.PAUSED,
                dispatch_status=DispatchStatus.TERMINAL,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            _run(
                other_thread_newest,
                status=RunStatus.PAUSED,
                dispatch_status=DispatchStatus.TERMINAL,
                created_at=datetime(2026, 1, 4, tzinfo=UTC),
            ),
        ],
        {
            older: [
                _event(
                    older,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "older"},
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ],
            latest: [
                _event(
                    latest,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "latest"},
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                ),
                _event(
                    latest,
                    2,
                    EventType.MODEL_CALL_CREATED,
                    {"text_delta": "already streamed"},
                    created_at=datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC),
                ),
                _event(
                    latest,
                    3,
                    EventType.RUN_STATUS_CHANGED,
                    {"status": RunStatus.COMPLETED.value},
                    created_at=datetime(2026, 1, 2, 0, 0, 2, tzinfo=UTC),
                ),
            ],
            other_thread_newest: [
                _event(
                    other_thread_newest,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(other_thread_id), "goal": "other"},
                    created_at=datetime(2026, 1, 5, tzinfo=UTC),
                )
            ],
        },
    )
    service, repository, intake = _service(
        thread_id=thread_id,
        runtime_repository=runtime,
    )

    events = [event async for event in service.continue_turn(thread_id=thread_id)]

    assert repository.get_thread_calls == [thread_id, thread_id]
    assert intake.create_turn_run_calls == 0
    assert len(runtime.runs) == 3
    assert events[0].event is ConversationStreamEventKind.TURN_CONTINUED
    assert events[0].turn_id == UUID(str(events[0].payload["stream_id"]))
    assert events[0].payload == {
        "run_id": str(latest),
        "stream_id": str(events[0].turn_id),
        "status": RunStatus.PAUSED.value,
        "dispatch_status": DispatchStatus.TERMINAL.value,
        "resumed": True,
        "after_sequence": 0,
    }
    assert events[1].event is ConversationStreamEventKind.MESSAGE_DELTA
    assert events[1].run_id == latest
    assert events[1].payload["text"] == "already streamed"


@pytest.mark.asyncio
async def test_continue_turn_ignores_terminal_run_with_waiting_dispatch() -> None:
    thread_id = uuid4()
    terminal_newer = uuid4()
    resumable_older = uuid4()
    runtime = FakeRuntimeRepository(
        [
            _run(
                terminal_newer,
                status=RunStatus.COMPLETED,
                dispatch_status=DispatchStatus.WAITING,
                created_at=datetime(2026, 1, 3, tzinfo=UTC),
            ),
            _run(
                resumable_older,
                status=RunStatus.PAUSED,
                dispatch_status=DispatchStatus.WAITING,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ],
        {
            terminal_newer: [
                _event(
                    terminal_newer,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "terminal"},
                    created_at=datetime(2026, 1, 3, tzinfo=UTC),
                ),
                _event(
                    terminal_newer,
                    2,
                    EventType.RUN_STATUS_CHANGED,
                    {"status": RunStatus.COMPLETED.value},
                    created_at=datetime(2026, 1, 3, 0, 0, 1, tzinfo=UTC),
                ),
            ],
            resumable_older: [
                _event(
                    resumable_older,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "resumable"},
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                _event(
                    resumable_older,
                    2,
                    EventType.RUN_STATUS_CHANGED,
                    {"status": RunStatus.COMPLETED.value},
                    created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
                ),
            ],
        },
    )
    service, _repository, intake = _service(
        thread_id=thread_id,
        runtime_repository=runtime,
    )

    events = [event async for event in service.continue_turn(thread_id=thread_id)]

    assert intake.create_turn_run_calls == 0
    assert events[0].event is ConversationStreamEventKind.TURN_CONTINUED
    assert events[0].payload["run_id"] == str(resumable_older)


@pytest.mark.asyncio
async def test_continue_turn_rejects_expected_run_mismatch() -> None:
    thread_id = uuid4()
    latest = uuid4()
    expected = uuid4()
    runtime = FakeRuntimeRepository(
        [
            _run(
                latest,
                status=RunStatus.RUNNING,
                dispatch_status=DispatchStatus.WAITING,
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
        {
            latest: [
                _event(
                    latest,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "latest"},
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                )
            ]
        },
    )
    service, _repository, intake = _service(
        thread_id=thread_id,
        runtime_repository=runtime,
    )

    with pytest.raises(ValueError, match="resumable_run_changed"):
        [
            event
            async for event in service.continue_turn(
                thread_id=thread_id,
                expected_run_id=expected,
            )
        ]

    assert intake.create_turn_run_calls == 0


@pytest.mark.asyncio
async def test_continue_turn_rejects_expected_run_when_newer_resumable_exists() -> None:
    thread_id = uuid4()
    older = uuid4()
    latest = uuid4()
    runtime = FakeRuntimeRepository(
        [
            _run(
                older,
                status=RunStatus.RUNNING,
                dispatch_status=DispatchStatus.WAITING,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            _run(
                latest,
                status=RunStatus.RUNNING,
                dispatch_status=DispatchStatus.WAITING,
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ],
        {
            older: [
                _event(
                    older,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "older"},
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ],
            latest: [
                _event(
                    latest,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "latest"},
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                )
            ],
        },
    )
    service, _repository, intake = _service(
        thread_id=thread_id,
        runtime_repository=runtime,
    )

    with pytest.raises(ValueError, match="resumable_run_changed"):
        [
            event
            async for event in service.continue_turn(
                thread_id=thread_id,
                expected_run_id=older,
            )
        ]

    assert intake.create_turn_run_calls == 0


@pytest.mark.asyncio
async def test_continue_turn_rejects_missing_resumable_run() -> None:
    thread_id = uuid4()
    completed = uuid4()
    runtime = FakeRuntimeRepository(
        [
            _run(
                completed,
                status=RunStatus.COMPLETED,
                dispatch_status=DispatchStatus.TERMINAL,
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
        {
            completed: [
                _event(
                    completed,
                    1,
                    EventType.RUN_CREATED,
                    {"thread_id": str(thread_id), "goal": "done"},
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                )
            ]
        },
    )
    service, _repository, intake = _service(
        thread_id=thread_id,
        runtime_repository=runtime,
    )

    with pytest.raises(ValueError, match="no_resumable_turn"):
        [event async for event in service.continue_turn(thread_id=thread_id)]

    assert intake.create_turn_run_calls == 0
