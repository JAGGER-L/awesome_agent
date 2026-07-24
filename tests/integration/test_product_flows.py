from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from awesome_agent.application.command_results import (
    CommandError,
    CommandInteractionResult,
    CommandResult,
    DoctorCommandPayload,
    ModelCommandPayload,
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
)
from awesome_agent.application.facade import LocalApplication
from awesome_agent.config import CredentialValidation, CredentialValidationStatus
from awesome_agent.context import ContextManifestItem
from awesome_agent.conversation import ThreadView
from awesome_agent.core.events import CollectingEventSink, EventType
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
    for _ in range(200):
        view = _unwrap(
            await application.read_thread(ThreadReadQuery(thread_id=thread_id))
        ).view
        state = _unwrap(await application.get_state())
        if len(view.entries) >= entries and state.active_operation_id is None:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("foreground operation did not complete")


async def _wait_for_interaction(application: LocalApplication) -> str:
    for _ in range(200):
        state = _unwrap(await application.get_state())
        if state.pending_interaction_id is not None:
            return state.pending_interaction_id
        await asyncio.sleep(0.01)
    raise AssertionError("execute interaction was not requested")


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
