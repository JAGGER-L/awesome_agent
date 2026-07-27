from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr

import awesome_agent.core.resource_lock as resource_lock
from awesome_agent.application.command_results import (
    CommandApplicationInteraction,
    CommandError,
    CommandInteractionResult,
    CommandResult,
    CommandSelection,
    DoctorCommandPayload,
    ModelCommandPayload,
    ThreadExportCommandPayload,
    ThreadTransitionCommandPayload,
    ToolCatalogCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeStatus,
    ProviderCredentialSetRequest,
    ThreadListQuery,
    ThreadReadQuery,
    ThreadSearchQuery,
)
from awesome_agent.application.facade import LocalApplication
from awesome_agent.application.interactions import (
    InteractionKind,
    recovery_decision_choices,
)
from awesome_agent.application.operations import (
    OperationBusy,
    OperationContinuation,
)
from awesome_agent.config import CredentialValidation, CredentialValidationStatus
from awesome_agent.context import ContextManifestItem
from awesome_agent.conversation import (
    ThreadListPage,
    ThreadSearchLimitExceeded,
    ThreadView,
)
from awesome_agent.core.events import (
    CollectingEventSink,
    EventEnvelope,
    EventType,
    InteractionResolvedPayload,
)
from awesome_agent.core.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
)
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelGateway,
    ModelRequest,
    ModelTurn,
    ProviderId,
    SelectedModel,
    StopReason,
    TextDelta,
    ToolCall,
    TurnCompleted,
)


class FakeGateway:
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        self.requests.append(request)
        assert selected.provider == self.provider
        yield TextDelta(text="done")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
            )
        )

    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn:
        completed = [
            event.turn
            async for event in self.stream(selected, request)
            if isinstance(event, TurnCompleted)
        ]
        return completed[0]


class ExecuteGateway(FakeGateway):
    def __init__(self, provider: str, model: str, command: str) -> None:
        super().__init__(provider, model)
        self.command = command
        self.calls = 0

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        self.requests.append(request)
        self.calls += 1
        if self.calls == 1:
            yield TurnCompleted(
                turn=ModelTurn(
                    provider=selected.provider,
                    model=selected.model,
                    assistant=AssistantMessage(
                        tool_calls=(
                            ToolCall(
                                call_id="call_execute",
                                name="execute",
                                arguments_json=json.dumps({"command": self.command}),
                            ),
                        )
                    ),
                    stop_reason=StopReason.TOOL_CALLS,
                )
            )
            return
        yield TextDelta(text="done")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
            )
        )


class BlockingGateway(FakeGateway):
    def __init__(self, provider: str, model: str) -> None:
        super().__init__(provider, model)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        yield TextDelta(text="done")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
            )
        )


class BlockingCredentialValidator:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def validate(
        self,
        provider: str,
        api_key: SecretStr,
        *,
        kimi_region: object,
    ) -> CredentialValidation:
        del provider, api_key, kimi_region
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return CredentialValidation(
            status=CredentialValidationStatus.VALID,
            code="credential_valid",
        )


class BlockFirstConsistencyCheck:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()


class BlockingDirectExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def execute(
        self,
        request: ToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.SUCCESS,
            content="done",
            metadata={"exit_code": 0},
        )


