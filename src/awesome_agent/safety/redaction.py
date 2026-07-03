from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)

_Replacement = str | Callable[[re.Match[str]], str | tuple[str, str]]


class RedactionReport(BaseModel):
    applied: bool = False
    counts: dict[str, int] = Field(default_factory=dict)

    def merge(self, other: RedactionReport) -> RedactionReport:
        counts = Counter(self.counts)
        counts.update(other.counts)
        return RedactionReport(
            applied=self.applied or other.applied,
            counts=dict(counts),
        )


class RedactionResult(BaseModel):
    text: str
    redacted: bool = False
    counts: dict[str, int] = Field(default_factory=dict)

    @property
    def report(self) -> RedactionReport:
        return RedactionReport(applied=self.redacted, counts=self.counts)


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(authorization)\s*:\s*(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
_API_KEY_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET[_-]?KEY))\s*=\s*([^\s'\"\n]+)"
)
_CREDENTIAL_ASSIGN_RE = re.compile(
    r"(?i)(\b(?:token|password|secret|credential|auth|dsn)\b\s*[:=]\s*)"
    r"([A-Za-z0-9._~+/=@:-]{4,})"
)
_JSON_CREDENTIAL_RE = re.compile(
    r"(?i)([\"'](?:api_key|token|password|secret|credential|auth|dsn)[\"']"
    r"\s*:\s*[\"'])([^\"']{4,})([\"'])"
)
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")
_ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_URL_PASSWORD_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@[^ \n\t]+)",
    re.IGNORECASE,
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|signature|x-amz-signature|access_token|api_key)=)([^&#\s]+)"
)


def redact_text(text: str) -> RedactionResult:
    counts: Counter[str] = Counter()
    redacted = text

    redacted = _replace(
        redacted, _PRIVATE_KEY_RE, "[REDACTED:private_key]", counts, "private_key"
    )
    redacted = _replace(
        redacted,
        _AUTH_HEADER_RE,
        r"\1: [REDACTED:auth_header]",
        counts,
        "auth_header",
    )
    redacted = _replace(
        redacted,
        _API_KEY_ASSIGN_RE,
        r"\1=[REDACTED:api_key]",
        counts,
        "api_key",
    )
    redacted = _replace(
        redacted,
        _JSON_CREDENTIAL_RE,
        r"\1[REDACTED:token]\3",
        counts,
        "token",
    )
    redacted = _replace(redacted, _CREDENTIAL_ASSIGN_RE, _typed_assignment, counts)
    redacted = _replace(redacted, _ANTHROPIC_KEY_RE, "[REDACTED:api_key]", counts)
    redacted = _replace(redacted, _OPENAI_KEY_RE, "[REDACTED:api_key]", counts)
    redacted = _replace(redacted, _JWT_RE, "[REDACTED:token]", counts)
    redacted = _replace(
        redacted, _URL_PASSWORD_RE, r"\1[REDACTED:password]\3", counts, "password"
    )
    redacted = _replace(
        redacted, _QUERY_SECRET_RE, r"\1[REDACTED:token]", counts, "token"
    )

    return RedactionResult(
        text=redacted,
        redacted=bool(counts),
        counts=dict(counts),
    )


def redact_value(value: Any) -> tuple[Any, RedactionReport]:
    if isinstance(value, str):
        result = redact_text(value)
        return result.text, result.report
    if isinstance(value, list):
        items: list[Any] = []
        report = RedactionReport()
        for item in value:
            redacted_item, item_report = redact_value(item)
            items.append(redacted_item)
            report = report.merge(item_report)
        return items, report
    if isinstance(value, tuple):
        items = []
        report = RedactionReport()
        for item in value:
            redacted_item, item_report = redact_value(item)
            items.append(redacted_item)
            report = report.merge(item_report)
        return tuple(items), report
    if isinstance(value, Mapping):
        if "guardrails" in value and isinstance(value["guardrails"], Mapping):
            return _redact_guardrail_payload(dict(value))
        output: dict[str, Any] = {}
        report = RedactionReport()
        for key, item in value.items():
            redacted_item, item_report = redact_value(item)
            output[str(key)] = redacted_item
            report = report.merge(item_report)
        return output, report
    return value, RedactionReport()


def redaction_metadata(report: RedactionReport) -> dict[str, object]:
    return {"applied": report.applied, "counts": dict(report.counts)}


def redact_runtime_payload(payload: Mapping[str, object]) -> dict[str, object]:
    redacted, report = redact_value(payload)
    output = dict(redacted) if isinstance(redacted, Mapping) else {}
    if report.applied:
        output["redaction"] = redaction_metadata(report)
    return output


def redact_model_message(message: ModelMessage) -> ModelMessage:
    if isinstance(message, SystemMessage):
        return message.model_copy(update={"content": redact_text(message.content).text})
    if isinstance(message, UserMessage):
        return message.model_copy(update={"content": redact_text(message.content).text})
    if isinstance(message, AssistantMessage):
        return message.model_copy(update={"content": redact_text(message.content).text})
    if isinstance(message, ToolResultMessage):
        return message.model_copy(update={"content": redact_text(message.content).text})
    return message


def redact_model_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    return [redact_model_message(message) for message in messages]


def _redact_guardrail_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], RedactionReport]:
    guardrails = dict(payload["guardrails"])
    assessments = []
    report = RedactionReport()
    for raw in guardrails.get("assessments", []):
        if not isinstance(raw, Mapping):
            assessments.append(raw)
            continue
        assessment = dict(raw)
        reason, reason_report = redact_value(assessment.get("reason", ""))
        assessment["reason"] = reason
        report = report.merge(reason_report)
        targets = []
        for target_raw in assessment.get("targets", []):
            if not isinstance(target_raw, Mapping):
                targets.append(target_raw)
                continue
            target = dict(target_raw)
            value, value_report = redact_value(target.get("value", ""))
            target["value"] = value
            targets.append(target)
            report = report.merge(value_report)
        assessment["targets"] = targets
        assessments.append(assessment)
    guardrails["assessments"] = assessments
    payload["guardrails"] = guardrails
    for key, item in list(payload.items()):
        if key == "guardrails":
            continue
        redacted_item, item_report = redact_value(item)
        payload[key] = redacted_item
        report = report.merge(item_report)
    return payload, report


def _replace(
    text: str,
    pattern: re.Pattern[str],
    replacement: _Replacement,
    counts: Counter[str],
    kind: str | None = None,
) -> str:
    def repl(match: re.Match[str]) -> str:
        if callable(replacement):
            value = replacement(match)
            if isinstance(value, tuple):
                rendered, replacement_kind = value
                counts[replacement_kind] += 1
                return rendered
            if kind is not None:
                counts[kind] += 1
            return value
        if kind is None:
            raise ValueError("A static redaction replacement requires a kind.")
        counts[kind] += 1
        return match.expand(replacement)

    return pattern.sub(repl, text)


def _typed_assignment(match: re.Match[str]) -> tuple[str, str]:
    label = match.group(1)
    lower = label.lower()
    if "password" in lower:
        placeholder = "[REDACTED:password]"
        kind = "password"
    elif "api" in lower and "key" in lower:
        placeholder = "[REDACTED:api_key]"
        kind = "api_key"
    else:
        placeholder = "[REDACTED:token]"
        kind = "token"
    return f"{label}{placeholder}", kind


class RedactingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage()).text
        record.args = ()
        return True


def install_redacting_log_filter(target: Any) -> None:
    filters = getattr(target, "filters", None)
    add_filter = getattr(target, "addFilter", None)
    if filters is None or not callable(add_filter):
        return
    if any(isinstance(item, RedactingLogFilter) for item in filters):
        return
    add_filter(RedactingLogFilter())
