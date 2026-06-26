from __future__ import annotations

_SUMMARY_LIMIT = 2000


def summarize_output(value: str, *, limit: int = _SUMMARY_LIMIT) -> str:
    if len(value) <= limit:
        return value
    head = value[: limit // 2]
    tail = value[-(limit // 2) :]
    return f"{head}\n...[validation output truncated: {len(value)} chars]...\n{tail}"
