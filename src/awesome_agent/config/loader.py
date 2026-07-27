from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from yaml.nodes import MappingNode

from awesome_agent.config.credential_catalog import credential_descriptor
from awesome_agent.config.credentials import (
    ProviderCredentialStatus,
    ProviderCredentialStatuses,
    UserSecretStoreError,
    read_provider_secret_values,
    resolve_provider_credential_statuses,
)
from awesome_agent.config.models import (
    CredentialSelectionConfig,
    CredentialSource,
    MemoryConfig,
    ProviderConfig,
    SecretStatus,
    SkillConfig,
    UserConfigDocument,
    UserMcpServerConfig,
    WorkspaceConfigDocument,
)
from awesome_agent.contract_versions import (
    USER_CONFIG_CURRENT,
    USER_CONFIG_READABLE_VERSIONS,
)
from awesome_agent.core.safe_files import (
    FileChangedError,
    FileTooLargeError,
    PinnedPlainDirectory,
    UnsafePathError,
    lexical_absolute,
)
from awesome_agent.paths import AwesomePaths

WORKSPACE_CONFIG_MAX_BYTES = 1024 * 1024


class _UserCredentialSelectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    deepseek: CredentialSource | None = Field(default=None, strict=False)
    kimi: CredentialSource | None = Field(default=None, strict=False)
    mem0: CredentialSource | None = Field(default=None, strict=False)

    @field_validator("deepseek", "kimi", "mem0", mode="before")
    @classmethod
    def validate_credential_source_type(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("credential source must be a string or null")
        return value


class _UserBudgetConfigV1(BaseModel):
    """The exact v1 budget surface, before Web request budgets existed."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model_calls: int = Field(default=32, ge=1, le=256)
    tool_calls: int = Field(default=64, ge=1, le=512)
    provider_retries: int = Field(default=2, ge=0, le=6)
    compressions: int = Field(default=2, ge=0, le=10)
    active_execution_seconds: int = Field(default=1_800, ge=1, le=21_600)
    total_context_tokens: int = Field(default=262_144, ge=1)


class _UserConfigDocumentV1(BaseModel):
    """Closed legacy schema used only as the input to the v1 -> v2 migration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    credentials: _UserCredentialSelectionV1 = Field(
        default_factory=_UserCredentialSelectionV1
    )
    budgets: _UserBudgetConfigV1 = Field(default_factory=_UserBudgetConfigV1)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    mcp_servers: tuple[UserMcpServerConfig, ...] = Field(default=(), strict=False)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value


class ConfigurationInvalid(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ConfigSourcePaths:
    user_config: Path
    user_env: Path
    workspace_config: Path


@dataclass(frozen=True, slots=True)
class SecretValues:
    deepseek_api_key: SecretStr | None = None
    moonshot_api_key: SecretStr | None = None
    mem0_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    web_proxy_url: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class LoadedConfigSources:
    user: UserConfigDocument
    workspace: WorkspaceConfigDocument | None
    secrets: SecretValues
    secret_status: SecretStatus
    provider_credentials: ProviderCredentialStatuses


class _DuplicateConfigKey(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise _DuplicateConfigKey(str(key))
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def config_source_paths(
    *,
    paths: AwesomePaths,
    workspace: Path,
) -> ConfigSourcePaths:
    return ConfigSourcePaths(
        user_config=paths.config_file,
        user_env=paths.env_file,
        workspace_config=paths.workspace_config_file(workspace),
    )


def load_config_sources(
    *,
    paths: AwesomePaths,
    workspace: Path,
    workspace_trusted: bool,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfigSources:
    sources = config_source_paths(paths=paths, workspace=workspace)
    user = read_user_config_document(sources.user_config)
    workspace_document: WorkspaceConfigDocument | None = None
    if workspace_trusted:
        workspace_document = _read_workspace_yaml_document(
            workspace,
            WorkspaceConfigDocument,
            source_label="workspace config",
        )
    environment = os.environ if environ is None else environ
    try:
        from_file = read_provider_secret_values(sources.user_env)
    except UserSecretStoreError as error:
        raise ConfigurationInvalid(
            "provider_secret_file_unsafe",
            "user Provider secret file cannot be read safely.",
        ) from error
    provider_credentials = resolve_provider_credential_statuses(
        sources.user_env,
        environment,
        user.credentials,
        from_file=from_file,
    )
    secrets = _load_secrets(
        from_file,
        environment,
        provider_credentials,
        user.credentials,
    )
    status = SecretStatus(
        deepseek_api_key=secrets.deepseek_api_key is not None,
        moonshot_api_key=secrets.moonshot_api_key is not None,
        mem0_api_key=secrets.mem0_api_key is not None,
    )
    return LoadedConfigSources(
        user=user,
        workspace=workspace_document,
        secrets=secrets,
        secret_status=status,
        provider_credentials=provider_credentials,
    )


def _read_yaml_document[DocumentT: BaseModel](
    path: Path,
    model: type[DocumentT],
    *,
    source_label: str,
    transform: Callable[[dict[str, object]], dict[str, object]] | None = None,
) -> DocumentT:
    if not path.is_file():
        return model()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} could not be parsed.",
        ) from error
    return _parse_yaml_document(
        text,
        model,
        source_label=source_label,
        transform=transform,
    )


def _read_workspace_yaml_document[DocumentT: BaseModel](
    workspace: Path,
    model: type[DocumentT],
    *,
    source_label: str,
) -> DocumentT:
    root = lexical_absolute(workspace)
    try:
        with PinnedPlainDirectory(root, root) as pinned:
            bounded = pinned.read_file(
                Path(".awesome") / "config.yaml",
                max_bytes=WORKSPACE_CONFIG_MAX_BYTES,
            )
    except FileNotFoundError:
        return model()
    except (
        FileChangedError,
        FileTooLargeError,
        NotADirectoryError,
        OSError,
        UnsafePathError,
    ) as error:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} could not be read safely.",
        ) from error
    if b"\x00" in bounded.data:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} could not be read safely.",
        )
    try:
        text = bounded.data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} could not be parsed.",
        ) from error
    return _parse_yaml_document(text, model, source_label=source_label)


