from __future__ import annotations

from pathlib import Path


def test_product_surface_docs_keep_tui_as_client_surface() -> None:
    text = _normalized("docs/architecture/product-surfaces.md")

    assert "surfaceclient" in text or "surface client" in text
    assert "plain user messages are the only execution creation path" in text
    assert "`/" + "run`" not in text


def test_product_surface_docs_make_leader_agentloop_primary_turn_path() -> None:
    text = _normalized("docs/architecture/product-surfaces.md")

    assert "user message input enters the leader agentloop" in text
    assert "simple questions are leader turns with no tool calls" in text
    assert "tui never imports provider" in text


def test_docs_describe_user_message_turn_as_primary_execution_route() -> None:
    text = _normalized("docs/architecture/runtime-kernel.md")

    assert "user message turn -> conversation run -> initial leader agent" in text
    assert "surfaces do not execute graphs" in text


def test_runtime_profile_docs_keep_docker_api_without_cli() -> None:
    text = _normalized("docs/architecture/product-surfaces.md")

    assert "make docker-init" in text
    assert "make docker-start" in text
    assert "docker mode does not start the cli" in text
    assert "localsandbox" in text


def _normalized(path: str) -> str:
    return " ".join(Path(path).read_text(encoding="utf-8").lower().split())
