from __future__ import annotations

from datetime import UTC, datetime

import pytest

from awesome_agent.storage.pagination import (
    InvalidThreadCursor,
    decode_thread_cursor,
    decode_thread_search_cursor,
    encode_thread_cursor,
    encode_thread_search_cursor,
)


def test_thread_cursor_round_trips_deterministically() -> None:
    cursor = (datetime(2026, 7, 11, 8, 0, tzinfo=UTC), "thread_001")

    encoded = encode_thread_cursor(cursor)

    assert encode_thread_cursor(cursor) == encoded
    assert decode_thread_cursor(encoded) == cursor
    assert len(encoded) <= 1_024


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not base64!",
        "e30",
        "eCJ0aHJlYWRfaWQiOiJ0aHJlYWRfMSJ9",
        "a" * 1_025,
    ],
)
def test_thread_cursor_rejects_malformed_or_oversized_values(cursor: str) -> None:
    with pytest.raises(InvalidThreadCursor):
        decode_thread_cursor(cursor)


def test_thread_search_cursor_is_deterministic_and_scope_bound() -> None:
    cursor = (datetime(2026, 7, 11, 8, 0, tzinfo=UTC), "thread_001")

    encoded = encode_thread_search_cursor(
        cursor,
        workspace_key="workspace_1",
        query="  Needle  ",
    )

    assert (
        decode_thread_search_cursor(
            encoded,
            workspace_key="workspace_1",
            query="Needle",
        )
        == cursor
    )
    assert encoded == encode_thread_search_cursor(
        cursor,
        workspace_key="workspace_1",
        query="Needle",
    )
    with pytest.raises(InvalidThreadCursor):
        decode_thread_search_cursor(
            encoded,
            workspace_key="workspace_2",
            query="Needle",
        )
    with pytest.raises(InvalidThreadCursor):
        decode_thread_search_cursor(
            encoded,
            workspace_key="workspace_1",
            query="different",
        )


def test_list_and_search_cursors_are_not_interchangeable() -> None:
    cursor = (datetime(2026, 7, 11, 8, 0, tzinfo=UTC), "thread_001")
    list_cursor = encode_thread_cursor(cursor)
    search_cursor = encode_thread_search_cursor(
        cursor,
        workspace_key="workspace_1",
        query="needle",
    )

    with pytest.raises(InvalidThreadCursor):
        decode_thread_search_cursor(
            list_cursor,
            workspace_key="workspace_1",
            query="needle",
        )
    with pytest.raises(InvalidThreadCursor):
        decode_thread_cursor(search_cursor)
