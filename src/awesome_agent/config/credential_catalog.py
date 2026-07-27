from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

type CredentialId = Literal["deepseek", "kimi", "mem0", "tavily", "web_proxy"]
type CredentialValueKind = Literal["api_key", "proxy_url"]


@dataclass(frozen=True, slots=True)
class CredentialDescriptor:
    """Static metadata for one credential-bearing external service."""

    id: CredentialId
    label: str
    environment_variable: str
    value_kind: CredentialValueKind
    awesome_storage_supported: bool
    help_url: str | None = None


CREDENTIAL_DESCRIPTORS = (
    CredentialDescriptor(
        id="deepseek",
        label="DeepSeek",
        environment_variable="DEEPSEEK_API_KEY",
        value_kind="api_key",
        awesome_storage_supported=True,
        help_url="https://platform.deepseek.com/api_keys",
    ),
    CredentialDescriptor(
        id="kimi",
        label="Kimi",
        environment_variable="MOONSHOT_API_KEY",
        value_kind="api_key",
        awesome_storage_supported=True,
        help_url="https://platform.moonshot.cn/console/api-keys",
    ),
    CredentialDescriptor(
        id="mem0",
        label="Mem0 Cloud",
        environment_variable="MEM0_API_KEY",
        value_kind="api_key",
        awesome_storage_supported=True,
        help_url="https://app.mem0.ai/dashboard/api-keys",
    ),
    CredentialDescriptor(
        id="tavily",
        label="Tavily",
        environment_variable="TAVILY_API_KEY",
        value_kind="api_key",
        awesome_storage_supported=False,
        help_url="https://app.tavily.com/home",
    ),
    CredentialDescriptor(
        id="web_proxy",
        label="Web proxy",
        environment_variable="AWESOME_WEB_PROXY_URL",
        value_kind="proxy_url",
        awesome_storage_supported=True,
    ),
)

CREDENTIAL_CATALOG = MappingProxyType(
    {descriptor.id: descriptor for descriptor in CREDENTIAL_DESCRIPTORS}
)


def credential_descriptor(credential_id: CredentialId) -> CredentialDescriptor:
    return CREDENTIAL_CATALOG[credential_id]


def awesome_secret_names() -> frozenset[str]:
    return frozenset(
        descriptor.environment_variable
        for descriptor in CREDENTIAL_DESCRIPTORS
        if descriptor.awesome_storage_supported
    )
