from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.config import UserConfigDocument, UserConfigWriter


class Mem0Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: Literal["awesome-agent"] = "awesome-agent"
    user_id: str = Field(pattern=r"^user_[a-f0-9]{32}$")
    workspace_key: str = Field(pattern=r"^ws_[a-f0-9]{32}$")


def new_mem0_user_id() -> str:
    return f"user_{uuid4().hex}"


def ensure_mem0_user_id(writer: UserConfigWriter) -> str:
    def update(document: UserConfigDocument) -> UserConfigDocument:
        if document.memory.mem0_user_id is not None:
            return document
        memory = document.memory.model_copy(update={"mem0_user_id": new_mem0_user_id()})
        return document.model_copy(update={"memory": memory})

    updated = writer.update(update)
    user_id = updated.memory.mem0_user_id
    if user_id is None:
        raise RuntimeError("Mem0 identity persistence failed")
    return user_id
