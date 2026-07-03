from __future__ import annotations

from typing import Any, cast

from awesome_agent.settings import Settings


def test_settings(**overrides: object) -> Settings:
    """Construct Settings for tests without reading a local .env file."""
    settings = cast(Any, Settings)(_env_file=None, **overrides)
    return cast(Settings, settings)


test_settings.__test__ = False  # type: ignore[attr-defined]
