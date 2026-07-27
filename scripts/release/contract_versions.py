"""Parse the contract catalog and generate dependency-free runtime bindings."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CATALOG_PATH = Path("contract-versions.json")
PYTHON_BINDING_PATH = Path("src/awesome_agent/contract_versions.py")
TYPESCRIPT_BINDING_PATH = Path("tui/src/contract-versions.ts")
MAX_CONTRACT_CATALOG_BYTES = 16 * 1024
CONTRACT_KEYS = frozenset(
    {
        "application_log",
        "application_schema",
        "event_envelope",
        "headless_json",
        "protocol",
        "thread_export",
        "ui_preferences",
        "user_config",
        "workspace_config",
    }
)

_CATALOG_SCHEMA = "awesome.contract-versions"
_CATALOG_VERSION = 1
_MAX_VERSION = 1024
_SCHEMA_NAME = re.compile(r"awesome\.[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")


class ContractVersionsError(RuntimeError):
    """The contract catalog or one of its generated bindings is invalid."""


@dataclass(frozen=True, slots=True)
class ContractVersions:
    application_log_version: int
    application_schema_current: int
    application_schema_migration_floor: int
    event_envelope_version: int
    headless_json_schema: str
    headless_json_version: int
    protocol_version: int
    thread_export_json_schema: str
    thread_export_version: int
    ui_preferences_current: int
    ui_preferences_readable_versions: tuple[int, ...]
    user_config_current: int
    user_config_readable_versions: tuple[int, ...]
    workspace_config_current: int
    workspace_config_readable_versions: tuple[int, ...]

    def payload(self) -> dict[str, object]:
        return {
            "application_log": {"version": self.application_log_version},
            "application_schema": {
                "current": self.application_schema_current,
                "migration_floor": self.application_schema_migration_floor,
            },
            "event_envelope": {"version": self.event_envelope_version},
            "headless_json": {
                "schema": self.headless_json_schema,
                "version": self.headless_json_version,
            },
            "protocol": {"version": self.protocol_version},
            "thread_export": {
                "json_schema": self.thread_export_json_schema,
                "version": self.thread_export_version,
            },
            "ui_preferences": _readable_payload(
                self.ui_preferences_current,
                self.ui_preferences_readable_versions,
            ),
            "user_config": _readable_payload(
                self.user_config_current,
                self.user_config_readable_versions,
            ),
            "workspace_config": _readable_payload(
                self.workspace_config_current,
                self.workspace_config_readable_versions,
            ),
        }


def parse_contract_versions_payload(value: object) -> ContractVersions:
    payload = exact_object(value, CONTRACT_KEYS, "contract inventory")
    application = exact_object(
        payload["application_schema"],
        {"current", "migration_floor"},
        "Application schema",
    )
    application_current = version(
        application["current"], "Application schema current version"
    )
    application_floor = version(
        application["migration_floor"], "Application schema migration floor"
    )
    if application_floor > application_current:
        raise ContractVersionsError(
            "Application schema migration floor exceeds current version"
        )
    headless_schema, headless_version = _schema_version(
        payload["headless_json"], "schema", "headless JSON"
    )
    export_schema, export_version = _schema_version(
        payload["thread_export"], "json_schema", "Thread export JSON"
    )
    ui_current, ui_readable = _readable_versions(
        payload["ui_preferences"], "UI preferences"
    )
    user_current, user_readable = _readable_versions(
        payload["user_config"], "user config"
    )
    workspace_current, workspace_readable = _readable_versions(
        payload["workspace_config"], "workspace config"
    )
    return ContractVersions(
        application_log_version=_version_object(
            payload["application_log"], "Application log"
        ),
        application_schema_current=application_current,
        application_schema_migration_floor=application_floor,
        event_envelope_version=_version_object(
            payload["event_envelope"], "event envelope"
        ),
        headless_json_schema=headless_schema,
        headless_json_version=headless_version,
        protocol_version=_version_object(payload["protocol"], "Protocol"),
        thread_export_json_schema=export_schema,
        thread_export_version=export_version,
        ui_preferences_current=ui_current,
        ui_preferences_readable_versions=ui_readable,
        user_config_current=user_current,
        user_config_readable_versions=user_readable,
        workspace_config_current=workspace_current,
        workspace_config_readable_versions=workspace_readable,
    )


def parse_contract_versions(content: bytes) -> ContractVersions:
    payload = decode_json_object(
        content,
        maximum_bytes=MAX_CONTRACT_CATALOG_BYTES,
        label="contract catalog",
    )
    catalog = exact_object(
        payload,
        CONTRACT_KEYS | {"schema", "version"},
        "contract catalog",
    )
    if catalog["schema"] != _CATALOG_SCHEMA or catalog["version"] != (
        _CATALOG_VERSION
    ):
        raise ContractVersionsError("contract catalog identity is invalid")
    contracts = parse_contract_versions_payload(
        {key: catalog[key] for key in CONTRACT_KEYS}
    )
    if content != render_contract_versions(contracts):
        raise ContractVersionsError("contract catalog is not canonically encoded")
    return contracts


def load_contract_versions(root: Path) -> ContractVersions:
    path = root / CATALOG_PATH
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_CONTRACT_CATALOG_BYTES + 1)
    except OSError as error:
        raise ContractVersionsError("contract catalog is unavailable") from error
    if len(content) > MAX_CONTRACT_CATALOG_BYTES:
        raise ContractVersionsError("contract catalog exceeds its size limit")
    return parse_contract_versions(content)


def render_contract_versions(contracts: ContractVersions) -> bytes:
    return render_json_object(
        {
            **contracts.payload(),
            "schema": _CATALOG_SCHEMA,
            "version": _CATALOG_VERSION,
        }
    )


def render_python_binding(contracts: ContractVersions) -> bytes:
    return f'''"""Generated from contract-versions.json; do not edit by hand."""

from __future__ import annotations

from typing import Literal

type ApplicationLogVersion = Literal[{contracts.application_log_version}]
type EventEnvelopeVersion = Literal[{contracts.event_envelope_version}]
type ProtocolVersion = Literal[{contracts.protocol_version}]
type UserConfigVersion = Literal[{contracts.user_config_current}]
type WorkspaceConfigVersion = Literal[{contracts.workspace_config_current}]

APPLICATION_LOG_VERSION: ApplicationLogVersion = {contracts.application_log_version}
APPLICATION_SCHEMA_CURRENT = {contracts.application_schema_current}
APPLICATION_SCHEMA_MIGRATION_FLOOR = {contracts.application_schema_migration_floor}
EVENT_ENVELOPE_VERSION: EventEnvelopeVersion = {contracts.event_envelope_version}
PROTOCOL_VERSION: ProtocolVersion = {contracts.protocol_version}
THREAD_EXPORT_JSON_SCHEMA = "{contracts.thread_export_json_schema}"
THREAD_EXPORT_VERSION = {contracts.thread_export_version}
USER_CONFIG_CURRENT: UserConfigVersion = {contracts.user_config_current}
USER_CONFIG_READABLE_VERSIONS = {contracts.user_config_readable_versions!r}
WORKSPACE_CONFIG_CURRENT: WorkspaceConfigVersion = {contracts.workspace_config_current}
WORKSPACE_CONFIG_READABLE_VERSIONS = {contracts.workspace_config_readable_versions!r}
'''.encode("ascii")


def render_typescript_binding(contracts: ContractVersions) -> bytes:
    values: tuple[tuple[str, int | str | tuple[int, ...]], ...] = (
        ("EVENT_ENVELOPE_VERSION", contracts.event_envelope_version),
        ("HEADLESS_JSON_SCHEMA", contracts.headless_json_schema),
        ("HEADLESS_JSON_VERSION", contracts.headless_json_version),
        ("PROTOCOL_VERSION", contracts.protocol_version),
        ("UI_PREFERENCES_CURRENT", contracts.ui_preferences_current),
        (
            "UI_PREFERENCES_READABLE_VERSIONS",
            contracts.ui_preferences_readable_versions,
        ),
    )
    declarations = "\n".join(
        f"export const {name} = {json.dumps(value)} as const;"
        for name, value in values
    )
    return (
        "// Generated from contract-versions.json; do not edit by hand.\n\n"
        f"{declarations}\n"
    ).encode("ascii")


def check_generated_bindings(root: Path, contracts: ContractVersions) -> None:
    for relative, expected in _generated_bindings(contracts):
        try:
            with (root / relative).open("rb") as stream:
                observed = stream.read(len(expected) + 1)
        except OSError as error:
            raise ContractVersionsError(
                f"generated binding is unavailable: {relative.as_posix()}"
            ) from error
        if observed != expected:
            raise ContractVersionsError(
                f"generated binding is stale: {relative.as_posix()}"
            )


def write_generated_bindings(root: Path, contracts: ContractVersions) -> None:
    for relative, content in _generated_bindings(contracts):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.tmp"
        )
        try:
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def decode_json_object(
    content: bytes,
    *,
    maximum_bytes: int,
    label: str,
) -> dict[str, object]:
    if len(content) > maximum_bytes:
        raise ContractVersionsError(f"{label} is too large")
    try:
        loaded = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise ContractVersionsError(f"{label} JSON is invalid") from error
    if not isinstance(loaded, dict):
        raise ContractVersionsError(f"{label} JSON is not an object")
    return loaded


def render_json_object(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def exact_object(
    value: object,
    keys: set[str] | frozenset[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractVersionsError(f"{label} fields are invalid")
    return value


def version(value: object, label: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_VERSION:
        raise ContractVersionsError(f"{label} is invalid")
    return value


def _version_object(value: object, label: str) -> int:
    payload = exact_object(value, {"version"}, label)
    return version(payload["version"], f"{label} version")


def _schema_version(value: object, schema_key: str, label: str) -> tuple[str, int]:
    payload = exact_object(value, {schema_key, "version"}, label)
    schema = payload[schema_key]
    if not isinstance(schema, str) or _SCHEMA_NAME.fullmatch(schema) is None:
        raise ContractVersionsError(f"{label} schema is invalid")
    return schema, version(payload["version"], f"{label} version")


def _readable_versions(value: object, label: str) -> tuple[int, tuple[int, ...]]:
    payload = exact_object(value, {"current", "readable_versions"}, label)
    current = version(payload["current"], f"{label} current version")
    raw = payload["readable_versions"]
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_VERSION:
        raise ContractVersionsError(f"{label} readable versions are invalid")
    readable = tuple(version(item, f"{label} readable version") for item in raw)
    if (
        readable != tuple(sorted(set(readable)))
        or current not in readable
        or readable[-1] != current
    ):
        raise ContractVersionsError(f"{label} readable versions are invalid")
    return current, readable


def _readable_payload(current: int, readable: tuple[int, ...]) -> dict[str, object]:
    return {"current": current, "readable_versions": list(readable)}


def _generated_bindings(contracts: ContractVersions) -> tuple[tuple[Path, bytes], ...]:
    return (
        (PYTHON_BINDING_PATH, render_python_binding(contracts)),
        (TYPESCRIPT_BINDING_PATH, render_typescript_binding(contracts)),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-finite JSON number")


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[2])
    options = parser.parse_args(arguments)
    root = options.root.resolve()
    try:
        contracts = load_contract_versions(root)
        if options.write:
            write_generated_bindings(root, contracts)
        else:
            check_generated_bindings(root, contracts)
    except ContractVersionsError as error:
        print(f"contract version check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
