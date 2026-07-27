from __future__ import annotations

import json
import os
import stat
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from awesome_agent.modeling import MODEL_CATALOG

_MAX_JOURNAL_BYTES = 4 * 1024


class ProviderModelTransactionJournalError(RuntimeError):
    pass


class ProviderModelTransactionPhase(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"


class ProviderModelTransactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    transaction_id: str = Field(
        default="legacy",
        min_length=6,
        max_length=32,
        pattern=r"^(?:legacy|[0-9a-f]{32})$",
    )
    phase: ProviderModelTransactionPhase
    thread_id: str = Field(min_length=1, max_length=128)
    previous_default_model: str | None = None
    target_default_model: str
    previous_thread_model: str | None = None
    target_thread_model: str

    @field_validator(
        "previous_default_model",
        "target_default_model",
        "previous_thread_model",
        "target_thread_model",
    )
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                MODEL_CATALOG.profile(value)
            except ValueError as error:
                raise ValueError(
                    "Journal model must be a curated Provider/model id."
                ) from error
        return value

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.target_default_model != self.target_thread_model:
            raise ValueError("Journal target models must match.")
        return self


class ProviderModelTransactionJournal:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def read(self) -> ProviderModelTransactionRecord | None:
        try:
            parent_before = os.lstat(self.path.parent)
        except FileNotFoundError:
            return None
        _require_private_directory(parent_before)
        try:
            before = os.lstat(self.path)
        except FileNotFoundError:
            return None
        _require_regular_file(before)
        if before.st_size > _MAX_JOURNAL_BYTES:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal exceeds its size limit."
            )
        flags = os.O_RDONLY
        for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        descriptor = os.open(self.path, flags)
        try:
            opened = os.fstat(descriptor)
            _require_regular_file(opened)
            if _identity(os.lstat(self.path.parent)) != _identity(
                parent_before
            ) or _identity(opened) != _identity(before):
                raise ProviderModelTransactionJournalError(
                    "Provider model transaction journal changed while opening."
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_JOURNAL_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_JOURNAL_BYTES:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal exceeds its size limit."
            )
        try:
            after = os.lstat(self.path)
        except FileNotFoundError:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal disappeared while reading."
            ) from None
        if _identity(os.lstat(self.path.parent)) != _identity(
            parent_before
        ) or _identity(after) != _identity(before):
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal changed while reading."
            )
        try:
            decoded = raw.decode("utf-8")
            document = json.loads(
                decoded,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
            return ProviderModelTransactionRecord.model_validate(document)
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal is invalid."
            ) from error

    def prepare(
        self,
        record: ProviderModelTransactionRecord,
    ) -> ProviderModelTransactionRecord:
        if record.phase is not ProviderModelTransactionPhase.PREPARED:
            raise ValueError("A new Provider model transaction must be prepared.")
        if self.read() is not None:
            raise ProviderModelTransactionJournalError(
                "A Provider model transaction already requires recovery."
            )
        self._write(record)
        return record

    def mark_committed(
        self,
        prepared: ProviderModelTransactionRecord,
    ) -> ProviderModelTransactionRecord:
        if prepared.phase is not ProviderModelTransactionPhase.PREPARED:
            raise ValueError("Only a prepared Provider model transaction can commit.")
        if self.read() != prepared:
            raise ProviderModelTransactionJournalError(
                "Prepared Provider model transaction identity changed."
            )
        committed = prepared.model_copy(
            update={"phase": ProviderModelTransactionPhase.COMMITTED}
        )
        self._write(committed)
        return committed

    def clear(self, expected: ProviderModelTransactionRecord) -> None:
        current = self.read()
        if current != expected:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction changed before cleanup."
            )
        before = os.lstat(self.path)
        _require_regular_file(before)
        if self.read() != expected:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction changed during cleanup."
            )
        current_status = os.lstat(self.path)
        if _identity(current_status) != _identity(before):
            raise ProviderModelTransactionJournalError(
                "Provider model transaction changed during cleanup."
            )
        self.path.unlink()
        _sync_directory(self.path.parent)

    def _write(self, record: ProviderModelTransactionRecord) -> None:
        raw = record.model_dump_json().encode("utf-8")
        if len(raw) > _MAX_JOURNAL_BYTES:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal exceeds its size limit."
            )
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        parent_before = os.lstat(parent)
        _require_private_directory(parent_before)
        try:
            existing = os.lstat(self.path)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _require_regular_file(existing)
        temporary = parent / f".{self.path.name}.{uuid4().hex}.tmp"
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
                raise ProviderModelTransactionJournalError(
                    "Provider model transaction journal directory changed."
                )
            if existing is not None:
                current = os.lstat(self.path)
                if _identity(current) != _identity(existing):
                    raise ProviderModelTransactionJournalError(
                        "Provider model transaction journal changed before replace."
                    )
            os.replace(temporary, self.path)
            _sync_directory(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        if self.read() != record:
            raise ProviderModelTransactionJournalError(
                "Provider model transaction journal verification failed."
            )


def _require_regular_file(status: os.stat_result) -> None:
    if (
        _is_link_or_reparse(status)
        or not stat.S_ISREG(status.st_mode)
        or int(status.st_nlink) != 1
    ):
        raise ProviderModelTransactionJournalError(
            "Provider model transaction journal is not a regular private file."
        )


def _require_private_directory(status: os.stat_result) -> None:
    if _is_link_or_reparse(status) or not stat.S_ISDIR(status.st_mode):
        raise ProviderModelTransactionJournalError(
            "Provider model transaction journal directory is unsafe."
        )


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
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
