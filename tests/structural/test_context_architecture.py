from pathlib import Path


def test_agent_nodes_invoke_context_explicitly() -> None:
    nodes = Path("src/awesome_agent/agent/nodes.py").read_text(encoding="utf-8")
    context_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/awesome_agent/context").rglob("*.py")
    )

    assert "context.context_builder(state)" in nodes
    assert "context.compressor.compress(updated)" in nodes
    assert "middleware" not in context_sources.casefold()
