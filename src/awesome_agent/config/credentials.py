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

from awesome_agent.config.models import CredentialSelectionConfig, CredentialSource

type ProviderName = Literal["deepseek", "kimi"]
type CredentialService = Literal["deepseek", "kimi", "mem0"]

_PROVIDER_ENVIRONMENT_VARIABLES: dict[CredentialService, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "mem0": "MEM0_API_KEY",
}
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


class CredentialValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNVERIFIED = "unverified"


class CredentialValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CredentialValidationStatus
    code: str


class ProviderCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: CredentialService
    environment_variable: str
    environment_configured: bool
    awesome_configured: bool
    selected_source: CredentialSource | None = None

    @property
    def configured(self) -> bool:
        return self.selected_source is not None and self.source_available

    @property
    def source_available(self) -> bool:
        if self.selected_source is CredentialSource.ENVIRONMENT:
            return self.environment_configured
        if self.selected_source is CredentialSource.AWESOME:
            return self.awesome_configured
        return False


class ProviderCredentialStatuses(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deepseek: ProviderCredentialStatus
    kimi: ProviderCredentialStatus
    mem0: ProviderCredentialStatus


def provider_environment_variable(provider: CredentialService) -> str:
    return _PROVIDER_ENVIRONMENT_VARIABLES[provider]


def missing_provider_credential_statuses() -> ProviderCredentialStatuses:
    return ProviderCredentialStatuses(
        deepseek=_missing_status("deepseek"),
        kimi=_missing_status("kimi"),
        mem0=_missing_status("mem0"),
    )


def resolve_provider_credential_statuses(
    path: Path,
    environ: Mapping[str, str],
    selections: CredentialSelectionConfig | None = None,
) -> ProviderCredentialStatuses:
    from_file = dotenv_values(path) if path.is_file() else {}
    configured = selections or CredentialSelectionConfig()

    def status(provider: CredentialService) -> ProviderCredentialStatus:
        name = provider_environment_variable(provider)
        process_value = environ.get(name)
        file_value = from_file.get(name)
        environment_configured = bool(process_value and process_value.strip())
        awesome_configured = bool(isinstance(file_value, str) and file_value.strip())
        selected = getattr(configured, provider)
        if selected is None:
            selected = (
                CredentialSource.ENVIRONMENT
                if environment_configured
                else CredentialSource.AWESOME
                if awesome_configured
                else None
            )
        return ProviderCredentialStatus(
            provider=provider,
            environment_variable=name,
            environment_configured=environment_configured,
            awesome_configured=awesome_configured,
            selected_source=selected,
        )

    return ProviderCredentialStatuses(
        deepseek=status("deepseek"),
        kimi=status("kimi"),
        mem0=status("mem0"),
    )


def _missing_status(provider: CredentialService) -> ProviderCredentialStatus:
    return ProviderCredentialStatus(
        provider=provider,
        environment_variable=provider_environment_variable(provider),
        environment_configured=False,
        awesome_configured=False,
        selected_source=None,
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
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self._path.parent, stat.S_IRWXU)
        temporary = self._path.parent / f".{self._path.name}.{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(
            temporary,
            flags,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
            descriptor = -1
            with stream:
                stream.writelines(lines)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
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
