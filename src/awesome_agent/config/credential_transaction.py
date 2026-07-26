from __future__ import annotations

import json
import os
import stat
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.config.credentials import (
    CredentialService,
    SecretFileSnapshot,
    provider_environment_variable,
)
from awesome_agent.config.models import CredentialSource

_MAX_JOURNAL_BYTES = 4 * 1024
_MAX_BACKUP_BYTES = 1024 * 1024
_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ProviderCredentialTransactionError(RuntimeError):
    pass


class ProviderCredentialTransactionPhase(StrEnum):
    PREPARED = "prepared"
    SECRET_COMMITTED = "secret_committed"
    COMMITTED = "committed"


class ProviderCredentialTransactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    phase: ProviderCredentialTransactionPhase
    service: CredentialService
    environment_variable: str = Field(min_length=1, max_length=128)
    action: Literal["add", "replace", "delete"]
    previous_source: CredentialSource | None
    target_source: CredentialSource | None
    previous_env_existed: bool
    previous_env_sha256: str = Field(pattern=_SHA256_PATTERN)
    target_env_existed: bool
    target_env_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.environment_variable != provider_environment_variable(self.service):
            raise ValueError("Credential transaction environment variable differs.")
        if self.action == "delete":
            if self.target_source is not self.previous_source:
                raise ValueError("Credential deletion must preserve source selection.")
        else:
            if self.target_source is not CredentialSource.AWESOME:
                raise ValueError(
                    "Credential writes must select the Awesome secret source."
                )
            if not self.target_env_existed:
                raise ValueError("Credential writes require a target secret file.")
        return self

    def previous_snapshot(self, content: bytes) -> SecretFileSnapshot:
        snapshot = SecretFileSnapshot(
            existed=self.previous_env_existed,
            content=content,
        )
        if snapshot.content_hash != self.previous_env_sha256:
            raise ProviderCredentialTransactionError(
                "Provider credential backup identity differs from the journal."
            )
        return snapshot

    def matches_previous(self, snapshot: SecretFileSnapshot) -> bool:
        return (
            snapshot.existed is self.previous_env_existed
            and snapshot.content_hash == self.previous_env_sha256
        )

    def matches_target(self, snapshot: SecretFileSnapshot) -> bool:
        return (
            snapshot.existed is self.target_env_existed
            and snapshot.content_hash == self.target_env_sha256
        )


