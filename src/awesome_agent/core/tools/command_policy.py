from __future__ import annotations

import ast
import base64
import binascii
import ntpath
import os
import posixpath
import re
import shlex
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath


class CommandPolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ShellDialect(StrEnum):
    CMD = "cmd"
    POSIX = "posix"
    POWERSHELL = "powershell"


class _PowerShellExecutionOption(StrEnum):
    COMMAND = "command"
    ENCODED_COMMAND = "encoded_command"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class CommandPolicyDecision:
    action: CommandPolicyAction
    reason: str


@dataclass(slots=True)
class _InspectionState:
    nodes: int = 0
    outside_workspace: bool = False


@dataclass(frozen=True, slots=True)
class _CompoundSegment:
    command: str
    following_separator: str | None


@dataclass(frozen=True, slots=True)
class _SegmentInspection:
    changed_cwds: tuple[Path, ...] | None = None


class _DeniedCommand(Exception):
    pass


_MAX_INSPECTION_DEPTH = 8
_MAX_INSPECTION_NODES = 64
_EXECUTABLE_SUFFIXES = (".exe", ".com", ".cmd", ".bat")
_ELEVATION_COMMANDS = {"doas", "runas", "su", "sudo"}
_SHUTDOWN_COMMANDS = {
    "halt",
    "poweroff",
    "reboot",
    "restart-computer",
    "shutdown",
    "stop-computer",
}
_DISK_COMMANDS = {
    "blkdiscard",
    "clear-disk",
    "diskpart",
    "fdisk",
    "format",
    "format-volume",
    "initialize-disk",
    "mkfs",
    "new-partition",
    "parted",
    "remove-partition",
    "sfdisk",
    "wipefs",
}
_DESTRUCTIVE_COMMANDS = {
    "del",
    "erase",
    "rd",
    "remove-item",
    "ri",
    "rm",
    "rmdir",
}
_POSIX_SHELLS = {"ash", "bash", "dash", "ksh", "sh", "zsh"}
_POWERSHELLS = {"powershell", "pwsh"}
_POWERSHELL_DASHES = {"-", "\u2013", "\u2014", "\u2015"}
_POWERSHELL_NON_EXECUTION_SWITCHES = (
    ("configurationfile", "configurationfile"),
    ("configurationname", "config"),
    ("custompipename", "cus"),
    ("executionpolicy", "ex"),
    ("ep", "ep"),
    ("encodedarguments", "encodeda"),
    ("ea", "ea"),
)
_PYTHON_NAME = re.compile(r"(?:python(?:\d+(?:\.\d+)*)?|py)", re.IGNORECASE)
_ENVIRONMENT_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_FORK_BOMB = re.compile(
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    re.DOTALL,
)
_UNSUPPORTED_CONTROL_WORDS = {
    "case",
    "do",
    "elif",
    "else",
    "for",
    "foreach",
    "function",
    "if",
    "select",
    "switch",
    "then",
    "trap",
    "try",
    "until",
    "while",
}


def host_shell_dialect() -> ShellDialect:
    return ShellDialect.CMD if os.name == "nt" else ShellDialect.POSIX


def _strip_quotes(token: str) -> str:
    cleaned = token.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        return cleaned[1:-1]
    return cleaned


def _command_name(token: str) -> str:
    cleaned = _strip_quotes(token).lstrip("@({").rstrip("/\\)}")
    native_name = Path(cleaned).name
    windows_name = PureWindowsPath(cleaned).name
    name = windows_name if len(windows_name) < len(native_name) else native_name
    lowered = name.casefold()
    for suffix in _EXECUTABLE_SUFFIXES:
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def _split_compound(command: str, dialect: ShellDialect) -> list[_CompoundSegment]:
    segments: list[_CompoundSegment] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        escape_char = (
            "^"
            if dialect is ShellDialect.CMD
            else "`"
            if dialect is ShellDialect.POWERSHELL
            else "\\"
        )
        if char == escape_char and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote != "'" and (
            (
                dialect is ShellDialect.POSIX
                and (
                    char == "`"
                    or (quote is None and command[index : index + 2] in {"<(", ">("})
                    or (
                        command[index : index + 2] == "$("
                        and command[index : index + 3] != "$(("
                    )
                )
            )
            or (
                dialect is ShellDialect.POWERSHELL
                and command[index : index + 2] == "$("
            )
        ):
            raise _DeniedCommand(
                "Shell command substitutions cannot be inspected safely."
            )
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        quote_characters = '"' if dialect is ShellDialect.CMD else "\"'"
        if char in quote_characters:
            quote = char
            current.append(char)
            index += 1
            continue
        pair = command[index : index + 2]
        separator_length = 0
        if pair in {"&&", "||"}:
            separator_length = 2
        elif char in "\r\n|&" or (char == ";" and dialect is not ShellDialect.CMD):
            separator_length = 1
        if separator_length:
            rendered = "".join(current).strip()
            if rendered:
                separator = pair if separator_length == 2 else char
                segments.append(_CompoundSegment(rendered, separator))
            current = []
            index += separator_length
            continue
        current.append(char)
        index += 1
    if quote is not None or escaped:
        raise _DeniedCommand("Command could not be parsed safely.")
    rendered = "".join(current).strip()
    if rendered:
        segments.append(_CompoundSegment(rendered, None))
    return segments


