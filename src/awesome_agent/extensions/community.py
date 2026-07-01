from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field, ValidationError

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionConfigError,
    ExtensionDiscoverySnapshot,
    ExtensionHealthSnapshot,
    ExtensionHealthStatus,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
    ExtensionToolInventoryItem,
    ExtensionTrustLevel,
)
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ProgressCallback, ToolRegistry

_MANIFEST_FILENAMES = (
    "awesome-agent-community.json",
    "awesome-agent-community.yaml",
    "awesome-agent-community.yml",
)
_HANDLER_TYPE_SUBPROCESS_JSON = "subprocess_json"


class CommunityToolHandlerConfig(BaseModel):
    type: str
    command: list[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)


class CommunityToolDefinition(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    description: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    required_capabilities: set[str] = Field(default_factory=set)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    handler: CommunityToolHandlerConfig


class CommunityToolPackageManifest(BaseModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    version: str = Field(min_length=1)
    trust: ExtensionTrustLevel = ExtensionTrustLevel.USER
    tools: list[CommunityToolDefinition] = Field(min_length=1)


class CommunityToolPackageSource:
    def __init__(
        self,
        *,
        root: Path,
        allowlisted_roots: Iterable[Path],
    ) -> None:
        self._root = _ensure_allowlisted_root(root, allowlisted_roots)
        self._manifest: CommunityToolPackageManifest | None = None

    @property
    def source_id(self) -> str:
        return (
            _community_source_id(self._manifest.id)
            if self._manifest is not None
            else f"community.{self._root.name}"
        )

    @property
    def root(self) -> Path:
        return self._root

    async def discover(self) -> ExtensionDiscoverySnapshot:
        return await self.discover_package(self._root)

    async def discover_package(self, package: Path) -> ExtensionDiscoverySnapshot:
        package_root = _ensure_allowlisted_root(package, [self._root])
        manifest = await asyncio.to_thread(_load_manifest, package_root)
        self._manifest = manifest
        tools = [_inventory_item(manifest, tool) for tool in manifest.tools]
        return ExtensionDiscoverySnapshot(
            source=ExtensionSourceSnapshot(
                id=_community_source_id(manifest.id),
                type=ExtensionSourceType.COMMUNITY_TOOL_PACKAGE,
                trust=manifest.trust,
                health=ExtensionHealthSnapshot(status=ExtensionHealthStatus.HEALTHY),
            ),
            tools=tools,
        )

    def manifest(self) -> CommunityToolPackageManifest:
        if self._manifest is None:
            self._manifest = _load_manifest(self._root)
        return self._manifest


class CommunitySubprocessJsonHandler:
    def __init__(
        self,
        *,
        package_root: Path,
        manifest: CommunityToolPackageManifest,
        tool: CommunityToolDefinition,
        catalog_version: str,
    ) -> None:
        self._package_root = package_root
        self._manifest = manifest
        self._tool = tool
        self._catalog_version = catalog_version

    async def __call__(
        self,
        invocation: ToolInvocation,
        _: ProgressCallback | None,
    ) -> ToolResult:
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                self._tool.handler.command,
                cwd=self._package_root,
                input=json.dumps(invocation.arguments).encode(),
                capture_output=True,
                timeout=self._tool.handler.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(
                "Community subprocess_json handler timed out."
            ) from error
        if completed.returncode != 0:
            raise ValueError(_subprocess_error(completed.stderr))
        result = _decode_tool_result(completed.stdout)
        return ToolResult(
            invocation_id=invocation.id,
            output={
                "status": "ok",
                "extension": {
                    "source_id": _community_source_id(self._manifest.id),
                    "catalog_version": self._catalog_version,
                },
                "community": {
                    "package_id": self._manifest.id,
                    "tool": self._tool.name,
                    "risk_level": self._tool.risk_level.value,
                },
                "arguments_hash": _arguments_hash(invocation.arguments),
                "result_summary": _result_summary(result),
                "artifact_refs": [],
                "result": result,
            },
        )


def register_community_tools(
    registry: ToolRegistry,
    *,
    source: CommunityToolPackageSource,
    catalog: ExtensionCatalog,
    exposed_tool_names: set[str] | frozenset[str] | None = None,
) -> None:
    manifest = source.manifest()
    tools_by_name = {tool.name: tool for tool in manifest.tools}
    source_id = _community_source_id(manifest.id)
    for inventory in catalog.tools:
        if inventory.source_id != source_id:
            continue
        if exposed_tool_names is not None and inventory.name not in exposed_tool_names:
            continue
        tool_name = _source_tool_name(manifest.id, inventory.name)
        tool = tools_by_name[tool_name]
        registry.register(
            ToolSpec(
                name=inventory.name,
                description=inventory.description,
                risk_level=inventory.risk_level,
                required_capabilities=set(inventory.required_capabilities),
                sandbox_required=True,
                timeout_seconds=tool.handler.timeout_seconds,
                input_schema=inventory.input_schema,
            ),
            CommunitySubprocessJsonHandler(
                package_root=source.root,
                manifest=manifest,
                tool=tool,
                catalog_version=catalog.version,
            ),
        )


def _load_manifest(package_root: Path) -> CommunityToolPackageManifest:
    manifest_path = _manifest_path(package_root)
    try:
        payload = _load_manifest_payload(manifest_path)
        manifest = CommunityToolPackageManifest.model_validate(payload)
    except ValidationError as error:
        raise ExtensionConfigError(str(error)) from error
    for tool in manifest.tools:
        if tool.handler.type != _HANDLER_TYPE_SUBPROCESS_JSON:
            raise ExtensionConfigError(
                f"Unsupported community tool handler: {tool.handler.type}"
            )
    return manifest


def _manifest_path(package_root: Path) -> Path:
    for filename in _MANIFEST_FILENAMES:
        candidate = package_root / filename
        if candidate.exists():
            return candidate
    raise ExtensionConfigError(
        f"Community package manifest not found under {package_root}."
    )


def _load_manifest_payload(manifest_path: Path) -> Mapping[str, object]:
    text = manifest_path.read_text(encoding="utf-8")
    if manifest_path.suffix == ".json":
        loaded = json.loads(text)
    else:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise ExtensionConfigError("Community package manifest must be an object.")
    return cast(Mapping[str, object], loaded)


def _inventory_item(
    manifest: CommunityToolPackageManifest,
    tool: CommunityToolDefinition,
) -> ExtensionToolInventoryItem:
    source_id = _community_source_id(manifest.id)
    return ExtensionToolInventoryItem(
        name=f"community.{manifest.id}.{tool.name}",
        source_id=source_id,
        description=tool.description,
        risk_level=tool.risk_level,
        required_capabilities=set(tool.required_capabilities),
        input_schema=dict(tool.input_schema),
    )


def _ensure_allowlisted_root(path: Path, allowlisted_roots: Iterable[Path]) -> Path:
    resolved = path.resolve()
    for root in allowlisted_roots:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return resolved
    raise ExtensionConfigError(f"Community package root is not allowlisted: {path}.")


def _community_source_id(package_id: str) -> str:
    return f"community.{package_id}"


def _source_tool_name(package_id: str, tool_name: str) -> str:
    prefix = f"community.{package_id}."
    if not tool_name.startswith(prefix):
        raise ExtensionConfigError(f"Community tool is outside package: {tool_name}.")
    return tool_name.removeprefix(prefix)


def _arguments_hash(arguments: Mapping[str, object]) -> str:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decode_tool_result(stdout: bytes) -> dict[str, object]:
    try:
        loaded = json.loads(stdout.decode())
    except json.JSONDecodeError as error:
        raise ValueError("Community subprocess_json output must be JSON.") from error
    if not isinstance(loaded, dict):
        raise ValueError("Community subprocess_json output must be a JSON object.")
    return cast(dict[str, object], loaded)


def _subprocess_error(stderr: bytes) -> str:
    text = stderr.decode(errors="replace").strip()
    if not text:
        return "Community subprocess_json handler failed."
    return text[:500]


def _result_summary(result: Mapping[str, object]) -> dict[str, object]:
    return {
        "keys": sorted(result.keys()),
        "truncated": False,
    }
