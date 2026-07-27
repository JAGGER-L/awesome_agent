from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.application.contracts import ThreadSearchQuery


def test_thread_search_query_trims_before_enforcing_bounds() -> None:
    query = ThreadSearchQuery(query="  two words  ", limit=50)

    assert query.query == "two words"
    assert query.cursor is None
    assert query.limit == 50


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   "},
        {"query": "x" * 201},
        {"query": 1},
        {"query": "query", "limit": "1"},
        {"query": "query", "limit": 0},
        {"query": "query", "limit": 51},
        {"query": "query", "cursor": 1},
        {"query": "query", "unknown": True},
    ],
)
def test_thread_search_query_is_strict_closed_and_bounded(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ThreadSearchQuery.model_validate(payload)