def _raw_tokens(segment: str, dialect: ShellDialect) -> list[str]:
    try:
        return shlex.split(segment, posix=dialect is ShellDialect.POSIX)
    except ValueError as error:
        raise _DeniedCommand("Command could not be parsed safely.") from error


def _unescape_token(token: str, dialect: ShellDialect) -> str:
    escape = (
        "^"
        if dialect is ShellDialect.CMD
        else "`"
        if dialect is ShellDialect.POWERSHELL
        else None
    )
    if escape is None or escape not in token:
        return token
    rendered: list[str] = []
    index = 0
    while index < len(token):
        if token[index] == escape and index + 1 < len(token):
            rendered.append(token[index + 1])
            index += 2
            continue
        rendered.append(token[index])
        index += 1
    return "".join(rendered)


def _normalize_token(token: str, dialect: ShellDialect) -> str:
    return _unescape_token(_strip_quotes(token), dialect)


def _tokens(segment: str, dialect: ShellDialect) -> list[str]:
    return [_normalize_token(token, dialect) for token in _raw_tokens(segment, dialect)]


def _is_filesystem_root(value: str) -> bool:
    cleaned = value.strip().rstrip(".,")
    if cleaned in {"/", "\\"}:
        return True
    windows = PureWindowsPath(cleaned)
    return bool(windows.anchor) and cleaned.rstrip("/\\").casefold() == (
        windows.anchor.rstrip("/\\").casefold()
    )


def _normalizes_to_filesystem_root(value: str) -> bool:
    posix = PurePosixPath(value)
    if posix.is_absolute():
        normalized_posix = posixpath.normpath(value)
        if normalized_posix.rstrip("/") == "":
            return True
    windows = PureWindowsPath(value)
    if windows.is_absolute():
        normalized_windows = PureWindowsPath(ntpath.normpath(value))
        anchor = normalized_windows.anchor.rstrip("/\\")
        return (
            bool(anchor)
            and str(normalized_windows).rstrip("/\\").casefold() == anchor.casefold()
        )
    return False


def _glob_base(value: str) -> str:
    positions = [value.find(marker) for marker in "*?[" if marker in value]
    if not positions:
        return value
    raw_prefix = value[: min(positions)]
    prefix = raw_prefix.rstrip("/\\")
    if prefix:
        return prefix
    if raw_prefix.startswith(("/", "\\")):
        return raw_prefix[0]
    return "."


