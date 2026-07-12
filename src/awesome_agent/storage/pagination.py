from __future__ import annotations

import base64
import binascii
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
    if not isinstance(payload, dict) or set(payload) != {"thread_id", "updated_at"}:
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
    "encode_thread_cursor",
]
