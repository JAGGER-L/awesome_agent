from __future__ import annotations

import ast
from pathlib import Path


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value)
        return f"{owner}.{node.attr}" if owner is not None else None
    return None


def _calls(function: ast.AsyncFunctionDef, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == name
    ]


def _only_call(function: ast.AsyncFunctionDef, name: str) -> ast.Call:
    matches = _calls(function, name)
    assert len(matches) == 1
    return matches[0]


def _signature(call: ast.Call) -> tuple[tuple[str, ...], dict[str, str]]:
    assert all(keyword.arg is not None for keyword in call.keywords)
    return (
        tuple(ast.unparse(argument) for argument in call.args),
        {str(keyword.arg): ast.unparse(keyword.value) for keyword in call.keywords},
    )


def test_agent_nodes_invoke_context_explicitly() -> None:
    nodes_path = Path("src/awesome_agent/agent/nodes.py")
    nodes = nodes_path.read_text(encoding="utf-8")
    tree = ast.parse(nodes)
    context_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/awesome_agent/context").rglob("*.py")
    )

    assert _signature(
        _only_call(_function(tree, "prepare_context"), "context.context_builder")
    ) == (("state",), {})
    assert _signature(
        _only_call(_function(tree, "compress_context"), "context.compressor.compress")
    ) == (
        ("updated",),
        {"max_provider_retries": "max_provider_retries"},
    )
    assert "middleware" not in context_sources.casefold()


def test_application_compression_preserves_tool_tail_and_retry_budget() -> None:
    tree = ast.parse(
        Path("src/awesome_agent/application/context.py").read_text(encoding="utf-8")
    )
    compress = _function(tree, "compress")

    assert _signature(_only_call(compress, "_active_turn_tool_tail")) == (
        ("state",),
        {},
    )
    assert _signature(_only_call(compress, "_append_tool_tail")) == (
        ("base", "tool_tail"),
        {},
    )
    request = _only_call(compress, "CompressionRequest")
    assert {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in request.keywords
        if keyword.arg == "max_provider_retries"
    } == {"max_provider_retries": "max_provider_retries"}
