from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from awesome_agent.config import UserSecretStore


def test_secret_store_adds_and_replaces_without_losing_unrelated_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text("# user note\nOTHER=value\n", encoding="utf-8")
    store = UserSecretStore(path)

    store.set("DEEPSEEK_API_KEY", SecretStr("first'value"))
    store.set("DEEPSEEK_API_KEY", SecretStr("replacement"))

    content = path.read_text(encoding="utf-8")
    values = dotenv_values(path)
    assert "# user note" in content
    assert "OTHER=value" in content
    assert content.count("DEEPSEEK_API_KEY=") == 1
    assert values["DEEPSEEK_API_KEY"] == "replacement"
    assert not list(tmp_path.glob("..env.*.tmp"))


def test_secret_store_delete_is_idempotent_and_preserves_other_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "DEEPSEEK_API_KEY=remove-me\nOTHER=value\n",
        encoding="utf-8",
    )
    store = UserSecretStore(path)

    assert store.delete("DEEPSEEK_API_KEY") is True
    assert store.delete("DEEPSEEK_API_KEY") is False
    assert path.read_text(encoding="utf-8") == "OTHER=value\n"


@pytest.mark.parametrize("value", ["", "   ", "line\nbreak", "line\rbreak"])
def test_secret_store_rejects_invalid_values_without_leaking_them(
    tmp_path: Path,
    value: str,
) -> None:
    store = UserSecretStore(tmp_path / ".env")

    with pytest.raises(ValueError) as raised:
        store.set("DEEPSEEK_API_KEY", SecretStr(value))

    if value:
        assert value not in str(raised.value)
    assert not (tmp_path / ".env").exists()


def test_secret_store_rejects_non_provider_names(tmp_path: Path) -> None:
    store = UserSecretStore(tmp_path / ".env")

    with pytest.raises(ValueError, match="Unsupported"):
        store.set("MEM0_API_KEY", SecretStr("value"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode is not available on Windows")
def test_secret_store_uses_owner_only_mode_on_posix(tmp_path: Path) -> None:
    path = tmp_path / ".env"

    UserSecretStore(path).set("MOONSHOT_API_KEY", SecretStr("secret"))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
