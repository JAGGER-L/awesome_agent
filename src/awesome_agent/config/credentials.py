from __future__ import annotations

import os
import stat
import threading
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import uuid4

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, SecretStr

type ProviderName = Literal["deepseek", "kimi"]

_PROVIDER_ENVIRONMENT_VARIABLES: dict[ProviderName, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


class CredentialSource(StrEnum):
    MISSING = "missing"
    USER_ENV_FILE = "user_env_file"
    PROCESS_ENVIRONMENT = "process_environment"


class ProviderCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    environment_variable: str
    source: CredentialSource
    mutable: bool

    @property
    def configured(self) -> bool:
        return self.source is not CredentialSource.MISSING


class ProviderCredentialStatuses(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deepseek: ProviderCredentialStatus
    kimi: ProviderCredentialStatus


def provider_environment_variable(provider: ProviderName) -> str:
    return _PROVIDER_ENVIRONMENT_VARIABLES[provider]


def missing_provider_credential_statuses() -> ProviderCredentialStatuses:
    return ProviderCredentialStatuses(
        deepseek=_missing_status("deepseek"),
        kimi=_missing_status("kimi"),
    )


def resolve_provider_credential_statuses(
    path: Path,
    environ: Mapping[str, str],
) -> ProviderCredentialStatuses:
    from_file = dotenv_values(path) if path.is_file() else {}

    def status(provider: ProviderName) -> ProviderCredentialStatus:
        name = provider_environment_variable(provider)
        process_value = environ.get(name)
        file_value = from_file.get(name)
        if process_value is not None and process_value.strip():
            source = CredentialSource.PROCESS_ENVIRONMENT
        elif isinstance(file_value, str) and file_value.strip():
            source = CredentialSource.USER_ENV_FILE
        else:
            source = CredentialSource.MISSING
        return ProviderCredentialStatus(
            provider=provider,
            environment_variable=name,
            source=source,
            mutable=source is not CredentialSource.PROCESS_ENVIRONMENT,
        )

    return ProviderCredentialStatuses(
        deepseek=status("deepseek"),
        kimi=status("kimi"),
    )


def _missing_status(provider: ProviderName) -> ProviderCredentialStatus:
    return ProviderCredentialStatus(
        provider=provider,
        environment_variable=provider_environment_variable(provider),
        source=CredentialSource.MISSING,
        mutable=True,
    )


class UserSecretStore:
    def __init__(self, path: Path) -> None:
        self._path = path.expanduser()
        self._lock = _lock_for(self._path)

    def set(self, name: str, value: SecretStr) -> None:
        _validate_name(name)
        raw = value.get_secret_value()
        _validate_value(raw)
        with self._lock:
            lines = _read_lines(self._path)
            replacement = f"{name}={_quote(raw)}\n"
            updated, found = _replace(lines, name, replacement)
            if not found:
                if updated and not updated[-1].endswith(("\n", "\r")):
                    updated[-1] = f"{updated[-1]}\n"
                updated.append(replacement)
            self._write(updated)

    def delete(self, name: str) -> bool:
        _validate_name(name)
        with self._lock:
            lines = _read_lines(self._path)
            updated, found = _replace(lines, name, None)
            if not found:
                return False
            self._write(updated)
            return True

    def _write(self, lines: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.parent / f".{self._path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="") as stream:
                stream.writelines(lines)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_name(name: str) -> None:
    if name not in _PROVIDER_ENVIRONMENT_VARIABLES.values():
        raise ValueError("Unsupported Provider credential name.")


def _validate_value(value: str) -> None:
    if not value.strip() or "\r" in value or "\n" in value:
        raise ValueError("Provider credential value is invalid.")


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _replace(
    lines: list[str],
    name: str,
    replacement: str | None,
) -> tuple[list[str], bool]:
    prefix = f"{name}="
    found = False
    updated: list[str] = []
    for line in lines:
        if line.lstrip().startswith(prefix):
            if not found and replacement is not None:
                updated.append(replacement)
            found = True
            continue
        updated.append(line)
    return updated, found


def _quote(value: str) -> str:
    escaped = value.replace("'", "\\'")
    return f"'{escaped}'"


def _lock_for(path: Path) -> threading.RLock:
    key = path.absolute()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock
