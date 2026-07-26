from __future__ import annotations

import math
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BeforeValidator

MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991


def normalize_json_safe_integer(value: object) -> int:
    """Normalize one interoperable JSON integer without losing precision."""

    if type(value) is int:
        normalized = value
    elif type(value) is float:
        observed = value
        if not math.isfinite(observed) or not observed.is_integer():
            raise ValueError("Expected an integral JSON number.")
        normalized = int(observed)
    else:
        raise ValueError("Expected an integral JSON number.")
    if abs(normalized) > MAX_JSON_SAFE_INTEGER:
        raise ValueError("JSON integer exceeds the interoperable safe range.")
    return normalized


type JsonSafeInteger = Annotated[
    int,
    BeforeValidator(normalize_json_safe_integer),
]

type SessionId = str
type TurnId = str
type OperationId = str
type ToolCallId = str
type ChangeSetId = str
type ClientMessageId = str

type IdentifierPrefix = Literal[
    "session",
    "turn",
    "operation",
    "call",
    "change",
    "client",
]


def new_identifier(prefix: IdentifierPrefix) -> str:
    return f"{prefix}_{uuid4().hex}"
