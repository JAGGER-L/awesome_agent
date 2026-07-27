from typing import Literal, cast

from pydantic import BaseModel

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.builtins.delete import (
    DeleteArguments,
    admit_delete,
    create_delete_handler,
)
from awesome_agent.core.tools.builtins.edit_file import (
    EditFileArguments,
    create_edit_file_handler,
)
from awesome_agent.core.tools.builtins.execute import (
    ExecuteArguments,
    admit_execute,
    create_execute_handler,
    resolve_execute_timeout,
)
from awesome_agent.core.tools.builtins.listing import LsArguments, list_directory
from awesome_agent.core.tools.builtins.read_file import ReadFileArguments, read_file
from awesome_agent.core.tools.builtins.search import (
    GlobArguments,
    GrepArguments,
    admit_glob,
    admit_grep,
    glob_files,
    grep_files,
)
from awesome_agent.core.tools.builtins.write_file import (
    WriteFileArguments,
    create_write_file_handler,
)
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolInvocationDescription,
    ToolSpec,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.permissions import ToolCapability
from awesome_agent.core.tools.policy import resolve_workspace_path
from awesome_agent.core.tools.process import ShellExecutionBackend
from awesome_agent.core.tools.registry import (
    ToolAdmitter,
    ToolDescriber,
    ToolRegistry,
    ToolReplaySafety,
    ToolTimeoutResolver,
)
from awesome_agent.core.workspace import WorkspaceIdentity

type _ExpectedKind = Literal["file", "directory"]


def _bounded_description(
    *,
    verb: str,
    operation: str,
    target: str,
) -> ToolInvocationDescription:
    return ToolInvocationDescription(
        verb=verb,
        display_target=target[:2_000],
        approval_operation=operation,
        approval_target=target[:8_000],
    )


def _field_describer(
    *,
    verb: str,
    operation: str,
    field: str,
) -> ToolDescriber:
    def describe(arguments: BaseModel) -> ToolInvocationDescription:
        target = getattr(arguments, field, None)
        if not isinstance(target, str):
            raise ToolInvariantError("Tool description field is not a string.")
        return _bounded_description(
            verb=verb,
            operation=operation,
            target=target,
        )

    return describe


def _path_admitter(
    *,
    must_exist: bool,
    expected_kind: _ExpectedKind | None = None,
) -> ToolAdmitter:
    def admit(arguments: BaseModel, context: ToolExecutionContext) -> None:
        requested = getattr(arguments, "path", None)
        if not isinstance(requested, str):
            raise ToolInvariantError("Tool path field is not a string.")
        resolve_workspace_path(
            context.workspace,
            requested,
            must_exist=must_exist,
            expected_kind=expected_kind,
        )

    return admit


def _same_workspace(
    left: WorkspaceIdentity,
    right: WorkspaceIdentity,
) -> bool:
    return (
        left.key == right.key
        and left.canonical_path == right.canonical_path
        and left.root_identity == right.root_identity
    )


def _write_admitter(workspace: WorkspaceIdentity) -> ToolAdmitter:
    def admit(arguments: BaseModel, context: ToolExecutionContext) -> None:
        if not _same_workspace(workspace, context.workspace):
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Tool registration is bound to a different workspace.",
            )
        options = cast(WriteFileArguments, arguments)
        safe = resolve_workspace_path(
            workspace,
            options.path,
            must_exist=False,
        )
        if safe.target_existed:
            resolve_workspace_path(
                workspace,
                options.path,
                must_exist=True,
                expected_kind="file",
            )

    return admit


def _write_describer(workspace: WorkspaceIdentity) -> ToolDescriber:
    def describe(arguments: BaseModel) -> ToolInvocationDescription:
        options = cast(WriteFileArguments, arguments)
        safe = resolve_workspace_path(
            workspace,
            options.path,
            must_exist=False,
        )
        return _bounded_description(
            verb="Write",
            operation="overwrite" if safe.target_existed else "create",
            target=options.path,
        )

    return describe


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
    describe: ToolDescriber,
    admit: ToolAdmitter,
    replay_safety: ToolReplaySafety,
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
        describe=describe,
        admit=admit,
        replay_safety=replay_safety,
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
        describe=_field_describer(verb="Glob", operation="search", field="pattern"),
        admit=admit_glob,
        replay_safety=ToolReplaySafety.REPLAYABLE,
    )
    _register(
        registry,
        name="grep",
        description="Search file contents",
        input_model=GrepArguments,
        handler=grep_files,
        capability=ToolCapability.WORKSPACE_READ,
        verb="Grep",
        describe=_field_describer(verb="Grep", operation="search", field="pattern"),
        admit=admit_grep,
        replay_safety=ToolReplaySafety.REPLAYABLE,
    )
    _register(
        registry,
        name="ls",
        description="List files in a directory",
        input_model=LsArguments,
        handler=list_directory,
        capability=ToolCapability.WORKSPACE_READ,
        verb="List",
        describe=_field_describer(verb="List", operation="list", field="path"),
        admit=_path_admitter(must_exist=True, expected_kind="directory"),
        replay_safety=ToolReplaySafety.REPLAYABLE,
    )
    _register(
        registry,
        name="read_file",
        description="Read file contents",
        input_model=ReadFileArguments,
        handler=read_file,
        capability=ToolCapability.WORKSPACE_READ,
        verb="Read",
        describe=_field_describer(verb="Read", operation="read", field="path"),
        admit=_path_admitter(must_exist=True, expected_kind="file"),
        replay_safety=ToolReplaySafety.REPLAYABLE,
    )


def register_modifying_tools(
    registry: ToolRegistry,
    journal: ChangeJournal,
    process_runner: ShellExecutionBackend | None = None,
    *,
    workspace: WorkspaceIdentity,
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
        describe=_field_describer(verb="Delete", operation="delete", field="path"),
        admit=admit_delete,
        replay_safety=ToolReplaySafety.NON_REPLAYABLE,
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
        describe=_field_describer(verb="Edit", operation="edit", field="path"),
        admit=_path_admitter(must_exist=True, expected_kind="file"),
        replay_safety=ToolReplaySafety.NON_REPLAYABLE,
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
            describe=_field_describer(
                verb="Run",
                operation="run",
                field="command",
            ),
            admit=admit_execute,
            replay_safety=ToolReplaySafety.NON_REPLAYABLE,
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
        describe=_write_describer(workspace),
        admit=_write_admitter(workspace),
        replay_safety=ToolReplaySafety.NON_REPLAYABLE,
    )


__all__ = ["register_modifying_tools", "register_read_tools"]
