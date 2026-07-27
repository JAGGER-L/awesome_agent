from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, SecretStr

from awesome_agent.config.credential_catalog import (
    awesome_secret_names,
    credential_descriptor,
)
from awesome_agent.config.models import CredentialSelectionConfig, CredentialSource
from awesome_agent.core.resource_lock import exclusive_resource_lock

type ProviderName = Literal["deepseek", "kimi"]
type CredentialService = Literal["deepseek", "kimi", "mem0"]

_MAX_SECRET_FILE_BYTES = 1024 * 1024


class UserSecretStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecretFileSnapshot:
    existed: bool
    content: bytes

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


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
    return credential_descriptor(provider).environment_variable


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
    *,
    from_file: Mapping[str, str | None] | None = None,
) -> ProviderCredentialStatuses:
    file_values = read_provider_secret_values(path) if from_file is None else from_file
    configured = selections or CredentialSelectionConfig()

    def status(provider: CredentialService) -> ProviderCredentialStatus:
        name = provider_environment_variable(provider)
        process_value = environ.get(name)
        file_value = file_values.get(name)
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


def read_provider_secret_values(path: Path) -> dict[str, str | None]:
    snapshot = UserSecretStore(path).snapshot()
    if not snapshot.existed:
        return {}
    if b"\0" in snapshot.content:
        raise UserSecretStoreError("Provider secret file contains NUL bytes.")
    try:
        text = snapshot.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UserSecretStoreError(
            "Provider secret file is not valid UTF-8."
        ) from error
    return dict(dotenv_values(stream=StringIO(text)))


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

    def set(self, name: str, value: SecretStr) -> None:
        _validate_name(name)
        raw = value.get_secret_value()
        _validate_value(raw)
        with self.transaction():
            self.restore(self.plan_set(name, value))

    def delete(self, name: str) -> bool:
        _validate_name(name)
        with self.transaction():
            updated, found = self.plan_delete(name)
            if not found:
                return False
            self.restore(updated)
            return True

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with exclusive_resource_lock(self._path):
            yield

    def snapshot(self) -> SecretFileSnapshot:
        # Writers publish with atomic replace, so a bounded identity-checked read does
        # not need to wait for the mutation lock and cannot observe partial bytes.
        return _read_snapshot(self._path)

    def plan_set(self, name: str, value: SecretStr) -> SecretFileSnapshot:
        _validate_name(name)
        raw_value = value.get_secret_value()
        _validate_value(raw_value)
        with self.transaction():
            current = _read_snapshot(self._path)
            lines = _decode_lines(current.content)
            replacement = f"{name}={_quote(raw_value)}\n"
            updated, found = _replace(lines, name, replacement)
            if not found:
                if updated and not updated[-1].endswith(("\n", "\r")):
                    updated[-1] = f"{updated[-1]}\n"
                updated.append(replacement)
            return SecretFileSnapshot(
                existed=True,
                content="".join(updated).encode(),
            )

    def plan_delete(self, name: str) -> tuple[SecretFileSnapshot, bool]:
        _validate_name(name)
        with self.transaction():
            current = _read_snapshot(self._path)
            lines = _decode_lines(current.content)
            updated, found = _replace(lines, name, None)
            if not found:
                return current, False
            return (
                SecretFileSnapshot(
                    existed=current.existed,
                    content="".join(updated).encode(),
                ),
                True,
            )

    def restore(self, snapshot: SecretFileSnapshot) -> None:
        if not isinstance(snapshot, SecretFileSnapshot):
            raise TypeError("Secret store restore requires a file snapshot.")
        if len(snapshot.content) > _MAX_SECRET_FILE_BYTES:
            raise UserSecretStoreError("Provider secret file exceeds its size limit.")
        with self.transaction():
            if snapshot.existed:
                self._write(snapshot.content)
                return
            _delete_file(self._path)

    def _write(self, content: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_before = os.lstat(self._path.parent)
        _require_private_directory(parent_before)
        if os.name != "nt":
            os.chmod(self._path.parent, stat.S_IRWXU)
        existing = _lstat_optional(self._path)
        if existing is not None:
            _require_private_regular_file(existing)
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
                stream.write(content.decode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if _identity(os.lstat(self._path.parent)) != _identity(parent_before):
                raise UserSecretStoreError(
                    "Provider secret file directory changed before replace."
                )
            current = _lstat_optional(self._path)
            if (existing is None) != (current is None) or (
                existing is not None
                and current is not None
                and _identity(existing) != _identity(current)
            ):
                raise UserSecretStoreError(
                    "Provider secret file changed before replace."
                )
            os.replace(temporary, self._path)
            if os.name != "nt":
                os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
            _sync_directory(self._path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)


def _validate_name(name: str) -> None:
    if name not in awesome_secret_names():
        raise ValueError("Unsupported Provider credential name.")


def _validate_value(value: str) -> None:
    if not value.strip() or "\0" in value or "\r" in value or "\n" in value:
        raise ValueError("Provider credential value is invalid.")


def _read_snapshot(path: Path) -> SecretFileSnapshot:
    try:
        parent_before = os.lstat(path.parent)
    except FileNotFoundError:
        return SecretFileSnapshot(existed=False, content=b"")
    _require_private_directory(parent_before)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return SecretFileSnapshot(existed=False, content=b"")
    _require_private_regular_file(before)
    if before.st_size > _MAX_SECRET_FILE_BYTES:
        raise UserSecretStoreError("Provider secret file exceeds its size limit.")
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise UserSecretStoreError(
            "Provider secret file cannot be opened safely."
        ) from error
    try:
        opened = os.fstat(descriptor)
        _require_private_regular_file(opened)
        if _identity(os.lstat(path.parent)) != _identity(parent_before) or _identity(
            opened
        ) != _identity(before):
            raise UserSecretStoreError("Provider secret file changed while opening.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(_MAX_SECRET_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(content) > _MAX_SECRET_FILE_BYTES:
        raise UserSecretStoreError("Provider secret file exceeds its size limit.")
    try:
        after = os.lstat(path)
    except FileNotFoundError:
        raise UserSecretStoreError(
            "Provider secret file disappeared while reading."
        ) from None
    if _identity(os.lstat(path.parent)) != _identity(parent_before) or _identity(
        after
    ) != _identity(before):
        raise UserSecretStoreError("Provider secret file changed while reading.")
    return SecretFileSnapshot(existed=True, content=content)


def _decode_lines(content: bytes) -> list[str]:
    if b"\0" in content:
        raise UserSecretStoreError("Provider secret file contains NUL bytes.")
    try:
        return content.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as error:
        raise UserSecretStoreError(
            "Provider secret file is not valid UTF-8."
        ) from error


def _delete_file(path: Path) -> None:
    try:
        parent_before = os.lstat(path.parent)
    except FileNotFoundError:
        return
    _require_private_directory(parent_before)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return
    _require_private_regular_file(before)
    if _identity(os.lstat(path.parent)) != _identity(parent_before) or _identity(
        os.lstat(path)
    ) != _identity(before):
        raise UserSecretStoreError("Provider secret file changed before deletion.")
    path.unlink()
    _sync_directory(path.parent)


def _require_private_regular_file(status: os.stat_result) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(status, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or attributes & reparse_flag
        or int(status.st_nlink) != 1
    ):
        raise UserSecretStoreError(
            "Provider secret file is not a private regular file."
        )


def _require_private_directory(status: os.stat_result) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(status, "st_file_attributes", 0)
    if (
        not stat.S_ISDIR(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or attributes & reparse_flag
    ):
        raise UserSecretStoreError("Provider secret file directory is unsafe.")


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _identity(status: os.stat_result) -> tuple[int, int, int]:
    return int(status.st_dev), int(status.st_ino), stat.S_IFMT(status.st_mode)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
