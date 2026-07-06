from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

GuardrailAction = Literal["allow", "ask", "deny"]

_SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_SENSITIVE_ENV_RE = re.compile(
    r"(api[_-]?key|token|secret|password|credential|private[_-]?key|auth)",
    re.IGNORECASE,
)
_EXTREME_DESTRUCTIVE_RE = re.compile(
    r"(^|\s)(rm\s+-rf\s+[/~*]|format(\.com)?|diskpart|mkfs|del\s+/[fsq])(\s|$)",
    re.IGNORECASE,
)
_COMMAND_APPROVAL_RE = re.compile(
    r"(^|\s)(git\s+commit|git\s+push|git\s+reset|git\s+clean|"
    r"git\s+checkout|git\s+switch|npm\s+install|pip\s+install)(\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    action: GuardrailAction
    reason: str
    guardrail: str


def classify_command(argv: list[str]) -> GuardrailAction:
    return evaluate_command(argv).action


def evaluate_command(argv: list[str]) -> GuardrailDecision:
    executable = Path(argv[0]).name.lower()
    lowered = [item.lower() for item in argv]
    command = " ".join(argv)
    if _EXTREME_DESTRUCTIVE_RE.search(command):
        return GuardrailDecision(
            action="deny",
            reason="Extreme destructive command is blocked.",
            guardrail="command.extreme_destructive",
        )
    if executable in {
        "rm",
        "curl",
        "wget",
        "ssh",
        "docker",
        "docker-compose",
        "powershell",
        "powershell.exe",
        "pwsh",
        "cmd",
        "cmd.exe",
        "sh",
        "bash",
    }:
        return GuardrailDecision(
            action="deny",
            reason="Shell or high-risk host command is blocked.",
            guardrail="command.high_risk_executable",
        )
    if _COMMAND_APPROVAL_RE.search(command):
        return GuardrailDecision(
            action="ask",
            reason="Command matches the trusted-local approval gate.",
            guardrail="command.regex_approval",
        )
    if (
        executable == "git"
        and len(lowered) > 1
        and lowered[1] in {"status", "diff", "grep"}
    ):
        return GuardrailDecision(
            action="allow",
            reason="Read-only Git command is allowed.",
            guardrail="command.readonly_git",
        )
    if executable == "pytest":
        return GuardrailDecision(
            action="allow",
            reason="Test command is allowed.",
            guardrail="command.validation",
        )
    if (
        executable == "python"
        and len(lowered) > 2
        and lowered[1:3]
        == [
            "-m",
            "unittest",
        ]
    ):
        return GuardrailDecision(
            action="allow",
            reason="Unit-test command is allowed.",
            guardrail="command.validation",
        )
    if executable == "ruff" and len(lowered) > 1 and lowered[1] == "check":
        return GuardrailDecision(
            action="allow",
            reason="Lint command is allowed.",
            guardrail="command.validation",
        )
    if executable == "mypy":
        return GuardrailDecision(
            action="allow",
            reason="Type-check command is allowed.",
            guardrail="command.validation",
        )
    if executable == "npm" and len(lowered) > 1:
        if lowered[1] == "publish":
            return GuardrailDecision(
                action="deny",
                reason="Package publishing is blocked.",
                guardrail="command.publish",
            )
        if lowered[1:] == ["run", "lint"] or lowered[1] == "test":
            return GuardrailDecision(
                action="allow",
                reason="Node validation command is allowed.",
                guardrail="command.validation",
            )
    if executable == "cargo" and len(lowered) > 1 and lowered[1] == "test":
        return GuardrailDecision(
            action="allow",
            reason="Rust test command is allowed.",
            guardrail="command.validation",
        )
    if executable == "go" and len(lowered) > 1 and lowered[1] == "test":
        return GuardrailDecision(
            action="allow",
            reason="Go test command is allowed.",
            guardrail="command.validation",
        )
    return GuardrailDecision(
        action="ask",
        reason=(
            "Command requires approval because it is not in the automatic "
            "trusted-local allowlist."
        ),
        guardrail="command.default_ask",
    )


def evaluate_patch_write(
    *,
    workspace: Path | None,
    patch: str,
) -> GuardrailDecision:
    if workspace is None:
        return GuardrailDecision(
            action="deny",
            reason="Patch execution has no workspace.",
            guardrail="path.workspace_required",
        )
    paths = parse_patch_paths(patch)
    if not paths:
        return GuardrailDecision(
            action="allow",
            reason="Patch paths are not yet parseable by guardrail policy.",
            guardrail="path.defer_to_tool_validation",
        )
    safe_root = write_safe_root()
    if safe_root is not None:
        root = safe_root.resolve()
        for relative in paths:
            target = (workspace.resolve() / relative).resolve()
            if target != root and not target.is_relative_to(root):
                return GuardrailDecision(
                    action="deny",
                    reason="Patch target is outside AWESOME_AGENT_WRITE_SAFE_ROOT.",
                    guardrail="path.write_safe_root",
                )
    if any(is_sensitive_path(path) for path in paths):
        return GuardrailDecision(
            action="ask",
            reason="Patch writes a sensitive local file path.",
            guardrail="path.sensitive_write",
        )
    return GuardrailDecision(
        action="allow",
        reason="Patch paths passed trusted-local write guardrails.",
        guardrail="path.patch_write",
    )


def evaluate_file_write(
    *,
    workspace: Path | None,
    paths: set[Path],
) -> GuardrailDecision:
    if workspace is None:
        return GuardrailDecision(
            action="deny",
            reason="File write has no workspace.",
            guardrail="path.workspace_required",
        )
    if not paths:
        return GuardrailDecision(
            action="deny",
            reason="File write has no target path.",
            guardrail="path.write_target_required",
        )
    for relative in paths:
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            return GuardrailDecision(
                action="deny",
                reason="File write target must stay inside the workspace.",
                guardrail="path.workspace_escape",
            )
    safe_root = write_safe_root()
    if safe_root is not None:
        root = safe_root.resolve()
        for relative in paths:
            target = (workspace.resolve() / relative).resolve()
            if target != root and not target.is_relative_to(root):
                return GuardrailDecision(
                    action="deny",
                    reason=(
                        "File write target is outside "
                        "AWESOME_AGENT_WRITE_SAFE_ROOT."
                    ),
                    guardrail="path.write_safe_root",
                )
    if any(is_sensitive_path(path) for path in paths):
        return GuardrailDecision(
            action="ask",
            reason="File write targets a sensitive local file path.",
            guardrail="path.sensitive_write",
        )
    return GuardrailDecision(
        action="allow",
        reason="File path passed trusted-local write guardrails.",
        guardrail="path.file_write",
    )


def parse_patch_paths(patch: str) -> set[Path]:
    paths: set[Path] = set()
    for line in patch.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        raw = line[4:].split("\t", maxsplit=1)[0].strip()
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            return set()
        if path.parts:
            paths.add(path)
    return paths


def is_sensitive_path(path: Path) -> bool:
    return (
        path.name.lower() in _SENSITIVE_NAMES
        or path.suffix.lower() in _SENSITIVE_SUFFIXES
    )


def write_safe_root() -> Path | None:
    value = os.environ.get("AWESOME_AGENT_WRITE_SAFE_ROOT")
    if not value:
        return None
    return Path(value)


def scrub_subprocess_environment(
    *,
    base: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    cleaned = {
        key: value
        for key, value in (base or os.environ).items()
        if not is_sensitive_environment_name(key)
    }
    for key, value in (overrides or {}).items():
        if not is_sensitive_environment_name(key):
            cleaned[key] = value
    return cleaned


def is_sensitive_environment_name(name: str) -> bool:
    return bool(_SENSITIVE_ENV_RE.search(name))
