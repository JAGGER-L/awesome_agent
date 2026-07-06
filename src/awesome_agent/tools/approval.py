from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from awesome_agent.domain.enums import ApprovalDecision, RiskLevel
from awesome_agent.tools.guardrails import (
    evaluate_command,
    evaluate_file_write,
    evaluate_patch_write,
)
from awesome_agent.tools.models import ApprovalOutcome, ToolInvocation, ToolSpec
from awesome_agent.tools.workspace import WorkspaceToolError, parse_bash_command


@dataclass(frozen=True, slots=True)
class CommandRule:
    pattern: re.Pattern[str]
    decision: ApprovalDecision
    reason: str

    @classmethod
    def build(
        cls, pattern: str, decision: ApprovalDecision, reason: str
    ) -> CommandRule:
        return cls(re.compile(pattern, re.IGNORECASE), decision, reason)


class ApprovalPolicy:
    def __init__(self, command_rules: list[CommandRule] | None = None) -> None:
        self._command_rules = command_rules or []

    def evaluate(self, spec: ToolSpec, invocation: ToolInvocation) -> ApprovalOutcome:
        if spec.name == "shell.execute":
            argv = invocation.arguments.get("argv")
            if not isinstance(argv, list) or not all(
                isinstance(item, str) for item in argv
            ):
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason="Shell command argv must be a list of strings.",
                )
            decision = evaluate_command(argv)
            if decision.action == "deny":
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason=decision.reason,
                )
            if decision.action == "ask":
                return ApprovalOutcome(
                    decision=ApprovalDecision.ASK,
                    reason=decision.reason,
                )
            return ApprovalOutcome(
                decision=ApprovalDecision.ALLOW,
                reason=decision.reason,
            )

        if spec.name == "Bash":
            command = invocation.arguments.get("command")
            if not isinstance(command, str):
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason="Bash command must be a string.",
                )
            try:
                argv = parse_bash_command(command)
            except WorkspaceToolError as error:
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason=str(error),
                )
            decision = evaluate_command(argv)
            if decision.action == "deny":
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason=decision.reason,
                )
            if decision.action == "ask":
                return ApprovalOutcome(
                    decision=ApprovalDecision.ASK,
                    reason=decision.reason,
                )
            return ApprovalOutcome(
                decision=ApprovalDecision.ALLOW,
                reason=decision.reason,
            )

        if spec.name == "repo.apply_patch":
            patch = invocation.arguments.get("patch")
            if not isinstance(patch, str):
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason="Patch must be a string.",
                )
            decision = evaluate_patch_write(
                workspace=invocation.workspace,
                patch=patch,
            )
            if decision.action == "deny":
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason=decision.reason,
                )
            if decision.action == "ask":
                return ApprovalOutcome(
                    decision=ApprovalDecision.ASK,
                    reason=decision.reason,
                )
            return ApprovalOutcome(
                decision=ApprovalDecision.ALLOW,
                reason=decision.reason,
            )

        if spec.name in {"WriteFile", "EditFile"}:
            path = invocation.arguments.get("path")
            if not isinstance(path, str):
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason="File path must be a string.",
                )
            relative = Path(path)
            decision = evaluate_file_write(
                workspace=invocation.workspace,
                paths={relative},
            )
            if decision.action == "deny":
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason=decision.reason,
                )
            if decision.action == "ask":
                return ApprovalOutcome(
                    decision=ApprovalDecision.ASK,
                    reason=decision.reason,
                )
            return ApprovalOutcome(
                decision=ApprovalDecision.ALLOW,
                reason=decision.reason,
            )

        command = str(invocation.arguments.get("command", ""))
        for rule in self._command_rules:
            if rule.pattern.search(command):
                return ApprovalOutcome(
                    decision=rule.decision,
                    reason=rule.reason,
                )

        if spec.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return ApprovalOutcome(
                decision=ApprovalDecision.ASK,
                reason=f"{spec.risk_level.value} risk tool requires approval.",
            )
        return ApprovalOutcome(
            decision=ApprovalDecision.ALLOW,
            reason="Tool risk is within automatic policy.",
        )


def default_command_policy() -> ApprovalPolicy:
    return ApprovalPolicy(
        [
            CommandRule.build(
                r"(^|\s)(rm\s+-rf|format(\.com)?|diskpart)(\s|$)",
                ApprovalDecision.DENY,
                "Destructive filesystem command is blocked.",
            ),
            CommandRule.build(
                r"(^|\s)(git\s+push|git\s+reset|Remove-Item)(\s|$)",
                ApprovalDecision.ASK,
                "Repository mutation requires explicit approval.",
            ),
        ]
    )
