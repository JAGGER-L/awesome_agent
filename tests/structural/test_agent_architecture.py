from __future__ import annotations

import ast
from pathlib import Path

AGENT_STATE_FIELDS = {
    "active_execution_seconds",
    "compressions",
    "compression_reason",
    "compression_requested",
    "context_effective_limit",
    "context_estimated_tokens",
    "context_manifest",
    "continuation",
    "final_answer",
    "messages",
    "model",
    "model_calls",
    "next_tool_index",
    "pending_tool_calls",
    "provider",
    "provider_retries",
    "recovery_issue",
    "termination_reason",
    "thinking_enabled",
    "thread_id",
    "tool_calls",
    "tool_results",
    "turn_id",
    "usage",
    "workspace_key",
}


def test_agent_state_is_the_current_checkpoint_contract() -> None:
    path = Path("src/awesome_agent/agent/state.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    state = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentState"
    )
    field_names = {
        node.target.id
        for node in state.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert field_names == AGENT_STATE_FIELDS


def test_agent_finalization_boundary_does_not_import_memory() -> None:
    violations: dict[str, list[str]] = {}
    for path in Path("src/awesome_agent/agent").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = sorted(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (
                node.module == "awesome_agent.memory"
                or node.module.startswith("awesome_agent.memory.")
            )
        )
        if imports:
            violations[path.name] = imports

    assert violations == {}