class FailOnceOnFullAccessResolvedSink(CollectingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    async def emit(self, event: EventEnvelope) -> None:
        if (
            self.failures
            and event.event_type is EventType.INTERACTION_RESOLVED
            and isinstance(event.payload, InteractionResolvedPayload)
            and event.payload.decision == "enable_full_access"
        ):
            self.failures -= 1
            raise BrokenPipeError("protocol output closed")
        await super().emit(event)


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.value is not None
    return result.value


async def _wait_for_thread(
    application: LocalApplication,
    thread_id: str,
    *,
    entries: int,
) -> ThreadView:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while True:
        view = _unwrap(
            await application.read_thread(ThreadReadQuery(thread_id=thread_id))
        ).view
        state = _unwrap(await application.get_state())
        if len(view.entries) >= entries and state.active_operation_id is None:
            return view
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(
                "foreground operation did not complete within 5 seconds "
                f"(entries={len(view.entries)}, "
                f"active_operation_id={state.active_operation_id!r})"
            )
        await asyncio.sleep(min(0.01, remaining))


async def _wait_for_interaction(application: LocalApplication) -> str:
    for _ in range(200):
        state = _unwrap(await application.get_state())
        if state.pending_interaction_id is not None:
            return state.pending_interaction_id
        await asyncio.sleep(0.01)
    raise AssertionError("execute interaction was not requested")


async def _wait_for_idle(application: LocalApplication) -> None:
    for _ in range(200):
        if _unwrap(await application.get_state()).active_operation_id is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("foreground operation did not become idle")


@pytest.mark.asyncio
async def test_thread_search_selection_revalidates_before_resume_and_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "exports").mkdir()
    gateway = BlockingGateway(
        "deepseek",
        "deepseek/deepseek-v4-flash",
    )
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=lambda _provider, _model: cast(ModelGateway, gateway),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))

    first = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(first, CommandResult)
    assert isinstance(first.payload, ThreadTransitionCommandPayload)
    first_id = first.payload.transition.thread.view.thread.id
    _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.RENAME, arguments=("Needle thread",))
        )
    )
    second = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(second, CommandResult)
    assert isinstance(second.payload, ThreadTransitionCommandPayload)
    second_id = second.payload.transition.thread.view.thread.id

    accepted = _unwrap(
        await application.submit_turn(second_id, "block", "client_search_block")
    )
    await asyncio.wait_for(gateway.started.wait(), timeout=0.5)

    direct_search = _unwrap(
        await application.search_threads(ThreadSearchQuery(query=" needle "))
    )
    assert [thread.id for thread in direct_search.threads] == [first_id]
    selection = _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.SEARCH, arguments=("needle",))
        )
    )
    assert isinstance(selection, CommandInteractionResult)
    assert isinstance(selection.interaction, CommandSelection)
    assert [option.value for option in selection.interaction.options] == [first_id]
    assert _unwrap(await application.get_state()).current_thread_id == second_id

    backend = cast(Any, application)._backend
    runtime = backend._runtime
    assert runtime is not None
    original_search = runtime.conversation.search_thread_page
    template = direct_search.threads[0]

    async def capped_search(*_args: object, **_kwargs: object) -> ThreadListPage:
        return ThreadListPage(
            threads=tuple(
                template.model_copy(update={"id": f"fixture_thread_{index:02d}"})
                for index in range(50)
            ),
            has_more=True,
        )

    monkeypatch.setattr(runtime.conversation, "search_thread_page", capped_search)
    capped = _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.SEARCH, arguments=("needle",))
        )
    )
    assert isinstance(capped, CommandInteractionResult)
    assert isinstance(capped.interaction, CommandSelection)
    assert capped.interaction.prompt == (
        "Showing the 50 most recent matches; refine the query for older results."
    )
    monkeypatch.setattr(
        runtime.conversation,
        "search_thread_page",
        original_search,
    )

    async def exhausted_search(*_args: object, **_kwargs: object) -> ThreadListPage:
        raise ThreadSearchLimitExceeded("fixture budget")

    monkeypatch.setattr(runtime.conversation, "search_thread_page", exhausted_search)
    limited = await application.search_threads(ThreadSearchQuery(query="needle"))
    assert limited.ok is False
    assert limited.error is not None
    assert limited.error.code == "result_too_large"
    limited_command = _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.SEARCH, arguments=("needle",))
        )
    )
    assert isinstance(limited_command, CommandError)
    assert limited_command.code == "result_too_large"
    monkeypatch.setattr(
        runtime.conversation,
        "search_thread_page",
        original_search,
    )

    blocked = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.SEARCH,
                arguments=("needle", first_id),
            )
        )
    )
    assert isinstance(blocked, CommandError)
    assert blocked.code == "operation_busy"

    _unwrap(await application.cancel_operation(accepted.operation_id))
    await _wait_for_idle(application)
    resumed = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.SEARCH,
                arguments=("needle", first_id),
            )
        )
    )
    assert isinstance(resumed, CommandResult)
    assert isinstance(resumed.payload, ThreadTransitionCommandPayload)
    assert resumed.payload.transition.reason == "resume"
    assert resumed.payload.transition.thread.view.thread.id == first_id

    stale = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.SEARCH,
                arguments=("needle", second_id),
            )
        )
    )
    assert isinstance(stale, CommandError)
    assert stale.code == "thread_not_found"
    assert _unwrap(await application.get_state()).current_thread_id == first_id
    unquoted = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.SEARCH,
                arguments=("two", "words"),
            )
        )
    )
    assert isinstance(unquoted, CommandError)
    assert unquoted.code == "thread_not_found"
    assert _unwrap(await application.get_state()).current_thread_id == first_id

    exported = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.EXPORT,
                arguments=("exports/thread.md",),
            )
        )
    )
    assert isinstance(exported, CommandResult)
    assert isinstance(exported.payload, ThreadExportCommandPayload)
    assert exported.payload.thread_id == first_id
    assert exported.payload.write_status == "created"
    assert exported.payload.byte_count == len(
        (workspace / "exports" / "thread.md").read_bytes()
    )
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_code", "retryable"),
    [
        (ResourceLockTimeout(), "operation_busy", True),
        (ResourceLockUnavailable(), "state_unavailable", True),
    ],
)
async def test_credential_resource_lock_failures_are_typed_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    error_code: str,
    retryable: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))

    def fail_lock(_: int, __: float) -> None:
        raise failure

    monkeypatch.setattr(resource_lock, "_acquire_platform_lock", fail_lock)
    outcome = await application.set_provider_credential(
        ProviderCredentialSetRequest(
            provider="mem0",
            action="add",
            api_key=SecretStr("synthetic-secret"),
        )
    )

    assert outcome.ok is False
    assert outcome.error is not None
    assert outcome.error.code == error_code
    assert outcome.error.retryable is retryable
    assert "lock" not in outcome.error.message.lower()
    if error_code == "state_unavailable":
        assert outcome.error.data == {
            "state_directory": str((tmp_path / "home" / "state").resolve())
        }
    assert not (tmp_path / "home" / ".env").exists()
    await application.shutdown()


