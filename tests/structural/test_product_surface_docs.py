from __future__ import annotations

from pathlib import Path


def test_product_surface_docs_keep_tui_as_client_surface() -> None:
    text = _normalized("docs/design-docs/product-surface-architecture.md")

    assert "surfaceclient" in text or "surface client" in text
    assert "ordinary input is the primary execution route" in text
    assert "/run" in text
    assert "advanced" in text or "manual" in text


def test_runtime_profile_docs_keep_docker_api_without_cli() -> None:
    text = _normalized("docs/design-docs/runtime-profiles-and-startup.md")

    assert "make docker-init" in text
    assert "make docker-start" in text
    assert "docker mode does not start the cli" in text
    assert "localsandbox" in text


def _normalized(path: str) -> str:
    return " ".join(Path(path).read_text(encoding="utf-8").lower().split())
