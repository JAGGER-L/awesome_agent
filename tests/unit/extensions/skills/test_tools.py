import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue

from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    ToolActivityDraft,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolInvocationDescription,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.extensions.skills import (
    LoadedSkill,
    SkillCatalog,
    SkillLoader,
    SkillResource,
    discover_skills,
    register_skill_tools,
)


class RecordingSkillLoader(SkillLoader):
    def __init__(self, catalog: SkillCatalog) -> None:
        super().__init__(catalog)
        self.load_calls = 0
        self.resource_calls = 0

    def load(self, name: str, *, token_limit: int = 5_000) -> LoadedSkill:
        self.load_calls += 1
        return super().load(name, token_limit=token_limit)

    def read_resource(
        self,
        name: str,
        relative_path: str,
        *,
        token_limit: int,
    ) -> SkillResource:
        self.resource_calls += 1
        return super().read_resource(name, relative_path, token_limit=token_limit)


class CollectingActivityWriter:
    def __init__(self) -> None:
        self.activities: list[ToolActivityDraft] = []

    async def finalize(self, activity: ToolActivityDraft) -> None:
        self.activities.append(activity)


def _user_catalog(tmp_path: Path) -> SkillCatalog:
    root = tmp_path / "skills" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nbody",
        encoding="utf-8",
    )
    (root / "guide.md").write_text("safe guide", encoding="utf-8")
    return discover_skills(
        bundled_root=None,
        user_root=tmp_path / "skills",
        workspace_root=None,
        workspace_trusted=False,
    )


def _workspace_catalog(tmp_path: Path) -> SkillCatalog:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nbody",
        encoding="utf-8",
    )
    (root / "guide.md").write_text("safe guide", encoding="utf-8")
    return discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root.parent,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )


def _execution_context(
    workspace_path: Path,
) -> tuple[
    ToolExecutionContext,
    CollectingEventSink,
    CollectingActivityWriter,
    list[ToolApprovalRequest],
]:
    workspace_path.mkdir(exist_ok=True)
    workspace = resolve_workspace(workspace_path)
    sink = CollectingEventSink()
    writer = CollectingActivityWriter()
    approvals: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalDecision:
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_ONCE

    return (
        ToolExecutionContext(
            workspace=workspace,
            thread_id="thread_skills",
            operation_id="operation_skills",
            turn_id="turn_skills",
            origin=ToolExecutionOrigin.AGENT,
            emitter=EventEmitter(
                session_id="session_skills",
                workspace_key=workspace.key,
                sink=sink,
            ),
            activity_writer=writer,
            monotonic=time.monotonic,
            approval_resolver=approve,
        ),
        sink,
        writer,
        approvals,
    )


def _track_description(registry: ToolRegistry, tool_name: str) -> list[str]:
    registered = registry.resolve(tool_name)
    assert registered is not None
    calls: list[str] = []

    def describe(arguments: BaseModel) -> ToolInvocationDescription:
        calls.append(tool_name)
        return registered.describe(arguments)

    registry.unregister(tool_name)
    registry.register(
        spec=registered.spec,
        input_model=registered.input_model,
        handler=registered.handler,
        describe=describe,
        admit=registered.admit,
        replay_safety=registered.replay_safety,
        timeout_resolver=registered.timeout_resolver,
        cancellation_grace_seconds=registered.cancellation_grace_seconds,
    )
    return calls


