"""Generated from contract-versions.json; do not edit by hand."""

from __future__ import annotations

from typing import Literal

type ApplicationLogVersion = Literal[1]
type EventEnvelopeVersion = Literal[1]
type ProtocolVersion = Literal[5]
type UserConfigVersion = Literal[2]
type WorkspaceConfigVersion = Literal[1]

APPLICATION_LOG_VERSION: ApplicationLogVersion = 1
APPLICATION_SCHEMA_CURRENT = 8
APPLICATION_SCHEMA_MIGRATION_FLOOR = 7
EVENT_ENVELOPE_VERSION: EventEnvelopeVersion = 1
PROTOCOL_VERSION: ProtocolVersion = 5
THREAD_EXPORT_JSON_SCHEMA = "awesome.thread-export"
THREAD_EXPORT_VERSION = 1
USER_CONFIG_CURRENT: UserConfigVersion = 2
USER_CONFIG_READABLE_VERSIONS = (1, 2)
WORKSPACE_CONFIG_CURRENT: WorkspaceConfigVersion = 1
WORKSPACE_CONFIG_READABLE_VERSIONS = (1,)
