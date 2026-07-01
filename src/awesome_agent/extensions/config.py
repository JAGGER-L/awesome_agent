from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, model_validator

from awesome_agent.extensions.catalog import empty_extension_catalog
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionConfigError,
    ExtensionSourceConfig,
    ExtensionSourceType,
    ExtensionTrustLevel,
)
from awesome_agent.extensions.service import ExtensionDiscoveryService
from awesome_agent.extensions.sources import ExtensionSourceFactory

PROJECT_CONFIG_FILENAME = "awesome-agent.yaml"
PROJECT_SKILLS_ROOT = "skills"
PROJECT_SKILLS_SOURCE_ID = "project-skills"


class ProjectSkillsConfig(BaseModel):
    auto_discover_project_skills: bool = True
    roots: list[Path] = Field(default_factory=list)


class ProjectExtensionsConfig(BaseModel):
    skills: ProjectSkillsConfig = Field(default_factory=ProjectSkillsConfig)
    sources: list[ExtensionSourceConfig] = Field(default_factory=list)


class ProjectExtensionConfig(BaseModel):
    version: int = 1
    extensions: ProjectExtensionsConfig = Field(default_factory=ProjectExtensionsConfig)

    @model_validator(mode="after")
    def _validate_version(self) -> ProjectExtensionConfig:
        if self.version != 1:
            raise ValueError("Only awesome-agent.yaml version 1 is supported.")
        return self

    @property
    def sources(self) -> list[ExtensionSourceConfig]:
        return self.extensions.sources


def load_project_extension_config(
    project_root: Path | None = None,
) -> ProjectExtensionConfig:
    root = (project_root or Path.cwd()).resolve()
    config_path = root / PROJECT_CONFIG_FILENAME
    raw = _load_yaml(config_path)
    config = ProjectExtensionConfig.model_validate(raw)
    sources = [
        *_project_skill_sources(root, config),
        *[_resolve_source_paths(root, source) for source in config.extensions.sources],
    ]
    config.extensions.sources = sources
    return config


async def build_project_extension_catalog(
    project_root: Path | None = None,
) -> ExtensionCatalog:
    config = load_project_extension_config(project_root)
    if not config.sources:
        return empty_extension_catalog()
    factory = ExtensionSourceFactory()
    sources = [factory.create(source) for source in config.sources]
    return await ExtensionDiscoveryService(sources).publish()


def build_project_extension_catalog_sync(
    project_root: Path | None = None,
) -> ExtensionCatalog:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(build_project_extension_catalog(project_root))
    result: ExtensionCatalog | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal error, result
        try:
            result = asyncio.run(build_project_extension_catalog(project_root))
        except BaseException as caught:
            error = caught

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    if result is None:
        raise ExtensionConfigError(
            "Project extension catalog build returned no result."
        )
    return result


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ExtensionConfigError("awesome-agent.yaml must contain a mapping.")
    return {str(key): value for key, value in loaded.items()}


def _project_skill_sources(
    root: Path,
    config: ProjectExtensionConfig,
) -> list[ExtensionSourceConfig]:
    roots: list[Path] = []
    default_root = root / PROJECT_SKILLS_ROOT
    if config.extensions.skills.auto_discover_project_skills and default_root.is_dir():
        roots.append(default_root)
    for configured_root in config.extensions.skills.roots:
        resolved = _resolve_path(root, configured_root)
        if resolved not in roots:
            roots.append(resolved)
    sources: list[ExtensionSourceConfig] = []
    for index, skill_root in enumerate(roots):
        source_id = (
            PROJECT_SKILLS_SOURCE_ID
            if index == 0
            else f"{PROJECT_SKILLS_SOURCE_ID}-{index + 1}"
        )
        sources.append(
            ExtensionSourceConfig(
                id=source_id,
                type=ExtensionSourceType.SKILL_DIRECTORY,
                trust=ExtensionTrustLevel.PROJECT,
                path=skill_root,
            )
        )
    return sources


def _resolve_source_paths(
    root: Path,
    source: ExtensionSourceConfig,
) -> ExtensionSourceConfig:
    if source.path is None:
        return source
    return source.model_copy(update={"path": _resolve_path(root, source.path)})


def _resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path
