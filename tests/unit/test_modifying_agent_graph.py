from __future__ import annotations

import json
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    ApprovalStatus,
    ExecutionKind,
    RunIntent,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ModelUsage,
    StopReason,
    StructuredModelProvider,
    SystemMessage,
    ToolCall,
    ToolChoiceMode,
    ToolResultMessage,
    TurnCompleted,
    UserMessage,
)
from awesome_agent.persistence.approvals import InMemoryApprovalRepository
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    InMemoryToolInvocationRepository,
)
from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    ValidationReportWithGates,
)
from awesome_agent.runtime.budget import BudgetDecision, BudgetPolicy
from awesome_agent.runtime.context import (
    ContextManager,
    DeterministicSummaryProvider,
)
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    IncompatibleGraphError,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_ROUTE,
)
from awesome_agent.runtime.modifying_graph import (
    ModifyingAgentLoopFailed,
    ModifyingAgentState,
    ModifyingCodingGraph,
    _idempotency_key,
)
from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan
from awesome_agent.sandbox.base import CommandResult
from awesome_agent.tools.repository import canonical_arguments_hash_from_arguments


class SequenceProvider(StructuredModelProvider):
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = deque(turns)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        yield TurnCompleted(turn=self.turns.popleft())


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _run(workspace: Path) -> tuple[Run, Agent]:
    run = Run(
        goal="Change README",
        intent=RunIntent.MODIFYING,
        runtime_route=MODIFYING_CODING_ROUTE,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=workspace,
    )
    return run, Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
    )


def _budget_policy(
    *,
    soft_context_tokens: int = 10_000,
    hard_context_tokens: int = 20_000,
    recent_context_tokens: int = 5_000,
    max_total_tokens_per_run: int = 100_000,
    max_reasoning_tokens_per_run: int = 100_000,
    max_active_seconds_per_run: int = 3600,
) -> BudgetPolicy:
    return BudgetPolicy(
        soft_context_tokens=soft_context_tokens,
        hard_context_tokens=hard_context_tokens,
        recent_context_tokens=recent_context_tokens,
        max_total_tokens_per_run=max_total_tokens_per_run,
        max_reasoning_tokens_per_run=max_reasoning_tokens_per_run,
        max_active_seconds_per_run=max_active_seconds_per_run,
    )


def _node_state(
    run: Run,
    agent: Agent,
    messages: list[dict[str, object]],
    *,
    successful_writes: int = 0,
    final_diff_after_write: bool = False,
) -> ModifyingAgentState:
    return cast(
        ModifyingAgentState,
        {
            "run_id": str(run.id),
            "agent_id": str(agent.id),
            "runtime_route": MODIFYING_CODING_ROUTE,
            "messages": messages,
            "continuation": None,
            "model_turn_count": 0,
            "tool_call_count": 0,
            "successful_writes": successful_writes,
            "final_diff_after_write": final_diff_after_write,
            "progress_fingerprints": [],
            "stagnant_turns": 0,
            "validation_rework_count": 0,
            "validation_reports": [],
            "phase": "tools_completed",
            "force_final": False,
            "rolling_summary": "",
            "budget_ledger": {},
            "context_artifact_refs": [],
        },
    )


