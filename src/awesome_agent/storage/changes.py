from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from awesome_agent.core.changes.errors import ChangeBlobCorrupt
from awesome_agent.core.changes.models import ChangeSet
from awesome_agent.core.changes.ports import PendingMutation
from awesome_agent.storage.database import application_connection


class FileChangeBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ChangeBlobCorrupt("Invalid change blob digest.")
        return self._root / "blobs" / digest[:2] / digest

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        target = self._path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.get(digest)
            return digest

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                prefix=f".{digest}.",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return digest

    def get(self, digest: str) -> bytes:
        path = self._path(digest)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ChangeBlobCorrupt(f"Change blob {digest} is unavailable.") from error
        if hashlib.sha256(content).hexdigest() != digest:
            raise ChangeBlobCorrupt(
                f"Change blob {digest} failed integrity validation."
            )
        return content


class SQLiteChangeSetStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, change_set: ChangeSet) -> None:
        with application_connection(self._path) as connection, connection:
            connection.execute(
                "INSERT INTO change_sets ("
                "change_set_id, workspace_key, session_id, turn_id, lifecycle, "
                "reversibility, payload_json, created_at, sealed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(change_set_id) DO UPDATE SET "
                "workspace_key = excluded.workspace_key, "
                "session_id = excluded.session_id, turn_id = excluded.turn_id, "
                "lifecycle = excluded.lifecycle, "
                "reversibility = excluded.reversibility, "
                "payload_json = excluded.payload_json, "
                "created_at = excluded.created_at, sealed_at = excluded.sealed_at",
                (
                    change_set.id,
                    change_set.workspace_key,
                    change_set.session_id,
                    change_set.turn_id,
                    change_set.lifecycle.value,
                    change_set.reversibility.value,
                    change_set.model_dump_json(),
                    change_set.created_at.isoformat(),
                    change_set.sealed_at.isoformat() if change_set.sealed_at else None,
                ),
            )

    def get(self, change_set_id: str) -> ChangeSet | None:
        with application_connection(self._path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM change_sets WHERE change_set_id = ?",
                (change_set_id,),
            ).fetchone()
        if row is None:
            return None
        return ChangeSet.model_validate_json(row["payload_json"])

    def latest(self, workspace_key: str) -> ChangeSet | None:
        with application_connection(self._path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM change_sets WHERE workspace_key = ? "
                "ORDER BY created_at DESC, change_set_id DESC LIMIT 1",
                (workspace_key,),
            ).fetchone()
        if row is None:
            return None
        return ChangeSet.model_validate_json(row["payload_json"])

    def save_pending(self, pending: PendingMutation) -> None:
        with application_connection(self._path) as connection, connection:
            connection.execute(
                "INSERT INTO pending_mutations ("
                "pending_id, change_set_id, relative_path, kind, node_type, "
                "before_hash, before_blob, before_mode, intended_after_hash, "
                "intended_after_blob, intended_after_mode, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(pending_id) DO UPDATE SET "
                "change_set_id = excluded.change_set_id, "
                "relative_path = excluded.relative_path, kind = excluded.kind, "
                "node_type = excluded.node_type, before_hash = excluded.before_hash, "
                "before_blob = excluded.before_blob, "
                "before_mode = excluded.before_mode, "
                "intended_after_hash = excluded.intended_after_hash, "
                "intended_after_blob = excluded.intended_after_blob, "
                "intended_after_mode = excluded.intended_after_mode, "
                "created_at = excluded.created_at",
                (
                    pending.id,
                    pending.change_set_id,
                    pending.relative_path,
                    pending.kind.value,
                    pending.node_type.value,
                    pending.before_hash,
                    pending.before_blob,
                    pending.before_mode,
                    pending.intended_after_hash,
                    pending.intended_after_blob,
                    pending.intended_after_mode,
                    pending.created_at.isoformat(),
                ),
            )

    def list_pending(self) -> list[PendingMutation]:
        with application_connection(self._path) as connection:
            rows = connection.execute(
                "SELECT p.*, c.workspace_key FROM pending_mutations AS p "
                "JOIN change_sets AS c ON c.change_set_id = p.change_set_id "
                "ORDER BY p.created_at, p.pending_id"
            ).fetchall()
        return [
            PendingMutation(
                id=row["pending_id"],
                change_set_id=row["change_set_id"],
                workspace_key=row["workspace_key"],
                relative_path=row["relative_path"],
                kind=row["kind"],
                node_type=row["node_type"],
                before_hash=row["before_hash"],
                before_blob=row["before_blob"],
                before_mode=row["before_mode"],
                intended_after_hash=row["intended_after_hash"],
                intended_after_blob=row["intended_after_blob"],
                intended_after_mode=row["intended_after_mode"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_pending(self, pending_id: str) -> None:
        with application_connection(self._path) as connection, connection:
            connection.execute(
                "DELETE FROM pending_mutations WHERE pending_id = ?",
                (pending_id,),
            )