def _native_path(value: str, cwd: Path) -> Path | None:
    cleaned = _glob_base(value.strip().rstrip(","))
    if not cleaned:
        return None
    lowered = cleaned.casefold()
    for marker in ("%cd%", "$pwd.path", "${pwd}", "$pwd", "$(pwd)"):
        if lowered == marker:
            return Path(os.path.abspath(cwd))
        if lowered.startswith((f"{marker}/", f"{marker}\\")):
            suffix = cleaned[len(marker) :].lstrip("/\\")
            return Path(os.path.abspath(os.path.normpath(cwd / suffix)))
    if any(marker in cleaned for marker in ("$", "%", "`")):
        return None
    windows = PureWindowsPath(cleaned)
    if windows.is_absolute() and os.name != "nt":
        return None
    candidate = Path(cleaned)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return Path(os.path.abspath(os.path.normpath(candidate)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _path_outside_workspace(value: str, cwd: Path, workspace: Path) -> bool:
    candidate = _native_path(value, cwd)
    if candidate is None:
        return False
    try:
        return not candidate.is_relative_to(workspace)
    except ValueError:
        return True


def _dangerous_delete_target(value: str, cwd: Path, workspace: Path) -> bool:
    cleaned = _glob_base(value.strip().rstrip(","))
    if _is_filesystem_root(cleaned) or _normalizes_to_filesystem_root(cleaned):
        return True
    candidate = _native_path(cleaned, cwd)
    return candidate is not None and _same_path(candidate, workspace)


def _is_block_device(value: str) -> bool:
    cleaned = _strip_quotes(value).strip().rstrip(",").casefold()
    if re.fullmatch(
        r"/dev/(?:disk/.+|(?:hd|mmcblk|nvme|sd|vd)[a-z0-9._/-]*)",
        cleaned,
    ):
        return True
    windows = cleaned.replace("/", "\\")
    return bool(
        re.fullmatch(r"\\\\[.?]\\physicaldrive\d+", windows)
        or windows.startswith(r"\\?\globalroot\device\harddisk")
    )


def _command_payload(
    tokens: list[str],
    switches: set[str],
    *,
    allow_attached: bool = False,
) -> str | None:
    for index, token in enumerate(tokens[1:], start=1):
        lowered = token.casefold()
        if lowered in switches:
            return " ".join(tokens[index + 1 :]).strip()
        if allow_attached:
            for switch in switches:
                if lowered.startswith(switch) and len(token) > len(switch):
                    return " ".join(
                        [token[len(switch) :], *tokens[index + 1 :]]
                    ).strip()
    return None


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for item in node.values:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            parts.append(item.value)
        return "".join(parts)
    return None


def _join_command_tokens(parts: list[str], dialect: ShellDialect) -> str:
    if dialect is ShellDialect.CMD:
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def _literal_command(node: ast.AST, dialect: ShellDialect) -> str | None:
    direct = _literal_string(node)
    if direct is not None:
        return direct
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = [_literal_string(item) for item in node.elts]
        if all(part is not None for part in parts):
            return _join_command_tokens([part or "" for part in parts], dialect)
    return None


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr}"
    return None


def _call_argument(
    node: ast.Call,
    position: int,
    keyword_names: set[str],
) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    for keyword in node.keywords:
        if keyword.arg in keyword_names:
            return keyword.value
    return None


class _PythonInspector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        dialect: ShellDialect,
        cwd: Path,
        workspace: Path,
        state: _InspectionState,
        depth: int,
    ) -> None:
        self._dialect = dialect
        self._cwd = cwd
        self._workspace = workspace
        self._state = state
        self._depth = depth
        self._aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self._aliases[item.asname or item.name.split(".", 1)[0]] = item.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            for item in node.names:
                self._aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    def visit_Call(self, node: ast.Call) -> None:
        raw_name = _dotted_name(node.func)
        if raw_name is not None:
            first, *remaining = raw_name.split(".")
            resolved = ".".join([self._aliases.get(first, first), *remaining])
            if resolved in {
                "os.popen",
                "os.system",
                "subprocess.Popen",
                "subprocess.call",
                "subprocess.check_call",
                "subprocess.check_output",
                "subprocess.run",
            }:
                command_node = _call_argument(
                    node,
                    0,
                    {"args", "cmd", "command"},
                )
                command = (
                    _literal_command(command_node, self._dialect)
                    if command_node is not None
                    else None
                )
                if command is not None:
                    nested_cwd = self._cwd
                    cwd_node = _call_argument(node, len(node.args), {"cwd"})
                    cwd_literal = (
                        _literal_string(cwd_node) if cwd_node is not None else None
                    )
                    if cwd_literal is not None:
                        resolved_cwd = _native_path(cwd_literal, self._cwd)
                        if resolved_cwd is not None:
                            nested_cwd = resolved_cwd
                    _inspect_command(
                        command,
                        dialect=self._dialect,
                        cwd=nested_cwd,
                        workspace=self._workspace,
                        state=self._state,
                        depth=self._depth + 1,
                    )
            elif resolved == "shutil.rmtree":
                target_node = _call_argument(node, 0, {"path"})
                target = (
                    _literal_string(target_node) if target_node is not None else None
                )
                if target is not None and _dangerous_delete_target(
                    target, self._cwd, self._workspace
                ):
                    raise _DeniedCommand(
                        "Destructive Python calls cannot target a filesystem "
                        "or workspace root."
                    )
        self.generic_visit(node)


def _inspect_python(
    source: str,
    *,
    dialect: ShellDialect,
    cwd: Path,
    workspace: Path,
    state: _InspectionState,
    depth: int,
) -> None:
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as error:
        raise _DeniedCommand(
            "Python command payload could not be parsed safely."
        ) from error
    _PythonInspector(
        dialect=dialect,
        cwd=cwd,
        workspace=workspace,
        state=state,
        depth=depth,
    ).visit(tree)


def _python_command_source(
    tokens: list[str],
    index: int,
    *,
    dialect: ShellDialect,
) -> str:
    option = tokens[index]
    if option == "-c":
        if index + 1 >= len(tokens):
            raise _DeniedCommand("Python command is missing its payload.")
        return tokens[index + 1]

    source = option[2:]
    if not source:
        raise _DeniedCommand("Python command is missing its payload.")
    quote_characters = '"' if dialect is ShellDialect.CMD else "\"'"
    if source[0] not in quote_characters:
        return source
    quote = source[0]
    parts = [source]
    cursor = index + 1
    while not parts[-1].endswith(quote):
        if cursor >= len(tokens):
            raise _DeniedCommand("Python command payload could not be parsed safely.")
        parts.append(tokens[cursor])
        cursor += 1
    return _strip_quotes(" ".join(parts))


def _powershell_switch_key(token: str) -> str | None:
    if len(token) < 2:
        return None
    first = token[0]
    if first == "/":
        return token[1:].casefold()
    if first not in _POWERSHELL_DASHES:
        return None
    offset = 2 if len(token) > 1 and token[1] == first else 1
    return token[offset:].casefold()


