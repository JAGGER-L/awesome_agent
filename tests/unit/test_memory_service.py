from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import NoopMemoryProvider
from awesome_agent.memory.models import MemoryTarget
from awesome_agent.memory.policy import MemoryPolicy
from awesome_agent.memory.service import EffectiveMemory, MemoryService


def _service(tmp_path: Path, *, builtin_enabled: bool = True) -> MemoryService:
    return MemoryService(
        builtin=BuiltinMemoryStore(root=tmp_path / "memory", policy=MemoryPolicy()),
        provider=NoopMemoryProvider(),
        builtin_enabled=builtin_enabled,
        provider_enabled=False,
    )


def test_status_projects_real_paths_counts_and_disabled_state(tmp_path: Path) -> None:
    service = _service(tmp_path, builtin_enabled=False)

    status = service.status()

    assert status.enabled is False
    assert status.builtin_enabled is False
    assert status.files["user"].endswith("USER.md")
    assert status.counts == {"user": 0, "memory": 0}


@pytest.mark.asyncio
async def test_add_list_delete_builtin_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)

    added = await service.add(
        target=MemoryTarget.USER,
        content="Prefer direct implementation plans.",
        source="explicit_user_request",
        run_id=uuid4(),
        agent_id=uuid4(),
    )
    assert added.entry is not None
    listed = await service.list_entries(target=MemoryTarget.USER)
    deleted = await service.delete(
        target=MemoryTarget.USER,
        memory_id=added.entry.id,
        run_id=uuid4(),
        agent_id=uuid4(),
    )

    assert added.status == "added"
    assert listed.entries[0].id == added.entry.id
    assert deleted.status == "deleted"


def test_context_is_empty_when_effective_memory_disabled(tmp_path: Path) -> None:
    service = _service(tmp_path)

    snapshot = service.context_snapshot(
        EffectiveMemory(local_enabled=False, provider=None)
    )

    assert snapshot.enabled is False
    assert snapshot.render() == ""


def test_context_contains_fenced_untrusted_builtin_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.builtin.add(
        service.add_request(
            target=MemoryTarget.USER,
            content="Prefer concise answers.",
            source="explicit_user_request",
        )
    )

    snapshot = service.context_snapshot(EffectiveMemory(local_enabled=True))

    assert snapshot.enabled is True
    rendered = service.render_context(snapshot)
    assert "untrusted reference context" in rendered
    assert "Prefer concise answers." in rendered
