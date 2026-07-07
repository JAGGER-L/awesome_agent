from __future__ import annotations

import json
from pathlib import Path

from awesome_agent.modeling import ToolCall, ToolResultMessage

RESULT_SUMMARY_LIMIT = 500
ARGUMENT_SUMMARY_LIMIT = 500


def tool_event_payload(
    *,
    tool_name: str,
    call: ToolCall | None,
    result: ToolResultMessage,
    workspace: Path | None,
    duration_ms: int | None = None,
) -> dict[str, object]:
    result_payload = _json_object(result.content)
    error_payload = _error_payload(result.content) if result.is_error else {}
    arguments = _json_object(call.arguments_json) if call is not None else {}
    operation_status = _operation_status(tool_name, result, result_payload)
    status = (
        "failed" if result.is_error or operation_status == "failed" else "completed"
    )
    changed_files = _changed_files_from_payload(result_payload)
    payload: dict[str, object] = {
        "tool": tool_name,
        "call_id": result.call_id,
        "status": status,
        "invocation_status": "completed",
        "operation_status": operation_status,
        "result_summary": _result_summary(result, result_payload, error_payload),
        "changed_files": changed_files,
    }
    if call is not None:
        payload["arguments_summary"] = _bounded_json(arguments or call.arguments_json)
    if workspace is not None:
        payload["workspace"] = str(workspace)
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    _copy_fields(
        payload,
        error_payload,
        ("requested_path", "resolved_path", "error", "hint"),
    )
    _copy_fields(
        payload,
        result_payload,
        ("path", "paths", "command", "exit_code", "stderr", "sandbox"),
    )
    requested_path = _requested_path(arguments)
    if requested_path and "requested_path" not in payload:
        payload["requested_path"] = requested_path
    change_stats = result_payload.get("change_stats")
    if isinstance(change_stats, dict):
        payload["change_stats"] = change_stats
    return payload


def _operation_status(
    tool_name: str,
    result: ToolResultMessage,
    result_payload: dict[str, object],
) -> str:
    status = result_payload.get("status")
    if isinstance(status, str):
        return status
    if result.is_error:
        return "failed"
    if tool_name == "Bash":
        exit_code = result_payload.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return "failed"
    return "completed"


def _result_summary(
    result: ToolResultMessage,
    result_payload: dict[str, object],
    error_payload: dict[str, object],
) -> str:
    error = error_payload.get("error")
    if isinstance(error, str):
        return _bound(error, RESULT_SUMMARY_LIMIT)
    status = result_payload.get("status")
    if isinstance(status, str):
        return _bound(status, RESULT_SUMMARY_LIMIT)
    return _bound(result.content, RESULT_SUMMARY_LIMIT)


def _requested_path(arguments: dict[str, object]) -> str | None:
    value = arguments.get("path")
    return value if isinstance(value, str) else None


def _copy_fields(
    target: dict[str, object],
    source: dict[str, object],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        value = source.get(key)
        if value is not None:
            target[key] = value


def _error_payload(content: str) -> dict[str, object]:
    _prefix, separator, remainder = content.partition(": ")
    if not separator:
        return {}
    return _json_object(remainder)


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _changed_files_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return []
    preimage_hashes = payload.get("preimage_hashes")
    postimage_hashes = payload.get("postimage_hashes")
    if not isinstance(preimage_hashes, dict) and not isinstance(postimage_hashes, dict):
        return []
    preimages = preimage_hashes if isinstance(preimage_hashes, dict) else {}
    postimages = postimage_hashes if isinstance(postimage_hashes, dict) else {}
    changed_files: list[dict[str, object]] = []
    for item in paths:
        if not isinstance(item, str):
            continue
        before = preimages.get(item)
        after = postimages.get(item)
        if after == "<missing>":
            status = "deleted"
        elif before == "<missing>":
            status = "created"
        else:
            status = "updated"
        changed_files.append({"path": item, "status": status})
    return changed_files


def _bounded_json(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _bound(text, ARGUMENT_SUMMARY_LIMIT)


def _bound(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 15)] + " ... [truncated]"