def _powershell_switch_matches(
    key: str,
    canonical: str,
    smallest_unambiguous: str,
) -> bool:
    return len(key) >= len(smallest_unambiguous) and canonical.startswith(key)


def _known_non_execution_powershell_switch(key: str) -> bool:
    return any(
        _powershell_switch_matches(key, canonical, smallest)
        for canonical, smallest in _POWERSHELL_NON_EXECUTION_SWITCHES
    )


def _powershell_execution_option(token: str) -> _PowerShellExecutionOption | None:
    key = _powershell_switch_key(token)
    if key is None:
        return None
    stem = re.split("[:=]", key, maxsplit=1)[0]
    has_attached_separator = stem != key
    if _powershell_switch_matches(
        stem, "commandwithargs", "commandwithargs"
    ) or _powershell_switch_matches(stem, "cwa", "cwa"):
        return _PowerShellExecutionOption.UNSAFE
    if _powershell_switch_matches(stem, "command", "c"):
        return (
            _PowerShellExecutionOption.UNSAFE
            if has_attached_separator
            else _PowerShellExecutionOption.COMMAND
        )
    if _powershell_switch_matches(stem, "executionpolicy", "ex") or (
        _powershell_switch_matches(stem, "ep", "ep")
    ):
        return None
    if _powershell_switch_matches(
        stem, "encodedcommand", "e"
    ) or _powershell_switch_matches(stem, "ec", "e"):
        return (
            _PowerShellExecutionOption.UNSAFE
            if has_attached_separator
            else _PowerShellExecutionOption.ENCODED_COMMAND
        )
    if _known_non_execution_powershell_switch(stem):
        return None
    if stem.startswith(("c", "e")):
        return _PowerShellExecutionOption.UNSAFE
    return None


def _inspect_start_process(tokens: list[str]) -> None:
    file_path_seen = False
    for token in tokens[1:]:
        key = (
            _powershell_switch_key(token)
            if token and token[0] in _POWERSHELL_DASHES
            else None
        )
        if key is not None:
            stem = re.split("[:=]", key, maxsplit=1)[0]
            if _powershell_switch_matches(stem, "argumentlist", "a"):
                raise _DeniedCommand(
                    "PowerShell Start-Process arguments cannot be inspected safely."
                )
            continue
        if token.startswith("@"):
            raise _DeniedCommand(
                "PowerShell Start-Process splatting cannot be inspected safely."
            )
        if not file_path_seen:
            file_path_seen = True
            continue
        raise _DeniedCommand(
            "PowerShell Start-Process positional arguments cannot be inspected safely."
        )


def _inspect_powershell(
    tokens: list[str],
    *,
    cwd: Path,
    workspace: Path,
    state: _InspectionState,
    depth: int,
) -> None:
    for index, token in enumerate(tokens[1:], start=1):
        option = _powershell_execution_option(token)
        if option is _PowerShellExecutionOption.UNSAFE:
            raise _DeniedCommand(
                "PowerShell execution option could not be parsed safely."
            )
        if option is _PowerShellExecutionOption.ENCODED_COMMAND:
            if index + 1 >= len(tokens):
                raise _DeniedCommand(
                    "PowerShell encoded command is missing its payload."
                )
            try:
                raw = base64.b64decode(tokens[index + 1], validate=True)
                payload = raw.decode("utf-16-le")
            except (binascii.Error, UnicodeDecodeError) as error:
                raise _DeniedCommand(
                    "PowerShell encoded command could not be decoded safely."
                ) from error
            _inspect_command(
                payload,
                dialect=ShellDialect.POWERSHELL,
                cwd=cwd,
                workspace=workspace,
                state=state,
                depth=depth + 1,
            )
            return
        if option is _PowerShellExecutionOption.COMMAND:
            payload = " ".join(tokens[index + 1 :]).strip()
            if not payload:
                raise _DeniedCommand("PowerShell command is missing its payload.")
            _inspect_command(
                payload,
                dialect=ShellDialect.POWERSHELL,
                cwd=cwd,
                workspace=workspace,
                state=state,
                depth=depth + 1,
            )
            return


