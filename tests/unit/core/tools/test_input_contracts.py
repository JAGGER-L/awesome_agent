from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from awesome_agent.core.tools.builtins.delete import DeleteArguments
from awesome_agent.core.tools.builtins.edit_file import EditFileArguments
from awesome_agent.core.tools.builtins.execute import ExecuteArguments
from awesome_agent.core.tools.builtins.listing import LsArguments
from awesome_agent.core.tools.builtins.read_file import ReadFileArguments
from awesome_agent.core.tools.builtins.search import GlobArguments, GrepArguments
from awesome_agent.core.tools.builtins.write_file import WriteFileArguments
from awesome_agent.core.tools.contracts import ToolArguments
from awesome_agent.extensions.skills.tools import (
    LoadSkillArguments,
    ReadSkillResourceArguments,
)
from awesome_agent.memory.tools import (
    MemoryAddArguments,
    MemoryListArguments,
    MemoryRemoveArguments,
    MemoryReplaceArguments,
)

_HASH = "0" * 64
_ENTRY_ID = "memory_" + "1" * 32


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (DeleteArguments, {"path": "old.txt"}),
        (
            EditFileArguments,
            {"path": "file.txt", "old_string": "old", "new_string": "new"},
        ),
        (ExecuteArguments, {"command": "git status"}),
        (LsArguments, {}),
        (ReadFileArguments, {"path": "README.md"}),
        (GlobArguments, {"pattern": "*.py"}),
        (GrepArguments, {"pattern": "needle"}),
        (WriteFileArguments, {"path": "new.txt", "content": "content"}),
        (LoadSkillArguments, {"name": "review"}),
        (
            ReadSkillResourceArguments,
            {"name": "review", "relative_path": "references/checklist.md"},
        ),
        (MemoryListArguments, {"scope": "user"}),
        (
            MemoryAddArguments,
            {"scope": "user", "content": "fact", "expected_hash": _HASH},
        ),
        (
            MemoryReplaceArguments,
            {
                "scope": "workspace",
                "content": "fact",
                "expected_hash": _HASH,
                "entry_id": _ENTRY_ID,
            },
        ),
        (
            MemoryRemoveArguments,
            {
                "scope": "workspace",
                "expected_hash": _HASH,
                "entry_id": _ENTRY_ID,
            },
        ),
    ],
)
def test_awesome_owned_tool_arguments_are_closed_and_strict(
    model: type[ToolArguments],
    arguments: dict[str, Any],
) -> None:
    parsed = model.model_validate(arguments)

    assert model.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate({**arguments, "unexpected": True})
    with pytest.raises(ValidationError, match="Instance is frozen"):
        parsed.__setattr__(next(iter(model.model_fields)), "changed")


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (LsArguments, {"max_entries": "5"}),
        (
            EditFileArguments,
            {
                "path": "file.txt",
                "old_string": "old",
                "new_string": "new",
                "replace_all": "false",
            },
        ),
        (ExecuteArguments, {"command": "pwd", "timeout_seconds": "60"}),
        (GrepArguments, {"pattern": "needle", "regex": 0}),
        (MemoryListArguments, {"scope": 1}),
    ],
)
def test_awesome_owned_tool_arguments_reject_scalar_coercion(
    model: type[ToolArguments],
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
