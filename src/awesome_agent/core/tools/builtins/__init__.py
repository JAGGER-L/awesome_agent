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
    resolve_execute_timeout,
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
from awesome_agent.core.tools.permissions import ToolCapability
from awesome_agent.core.tools.process import ShellExecutionBackend
from awesome_agent.core.tools.registry import ToolRegistry, ToolTimeoutResolver


def _register(
    registry: ToolRegistry,
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    handler: ToolHandler,
    capability: ToolCapability,
    verb: str,
    read_only: bool = True,
    timeout_resolver: ToolTimeoutResolver | None = None,
) -> None:
    registry.register(
        spec=ToolSpec(
            name=name,
            description=description,
            input_schema=input_model.model_json_schema(),
            capability=capability,
            read_only=read_only,
            display_metadata={"verb": verb},
        ),
        input_model=input_model,
        handler=handler,
        timeout_resolver=timeout_resolver,
    )


def register_read_tools(registry: ToolRegistry) -> None:
    _register(
        registry,
        name="glob",
        description="Find files matching a glob pattern",
        input_model=GlobArguments,
        handler=glob_files,
        capability=ToolCapability.WORKSPACE_READ,
        verb="Glob",
    )
    _register(
        registry,
        name="grep",
        description="Search file contents",
        input_model=GrepArguments,
        handler=grep_files,
        capability=ToolCapability.WORKSPACE_READ,
        verb="Grep",
    )
    _register(
        registry,
        name="ls",
        description="List files in a directory",
        input_model=LsArguments,
        handler=list_directory,
        capability=ToolCapability.WORKSPACE_READ,
        verb="List",
    )
    _register(
        registry,
        name="read_file",
        description="Read file contents",
        input_model=ReadFileArguments,
        handler=read_file,
        capability=ToolCapability.WORKSPACE_READ,
        verb="Read",
    )


def register_modifying_tools(
    registry: ToolRegistry,
    journal: ChangeJournal,
    process_runner: ShellExecutionBackend | None = None,
) -> None:
    _register(
        registry,
        name="delete",
        description="Delete a file, or a directory and its contents recursively",
        input_model=DeleteArguments,
        handler=create_delete_handler(journal),
        capability=ToolCapability.WORKSPACE_DELETE,
        verb="Delete",
        read_only=False,
    )
    _register(
        registry,
        name="edit_file",
        description="Perform exact string replacements in files",
        input_model=EditFileArguments,
        handler=create_edit_file_handler(journal),
        capability=ToolCapability.WORKSPACE_WRITE,
        verb="Edit",
        read_only=False,
    )
    if process_runner is not None:
        _register(
            registry,
            name="execute",
            description="Run shell commands",
            input_model=ExecuteArguments,
            handler=create_execute_handler(journal, process_runner),
            capability=ToolCapability.SHELL_EXECUTE,
            verb="Run",
            read_only=False,
            timeout_resolver=resolve_execute_timeout,
        )
    _register(
        registry,
        name="write_file",
        description="Create a new file, or overwrite an existing one",
        input_model=WriteFileArguments,
        handler=create_write_file_handler(journal),
        capability=ToolCapability.WORKSPACE_WRITE,
        verb="Write",
        read_only=False,
    )


__all__ = ["register_modifying_tools", "register_read_tools"]
