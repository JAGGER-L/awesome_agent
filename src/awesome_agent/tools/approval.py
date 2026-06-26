from __future__ import annotations

import re
from dataclasses import dataclass

from awesome_agent.domain.enums import ApprovalDecision, RiskLevel
from awesome_agent.tools.models import ApprovalOutcome, ToolInvocation, ToolSpec
from awesome_agent.tools.shell import classify_command


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
            decision = classify_command(argv)
            if decision == "deny":
                return ApprovalOutcome(
                    decision=ApprovalDecision.DENY,
                    reason="Shell command is denied by policy.",
                )
            if decision == "ask":
                return ApprovalOutcome(
                    decision=ApprovalDecision.ASK,
                    reason="Shell command requires durable approval.",
                )
            return ApprovalOutcome(
                decision=ApprovalDecision.ALLOW,
                reason="Shell command is allowed by automatic policy.",
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
