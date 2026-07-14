import pytest

from awesome_agent.conversation.titles import (
    automatic_title,
    normalize_title,
    visible_graphemes,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  fix   the tests  ", "fix the tests"),
        ("你\u0301 好", "你\u0301 好"),
        ("👩‍💻" * 49, "👩‍💻" * 47 + "…"),
    ],
)
def test_automatic_title_is_normalized_and_bounded(
    raw: str,
    expected: str,
) -> None:
    assert automatic_title(raw) == expected


def test_visible_graphemes_group_common_terminal_sequences() -> None:
    assert visible_graphemes("A\u0301👩🏽‍💻🇨🇳") == ("A\u0301", "👩🏽‍💻", "🇨🇳")


def test_normalize_title_collapses_all_whitespace() -> None:
    assert normalize_title("\n  cube\t helper \r\n") == "cube helper"