def _inspect_known_wrapper(
    name: str,
    tokens: list[str],
    *,
    segment: str,
    dialect: ShellDialect,
    cwd: Path,
    workspace: Path,
    state: _InspectionState,
    depth: int,
) -> _SegmentInspection | None:
    payload_tokens: list[str] | None = None
    payload_cwd = cwd
    if name in {"builtin", "call", "command", "nohup"} or (
        name == "exec" and dialect is ShellDialect.POSIX
    ):
        remaining = tokens[1:]
        while remaining:
            if remaining[0] == "--":
                remaining = remaining[1:]
                break
            if name == "exec" and remaining[0] == "-a":
                if len(remaining) < 2:
                    raise _DeniedCommand("Exec wrapper is missing an argument.")
                remaining = remaining[2:]
                continue
            if remaining[0].startswith("-") or not remaining[0]:
                remaining = remaining[1:]
                continue
            break
        payload_tokens = remaining
    elif name == "env":
        remaining = tokens[1:]
        nested_cwd = cwd
        cwd_changed = False
        while remaining:
            option = remaining[0].casefold()
            if option == "--":
                remaining = remaining[1:]
                break
            if option in {"-u", "--unset"}:
                if len(remaining) < 2:
                    raise _DeniedCommand(
                        "Environment wrapper is missing an option value."
                    )
                remaining = remaining[2:]
                continue
            chdir_target: str | None = None
            consumed = 0
            if option in {"-c", "--chdir"}:
                if len(remaining) < 2:
                    raise _DeniedCommand(
                        "Environment wrapper is missing its working directory."
                    )
                chdir_target = remaining[1]
                consumed = 2
            elif option.startswith("--chdir="):
                chdir_target = remaining[0].split("=", 1)[1]
                consumed = 1
            elif remaining[0].startswith("-C") and len(remaining[0]) > 2:
                chdir_target = remaining[0][2:]
                consumed = 1
            if chdir_target is not None:
                if cwd_changed:
                    raise _DeniedCommand(
                        "Multiple environment working directories cannot be inspected "
                        "safely."
                    )
                resolved_cwd = _native_path(chdir_target, cwd)
                if resolved_cwd is None:
                    raise _DeniedCommand(
                        "Environment working directory cannot be resolved safely."
                    )
                if _path_outside_workspace(chdir_target, cwd, workspace):
                    state.outside_workspace = True
                nested_cwd = resolved_cwd
                cwd_changed = True
                remaining = remaining[consumed:]
                continue
            if option in {"-a", "--argv0"}:
                if len(remaining) < 2:
                    raise _DeniedCommand(
                        "Environment wrapper is missing its argv0 value."
                    )
                remaining = remaining[2:]
                continue
            if option.startswith("--argv0="):
                remaining = remaining[1:]
                continue
            if remaining[0].startswith("-a") and len(remaining[0]) > 2:
                remaining = remaining[1:]
                continue
            split_payload: str | None = None
            if option in {"-s", "--split-string"}:
                if len(remaining) < 2:
                    raise _DeniedCommand("Environment wrapper is missing its payload.")
                split_payload = remaining[1]
                remaining = remaining[2:]
            elif option.startswith("--split-string="):
                split_payload = remaining[0].split("=", 1)[1]
                remaining = remaining[1:]
            elif remaining[0].startswith("-S") and len(remaining[0]) > 2:
                split_payload = remaining[0][2:]
                remaining = remaining[1:]
            if split_payload is not None:
                suffix = _join_command_tokens(remaining, dialect) if remaining else ""
                _inspect_command(
                    f"{split_payload} {suffix}".strip(),
                    dialect=dialect,
                    cwd=nested_cwd,
                    workspace=workspace,
                    state=state,
                    depth=depth + 1,
                )
                return _SegmentInspection()
            if _ENVIRONMENT_ASSIGNMENT.fullmatch(remaining[0]) is not None:
                remaining = remaining[1:]
                continue
            if option in {
                "-",
                "-0",
                "--debug",
                "--help",
                "--ignore-environment",
                "--list-signal-handling",
                "--null",
                "--version",
                "-i",
                "-v",
            } or option.startswith(
                ("--block-signal", "--default-signal", "--ignore-signal")
            ):
                remaining = remaining[1:]
                continue
            if option.startswith("-"):
                raise _DeniedCommand(
                    "Environment wrapper option cannot be inspected safely."
                )
            break
        payload_tokens = remaining
        payload_cwd = nested_cwd
    elif name == "nice":
        remaining = tokens[1:]
        while remaining:
            if remaining[0] in {"-n", "--adjustment"}:
                remaining = remaining[2:]
            elif remaining[0].startswith("-"):
                remaining = remaining[1:]
            else:
                break
        payload_tokens = remaining
    elif name == "time" and dialect is ShellDialect.POSIX:
        remaining = tokens[1:]
        while remaining:
            option = remaining[0]
            if option == "--":
                remaining = remaining[1:]
                break
            if option in {"-f", "--format", "-o", "--output"}:
                if len(remaining) < 2:
                    raise _DeniedCommand("Time wrapper is missing an option value.")
                remaining = remaining[2:]
                continue
            if option.startswith(("--format=", "--output=")):
                remaining = remaining[1:]
                continue
            if option.startswith("-"):
                remaining = remaining[1:]
                continue
            break
        payload_tokens = remaining
    elif name == "timeout":
        remaining = tokens[1:]
        while remaining:
            if remaining[0] in {"-k", "--kill-after", "-s", "--signal"}:
                remaining = remaining[2:]
            elif remaining[0].startswith("-"):
                remaining = remaining[1:]
            else:
                break
        payload_tokens = remaining[1:] if remaining else []
    elif name == "xargs" and dialect is ShellDialect.POSIX:
        remaining = tokens[1:]
        while remaining:
            option = remaining[0]
            if option == "--":
                remaining = remaining[1:]
                break
            if option in {
                "--arg-file",
                "--delimiter",
                "--eof",
                "--max-args",
                "--max-chars",
                "--max-lines",
                "--max-procs",
                "--replace",
                "-E",
                "-I",
                "-L",
                "-P",
                "-a",
                "-d",
                "-e",
                "-i",
                "-l",
                "-n",
                "-s",
            }:
                if len(remaining) < 2:
                    raise _DeniedCommand("Xargs wrapper is missing an option value.")
                remaining = remaining[2:]
            elif option.startswith("-"):
                remaining = remaining[1:]
            else:
                break
        payload_tokens = remaining
    elif name == "start" and dialect is ShellDialect.CMD:
        remaining = _raw_tokens(segment, dialect)[1:]

        def discard_options(items: list[str]) -> list[str]:
            while items:
                option = _normalize_token(items[0], dialect).casefold()
                if not option.startswith("/"):
                    break
                takes_value = option in {"/affinity", "/d", "/machine", "/node"}
                if takes_value and len(items) < 2:
                    raise _DeniedCommand("Start wrapper is missing an option value.")
                items = items[2:] if takes_value else items[1:]
            return items

        remaining = discard_options(remaining)
        if remaining and remaining[0].startswith('"'):
            remaining = remaining[1:]
            remaining = discard_options(remaining)
        payload_tokens = [_normalize_token(token, dialect) for token in remaining]
    elif name in {"eval", "iex", "invoke-expression"}:
        payload = " ".join(tokens[1:]).strip()
        if payload:
            changed_cwds = _inspect_command(
                payload,
                dialect=dialect,
                cwd=cwd,
                workspace=workspace,
                state=state,
                depth=depth + 1,
            )
            return _SegmentInspection(changed_cwds=changed_cwds)
        return _SegmentInspection()
    if payload_tokens:
        changed_cwds = _inspect_command(
            _join_command_tokens(payload_tokens, dialect),
            dialect=dialect,
            cwd=payload_cwd,
            workspace=workspace,
            state=state,
            depth=depth + 1,
        )
        if name in {"builtin", "call", "command"}:
            return _SegmentInspection(changed_cwds=changed_cwds)
    return _SegmentInspection() if payload_tokens is not None else None