def test_skill_tools_register_as_read_only_without_granting_capabilities(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n"
        "allowed-tools: [execute]\n---\nbody",
        encoding="utf-8",
    )
    loader = SkillLoader(
        discover_skills(
            bundled_root=None,
            user_root=tmp_path / "skills",
            workspace_root=None,
            workspace_trusted=False,
        )
    )
    registry = ToolRegistry()
    register_skill_tools(registry, loader)

    specs = {item.name: item for item in registry.specifications()}
    assert set(specs) == {"load_skill", "read_skill_resource"}
    assert all(item.read_only for item in specs.values())
    assert registry.resolve("execute") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "error_code"),
    [
        (
            "read_skill_resource",
            {"name": "review", "relative_path": "../secret"},
            "invalid_arguments",
        ),
        (
            "read_skill_resource",
            {"name": "review", "relative_path": "guide.md:secret"},
            "invalid_arguments",
        ),
        (
            "read_skill_resource",
            {"name": "review", "relative_path": "CON"},
            "invalid_arguments",
        ),
        (
            "read_skill_resource",
            {"name": "review", "relative_path": "guide.md."},
            "invalid_arguments",
        ),
        (
            "read_skill_resource",
            {"name": "review", "relative_path": "missing.md"},
            "not_found",
        ),
        ("load_skill", {"name": "missing"}, "not_found"),
    ],
)
async def test_skill_hard_admission_fails_before_description_or_handler(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, JsonValue],
    error_code: str,
) -> None:
    loader = RecordingSkillLoader(_user_catalog(tmp_path))
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    describe_calls = _track_description(registry, tool_name)
    context, sink, writer, approvals = _execution_context(tmp_path / "workspace")

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_rejected_skill",
            tool_name=tool_name,
            arguments=arguments,
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code == error_code
    assert "../secret" not in result.content
    assert describe_calls == []
    assert loader.load_calls == 0
    assert loader.resource_calls == 0
    assert approvals == []
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert all(event.payload.target is None for event in sink.events)  # type: ignore[union-attr]
    assert len(writer.activities) == 1
    assert writer.activities[0].outcome == "error"
    assert writer.activities[0].error_code == error_code


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["load_skill", "read_skill_resource"])
async def test_workspace_skill_identity_swap_fails_in_hard_admission(
    tmp_path: Path,
    tool_name: str,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = RecordingSkillLoader(catalog)
    package = catalog.resolve("review").root
    package.rename(package.with_name("review.original"))
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: review\ndescription: Replacement\n---\nunsafe",
        encoding="utf-8",
    )
    (package / "guide.md").write_text("unsafe", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    describe_calls = _track_description(registry, tool_name)
    context, sink, writer, approvals = _execution_context(tmp_path / "workspace")
    arguments: dict[str, JsonValue] = (
        {"name": "review"}
        if tool_name == "load_skill"
        else {"name": "review", "relative_path": "guide.md"}
    )

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_changed_skill",
            tool_name=tool_name,
            arguments=arguments,
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code == "conflict"
    assert describe_calls == []
    assert loader.load_calls == 0
    assert loader.resource_calls == 0
    assert approvals == []
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert all(event.payload.target is None for event in sink.events)  # type: ignore[union-attr]
    assert len(writer.activities) == 1


@pytest.mark.asyncio
async def test_skill_tools_preserve_normal_load_and_resource_behavior(
    tmp_path: Path,
) -> None:
    loader = RecordingSkillLoader(_user_catalog(tmp_path))
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    context, sink, writer, approvals = _execution_context(tmp_path / "workspace")

    loaded = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_load_skill",
            tool_name="load_skill",
            arguments={"name": "review"},
        ),
        context=context,
    )
    resource = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_read_resource",
            tool_name="read_skill_resource",
            arguments={"name": "review", "relative_path": "guide.md"},
        ),
        context=context,
    )

    assert loaded.status is ToolStatus.SUCCESS
    assert loaded.content == "body"
    assert resource.status is ToolStatus.SUCCESS
    assert resource.content == "safe guide"
    assert loader.load_calls == 1
    assert loader.resource_calls == 1
    assert approvals == []
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]
    assert len(writer.activities) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["user", "workspace"])
@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
async def test_skill_handler_rejects_link_swap_after_started(
    tmp_path: Path,
    source: str,
    link_kind: str,
) -> None:
    catalog = (
        _user_catalog(tmp_path) if source == "user" else _workspace_catalog(tmp_path)
    )
    loader = RecordingSkillLoader(catalog)
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    resource = catalog.resolve("review").root / "guide.md"
    outside = tmp_path / "outside-secret.md"
    outside.write_text("EXTERNAL-SECRET", encoding="utf-8")
    context, _, writer, approvals = _execution_context(tmp_path / "workspace")

    class LinkSwapSink(CollectingEventSink):
        async def emit(self, event):  # type: ignore[no-untyped-def]
            await super().emit(event)
            if event.event_type is EventType.TOOL_STARTED:
                resource.unlink()
                try:
                    if link_kind == "hardlink":
                        os.link(outside, resource)
                    else:
                        os.symlink(outside, resource)
                except OSError:
                    pytest.skip(f"{link_kind} creation is unavailable")

    sink = LinkSwapSink()
    context = replace(
        context,
        emitter=EventEmitter(
            session_id="session_skills_swap",
            workspace_key=context.workspace.key,
            sink=sink,
        ),
    )

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_swapped_resource",
            tool_name="read_skill_resource",
            arguments={"name": "review", "relative_path": "guide.md"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code == "permission_denied"
    assert "EXTERNAL-SECRET" not in result.content
    assert loader.resource_calls == 1
    assert approvals == []
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert len(writer.activities) == 1


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate streams are Windows-only")
@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["user", "workspace"])
async def test_skill_hard_admission_rejects_ntfs_alternate_stream(
    tmp_path: Path,
    source: str,
) -> None:
    catalog = (
        _user_catalog(tmp_path) if source == "user" else _workspace_catalog(tmp_path)
    )
    loader = RecordingSkillLoader(catalog)
    resource = catalog.resolve("review").root / "guide.md"
    Path(f"{resource}:secret").write_text("ADS-SECRET", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    describe_calls = _track_description(registry, "read_skill_resource")
    context, sink, writer, approvals = _execution_context(tmp_path / "workspace")

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_ads_resource",
            tool_name="read_skill_resource",
            arguments={"name": "review", "relative_path": "guide.md:secret"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code == "invalid_arguments"
    assert "ADS-SECRET" not in result.content
    assert describe_calls == []
    assert loader.resource_calls == 0
    assert approvals == []
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_FAILED,
    ]
    assert len(writer.activities) == 1
