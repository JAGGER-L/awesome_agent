from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from awesome_agent.extensions.catalog import empty_extension_catalog
from awesome_agent.extensions.config import (
    build_project_extension_catalog_sync,
    load_project_extension_config,
)
from awesome_agent.extensions.models import ExtensionCatalog, ExtensionSourceConfig
from awesome_agent.safety.redaction import redact_text


@dataclass(frozen=True, slots=True)
class StartupExtensionRuntime:
    catalog: ExtensionCatalog
    source_configs: tuple[ExtensionSourceConfig, ...]
    project_root: Path
    error: str | None = None


def build_startup_extension_runtime(project_root: Path) -> StartupExtensionRuntime:
    root = project_root.resolve()
    try:
        config = load_project_extension_config(root)
        catalog = (
            build_project_extension_catalog_sync(root)
            if config.sources
            else empty_extension_catalog()
        )
        return StartupExtensionRuntime(
            catalog=catalog,
            source_configs=tuple(config.sources),
            project_root=root,
        )
    except Exception as error:
        return StartupExtensionRuntime(
            catalog=empty_extension_catalog(),
            source_configs=(),
            project_root=root,
            error=redact_text(str(error)).text,
        )
