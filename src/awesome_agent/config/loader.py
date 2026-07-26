from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, SecretStr, ValidationError
from yaml.nodes import MappingNode

from awesome_agent.config.credentials import (
    ProviderCredentialStatus,
    ProviderCredentialStatuses,
    UserSecretStoreError,
    read_provider_secret_values,
    resolve_provider_credential_statuses,
)
from awesome_agent.config.models import (
    CredentialSource,
    SecretStatus,
    UserConfigDocument,
    WorkspaceConfigDocument,
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

_SECRET_NAMES = (
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "MEM0_API_KEY",
)


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
    user = _read_yaml_document(
        sources.user_config,
        UserConfigDocument,
        source_label="user config",
    )
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
    secrets = _load_secrets(from_file, environment, provider_credentials)
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
    return _parse_yaml_document(text, model, source_label=source_label)


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
        return model.model_validate(cast(dict[str, object], loaded))
    except ValidationError as error:
        raise ConfigurationInvalid(
            "configuration_invalid",
            f"{source_label} contains an unknown or invalid value.",
        ) from error


def read_user_config_document(path: Path) -> UserConfigDocument:
    return _read_yaml_document(path, UserConfigDocument, source_label="user config")


def _load_secrets(
    from_file: Mapping[str, str | None],
    environ: Mapping[str, str],
    statuses: ProviderCredentialStatuses,
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

    return SecretValues(
        deepseek_api_key=value(_SECRET_NAMES[0], statuses.deepseek),
        moonshot_api_key=value(_SECRET_NAMES[1], statuses.kimi),
        mem0_api_key=value(_SECRET_NAMES[2], statuses.mem0),
    )
