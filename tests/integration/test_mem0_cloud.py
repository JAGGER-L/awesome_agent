from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import CloudPostAnswerMemory
from awesome_agent.application.commands import CommandIntent, CommandName, CommandStatus
from awesome_agent.application.headless import ApplicationExtensionService
from awesome_agent.config import UserConfigWriter
from awesome_agent.config.loader import read_user_config_document
from awesome_agent.context import mem0_context_source
from awesome_agent.conversation import ConversationService
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.extensions.mcp import McpManager
from awesome_agent.extensions.skills import SkillCatalog, SkillLoader
from awesome_agent.memory import (
    DistillationResult,
    DistillationStatus,
    Mem0CloudAdapter,
    Mem0Identity,
    MemoryCandidate,
    MemoryScope,
)
from awesome_agent.modeling import SelectedModel
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage import SQLiteMcpEnablementStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories


class FakeMem0Client:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.calls: list[str] = []
        self.fail_content: str | None = None

    async def search(self, query: str, **kwargs: object) -> object:
        self.calls.append("search")
        user_id = kwargs["user_id"]
        expected_hash = _filter_value(kwargs["filters"], "fact_hash")
        results = []
        for record in self.records.values():
            metadata = cast(dict[str, object], record["metadata"])
            if record["user_id"] != user_id:
                continue
            if metadata["scope"] == "workspace" and metadata.get(
                "workspace_key"
            ) != _workspace_filter(kwargs["filters"]):
                continue
            if expected_hash is not None and metadata.get("fact_hash") != expected_hash:
                continue
            if (
                expected_hash is None
                and query not in {"*", "question"}
                and query.casefold() not in str(record["memory"]).casefold()
            ):
                continue
            results.append(record)
        return {"results": results[: cast(int, kwargs["limit"])]}

    async def add(self, messages: object, **kwargs: object) -> object:
        self.calls.append("add")
        content = cast(list[dict[str, str]], messages)[0]["content"]
        if content == self.fail_content:
            raise RuntimeError("cloud failure private detail")
        identifier = f"remote-{len(self.records) + 1}"
        self.records[identifier] = {
            "id": identifier,
            "memory": content,
            "user_id": kwargs["user_id"],
            "metadata": kwargs["metadata"],
        }
        assert kwargs["infer"] is False
        return {"id": identifier}

    async def get(self, memory_id: str) -> object:
        self.calls.append("get")
        return self.records.get(memory_id)

    async def delete(self, memory_id: str) -> object:
        self.calls.append("delete")
        self.records.pop(memory_id, None)
        return {"status": "deleted"}


def _workspace_filter(filters: object) -> object:
    return _filter_value(filters, "workspace_key")


def _filter_value(filters: object, key: str) -> object:
    if isinstance(filters, dict):
        if key in filters:
            return filters[key]
        for value in filters.values():
            found = _filter_value(value, key)
            if found is not None:
                return found
    if isinstance(filters, list):
        for value in filters:
            found = _filter_value(value, key)
            if found is not None:
                return found
    return None


class Distiller:
    def __init__(self, candidates: tuple[MemoryCandidate, ...]) -> None:
        self.candidates = candidates

    async def distill(self, **kwargs: object) -> DistillationResult:
        assert set(kwargs) >= {
            "user_text",
            "final_answer",
            "selected_model",
            "workspace_key",
        }
        return DistillationResult(
            status=DistillationStatus.COMPLETED,
            candidates=self.candidates,
            model_calls=1,
        )


def _extensions(
    tmp_path: Path,
    *,
    client: FakeMem0Client,
    mem0_enabled: bool = False,
) -> tuple[ApplicationExtensionService, str, Path, str, Mem0CloudAdapter]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir(exist_ok=True)
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    conversation = ConversationService(
        store=SQLiteConversationRepositories(paths.application_db)
    )
    thread = conversation.create_thread(workspace.key)
    catalog = SkillCatalog((), ())
    enablements = SQLiteMcpEnablementStore(paths.application_db)

    async def submit_turn(thread_id: str, content: str) -> object:
        return {"thread_id": thread_id, "content": content}

    adapter = Mem0CloudAdapter(client)
    current_config = read_user_config_document(paths.config_file)
    return (
        ApplicationExtensionService(
            conversation=conversation,
            catalog=catalog,
            loader=SkillLoader(catalog),
            manager=McpManager(
                configs=(),
                workspace_key=workspace.key,
                workspace_trusted=True,
                enablements=enablements,
            ),
            enablements=enablements,
            workspace_key=workspace.key,
            registry=ToolRegistry(),
            submit_turn=submit_turn,
            config_writer=UserConfigWriter(paths.config_file),
            mem0_cloud=adapter,
            mem0_enabled=mem0_enabled,
            mem0_user_id=current_config.memory.mem0_user_id,
        ),
        thread.id,
        paths.config_file,
        workspace.key,
        adapter,
    )