@pytest.mark.asyncio
async def test_modifying_graph_requires_patch_and_final_diff(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    (tmp_path / "large.txt").write_text("x" * 20_000, encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="large-read",
                            name="repo.read",
                            arguments_json=json.dumps({"path": "large.txt"}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="patch",
                            name="repo.apply_patch",
                            arguments_json=json.dumps({"patch": patch}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="diff",
                            name="repo.diff",
                            arguments_json="{}",
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    content=(
                        "Changed README.md. Validation has not been run; this is "
                        "modifying_unvalidated."
                    )
                ),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=InMemoryArtifactMetadataRepository(),
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=_passing_validation_runner,
    )
    run, agent = _run(tmp_path)

    state, recovered = await graph.execute(run, agent)

    assert not recovered
    assert state["successful_writes"] == 1
    assert state["final_diff_after_write"]
    assert state["phase"] == "completed"
    assert "new" in (tmp_path / "README.md").read_text(encoding="utf-8")
    tool_messages = [
        message
        for message in state["messages"]
        if message.get("role") == "tool" and message.get("artifact_refs")
    ]
    assert tool_messages


@pytest.mark.asyncio
async def test_modifying_model_turn_compacts_context_before_provider_call(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="bounded modifying summary"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        ]
    )
    artifact_repository = InMemoryArtifactMetadataRepository()
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifact_repository,
        context_manager=ContextManager(
            summary_provider=DeterministicSummaryProvider(),
            artifact_store=LocalArtifactStore(tmp_path / "context-artifacts"),
            artifact_repository=artifact_repository,
        ),
        budget_repository=InMemoryBudgetRepository(),
        budget_policy=_budget_policy(
            soft_context_tokens=100,
            hard_context_tokens=2_000,
            recent_context_tokens=80,
        ),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent
    tool_call = ToolCall(
        call_id="diff",
        name="repo.diff",
        arguments_json="{}",
    )

    updated = await graph._model_turn(
        _node_state(
            run,
            agent,
            [
                SystemMessage(content="system").model_dump(mode="json"),
                UserMessage(content="goal").model_dump(mode="json"),
                UserMessage(content="old context " * 1000).model_dump(mode="json"),
                AssistantMessage(tool_calls=[tool_call]).model_dump(mode="json"),
                ToolResultMessage(
                    call_id=tool_call.call_id,
                    content="diff -- README.md",
                ).model_dump(mode="json"),
            ],
            successful_writes=1,
            final_diff_after_write=True,
        )
    )

    request = provider.requests[0]
    assert all(
        "old context " * 20 not in getattr(message, "content", "")
        for message in request.messages
    )
    assert request.messages[1].content.startswith("Prior context summary:")
    assert updated["rolling_summary"]
    assert updated["context_artifact_refs"]
    assert await artifact_repository.list_for_run(run.id)


@pytest.mark.asyncio
async def test_modifying_model_turn_records_budget_usage(tmp_path: Path) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="answer"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
                usage=ModelUsage(
                    input_tokens=10,
                    output_tokens=20,
                    reasoning_tokens=5,
                ),
            )
        ]
    )
    budget_repository = InMemoryBudgetRepository()
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        budget_repository=budget_repository,
        budget_policy=_budget_policy(),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent

    await graph._model_turn(
        _node_state(
            run,
            agent,
            [
                SystemMessage(content="system").model_dump(mode="json"),
                UserMessage(content="goal").model_dump(mode="json"),
            ],
        )
    )

    ledger = await budget_repository.get_ledger(run.id)
    assert ledger.total_input_tokens == 10
    assert ledger.total_output_tokens == 20
    assert ledger.total_reasoning_tokens == 5
    assert ledger.model_call_count == 1


@pytest.mark.asyncio
async def test_modifying_hard_context_limit_disables_tools_and_routes_validation(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="bounded final"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        ]
    )
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=InMemoryArtifactMetadataRepository(),
        context_manager=ContextManager(
            summary_provider=DeterministicSummaryProvider(),
            artifact_store=LocalArtifactStore(tmp_path / "context-artifacts"),
            artifact_repository=InMemoryArtifactMetadataRepository(),
        ),
        budget_repository=InMemoryBudgetRepository(),
        budget_policy=_budget_policy(
            soft_context_tokens=10,
            hard_context_tokens=20,
            recent_context_tokens=5,
        ),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent

    updated = await graph._model_turn(
        _node_state(
            run,
            agent,
            [
                SystemMessage(content="system " * 200).model_dump(mode="json"),
                UserMessage(content="goal").model_dump(mode="json"),
            ],
            successful_writes=1,
            final_diff_after_write=True,
        )
    )

    request = provider.requests[0]
    assert request.tool_choice.mode is ToolChoiceMode.NONE
    assert request.tools == []
    assert updated["force_final"]
    assert graph._route_turn(updated) == "validate"