@pytest.mark.asyncio
async def test_operation_guard_state_unavailable_matches_protocol_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id

    def fail_lock(_: int, __: float) -> None:
        raise ResourceLockUnavailable

    monkeypatch.setattr(resource_lock, "_acquire_platform_lock", fail_lock)
    outcome = await application.execute_direct(thread_id, "echo must-not-run")

    assert outcome.ok is False
    assert outcome.error is not None
    assert outcome.error.code == "state_unavailable"
    assert outcome.error.retryable is True
    assert outcome.error.data == {
        "state_directory": str((tmp_path / "home" / "state").resolve())
    }
    await application.shutdown()


@pytest.mark.asyncio
async def test_trusted_agents_md_is_snapshotted_into_model_context_and_manifest(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    instructions = workspace / "AGENTS.md"
    instructions.write_text("MANDATORY_WORKSPACE_RULE", encoding="utf-8")
    gateways: list[FakeGateway] = []

    def gateway_factory(provider: ProviderId, model: str) -> ModelGateway:
        gateway = FakeGateway(provider, model)
        gateways.append(gateway)
        return cast(ModelGateway, gateway)

    sink = CollectingEventSink()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=sink,
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=gateway_factory,
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    instructions.write_text("REPLACED_AFTER_TRUST", encoding="utf-8")
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id

    _unwrap(await application.submit_turn(thread_id, "inspect", "client_agents"))
    view = await _wait_for_thread(application, thread_id, entries=2)

    requests = [request for gateway in gateways for request in gateway.requests]
    assert requests
    system_context = "\n".join(
        message.content
        for request in requests
        for message in request.messages
        if message.role == "system"
    )
    assert "MANDATORY_WORKSPACE_RULE" in system_context
    assert "REPLACED_AFTER_TRUST" not in system_context
    manifest = tuple(
        ContextManifestItem.model_validate(item)
        for item in view.turns[-1].context_manifest
    )
    assert any(item.source_id == "AGENTS.md" for item in manifest)
    assert (
        _unwrap(await application.get_state()).workspace_instruction_diagnostic is None
    )
    await application.shutdown()


@pytest.mark.asyncio
async def test_invalid_agents_md_is_ignored_without_invalidating_configuration(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_bytes(b"x" * (32 * 1024 + 1))
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))

    state = _unwrap(await application.get_state())
    assert state.configuration_valid is True
    assert state.workspace_instruction_diagnostic is not None
    assert (
        state.workspace_instruction_diagnostic.code
        == "workspace_instructions_too_large"
    )
    doctor = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.DOCTOR))
    )
    assert isinstance(doctor, CommandResult)
    assert isinstance(doctor.payload, DoctorCommandPayload)
    workspace_check = next(
        check
        for check in doctor.payload.checks
        if check.name == "Workspace instructions"
    )
    assert workspace_check.status == "error"
    assert workspace_check.detail == state.workspace_instruction_diagnostic.message
    await application.shutdown()


