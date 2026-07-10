import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from awesome_agent.config import (
    MemoryConfig,
    UserConfigDocument,
    UserConfigWriter,
    WorkspaceConfigDocument,
)
from awesome_agent.config.loader import read_user_config_document
from awesome_agent.memory.identity import (
    Mem0Identity,
    ensure_mem0_user_id,
    new_mem0_user_id,
)


def test_generated_and_explicit_identity_are_strict_and_opaque() -> None:
    generated = new_mem0_user_id()

    assert re.fullmatch(r"user_[a-f0-9]{32}", generated)
    identity = Mem0Identity(
        user_id=generated,
        workspace_key="ws_11111111111111111111111111111111",
    )
    assert identity.app_id == "awesome-agent"
    assert set(identity.model_dump()) == {"app_id", "user_id", "workspace_key"}

    for invalid in (
        "alice@example.com",
        "alice",
        "user_short",
        "user_../../private",
        "user_ABCDEF11111111111111111111111111",
    ):
        with pytest.raises(ValidationError):
            MemoryConfig(mem0_user_id=invalid)


def test_workspace_config_has_no_mem0_authority() -> None:
    with pytest.raises(ValidationError):
        WorkspaceConfigDocument.model_validate(
            {"version": 1, "memory": {"mem0_cloud": True}}
        )


def test_identity_is_generated_only_by_explicit_enable_and_persisted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    writer = UserConfigWriter(path)

    status_document = read_user_config_document(path)
    assert status_document.memory.mem0_user_id is None
    assert path.exists() is False

    first = ensure_mem0_user_id(writer)
    second = ensure_mem0_user_id(writer)
    persisted = read_user_config_document(path)

    assert first == second == persisted.memory.mem0_user_id
    assert persisted.memory.mem0_cloud is False


def test_changing_user_id_selects_new_namespace_without_migration() -> None:
    first = Mem0Identity(
        user_id="user_11111111111111111111111111111111",
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    second = first.model_copy(
        update={"user_id": "user_22222222222222222222222222222222"}
    )

    assert first.user_id != second.user_id
    assert first.workspace_key == second.workspace_key
    assert not hasattr(first, "migration")


def test_user_config_accepts_only_typed_mem0_identity() -> None:
    document = UserConfigDocument(
        memory=MemoryConfig(
            mem0_cloud=True,
            mem0_user_id="user_33333333333333333333333333333333",
        )
    )

    assert document.memory.mem0_cloud is True