@pytest.mark.asyncio
async def test_modifying_budget_exhaustion_fails_without_recovery_required(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="should not run"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        ]
    )
    budget_repository = InMemoryBudgetRepository()
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        budget_repository=budget_repository,
        budget_policy=_budget_policy(max_total_tokens_per_run=3),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent

    with pytest.raises(ModifyingAgentLoopFailed, match="budget_exhausted"):
        await graph._model_turn(
            _node_state(
                run,
                agent,
                [
                    SystemMessage(content="system prompt").model_dump(mode="json"),
                    UserMessage(content="goal").model_dump(mode="json"),
                ],
                successful_writes=1,
                final_diff_after_write=False,
            )
        )

    assert provider.requests == []
    ledger = await budget_repository.get_ledger(run.id)
    assert ledger.threshold_status == BudgetDecision.EXHAUSTED.value


@pytest.mark.asyncio
async def test_modifying_graph_validates_before_completion(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="patch",
                            name="repo.apply_patch",
                            arguments_json=json.dumps({"patch": patch}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="diff",
                            name="repo.diff",
                            arguments_json="{}",
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(content="Changed README.md."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    validation_calls = 0

    async def validation_runner(
        plan: ValidationPlan,
        run: Run,
        agent: Agent,
    ) -> ValidationReportWithGates:
        nonlocal validation_calls
        validation_calls += 1
        return _validation_result(run, agent, status="passed")

    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=validation_runner,
    )
    events: list[tuple[object, dict[str, object], str]] = []
    run, agent = _run(tmp_path)

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    state, _ = await graph.execute(run, agent, event_sink=emit)

    assert validation_calls == 1
    assert state["phase"] == "completed"
    assert state["validation_rework_count"] == 0
    assert state["validation_reports"][0]["status"] == "passed"
    assert events[-1][1]["validation_complete"] is True


@pytest.mark.asyncio
async def test_modifying_graph_reworks_after_validation_failure(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    first_patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+bad
"""
    second_patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-bad
+good
"""
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="patch-1",
                            name="repo.apply_patch",
                            arguments_json=json.dumps({"patch": first_patch}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="diff-1",
                            name="repo.diff",
                            arguments_json="{}",
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(content="Changed README.md."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="patch-2",
                            name="repo.apply_patch",
                            arguments_json=json.dumps({"patch": second_patch}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="diff-2",
                            name="repo.diff",
                            arguments_json="{}",
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(content="Fixed README.md."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    outcomes = ["failed", "passed"]

    async def validation_runner(
        plan: ValidationPlan,
        run: Run,
        agent: Agent,
    ) -> ValidationReportWithGates:
        return _validation_result(run, agent, status=outcomes.pop(0))

    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        validation_plan_resolver=lambda _: _validation_plan(max_rework_attempts=2),
        validation_runner=validation_runner,
    )
    run, agent = _run(tmp_path)

    state, _ = await graph.execute(run, agent)

    assert state["phase"] == "completed"
    assert state["validation_rework_count"] == 1
    assert [report["status"] for report in state["validation_reports"]] == [
        "failed",
        "passed",
    ]
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "good\n"


@pytest.mark.asyncio
async def test_modifying_graph_fails_when_no_validation_gates(
    tmp_path: Path,
) -> None:
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: _completion_provider(),
        validation_plan_resolver=lambda _: None,
    )
    run, agent = _run_with_change_ready_state(tmp_path)
    graph._run = run
    graph._agent = agent

    with pytest.raises(ModifyingAgentLoopFailed, match="no_validation_gates"):
        await graph._validate(_change_ready_state(run, agent))


@pytest.mark.asyncio
async def test_modifying_graph_fails_when_rework_attempts_are_exhausted(
    tmp_path: Path,
) -> None:
    async def validation_runner(
        plan: ValidationPlan,
        run: Run,
        agent: Agent,
    ) -> ValidationReportWithGates:
        return _validation_result(run, agent, status="failed")

    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: _completion_provider(),
        validation_plan_resolver=lambda _: _validation_plan(max_rework_attempts=0),
        validation_runner=validation_runner,
    )
    run, agent = _run_with_change_ready_state(tmp_path)
    graph._run = run
    graph._agent = agent

    with pytest.raises(ModifyingAgentLoopFailed, match="exhausted"):
        await graph._validate(_change_ready_state(run, agent))


@pytest.mark.asyncio
async def test_modifying_graph_interrupts_and_resumes_approved_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
    shell_runs = 0

    async def fake_run_process(
        arguments: list[str],
        *,
        command_label: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        nonlocal shell_runs
        shell_runs += 1
        return CommandResult(
            command=command_label,
            exit_code=0,
            stdout="approved\n",
            stderr="",
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="shell",
                            name="shell.execute",
                            arguments_json=json.dumps(
                                {"argv": ["python", "script.py"]}
                            ),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="patch",
                            name="repo.apply_patch",
                            arguments_json=json.dumps({"patch": patch}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="diff",
                            name="repo.diff",
                            arguments_json="{}",
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(content="Changed README.md."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    approvals = InMemoryApprovalRepository()
    tools = InMemoryToolInvocationRepository()
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        tool_repository=tools,
        approval_repository=approvals,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=_passing_validation_runner,
    )
    events: list[tuple[object, dict[str, object], str]] = []
    run, agent = _run(tmp_path)

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await graph.execute(run, agent, event_sink=emit)

    approval = await approvals.get(interrupted.value.approval_id)
    invocations = await tools.list_for_run(run.id)
    assert approval.status is ApprovalStatus.PENDING
    assert invocations[0].status == "approval_pending"
    assert events[-1][2] == "approval:shell"

    await approvals.decide(
        approval.id,
        approved=True,
        decided_by="test",
        reason=None,
        now=approval.created_at,
    )
    state, recovered = await graph.execute(run, agent, event_sink=emit)

    assert recovered
    assert shell_runs == 1
    assert state["phase"] == "completed"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_modifying_graph_rejects_incompatible_run(tmp_path: Path) -> None:
    provider = SequenceProvider([])
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
    )
    run, agent = _run(tmp_path)
    incompatible = Run(
        id=run.id,
        goal=run.goal,
        intent=RunIntent.READ_ONLY,
        execution_kind=ExecutionKind.CODING,
        runtime_route="other",
        graph_thread_id=run.graph_thread_id,
        workspace_path=tmp_path,
    )

    with pytest.raises(IncompatibleGraphError):
        await graph.execute(incompatible, agent)


@pytest.mark.asyncio
async def test_modifying_graph_requires_thread_and_workspace(tmp_path: Path) -> None:
    provider = SequenceProvider([])
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
    )
    run, agent = _run(tmp_path)

    missing_thread = Run(
        id=run.id,
        goal=run.goal,
        intent=run.intent,
        execution_kind=ExecutionKind.CODING,
        runtime_route=run.runtime_route,
        workspace_path=tmp_path,
    )
    with pytest.raises(CorruptRuntimeStateError, match="graph_thread_id"):
        await graph.execute(missing_thread, agent)

    missing_workspace = Run(
        id=run.id,
        goal=run.goal,
        intent=run.intent,
        execution_kind=ExecutionKind.CODING,
        runtime_route=run.runtime_route,
        graph_thread_id=run.graph_thread_id,
        workspace_path=tmp_path / "missing",
    )
    with pytest.raises(CorruptRuntimeStateError, match="workspace"):
        await graph.execute(missing_workspace, agent)


@pytest.mark.asyncio
async def test_modifying_graph_reuses_completed_durable_tool_result(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
    tool_repository = InMemoryToolInvocationRepository()
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: SequenceProvider([]),
        tool_repository=tool_repository,
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent
    call = ToolCall(
        call_id="patch",
        name="repo.apply_patch",
        arguments_json=json.dumps({"patch": patch}),
    )

    first = await graph._execute_durable_tool_call(call)
    second = await graph._execute_durable_tool_call(call)
    invocations = await tool_repository.list_for_run(run.id)

    assert not first.is_error
    assert second.content == first.content
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"
    assert len(invocations) == 1
    assert invocations[0].status == "completed"
    assert invocations[0].result_content == first.content


@pytest.mark.asyncio
async def test_modifying_graph_marks_started_shell_as_recovery_required(
    tmp_path: Path,
) -> None:
    tool_repository = InMemoryToolInvocationRepository()
    graph = ModifyingCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: SequenceProvider([]),
        tool_repository=tool_repository,
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent
    arguments = {
        "argv": ["pytest"],
        "timeout_seconds": 60.0,
        "max_output_chars": 30_000,
    }
    await tool_repository.upsert(
        DurableToolInvocation(
            id=uuid4(),
            run_id=run.id,
            agent_id=agent.id,
            tool_name="shell.execute",
            tool_version="1",
            status="started",
            idempotency_key=_idempotency_key(
                run_id=str(run.id),
                agent_id=str(agent.id),
                tool_name="shell.execute",
                tool_version="1",
                arguments_hash=canonical_arguments_hash_from_arguments(arguments),
                workspace=str(tmp_path),
            ),
            arguments_hash=canonical_arguments_hash_from_arguments(arguments),
            risk_level="medium",
        )
    )

    with pytest.raises(CorruptRuntimeStateError, match="Shell execution"):
        await graph._execute_durable_tool_call(
            ToolCall(
                call_id="shell",
                name="shell.execute",
                arguments_json=json.dumps(arguments),
            )
        )


def _validation_plan(*, max_rework_attempts: int = 2) -> ValidationPlan:
    return ValidationPlan(
        gates=[
            ValidationGate(
                id="pytest",
                name="Pytest",
                command=["pytest", "-q"],
                required=True,
                timeout_seconds=30,
            )
        ],
        source="detected",
        max_rework_attempts=max_rework_attempts,
    )


def _validation_result(
    run: Run,
    agent: Agent,
    *,
    status: str,
) -> ValidationReportWithGates:
    report = DurableValidationReport(
        run_id=run.id,
        agent_id=agent.id,
        attempt=0,
        status=status,
        summary=f"validation {status}",
    )
    gate = DurableValidationGateResult(
        report_id=report.id,
        run_id=run.id,
        gate_id="pytest",
        name="Pytest",
        command=["pytest", "-q"],
        required=True,
        status="passed" if status == "passed" else "failed",
        exit_code=0 if status == "passed" else 1,
        stdout_summary="" if status == "passed" else "test failed",
        failure_kind=None if status == "passed" else "command_failed",
    )
    return ValidationReportWithGates(report=report, gates=[gate])


async def _passing_validation_runner(
    plan: ValidationPlan,
    run: Run,
    agent: Agent,
) -> ValidationReportWithGates:
    return _validation_result(run, agent, status="passed")


def _completion_provider() -> SequenceProvider:
    return SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="Changed README.md."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        ]
    )


def _run_with_change_ready_state(workspace: Path) -> tuple[Run, Agent]:
    return _run(workspace)


def _change_ready_state(run: Run, agent: Agent) -> ModifyingAgentState:
    return cast(
        ModifyingAgentState,
        {
            "run_id": str(run.id),
            "agent_id": str(agent.id),
            "runtime_route": MODIFYING_CODING_ROUTE,
            "messages": [],
            "continuation": None,
            "model_turn_count": 1,
            "tool_call_count": 2,
            "successful_writes": 1,
            "final_diff_after_write": True,
            "progress_fingerprints": [],
            "stagnant_turns": 0,
            "validation_rework_count": 0,
            "validation_reports": [],
            "phase": "model_completed",
            "force_final": False,
            "last_turn": ModelTurn(
                assistant=AssistantMessage(content="Changed README.md."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ).model_dump(mode="json"),
        },
    )