@pytest.mark.asyncio
async def test_credential_and_turn_leases_exclude_each_other_in_both_directions(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    validator = BlockingCredentialValidator()
    gateway = BlockingGateway("deepseek", "deepseek/deepseek-v4-flash")
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "original-key"},
        gateway_factory=lambda provider, model: cast(ModelGateway, gateway),
        credential_validator=validator,
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id
    credential_request = ProviderCredentialSetRequest(
        provider="deepseek",
        action="replace",
        api_key=SecretStr("replacement-key"),
    )

    saving = asyncio.create_task(
        application.set_provider_credential(credential_request)
    )
    await validator.started.wait()
    blocked_turn = await application.submit_turn(
        thread_id,
        "must wait for credential mutation",
        "client_credential_busy",
    )
    assert blocked_turn.ok is False
    assert blocked_turn.error is not None
    assert blocked_turn.error.code == "operation_busy"
    validator.release.set()
    assert _unwrap(await saving).status == "configured"

    accepted = _unwrap(
        await application.submit_turn(
            thread_id,
            "hold the operation",
            "client_turn_busy",
        )
    )
    await gateway.started.wait()
    blocked_credential = await application.set_provider_credential(credential_request)
    assert blocked_credential.ok is False
    assert blocked_credential.error is not None
    assert blocked_credential.error.code == "operation_busy"
    assert validator.calls == 1
    _unwrap(await application.cancel_operation(accepted.operation_id))
    for _ in range(200):
        if _unwrap(await application.get_state()).active_operation_id is None:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("cancelled operation did not release its lease")
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_kind", ["turn", "direct"])
async def test_pending_interaction_wins_operation_admission_after_async_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway = FakeGateway("deepseek", "deepseek/deepseek-v4-flash")
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=lambda provider, model: cast(ModelGateway, gateway),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id

    backend = cast(Any, application)._backend
    runtime = backend._runtime
    assert runtime is not None
    consistency = BlockFirstConsistencyCheck()
    monkeypatch.setattr(
        runtime.provider_configuration,
        "ensure_consistent",
        consistency,
    )
    if operation_kind == "turn":
        starting = asyncio.create_task(
            application.submit_turn(
                thread_id,
                "must lose admission",
                "client_pending_race",
            )
        )
    else:
        starting = asyncio.create_task(
            application.execute_direct(thread_id, "echo must-not-run")
        )
    await consistency.entered.wait()

    confirmation = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("full_access",),
            )
        )
    )
    assert isinstance(confirmation, CommandInteractionResult)
    assert isinstance(confirmation.interaction, CommandApplicationInteraction)
    consistency.release.set()
    blocked = await starting

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code == "operation_busy"
    state = _unwrap(await application.get_state())
    assert state.active_operation_id is None
    assert state.pending_interaction_id == confirmation.interaction.interaction_id
    view = _unwrap(
        await application.read_thread(ThreadReadQuery(thread_id=thread_id))
    ).view
    assert view.turns == ()
    assert view.entries == ()
    assert gateway.requests == []

    _unwrap(
        await application.respond_interaction(
            confirmation.interaction.interaction_id,
            "deny",
        )
    )
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_kind", ["turn", "direct"])
async def test_active_operation_wins_pending_interaction_creation(
    tmp_path: Path,
    operation_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway = BlockingGateway("deepseek", "deepseek/deepseek-v4-flash")
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=lambda provider, model: cast(ModelGateway, gateway),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id

    direct_executor = BlockingDirectExecutor()
    if operation_kind == "turn":
        accepted = _unwrap(
            await application.submit_turn(
                thread_id,
                "hold operation lease",
                "client_operation_race",
            )
        )
        await gateway.started.wait()
    else:
        backend = cast(Any, application)._backend
        runtime = backend._runtime
        assert runtime is not None
        runtime.direct._executor = direct_executor
        accepted = _unwrap(
            await application.execute_direct(thread_id, "echo hold-operation")
        )
        await direct_executor.started.wait()

    blocked = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("full_access",),
            )
        )
    )
    assert isinstance(blocked, CommandError)
    assert blocked.code == "operation_busy"
    assert _unwrap(await application.get_state()).pending_interaction_id is None

    if operation_kind == "turn":
        gateway.release.set()
        await _wait_for_thread(application, thread_id, entries=2)
    else:
        direct_executor.release.set()
        for _ in range(200):
            state = _unwrap(await application.get_state())
            if state.active_operation_id is None:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("direct operation did not release its lease")
        assert direct_executor.calls == 1
    assert accepted.operation_id
    await application.shutdown()


