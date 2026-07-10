from pydantic import BaseModel

from awesome_agent.core.tools.builtins.listing import LsArguments, list_directory
from awesome_agent.core.tools.builtins.read_file import ReadFileArguments, read_file
from awesome_agent.core.tools.context import ToolHandler
from awesome_agent.core.tools.contracts import ToolSpec
from awesome_agent.core.tools.registry import ToolRegistry


def _register(
    registry: ToolRegistry,
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    handler: ToolHandler,
) -> None:
    registry.register(
        spec=ToolSpec(
            name=name,
            description=description,
            input_schema=input_model.model_json_schema(),
            read_only=True,
        ),
        input_model=input_model,
        handler=handler,
    )


def register_read_tools(registry: ToolRegistry) -> None:
    _register(
        registry,
        name="ls",
        description="List files in a directory",
        input_model=LsArguments,
        handler=list_directory,
    )
    _register(
        registry,
        name="read_file",
        description="Read file contents",
        input_model=ReadFileArguments,
        handler=read_file,
    )


__all__ = ["register_read_tools"]