def _directory_change_target(
    name: str,
    tokens: list[str],
    *,
    dialect: ShellDialect,
) -> str | None:
    if dialect is ShellDialect.CMD:
        for prefix in ("chdir", "cd"):
            if (
                name.startswith(prefix)
                and name != prefix
                and len(tokens) == 1
                and tokens[0][len(prefix) :].startswith((".", "/", "\\"))
            ):
                return tokens[0][len(prefix) :]
        if name not in {"cd", "chdir"}:
            return None
        remaining = tokens[1:]
        if remaining and remaining[0].casefold() == "/d":
            remaining = remaining[1:]
        if len(remaining) > 1:
            raise _DeniedCommand("Directory change could not be parsed safely.")
        return remaining[0] if remaining else ""
    if dialect is ShellDialect.POSIX:
        if name != "cd":
            return None
        remaining = tokens[1:]
        while remaining and remaining[0] in {"-e", "-L", "-P"}:
            remaining = remaining[1:]
        if remaining and remaining[0] == "--":
            remaining = remaining[1:]
        if len(remaining) > 1:
            raise _DeniedCommand("Directory change could not be parsed safely.")
        return remaining[0] if remaining else ""
    if name not in {"cd", "chdir", "set-location", "sl"}:
        return None
    remaining = tokens[1:]
    target: str | None = None
    index = 0
    while index < len(remaining):
        token = remaining[index]
        lowered = token.casefold()
        if lowered in {"-path", "-literalpath"}:
            if target is not None or index + 1 >= len(remaining):
                raise _DeniedCommand("Directory change could not be parsed safely.")
            target = remaining[index + 1]
            index += 2
            continue
        if lowered.startswith(("-path:", "-literalpath:")):
            if target is not None:
                raise _DeniedCommand("Directory change could not be parsed safely.")
            target = token.split(":", 1)[1]
            index += 1
            continue
        if token.startswith("-") or target is not None:
            raise _DeniedCommand("Directory change could not be parsed safely.")
        target = token
        index += 1
    return target or ""


def _inspect_directory_change(
    name: str,
    tokens: list[str],
    *,
    dialect: ShellDialect,
    cwd: Path,
    workspace: Path,
    state: _InspectionState,
    has_following_segment: bool,
) -> _SegmentInspection | None:
    target = _directory_change_target(name, tokens, dialect=dialect)
    if target is None:
        return None
    if not target:
        if has_following_segment:
            raise _DeniedCommand("Directory change target cannot be resolved safely.")
        return _SegmentInspection()
    changed_cwd = _native_path(target, cwd)
    if changed_cwd is None:
        raise _DeniedCommand("Directory change target cannot be resolved safely.")
    if _path_outside_workspace(target, cwd, workspace):
        state.outside_workspace = True
    return _SegmentInspection(changed_cwds=(changed_cwd,))