def _parse_yaml_document[DocumentT: BaseModel](
    text: str,
    model: type[DocumentT],
    *,
    source_label: str,
    transform: Callable[[dict[str, object]], dict[str, object]] | None = None,
) -> DocumentT:
    try:
        loaded = yaml.load(text, Loader=_UniqueKeyLoader)
    except _DuplicateConfigKey as error:
        raise ConfigurationInvalid(
            "duplicate_config_key",
            f"{source_label} contains a duplicate key: {error}.",
        ) from error
    except yaml.YAMLError as error:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} could not be parsed.",
        ) from error
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} must contain a mapping.",
        )
    try:
        document = cast(dict[str, object], loaded)
        if transform is not None:
            document = transform(document)
        return model.model_validate(document)
    except ValidationError as error:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} contains an unknown or invalid value.",
        ) from error


def read_user_config_document(path: Path) -> UserConfigDocument:
    return _read_yaml_document(
        path,
        UserConfigDocument,
        source_label="user config",
        transform=_upgrade_user_config,
    )


def _load_secrets(
    from_file: Mapping[str, str | None],
    environ: Mapping[str, str],
    statuses: ProviderCredentialStatuses,
    selections: CredentialSelectionConfig,
) -> SecretValues:
    def value(name: str, status: ProviderCredentialStatus) -> SecretStr | None:
        raw: str | None = None
        if status.selected_source is CredentialSource.ENVIRONMENT:
            raw = environ.get(name)
        elif status.selected_source is CredentialSource.AWESOME:
            file_value = from_file.get(name)
            raw = file_value if isinstance(file_value, str) else None
        if raw is None or not raw.strip():
            return None
        return SecretStr(raw)

    def selected_value(
        credential_id: Literal["tavily", "web_proxy"],
        selected: CredentialSource | None,
    ) -> SecretStr | None:
        name = credential_descriptor(credential_id).environment_variable
        raw: str | None = None
        if selected is CredentialSource.ENVIRONMENT:
            raw = environ.get(name)
        elif selected is CredentialSource.AWESOME:
            candidate = from_file.get(name)
            raw = candidate if isinstance(candidate, str) else None
        elif selected is None:
            candidate = environ.get(name)
            if candidate and candidate.strip():
                raw = candidate
            else:
                stored = from_file.get(name)
                raw = stored if isinstance(stored, str) else None
        if raw is None or not raw.strip():
            return None
        return SecretStr(raw)

    return SecretValues(
        deepseek_api_key=value(
            credential_descriptor("deepseek").environment_variable,
            statuses.deepseek,
        ),
        moonshot_api_key=value(
            credential_descriptor("kimi").environment_variable,
            statuses.kimi,
        ),
        mem0_api_key=value(
            credential_descriptor("mem0").environment_variable,
            statuses.mem0,
        ),
        tavily_api_key=selected_value("tavily", selections.tavily),
        web_proxy_url=selected_value("web_proxy", selections.web_proxy),
    )


def _upgrade_user_config_v1_to_v2(
    document: dict[str, object],
) -> dict[str, object]:
    legacy = _UserConfigDocumentV1.model_validate(document)
    upgraded = legacy.model_dump(mode="python")
    upgraded["version"] = 2
    credentials = upgraded.get("credentials")
    if credentials is None:
        upgraded["credentials"] = {
            "tavily": CredentialSource.ENVIRONMENT.value,
            "web_proxy": None,
        }
    elif isinstance(credentials, dict):
        updated_credentials = dict(credentials)
        updated_credentials.setdefault("tavily", CredentialSource.ENVIRONMENT.value)
        updated_credentials.setdefault("web_proxy", None)
        upgraded["credentials"] = updated_credentials
    budgets = upgraded.get("budgets")
    if budgets is None:
        upgraded["budgets"] = {"web_requests": 8}
    elif isinstance(budgets, dict):
        updated_budgets = dict(budgets)
        updated_budgets.setdefault("web_requests", 8)
        upgraded["budgets"] = updated_budgets
    upgraded.setdefault(
        "web",
        {
            "enabled": False,
            "provider": "tavily",
            "blocked_domains": [],
        },
    )
    return upgraded


_USER_CONFIG_MIGRATIONS: Mapping[
    int,
    Callable[[dict[str, object]], dict[str, object]],
] = {1: _upgrade_user_config_v1_to_v2}


def _upgrade_user_config(document: dict[str, object]) -> dict[str, object]:
    version = document.get("version", 1)
    if type(version) is not int or version not in USER_CONFIG_READABLE_VERSIONS:
        return document
    upgraded = document
    while version < USER_CONFIG_CURRENT:
        migration = _USER_CONFIG_MIGRATIONS.get(version)
        if migration is None:
            return upgraded
        upgraded = migration(upgraded)
        next_version = upgraded.get("version")
        if type(next_version) is not int or next_version != version + 1:
            raise RuntimeError("user config migration did not advance by one version")
        version = next_version
    return upgraded
