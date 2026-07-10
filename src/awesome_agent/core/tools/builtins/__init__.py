from pydantic import BaseModel

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.builtins.delete import (
    DeleteArguments,
    create_delete_handler,
)
from awesome_agent.core.tools.builtins.edit_file import (
    EditFileArguments,
    create_edit_file_handler,
)
from awesome_agent.core.tools.builtins.execute import (
    ExecuteArguments,
    create_execute_handler,
)
from awesome_agent.core.tools.builtins.listing import LsArguments, list_directory
from awesome_agent.core.tools.builtins.read_file import ReadFileArguments, read_file
from awesome_agent.core.tools.builtins.search import (
    GlobArguments,
    GrepArguments,
    glob_files,
    grep_files,
)
from awesome_agent.core.tools.builtins.write_file import (
    WriteFileArguments,
    create_write_file_handler,
)
from awesome_agent.core.tools.context import ToolHandler
from awesome_agent.core.tools.contracts import ToolSpec
from awesome_agent.core.tools.process import ProcessRunner
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
        name="glob",
        description="Find files matching a glob pattern",
        input_model=GlobArguments,
        handler=glob_files,
    )
    _register(
        registry,
        name="grep",
        description="Search file contents",
        input_model=GrepArguments,
        handler=grep_files,
    )
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


def register_modifying_tools(
    registry: ToolRegistry,
    journal: ChangeJournal,
    process_runner: ProcessRunner | None = None,
) -> None:
    _register(
        registry,
        name="delete",
        description="Delete a file, or a directory and its contents recursively",
        input_model=DeleteArguments,
        handler=create_delete_handler(journal),
    )
    _register(
        registry,
        name="edit_file",
        description="Perform exact string replacements in files",
        input_model=EditFileArguments,
        handler=create_edit_file_handler(journal),
    )
    if process_runner is not None:
        _register(
            registry,
            name="execute",
            description="Run shell commands",
            input_model=ExecuteArguments,
            handler=create_execute_handler(journal, process_runner),
        )
    _register(
        registry,
        name="write_file",
        description="Create a new file, or overwrite an existing one",
        input_model=WriteFileArguments,
        handler=create_write_file_handler(journal),
    )


__all__ = ["register_modifying_tools", "register_read_tools"]
