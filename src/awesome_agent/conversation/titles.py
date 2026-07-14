from __future__ import annotations

import unicodedata

_ZERO_WIDTH_JOINER = "\u200d"


def normalize_title(value: str) -> str:
    return " ".join(value.split())


def automatic_title(value: str) -> str:
    normalized = normalize_title(value)
    clusters = visible_graphemes(normalized)
    if len(clusters) <= 48:
        return normalized
    return "".join(clusters[:47]) + "…"


def visible_graphemes(value: str) -> tuple[str, ...]:
    clusters: list[str] = []
    current = ""
    for character in value:
        if not current:
            current = character
            continue
        if (
            character == _ZERO_WIDTH_JOINER
            or current.endswith(_ZERO_WIDTH_JOINER)
            or _is_grapheme_extension(character)
            or _continues_regional_indicator_pair(current, character)
        ):
            current += character
            continue
        clusters.append(current)
        current = character
    if current:
        clusters.append(current)
    return tuple(clusters)


def _is_grapheme_extension(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _continues_regional_indicator_pair(current: str, character: str) -> bool:
    return (
        len(current) == 1
        and _is_regional_indicator(current)
        and _is_regional_indicator(character)
    )


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF
