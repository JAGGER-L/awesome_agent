from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.conversation.models import ThreadMessageKind, ThreadMessageRole
from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionHealthSnapshot,
    ExtensionSkillInventoryItem,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
    ExtensionToolInventoryItem,
    ExtensionTrustLevel,
)
from awesome_agent.persistence.budget import (
    InMemoryBudgetRepository,
    RunBudgetLedgerRecord,
)
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.settings import Settings


def test_surface_endpoints_return_structured_redacted_state(tmp_path: Path) -> None:
    client, _threads, _runtime, _budget = _client(
        tmp_path,
        settings=Settings(
            _env_file=None,
            deepseek_api_key="super-secret-value",
            local_config_path=tmp_path / "config.toml",
            artifact_root=tmp_path / "runs",
        ),
        extension_catalog=_catalog(),
    )
    thread = client.post("/threads", json={"title": "Surface"}).json()

    responses = {
        "/models": client.get("/models"),
        "/surface/tools": client.get("/surface/tools"),
        "/extensions/skills": client.get("/extensions/skills"),
        "/extensions/mcp": client.get("/extensions/mcp"),
        "/memory": client.get("/memory"),
        f"/threads/{thread['id']}/uploads": client.get(
            f"/threads/{thread['id']}/uploads"
        ),
        f"/threads/{thread['id']}/artifacts": client.get(
            f"/threads/{thread['id']}/artifacts"
        ),
        f"/threads/{thread['id']}/usage": client.get(f"/threads/{thread['id']}/usage"),
        "/config": client.get("/config"),
    }

    assert all(response.status_code == 200 for response in responses.values())
    serialized = "\n".join(response.text for response in responses.values())
    assert "super-secret-value" not in serialized
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in serialized

    assert responses["/models"].json()[0]["configured"] is True
    assert responses["/surface/tools"].json()["builtin"][0]["name"].startswith("repo.")
    assert responses["/surface/tools"].json()["mcp"][0]["name"] == "mcp.github.search"
    assert responses["/extensions/skills"].json()["items"][0]["id"] == (
        "repository-inspection"
    )
    assert responses["/extensions/mcp"].json()["items"][0]["status"] == "healthy"
    assert responses["/memory"].json() == {
        "enabled": False,
        "provider": "none",
        "configured": False,
        "source": "not_configured",
        "hint": "Enable builtin_memory_enabled or mem0_enabled to inject memory.",
    }
    assert responses[f"/threads/{thread['id']}/uploads"].json()["configured"] is False
    assert responses[f"/threads/{thread['id']}/uploads"].json()["items"] == []
    assert responses[f"/threads/{thread['id']}/artifacts"].json()["items"] == []
    assert responses[f"/threads/{thread['id']}/usage"].json()["threshold_status"] == (
        "not_configured"
    )
    assert responses["/config"].json()["deepseek_api_key_configured"] is True


def test_thread_usage_and_artifacts_use_latest_thread_run(
    tmp_path: Path,
) -> None:
    threads = InMemoryConversationRepository()
    runtime = FakeRuntime(tmp_path)
    budget = InMemoryBudgetRepository()
    client, _threads, _runtime, _budget = _client(
        tmp_path,
        threads=threads,
        runtime=runtime,
        budget_repository=budget,
    )
    thread = client.post("/threads", json={"title": "Run"}).json()
    old_run_id = uuid4()
    latest_run_id = uuid4()
    asyncio.run(
        threads.append_message(
            thread_id=UUID(thread["id"]),
            role=ThreadMessageRole.SYSTEM,
            content="Started old run",
            kind=ThreadMessageKind.RUN,
            run_id=old_run_id,
            metadata={"run_id": str(old_run_id), "status": "created"},
        )
    )
    asyncio.run(
        threads.append_message(
            thread_id=UUID(thread["id"]),
            role=ThreadMessageRole.SYSTEM,
            content="Started latest run",
            kind=ThreadMessageKind.RUN,
            run_id=latest_run_id,
            metadata={"run_id": str(latest_run_id), "status": "created"},
        )
    )
    asyncio.run(
        budget.upsert_ledger(
            RunBudgetLedgerRecord(
                run_id=latest_run_id,
                total_input_tokens=10,
                total_output_tokens=20,
                total_reasoning_tokens=5,
                active_seconds=12,
                model_call_count=2,
                threshold_status="within_budget",
            )
        )
    )
    runtime.artifacts[latest_run_id] = [
        ArtifactMetadata(
            run_id=latest_run_id,
            artifact_type="html",
            path=tmp_path / "snake.html",
            sha256="abc",
            size=42,
            mime_type="text/html",
            summary="Snake",
        )
    ]

    usage = client.get(f"/threads/{thread['id']}/usage")
    artifacts = client.get(f"/threads/{thread['id']}/artifacts")

    assert usage.status_code == 200
    assert usage.json()["run_id"] == str(latest_run_id)
    assert usage.json()["total_tokens"] == 30
    assert usage.json()["reasoning_tokens"] == 5
    assert artifacts.status_code == 200
    assert artifacts.json()["items"][0]["path"].endswith("snake.html")


def test_thread_surface_endpoints_return_404_for_missing_thread(
    tmp_path: Path,
) -> None:
    client, _threads, _runtime, _budget = _client(tmp_path)
    missing = "00000000-0000-0000-0000-000000000000"

    assert client.get(f"/threads/{missing}/uploads").status_code == 404
    assert client.get(f"/threads/{missing}/artifacts").status_code == 404
    assert client.get(f"/threads/{missing}/usage").status_code == 404


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifacts: dict[UUID, list[ArtifactMetadata]] = {}

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.get(run_id, [])


def _client(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
    extension_catalog: ExtensionCatalog | None = None,
    threads: InMemoryConversationRepository | None = None,
    runtime: FakeRuntime | None = None,
    budget_repository: InMemoryBudgetRepository | None = None,
) -> tuple[
    TestClient, InMemoryConversationRepository, FakeRuntime, InMemoryBudgetRepository
]:
    thread_repository = threads or InMemoryConversationRepository()
    fake_runtime = runtime or FakeRuntime(tmp_path)
    budget = budget_repository or InMemoryBudgetRepository()
    return (
        TestClient(
            create_app(
                service=cast(Any, fake_runtime),
                intake=cast(Any, object()),
                registry=cast(Any, object()),
                settings=settings or Settings(_env_file=None),
                extension_catalog=extension_catalog or ExtensionCatalog(version="test"),
                thread_repository=thread_repository,
                budget_repository=budget,
            )
        ),
        thread_repository,
        fake_runtime,
        budget,
    )


def _catalog() -> ExtensionCatalog:
    return ExtensionCatalog(
        version="test",
        sources=[
            ExtensionSourceSnapshot(
                id="github",
                type=ExtensionSourceType.MCP_STDIO,
                trust=ExtensionTrustLevel.USER,
                health=ExtensionHealthSnapshot(status="healthy"),
            ),
            ExtensionSourceSnapshot(
                id="project-skills",
                type=ExtensionSourceType.SKILL_DIRECTORY,
                trust=ExtensionTrustLevel.PROJECT,
                health=ExtensionHealthSnapshot(status="healthy"),
            ),
        ],
        tools=[
            ExtensionToolInventoryItem(
                name="mcp.github.search",
                source_id="github",
                description="Search GitHub.",
                risk_level=RiskLevel.MEDIUM,
                required_capabilities={"network:request"},
            )
        ],
        skills=[
            ExtensionSkillInventoryItem(
                id="repository-inspection",
                source_id="project-skills",
                version="1",
                requested_tools=["repo.read"],
                required_capabilities={"repository:read"},
            )
        ],
    )