@pytest.mark.asyncio
async def test_recovery_operation_admission_requires_exact_current_binding(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    backend = cast(Any, application)._backend

    pending = backend._interactions.create(
        kind=InteractionKind.RECOVERY_DECISION,
        prompt="Resume recovery?",
        operation="recover_turn",
        target="turn_1",
        capability=None,
        choices=recovery_decision_choices(uncertain=False),
        thread_id="thread_1",
        turn_id="turn_1",
    )
    current = OperationContinuation(
        interaction_id=pending.id,
        interaction_generation=pending.generation,
        thread_id="thread_1",
        turn_id="turn_1",
    )
    mismatched = (
        current.__class__(
            interaction_id="interaction_stale",
            interaction_generation=current.interaction_generation,
            thread_id=current.thread_id,
            turn_id=current.turn_id,
        ),
        current.__class__(
            interaction_id=current.interaction_id,
            interaction_generation=current.interaction_generation + 1,
            thread_id=current.thread_id,
            turn_id=current.turn_id,
        ),
        current.__class__(
            interaction_id=current.interaction_id,
            interaction_generation=current.interaction_generation,
            thread_id="thread_other",
            turn_id=current.turn_id,
        ),
        current.__class__(
            interaction_id=current.interaction_id,
            interaction_generation=current.interaction_generation,
            thread_id=current.thread_id,
            turn_id="turn_other",
        ),
    )
    for continuation in mismatched:
        with pytest.raises(OperationBusy, match="pending interaction"):
            backend._operations.reserve(continuation=continuation)

    reservation = backend._operations.reserve(continuation=current)
    backend._operations.abort(reservation)
    assert backend._interactions.discard(pending.id) is True
    replacement = backend._interactions.create(
        kind=InteractionKind.RECOVERY_DECISION,
        prompt="Resume replacement recovery?",
        operation="recover_turn",
        target="turn_1",
        capability=None,
        choices=recovery_decision_choices(uncertain=False),
        thread_id="thread_1",
        turn_id="turn_1",
    )

    with pytest.raises(OperationBusy, match="pending interaction"):
        backend._operations.reserve(continuation=current)
    replacement_token = OperationContinuation(
        interaction_id=replacement.id,
        interaction_generation=replacement.generation,
        thread_id="thread_1",
        turn_id="turn_1",
    )
    reservation = backend._operations.reserve(continuation=replacement_token)
    backend._operations.abort(reservation)
    backend._interactions.discard(replacement.id)
    await application.shutdown()


@pytest.mark.asyncio
async def test_permission_mode_is_confirmed_and_resets_on_thread_switch(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=lambda provider, model: cast(
            ModelGateway, FakeGateway(provider, model)
        ),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    first = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(first, CommandResult)
    assert isinstance(first.payload, ThreadTransitionCommandPayload)
    first_id = first.payload.transition.thread.view.thread.id
    second = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(second, CommandResult)
    assert isinstance(second.payload, ThreadTransitionCommandPayload)
    second_id = second.payload.transition.thread.view.thread.id
    _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.RESUME, arguments=(first_id,))
        )
    )

    picker = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.PERMISSIONS))
    )
    assert isinstance(picker, CommandInteractionResult)
    assert picker.interaction.kind == "selection"
    assert _unwrap(await application.get_state()).permission_mode == "request_approval"

    accepted_edits = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("accept_edits",),
            )
        )
    )
    assert isinstance(accepted_edits, CommandResult)
    assert _unwrap(await application.get_state()).permission_mode == "accept_edits"

    confirmation = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("full_access",),
            )
        )
    )
    assert isinstance(confirmation, CommandInteractionResult)
    assert confirmation.interaction.kind == "application"
    interaction_id = confirmation.interaction.interaction_id
    blocked_turn = await application.submit_turn(
        first_id,
        "must not start",
        "client_permission_pending",
    )
    assert blocked_turn.ok is False
    assert blocked_turn.error is not None
    assert blocked_turn.error.code == "operation_busy"
    blocked_direct = await application.execute_direct(first_id, "echo blocked")
    assert blocked_direct.ok is False
    assert blocked_direct.error is not None
    assert blocked_direct.error.code == "operation_busy"
    for intent in (
        CommandIntent(name=CommandName.NEW),
        CommandIntent(name=CommandName.RESUME, arguments=(first_id,)),
    ):
        blocked_transition = _unwrap(await application.execute_command(intent))
        assert isinstance(blocked_transition, CommandError)
        assert blocked_transition.code == "interaction_busy"
    blocked = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("request_approval",),
            )
        )
    )
    assert isinstance(blocked, CommandError)
    assert blocked.code == "interaction_busy"
    _unwrap(
        await application.respond_interaction(
            interaction_id,
            "enable_full_access",
        )
    )
    assert _unwrap(await application.get_state()).permission_mode == "full_access"

    cross_thread_turn = await application.submit_turn(
        second_id,
        "must not inherit full access",
        "client_cross_thread_permission",
    )
    assert cross_thread_turn.ok is False
    assert cross_thread_turn.error is not None
    assert cross_thread_turn.error.code == "invalid_arguments"
    cross_thread_direct = await application.execute_direct(second_id, "echo blocked")
    assert cross_thread_direct.ok is False
    assert cross_thread_direct.error is not None
    assert cross_thread_direct.error.code == "invalid_arguments"
    second_view = _unwrap(
        await application.read_thread(ThreadReadQuery(thread_id=second_id))
    )
    assert second_view.view.turns == ()
    assert second_view.view.entries == ()

    _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.RESUME, arguments=(second_id,))
        )
    )
    assert _unwrap(await application.get_state()).permission_mode == "request_approval"
    stale = _unwrap(
        await application.respond_interaction(
            interaction_id,
            "enable_full_access",
        )
    )
    assert stale.accepted is False
    assert stale.status == "not_found"
    assert _unwrap(await application.get_state()).permission_mode == "request_approval"
    _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.RESUME, arguments=(first_id,))
        )
    )
    assert _unwrap(await application.get_state()).permission_mode == "request_approval"
    await application.shutdown()