@pytest.mark.asyncio
async def test_mem0_commands_recall_write_restart_remove_and_disable(
    tmp_path: Path,
) -> None:
    client = FakeMem0Client()
    service, thread_id, config_path, workspace_key, adapter = _extensions(
        tmp_path, client=client
    )

    enabled = await service.handle(
        CommandIntent(name=CommandName.MEMORY, arguments=("mem0", "on")),
        thread_id=thread_id,
    )
    document = read_user_config_document(config_path)
    assert enabled.status is CommandStatus.SUCCESS
    assert enabled.data["mem0"]["enabled"] is True
    assert document.memory.mem0_cloud is True
    assert document.memory.mem0_user_id is not None
    identity = Mem0Identity(
        user_id=document.memory.mem0_user_id,
        workspace_key=workspace_key,
    )

    candidates = (
        MemoryCandidate(
            scope=MemoryScope.USER,
            content="User prefers concise answers.",
            fact_hash="a" * 64,
        ),
        MemoryCandidate(
            scope=MemoryScope.WORKSPACE,
            content="Project uses pytest.",
            fact_hash="b" * 64,
        ),
        MemoryCandidate(
            scope=MemoryScope.USER,
            content="write fails safely",
            fact_hash="c" * 64,
        ),
    )
    client.fail_content = "write fails safely"
    finalizer = CloudPostAnswerMemory(
        distiller=cast(Any, Distiller(candidates)),
        adapter=adapter,
        identity=identity,
    )
    first = await finalizer.finalize(
        user_text="remember stable facts",
        final_answer="done",
        selected_model=SelectedModel(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
        ),
        remaining_model_calls=10,
        workspace_key=workspace_key,
    )
    await finalizer.finalize(
        user_text="remember stable facts",
        final_answer="done",
        selected_model=SelectedModel(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
        ),
        remaining_model_calls=10,
        workspace_key=workspace_key,
    )
    assert first.status == "warning"
    assert [call for call in client.calls if call == "add"].count("add") == 4

    recalled = await mem0_context_source(
        enabled=True,
        adapter=adapter,
        identity=identity,
        query="question",
    )
    assert recalled.source is not None
    assert "concise answers" in recalled.source.content
    assert "uses pytest" in recalled.source.content

    restarted, restarted_thread, _, _, _ = _extensions(
        tmp_path,
        client=client,
        mem0_enabled=True,
    )
    searched = await restarted.handle(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("mem0", "search", "pytest"),
        ),
        thread_id=restarted_thread,
    )
    assert searched.status is CommandStatus.SUCCESS
    memory_id = cast(str, searched.data["memories"][0]["id"])

    client.records["foreign"] = {
        "id": "foreign",
        "memory": "foreign fact",
        "user_id": "user_ffffffffffffffffffffffffffffffff",
        "metadata": {
            "app_id": "awesome-agent",
            "scope": "workspace",
            "workspace_key": "ws_ffffffffffffffffffffffffffffffff",
            "fact_hash": "f" * 64,
        },
    }
    removed = await restarted.handle(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("mem0", "remove", memory_id),
        ),
        thread_id=restarted_thread,
    )
    rejected = await restarted.handle(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("mem0", "remove", "foreign"),
        ),
        thread_id=restarted_thread,
    )
    assert removed.status is CommandStatus.SUCCESS
    assert rejected.data["status"] == "memory_not_found"

    disabled = await restarted.handle(
        CommandIntent(name=CommandName.MEMORY, arguments=("mem0", "off")),
        thread_id=restarted_thread,
    )
    before = list(client.calls)
    blocked = await restarted.handle(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("mem0", "search", "anything"),
        ),
        thread_id=restarted_thread,
    )
    assert disabled.data["mem0"]["enabled"] is False
    assert (
        read_user_config_document(config_path).memory.mem0_user_id == identity.user_id
    )
    assert blocked.data["error_code"] == "memory_disabled"
    assert client.calls == before
