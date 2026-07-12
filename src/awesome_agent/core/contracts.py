from __future__ import annotations

from typing import Literal
from uuid import uuid4

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
