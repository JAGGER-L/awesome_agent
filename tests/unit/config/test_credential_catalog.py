from __future__ import annotations

import pytest

from awesome_agent.config import (
    CREDENTIAL_CATALOG,
    CREDENTIAL_DESCRIPTORS,
    awesome_secret_names,
    credential_descriptor,
)


def test_credential_catalog_is_complete_unique_and_static() -> None:
    assert tuple(descriptor.id for descriptor in CREDENTIAL_DESCRIPTORS) == (
        "deepseek",
        "kimi",
        "mem0",
        "tavily",
        "web_proxy",
    )
    assert set(CREDENTIAL_CATALOG) == {
        "deepseek",
        "kimi",
        "mem0",
        "tavily",
        "web_proxy",
    }
    assert credential_descriptor("tavily").environment_variable == "TAVILY_API_KEY"
    assert (
        credential_descriptor("web_proxy").environment_variable
        == "AWESOME_WEB_PROXY_URL"
    )

    with pytest.raises(TypeError):
        CREDENTIAL_CATALOG["deepseek"] = credential_descriptor("deepseek")  # type: ignore[index]


def test_only_supported_awesome_secrets_are_writable() -> None:
    assert awesome_secret_names() == {
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "MEM0_API_KEY",
        "TAVILY_API_KEY",
        "AWESOME_WEB_PROXY_URL",
    }
