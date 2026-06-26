from __future__ import annotations

import json
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import AgentKind, RunIntent
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_GRAPH,
    MODIFYING_CODING_VERSION,
)
from awesome_agent.runtime.modifying_graph import ModifyingCodingGraph


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
        graph_name=MODIFYING_CODING_GRAPH,
        graph_version=MODIFYING_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=workspace,
    )
    return run, Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
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
