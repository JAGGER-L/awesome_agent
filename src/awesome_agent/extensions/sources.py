from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from awesome_agent.extensions.models import (
    ExtensionConfigError,
    ExtensionDiscoverySnapshot,
    ExtensionHealthSnapshot,
    ExtensionHealthStatus,
    ExtensionSkillInventoryItem,
    ExtensionSourceConfig,
    ExtensionSourceConfigInput,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
    ExtensionToolInventoryItem,
)


class ExtensionSource(Protocol):
    @property
    def source_id(self) -> str:
        """Stable source identifier used for lifecycle reporting."""

    async def discover(self) -> ExtensionDiscoverySnapshot:
        """Return current source inventory without exposing or executing tools."""


class StaticExtensionSource:
    def __init__(self, config: ExtensionSourceConfig) -> None:
        self._config = config

    @property
    def source_id(self) -> str:
        return self._config.id

    async def discover(self) -> ExtensionDiscoverySnapshot:
        source = ExtensionSourceSnapshot(
            id=self._config.id,
            type=self._config.type,
            trust=self._config.trust,
            health=ExtensionHealthSnapshot(status=ExtensionHealthStatus.HEALTHY),
        )
        return ExtensionDiscoverySnapshot(
            source=source,
            tools=[
                ExtensionToolInventoryItem(
                    name=_qualified_tool_name(self._config.id, tool.name),
                    source_id=self._config.id,
                    description=tool.description,
                    risk_level=tool.risk_level,
                    required_capabilities=set(tool.required_capabilities),
                    input_schema=tool.input_schema,
                )
                for tool in self._config.tools
            ],
            skills=[
                ExtensionSkillInventoryItem(
                    id=skill.id,
                    source_id=self._config.id,
                    version=skill.version,
                    requested_tools=[
                        _qualified_tool_name(self._config.id, tool_name)
                        for tool_name in skill.requested_tools
                    ],
                    required_capabilities=set(skill.required_capabilities),
                )
                for skill in self._config.skills
            ],
        )


class ExtensionSourceFactory:
    def create(self, config: ExtensionSourceConfigInput) -> ExtensionSource:
        try:
            parsed = (
                config
                if isinstance(config, ExtensionSourceConfig)
                else ExtensionSourceConfig.model_validate(config)
            )
        except ValidationError as error:
            raise ExtensionConfigError(str(error)) from error
        if parsed.type is ExtensionSourceType.STATIC:
            return StaticExtensionSource(parsed)
        raise ExtensionConfigError(f"Unsupported extension source type: {parsed.type}")


def _qualified_tool_name(source_id: str, tool_name: str) -> str:
    return f"extension.{source_id}.{tool_name}"