def _inspect_segment(
    segment: str,
    *,
    dialect: ShellDialect,
    cwd: Path,
    workspace: Path,
    state: _InspectionState,
    depth: int,
    has_following_segment: bool,
) -> _SegmentInspection:
    tokens = _tokens(segment, dialect)
    if not tokens:
        raise _DeniedCommand("Command could not be parsed safely.")
    if dialect is ShellDialect.POWERSHELL and tokens[0] in {"&", "."}:
        tokens = tokens[1:]
    if dialect is ShellDialect.POSIX:
        while tokens and _ENVIRONMENT_ASSIGNMENT.fullmatch(tokens[0]) is not None:
            tokens = tokens[1:]
    while tokens and tokens[0] in {"!", "(", "{", "@"}:
        tokens = tokens[1:]
    if not tokens:
        raise _DeniedCommand("Command could not be parsed safely.")

    executable = tokens[0]
    if (
        dialect is ShellDialect.CMD
        and any(marker in executable for marker in ("%", "!"))
    ) or (dialect is not ShellDialect.CMD and "$" in executable):
        raise _DeniedCommand(
            "Dynamically expanded executables cannot be inspected safely."
        )

    name = _command_name(tokens[0])
    if name in _UNSUPPORTED_CONTROL_WORDS:
        raise _DeniedCommand("Shell control-flow commands cannot be inspected safely.")
    if name in _ELEVATION_COMMANDS:
        raise _DeniedCommand("Privilege elevation commands are not allowed.")
    if name in _SHUTDOWN_COMMANDS:
        raise _DeniedCommand("Host shutdown and reboot commands are not allowed.")
    if name in _DISK_COMMANDS or name.startswith("mkfs."):
        raise _DeniedCommand("Disk formatting and partition commands are not allowed.")

    directory_change = _inspect_directory_change(
        name,
        tokens,
        dialect=dialect,
        cwd=cwd,
        workspace=workspace,
        state=state,
        has_following_segment=has_following_segment,
    )
    if directory_change is not None:
        return directory_change

    if name == "cmd":
        payload = _command_payload(
            tokens,
            {"/c", "/k", "-c", "-k"},
            allow_attached=True,
        )
        if payload is not None:
            if not payload:
                raise _DeniedCommand("CMD command is missing its payload.")
            _inspect_command(
                payload,
                dialect=ShellDialect.CMD,
                cwd=cwd,
                workspace=workspace,
                state=state,
                depth=depth + 1,
            )
        return _SegmentInspection()
    if name in _POSIX_SHELLS:
        payload = _command_payload(tokens, {"-c", "-lc", "-cl"})
        if payload is not None:
            if not payload:
                raise _DeniedCommand("Shell command is missing its payload.")
            _inspect_command(
                payload,
                dialect=ShellDialect.POSIX,
                cwd=cwd,
                workspace=workspace,
                state=state,
                depth=depth + 1,
            )
        return _SegmentInspection()
    if name in _POWERSHELLS:
        _inspect_powershell(
            tokens,
            cwd=cwd,
            workspace=workspace,
            state=state,
            depth=depth,
        )
        return _SegmentInspection()
    if _PYTHON_NAME.fullmatch(name) is not None:
        for index, token in enumerate(tokens[1:], start=1):
            if token == "-c" or token.startswith("-c"):
                _inspect_python(
                    _python_command_source(tokens, index, dialect=dialect),
                    dialect=dialect,
                    cwd=cwd,
                    workspace=workspace,
                    state=state,
                    depth=depth,
                )
                return _SegmentInspection()
    wrapper = _inspect_known_wrapper(
        name,
        tokens,
        segment=segment,
        dialect=dialect,
        cwd=cwd,
        workspace=workspace,
        state=state,
        depth=depth,
    )
    if wrapper is not None:
        return wrapper

    if name == "dd":
        for token in tokens[1:]:
            if token.casefold().startswith("of=/dev/"):
                raise _DeniedCommand("Raw block-device writes are not allowed.")
    is_start_process = name in {"saps", "start-process"} or (
        dialect is ShellDialect.POWERSHELL and name == "start"
    )
    if is_start_process and any(
        token.casefold() in {"-verb", "-verb:runas"} for token in tokens[1:]
    ):
        for index, token in enumerate(tokens[1:], start=1):
            if token.casefold() == "-verb" and index + 1 < len(tokens):
                if tokens[index + 1].casefold() == "runas":
                    raise _DeniedCommand(
                        "Privilege elevation commands are not allowed."
                    )
            elif token.casefold() == "-verb:runas":
                raise _DeniedCommand("Privilege elevation commands are not allowed.")
    if is_start_process and dialect is ShellDialect.POWERSHELL:
        _inspect_start_process(tokens)

    for index, token in enumerate(tokens[1:], start=1):
        if token in {">", ">>"} and index + 1 < len(tokens):
            if _is_block_device(tokens[index + 1]):
                raise _DeniedCommand("Raw block-device writes are not allowed.")
        elif token.startswith(">") and _is_block_device(token.lstrip(">")):
            raise _DeniedCommand("Raw block-device writes are not allowed.")
    if name in {"out-file", "set-content", "tee"} and any(
        _is_block_device(token) for token in tokens[1:] if not token.startswith("-")
    ):
        raise _DeniedCommand("Raw block-device writes are not allowed.")

    if name in _DESTRUCTIVE_COMMANDS:
        for token in tokens[1:]:
            lowered = token.casefold()
            if token == "--" or lowered in {"-path", "-literalpath"}:
                continue
            if lowered.startswith(("-path:", "-literalpath:")):
                _, _, target = token.partition(":")
                if _dangerous_delete_target(target, cwd, workspace):
                    raise _DeniedCommand(
                        "Destructive commands cannot target a filesystem "
                        "or workspace root."
                    )
                continue
            if token.startswith("-"):
                continue
            if dialect is ShellDialect.CMD and token.startswith("/") and token != "/":
                continue
            if _dangerous_delete_target(token, cwd, workspace):
                raise _DeniedCommand(
                    "Destructive commands cannot target a filesystem or workspace root."
                )

    for token in tokens[1:]:
        cleaned = token.strip().rstrip(",")
        is_absolute = (
            Path(cleaned).is_absolute() or PureWindowsPath(cleaned).is_absolute()
        )
        if (
            is_absolute
            and not _is_filesystem_root(cleaned)
            and _path_outside_workspace(cleaned, cwd, workspace)
        ):
            state.outside_workspace = True
    return _SegmentInspection()