@pytest.mark.asyncio
async def test_full_access_resolution_event_failure_is_fail_closed_and_not_replayable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sink = FailOnceOnFullAccessResolvedSink()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=sink,
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=lambda provider, model: cast(
            ModelGateway, FakeGateway(provider, model)
        ),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    _unwrap(await application.execute_command(CommandIntent(name=CommandName.NEW)))
    session = cast(Any, application)._backend._permission_session
    session.grant_thread_writes()
    original_generation = session.generation
    original_grants = set(session.granted_capabilities)

    confirmation = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("full_access",),
            )
        )
    )
    assert isinstance(confirmation, CommandInteractionResult)
    assert isinstance(confirmation.interaction, CommandApplicationInteraction)
    interaction_id = confirmation.interaction.interaction_id
    with pytest.raises(BrokenPipeError, match="protocol output closed"):
        await application.respond_interaction(interaction_id, "enable_full_access")

    state = _unwrap(await application.get_state())
    assert state.permission_mode == "request_approval"
    assert state.pending_interaction_id is None
    assert session.generation == original_generation
    assert session.granted_capabilities == original_grants
    replay = _unwrap(
        await application.respond_interaction(interaction_id, "enable_full_access")
    )
    assert (replay.accepted, replay.status) == (False, "not_found")

    replacement = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("full_access",),
            )
        )
    )
    assert isinstance(replacement, CommandInteractionResult)
    assert isinstance(replacement.interaction, CommandApplicationInteraction)
    _unwrap(
        await application.respond_interaction(
            replacement.interaction.interaction_id,
            "enable_full_access",
        )
    )
    assert _unwrap(await application.get_state()).permission_mode == "full_access"
    assert session.generation == original_generation + 1
    assert session.granted_capabilities == set()
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "command_runs", "terminal_event"),
    [
        ("allow_once", True, EventType.TOOL_COMPLETED),
        ("deny", False, EventType.TOOL_FAILED),
    ],
)
async def test_composed_agent_execute_waits_for_application_decision(
    tmp_path: Path,
    decision: str,
    command_runs: bool,
    terminal_event: EventType,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "agent-executed.txt"
    command = (
        f'"{sys.executable}" -c "from pathlib import Path; '
        "Path('agent-executed.txt').write_text('ran', encoding='utf-8')\""
    )

    def gateway_factory(provider: ProviderId, model: str) -> ModelGateway:
        return cast(ModelGateway, ExecuteGateway(provider, model, command))

    sink = CollectingEventSink()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=gateway_factory,
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id

    _unwrap(
        await application.submit_turn(thread_id, "run the command", "client_command")
    )
    interaction_id = await _wait_for_interaction(application)
    assert not marker.exists()
    blocked = _unwrap(
        await application.execute_command(
            CommandIntent(
                name=CommandName.PERMISSIONS,
                arguments=("full_access",),
            )
        )
    )
    assert isinstance(blocked, CommandError)
    assert blocked.code == "operation_busy"

    resolved = _unwrap(await application.respond_interaction(interaction_id, decision))
    assert resolved.accepted
    view = await _wait_for_thread(application, thread_id, entries=2)

    assert marker.exists() is command_runs
    assert view.entries[-1].content == "done"
    assert any(
        event.event_type is EventType.INTERACTION_REQUIRED for event in sink.events
    )
    assert any(
        event.event_type is EventType.INTERACTION_RESOLVED for event in sink.events
    )
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TOOL_STARTED) == 1
    assert event_types.count(terminal_event) == 1
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "secret"),
    [
        ("deepseek", "deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"),
        ("kimi", "kimi/kimi-k2.6", "MOONSHOT_API_KEY"),
    ],
)
async def test_fresh_home_trust_turn_direct_and_restart(
    tmp_path: Path,
    provider: str,
    model: str,
    secret: str,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateways: list[FakeGateway] = []

    def gateway_factory(
        selected_provider: ProviderId, selected_model: str
    ) -> ModelGateway:
        gateway = FakeGateway(selected_provider, selected_model)
        gateways.append(gateway)
        return cast(ModelGateway, gateway)

    sink = CollectingEventSink()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={secret: "fake-key"},
        gateway_factory=gateway_factory,
    )

    pending = _unwrap(await application.initialize())
    assert pending.status is InitializeStatus.TRUST_REQUIRED
    assert pending.interaction_id is not None
    trusted = _unwrap(
        await application.respond_interaction(pending.interaction_id, "trust")
    )
    assert trusted.accepted
    assert _unwrap(await application.initialize()).status is InitializeStatus.READY
    state = _unwrap(await application.get_state())
    assert state.initialized and state.workspace_trusted
    assert state.current_thread_id is None

    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    assert isinstance(created, CommandResult)
    assert isinstance(created.payload, ThreadTransitionCommandPayload)
    thread_id = created.payload.transition.thread.view.thread.id

    model_result = _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.MODEL, arguments=(provider, model))
        )
    )
    assert isinstance(model_result, CommandResult)
    assert isinstance(model_result.payload, ModelCommandPayload)
    accepted = _unwrap(
        await application.submit_turn(thread_id, "inspect workspace", "client_inspect")
    )
    assert accepted.turn_id is not None
    turn_view = await _wait_for_thread(application, thread_id, entries=2)
    assert turn_view.entries[-1].content == "done"
    assert turn_view.turns[-1].provider == provider
    assert gateways

    direct = _unwrap(await application.execute_direct(thread_id, "echo direct-ok"))
    assert direct.turn_id is None
    direct_view = await _wait_for_thread(application, thread_id, entries=3)
    assert direct_view.turns == turn_view.turns
    assert "direct-ok" in direct_view.entries[-1].content
    assert direct_view.tool_activities[-1].turn_id is None

    tools = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.TOOLS))
    )
    assert isinstance(tools, CommandResult)
    assert isinstance(tools.payload, ToolCatalogCommandPayload)
    names = {item.name for item in tools.payload.tools}
    assert {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    } <= names
    await application.shutdown()

    restart_sink = CollectingEventSink()
    restarted = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=restart_sink,
        environ={secret: "fake-key"},
        gateway_factory=gateway_factory,
    )
    ready = _unwrap(await restarted.initialize())
    assert ready.status is InitializeStatus.READY
    assert _unwrap(await restarted.get_state()).current_thread_id is None
    assert [
        item.id
        for item in _unwrap(await restarted.list_threads(ThreadListQuery())).threads
    ] == [thread_id]
    _unwrap(
        await restarted.execute_command(
            CommandIntent(name=CommandName.RESUME, arguments=(thread_id,))
        )
    )
    assert (
        _unwrap(await restarted.read_thread(ThreadReadQuery(thread_id=thread_id)))
        .view.entries[-1]
        .kind.value
        == "direct_command"
    )
    assert not any(
        event.event_type in {EventType.TURN_STARTED, EventType.TURN_COMPLETED}
        for event in restart_sink.events
    )
    await restarted.shutdown()