class ProviderCredentialTransactionJournal:
    def __init__(self, journal_path: Path, backup_path: Path) -> None:
        self.path = journal_path.expanduser()
        self.backup_path = backup_path.expanduser()
        if self.path.parent != self.backup_path.parent:
            raise ValueError("Credential transaction files must share one directory.")

    def read(self) -> ProviderCredentialTransactionRecord | None:
        raw = _read_private_file(
            self.path,
            max_bytes=_MAX_JOURNAL_BYTES,
            label="Provider credential transaction journal",
        )
        if raw is None:
            return None
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
            return ProviderCredentialTransactionRecord.model_validate(document)
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise ProviderCredentialTransactionError(
                "Provider credential transaction journal is invalid."
            ) from error

    def stage_backup(self, snapshot: SecretFileSnapshot) -> None:
        if self.read() is not None:
            raise ProviderCredentialTransactionError(
                "A Provider credential transaction already requires recovery."
            )
        if _lstat_optional(self.backup_path) is not None:
            raise ProviderCredentialTransactionError(
                "A Provider credential backup already requires recovery."
            )
        if snapshot.existed:
            if len(snapshot.content) > _MAX_BACKUP_BYTES:
                raise ProviderCredentialTransactionError(
                    "Provider credential backup exceeds its size limit."
                )
            _write_private_file(
                self.backup_path,
                snapshot.content,
                max_bytes=_MAX_BACKUP_BYTES,
                label="Provider credential backup",
            )

    def prepare(
        self,
        record: ProviderCredentialTransactionRecord,
    ) -> ProviderCredentialTransactionRecord:
        if record.phase is not ProviderCredentialTransactionPhase.PREPARED:
            raise ValueError("A new Provider credential transaction must be prepared.")
        if self.read() is not None:
            raise ProviderCredentialTransactionError(
                "A Provider credential transaction already requires recovery."
            )
        self._verify_staged_backup(record)
        self._write(record)
        return record

    def mark_secret_committed(
        self,
        prepared: ProviderCredentialTransactionRecord,
    ) -> ProviderCredentialTransactionRecord:
        if prepared.phase is not ProviderCredentialTransactionPhase.PREPARED:
            raise ValueError("Only a prepared credential transaction can advance.")
        return self._advance(
            prepared,
            ProviderCredentialTransactionPhase.SECRET_COMMITTED,
        )

    def mark_committed(
        self,
        secret_committed: ProviderCredentialTransactionRecord,
    ) -> ProviderCredentialTransactionRecord:
        if (
            secret_committed.phase
            is not ProviderCredentialTransactionPhase.SECRET_COMMITTED
        ):
            raise ValueError("Only a secret-committed transaction can commit.")
        return self._advance(
            secret_committed,
            ProviderCredentialTransactionPhase.COMMITTED,
        )

    def read_backup(
        self,
        record: ProviderCredentialTransactionRecord,
    ) -> SecretFileSnapshot:
        if not record.previous_env_existed:
            if _lstat_optional(self.backup_path) is not None:
                raise ProviderCredentialTransactionError(
                    "Unexpected Provider credential backup is present."
                )
            return record.previous_snapshot(b"")
        raw = _read_private_file(
            self.backup_path,
            max_bytes=_MAX_BACKUP_BYTES,
            label="Provider credential backup",
        )
        if raw is None:
            raise ProviderCredentialTransactionError(
                "Provider credential backup is missing."
            )
        return record.previous_snapshot(raw)

    def clear(self, expected: ProviderCredentialTransactionRecord) -> None:
        if self.read() != expected:
            raise ProviderCredentialTransactionError(
                "Provider credential transaction changed before cleanup."
            )
        _unlink_private_file(
            self.backup_path,
            label="Provider credential backup",
            missing_ok=True,
        )
        if self.read() != expected:
            raise ProviderCredentialTransactionError(
                "Provider credential transaction changed during cleanup."
            )
        _unlink_private_file(
            self.path,
            label="Provider credential transaction journal",
            missing_ok=False,
        )

    def clear_orphan_backup(self) -> bool:
        if self.read() is not None:
            raise ProviderCredentialTransactionError(
                "Cannot clear a backup for a pending credential transaction."
            )
        if _lstat_optional(self.backup_path) is None:
            return False
        _unlink_private_file(
            self.backup_path,
            label="Provider credential backup",
            missing_ok=False,
        )
        return True

    def require_clean(self) -> None:
        if self.read() is not None or _lstat_optional(self.backup_path) is not None:
            raise ProviderCredentialTransactionError(
                "A Provider credential transaction requires startup recovery."
            )

    def _advance(
        self,
        current: ProviderCredentialTransactionRecord,
        phase: ProviderCredentialTransactionPhase,
    ) -> ProviderCredentialTransactionRecord:
        if self.read() != current:
            raise ProviderCredentialTransactionError(
                "Provider credential transaction identity changed."
            )
        advanced = current.model_copy(update={"phase": phase})
        self._write(advanced)
        return advanced

    def _verify_staged_backup(
        self,
        record: ProviderCredentialTransactionRecord,
    ) -> None:
        if record.previous_env_existed:
            self.read_backup(record)
            return
        if _lstat_optional(self.backup_path) is not None:
            raise ProviderCredentialTransactionError(
                "Unexpected Provider credential backup is present."
            )

    def _write(self, record: ProviderCredentialTransactionRecord) -> None:
        _write_private_file(
            self.path,
            record.model_dump_json().encode("utf-8"),
            max_bytes=_MAX_JOURNAL_BYTES,
            label="Provider credential transaction journal",
        )
        if self.read() != record:
            raise ProviderCredentialTransactionError(
                "Provider credential transaction journal verification failed."
            )