def _inspect_command(
    command: str,
    *,
    dialect: ShellDialect,
    cwd: Path,
    workspace: Path,
    state: _InspectionState,
    depth: int,
) -> tuple[Path, ...]:
    if depth > _MAX_INSPECTION_DEPTH:
        raise _DeniedCommand("Command wrapper nesting exceeds the safety limit.")
    if _FORK_BOMB.search(command):
        raise _DeniedCommand("Shell fork bombs are not allowed.")
    segments = _split_compound(command, dialect)
    if not segments:
        raise _DeniedCommand("Command could not be parsed safely.")
    possible_cwds: tuple[Path, ...] = (cwd,)
    for index, segment in enumerate(segments):
        following_cwds: list[Path] = []
        for active_cwd in possible_cwds:
            state.nodes += 1
            if state.nodes > _MAX_INSPECTION_NODES:
                raise _DeniedCommand("Command complexity exceeds the safety limit.")
            inspected = _inspect_segment(
                segment.command,
                dialect=dialect,
                cwd=active_cwd,
                workspace=workspace,
                state=state,
                depth=depth,
                has_following_segment=index + 1 < len(segments),
            )
            changed_cwds = inspected.changed_cwds
            if changed_cwds is None:
                following_cwds.append(active_cwd)
            elif segment.following_separator == "&&":
                following_cwds.extend(changed_cwds)
            elif segment.following_separator == "||":
                following_cwds.append(active_cwd)
            elif segment.following_separator == "|":
                following_cwds.append(active_cwd)
                if dialect is ShellDialect.POWERSHELL:
                    following_cwds.extend(changed_cwds)
            else:
                following_cwds.append(active_cwd)
                following_cwds.extend(changed_cwds)
        possible_cwds = _unique_paths(following_cwds)
    return possible_cwds


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        unique.setdefault(key, path)
    return tuple(unique.values())


def evaluate_command(
    command: str,
    *,
    dialect: ShellDialect,
    cwd: Path,
    workspace: Path,
) -> CommandPolicyDecision:
    """Apply non-bypassable circuit breakers to one concrete shell invocation.

    This is deliberately a bounded accident-prevention policy, not a sandbox or
    a claim that arbitrary hostile shell code can be classified safely.
    """

    canonical_workspace = Path(os.path.abspath(workspace))
    canonical_cwd = Path(os.path.abspath(cwd))
    state = _InspectionState()
    try:
        _inspect_command(
            command,
            dialect=dialect,
            cwd=canonical_cwd,
            workspace=canonical_workspace,
            state=state,
            depth=0,
        )
    except _DeniedCommand as error:
        return CommandPolicyDecision(CommandPolicyAction.DENY, str(error))
    reason = (
        "Command references an absolute path outside the workspace."
        if state.outside_workspace
        else "Command passed hard safety checks."
    )
    return CommandPolicyDecision(CommandPolicyAction.ALLOW, reason)
