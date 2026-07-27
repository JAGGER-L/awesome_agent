from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime

_MAX_CURSOR_LENGTH = 1_024
_CURSOR_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class InvalidThreadCursor(ValueError):
    pass


def encode_thread_cursor(cursor: tuple[datetime, str]) -> str:
    updated_at, thread_id = cursor
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise InvalidThreadCursor("Thread cursor timestamp must be timezone-aware.")
    if not thread_id or len(thread_id) > 128:
        raise InvalidThreadCursor("Thread cursor identity is invalid.")
    payload = json.dumps(
        {
            "thread_id": thread_id,
            "updated_at": updated_at.astimezone(UTC).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    if len(encoded) > _MAX_CURSOR_LENGTH:
        raise InvalidThreadCursor("Thread cursor is too large.")
    return encoded


def decode_thread_cursor(value: str) -> tuple[datetime, str]:
    payload = _decode_payload(value)
    if set(payload) != {"thread_id", "updated_at"}:
        raise InvalidThreadCursor("Thread cursor fields are invalid.")
    thread_id = payload["thread_id"]
    updated_at_text = payload["updated_at"]
    if (
        not isinstance(thread_id, str)
        or not thread_id
        or len(thread_id) > 128
        or not isinstance(updated_at_text, str)
    ):
        raise InvalidThreadCursor("Thread cursor values are invalid.")
    try:
        updated_at = datetime.fromisoformat(updated_at_text)
    except ValueError as error:
        raise InvalidThreadCursor("Thread cursor timestamp is invalid.") from error
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise InvalidThreadCursor("Thread cursor timestamp must include a timezone.")
    return updated_at.astimezone(UTC), thread_id


def encode_thread_search_cursor(
    cursor: tuple[datetime, str],
    *,
    workspace_key: str,
    query: str,
) -> str:
    updated_at, thread_id = cursor
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise InvalidThreadCursor("Thread cursor timestamp must be timezone-aware.")
    if not thread_id or len(thread_id) > 128:
        raise InvalidThreadCursor("Thread cursor identity is invalid.")
    payload = json.dumps(
        {
            "scope_hash": _thread_search_scope(workspace_key, query),
            "thread_id": thread_id,
            "updated_at": updated_at.astimezone(UTC).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    if len(encoded) > _MAX_CURSOR_LENGTH:
        raise InvalidThreadCursor("Thread cursor is too large.")
    return encoded


def decode_thread_search_cursor(
    value: str,
    *,
    workspace_key: str,
    query: str,
) -> tuple[datetime, str]:
    payload = _decode_payload(value)
    if set(payload) != {"scope_hash", "thread_id", "updated_at"}:
        raise InvalidThreadCursor("Thread cursor fields are invalid.")
    scope_hash = payload["scope_hash"]
    thread_id = payload["thread_id"]
    updated_at_text = payload["updated_at"]
    if (
        not isinstance(scope_hash, str)
        or re.fullmatch(r"[a-f0-9]{64}", scope_hash) is None
        or not isinstance(thread_id, str)
        or not thread_id
        or len(thread_id) > 128
        or not isinstance(updated_at_text, str)
    ):
        raise InvalidThreadCursor("Thread cursor values are invalid.")
    if not hmac.compare_digest(scope_hash, _thread_search_scope(workspace_key, query)):
        raise InvalidThreadCursor("Thread cursor does not match this search.")
    try:
        updated_at = datetime.fromisoformat(updated_at_text)
    except ValueError as error:
        raise InvalidThreadCursor("Thread cursor timestamp is invalid.") from error
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise InvalidThreadCursor("Thread cursor timestamp must include a timezone.")
    return updated_at.astimezone(UTC), thread_id


def _thread_search_scope(workspace_key: str, query: str) -> str:
    normalized_query = query.strip()
    if not workspace_key or len(workspace_key) > 128:
        raise InvalidThreadCursor("Thread cursor workspace is invalid.")
    if not normalized_query or len(normalized_query) > 200:
        raise InvalidThreadCursor("Thread cursor query is invalid.")
    scope = json.dumps(
        {"query": normalized_query, "workspace_key": workspace_key},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(scope).hexdigest()


def _decode_payload(value: str) -> dict[str, object]:
    if (
        not value
        or len(value) > _MAX_CURSOR_LENGTH
        or _CURSOR_PATTERN.fullmatch(value) is None
    ):
        raise InvalidThreadCursor("Thread cursor encoding is invalid.")
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise InvalidThreadCursor("Thread cursor payload is invalid.") from error
    if not isinstance(payload, dict):
        raise InvalidThreadCursor("Thread cursor fields are invalid.")
    return payload


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate cursor field.")
        result[key] = value
    return result


__all__ = [
    "InvalidThreadCursor",
    "decode_thread_cursor",
    "decode_thread_search_cursor",
    "encode_thread_cursor",
    "encode_thread_search_cursor",
]