def _read_private_file(
    path: Path,
    *,
    max_bytes: int,
    label: str,
) -> bytes | None:
    parent = path.parent
    try:
        parent_before = os.lstat(parent)
    except FileNotFoundError:
        return None
    _require_private_directory(parent_before, label)
    before = _lstat_optional(path)
    if before is None:
        return None
    _require_private_regular_file(before, label)
    if before.st_size > max_bytes:
        raise ProviderCredentialTransactionError(f"{label} exceeds its size limit.")
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProviderCredentialTransactionError(
            f"{label} cannot be opened safely."
        ) from error
    try:
        opened = os.fstat(descriptor)
        _require_private_regular_file(opened, label)
        if _identity(os.lstat(parent)) != _identity(parent_before) or _identity(
            opened
        ) != _identity(before):
            raise ProviderCredentialTransactionError(f"{label} changed while opening.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise ProviderCredentialTransactionError(f"{label} exceeds its size limit.")
    after = _lstat_optional(path)
    if after is None or (
        _identity(os.lstat(parent)) != _identity(parent_before)
        or _identity(after) != _identity(before)
    ):
        raise ProviderCredentialTransactionError(f"{label} changed while reading.")
    return raw


def _write_private_file(
    path: Path,
    raw: bytes,
    *,
    max_bytes: int,
    label: str,
) -> None:
    if len(raw) > max_bytes:
        raise ProviderCredentialTransactionError(f"{label} exceeds its size limit.")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_before = os.lstat(parent)
    _require_private_directory(parent_before, label)
    if os.name != "nt":
        os.chmod(parent, stat.S_IRWXU)
    existing = _lstat_optional(path)
    if existing is not None:
        _require_private_regular_file(existing, label)
    temporary = parent / f".{path.name}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if _identity(os.lstat(parent)) != _identity(parent_before):
            raise ProviderCredentialTransactionError(f"{label} directory changed.")
        if existing is not None:
            current = os.lstat(path)
            if _identity(current) != _identity(existing):
                raise ProviderCredentialTransactionError(
                    f"{label} changed before replace."
                )
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        _sync_directory(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _unlink_private_file(path: Path, *, label: str, missing_ok: bool) -> None:
    before = _lstat_optional(path)
    if before is None:
        if missing_ok:
            return
        raise ProviderCredentialTransactionError(f"{label} is missing.")
    _require_private_regular_file(before, label)
    current = os.lstat(path)
    if _identity(current) != _identity(before):
        raise ProviderCredentialTransactionError(f"{label} changed before cleanup.")
    path.unlink()
    _sync_directory(path.parent)


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _require_private_regular_file(status: os.stat_result, label: str) -> None:
    if (
        _is_link_or_reparse(status)
        or not stat.S_ISREG(status.st_mode)
        or int(status.st_nlink) != 1
    ):
        raise ProviderCredentialTransactionError(
            f"{label} is not a regular private file."
        )


def _require_private_directory(status: os.stat_result, label: str) -> None:
    if _is_link_or_reparse(status) or not stat.S_ISDIR(status.st_mode):
        raise ProviderCredentialTransactionError(f"{label} directory is unsafe.")


def _is_link_or_reparse(status: os.stat_result) -> bool:
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(status.st_mode) or bool(attributes & reparse)


def _identity(status: os.stat_result) -> tuple[int, int, int, bool]:
    return (
        int(status.st_dev),
        int(status.st_ino),
        stat.S_IFMT(status.st_mode),
        _is_link_or_reparse(status),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Non-finite JSON number is invalid: {value}")


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
