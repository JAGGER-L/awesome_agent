import pytest
from pydantic import BaseModel

from awesome_agent.core.tools import ToolOutput, ToolSpec
from awesome_agent.core.tools.registry import DuplicateToolName, ToolRegistry


class EmptyArguments(BaseModel):
    pass


async def handler(arguments: BaseModel, context: object) -> ToolOutput:
    return ToolOutput(content="ok")


def test_registry_rejects_duplicates_and_lists_sorted_specs() -> None:
    registry = ToolRegistry()
    for name in ("grep", "ls"):
        registry.register(
            spec=ToolSpec(
                name=name,
                description=name,
                input_schema=EmptyArguments.model_json_schema(),
                read_only=True,
            ),
            input_model=EmptyArguments,
            handler=handler,
        )

    assert [spec.name for spec in registry.specifications()] == ["grep", "ls"]
    registered = registry.resolve("ls")
    assert registered is not None
    with pytest.raises(DuplicateToolName):
        registry.register(
            spec=registered.spec,
            input_model=EmptyArguments,
            handler=handler,
        )
