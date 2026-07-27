from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import (
    ApplicationResult,
    ProductErrorCode,
    SkillInstallRequest,
    SkillRemoveRequest,
)
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.core.filesystem import FileIdentity
from awesome_agent.core.filesystem import identity as file_identity
from awesome_agent.extensions.skills import SkillPackageManager


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.value is not None
    return result.value


def _skill_directory(
    root: Path,
    *,
    description: str,
) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: review\n"
        f"description: {description}\n"
        "---\n"
        "Review carefully.\n",
        encoding="utf-8",
    )
    return root


def _marker_identity(value: FileIdentity) -> dict[str, int | bool]:
    return {
        "device": value.device,
        "inode": value.inode,
        "file_type": value.file_type,
        "reparse": value.reparse,
    }


def _interrupted_replace(home: Path, phase: str) -> None:
    skills_root = home / "skills"
    target = skills_root / "review"
    original = file_identity(os.lstat(target))
    stage_name = ".skill-stage-" + "a" * 32
    quarantine_name = ".skill-quarantine-" + "b" * 32
    stage = _skill_directory(skills_root / stage_name, description="New")
    candidate = file_identity(os.lstat(stage))
    if phase in {"quarantined", "published"}:
        os.rename(target, skills_root / quarantine_name)
    if phase == "published":
        os.rename(stage, target)
    marker = {
        "version": 1,
        "action": "replace",
        "phase": phase,
        "name": "review",
        "stage_name": stage_name,
        "quarantine_name": quarantine_name,
        "stage_identity": _marker_identity(candidate),
        "original_identity": _marker_identity(original),
    }
    (home / ".skills-transaction.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_one_shot_skill_management_does_not_initialize_workspace_runtime(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "review"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nReview carefully.",
        encoding="utf-8",
    )
    (source / "guide.md").write_text("Bounded guide", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    try:
        installed = _unwrap(
            await application.install_skill(
                SkillInstallRequest(source_path=str(source), replace=False)
            )
        )
        assert installed.model_dump(mode="json") == {
            "name": "review",
            "status": "installed",
        }
        listed = _unwrap(await application.list_skills())
        assert listed.model_dump(mode="json") == {
            "skills": [{"name": "review", "description": "Review code"}]
        }
        backend = cast(Any, application)._backend
        assert backend._runtime is None

        duplicate = await application.install_skill(
            SkillInstallRequest(source_path=str(source), replace=False)
        )
        assert duplicate.ok is False
        assert duplicate.error is not None
        assert duplicate.error.code is ProductErrorCode.INVALID_ARGUMENTS
        assert duplicate.error.data == {"diagnostic_code": "package_exists"}
        assert str(source) not in duplicate.model_dump_json()

        removed = _unwrap(
            await application.remove_skill(SkillRemoveRequest(name="review"))
        )
        assert removed.model_dump(mode="json") == {
            "name": "review",
            "status": "removed",
        }
        assert _unwrap(await application.list_skills()).skills == ()
        assert backend._runtime is None

        missing = await application.remove_skill(SkillRemoveRequest(name="review"))
        assert missing.ok is False
        assert missing.error is not None
        assert missing.error.data == {"diagnostic_code": "package_not_found"}

        stopped = _unwrap(await application.shutdown())
        assert stopped.stopped is True
        assert backend._runtime is None
        unavailable = await application.list_skills()
        assert unavailable.ok is False
        assert unavailable.error is not None
        assert unavailable.error.code is ProductErrorCode.COMMAND_NOT_AVAILABLE
    finally:
        _unwrap(await application.shutdown())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "expected_description"),
    [
        ("prepared", "Old"),
        ("quarantined", "Old"),
        ("published", "New"),
    ],
)
async def test_initialize_recovers_skill_transaction_before_catalog_publication(
    tmp_path: Path,
    phase: str,
    expected_description: str,
) -> None:
    home = tmp_path / "home"
    manager = SkillPackageManager(home, home / "skills")
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    _interrupted_replace(home, phase)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    try:
        pending = _unwrap(await application.initialize())
        assert pending.interaction_id is not None
        _unwrap(
            await application.respond_interaction(
                pending.interaction_id,
                "trust",
            )
        )
        backend = cast(Any, application)._backend
        catalog = backend._skill_catalog
        assert catalog is not None
        installed = {
            descriptor.name: descriptor for descriptor in catalog.descriptors()
        }
        assert installed["review"].description == expected_description
        assert backend._runtime is not None
        assert not (home / ".skills-transaction.json").exists()
        assert not any(
            path.name.startswith((".skill-stage-", ".skill-quarantine-"))
            for path in (home / "skills").iterdir()
        )
    finally:
        _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_recovery_failure_prevents_runtime_and_catalog_publication(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    manager = SkillPackageManager(home, home / "skills")
    manager.list()
    quarantine = _skill_directory(
        home / "skills" / (".skill-quarantine-" + "c" * 32),
        description="Unknown",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    try:
        pending = _unwrap(await application.initialize())
        assert pending.interaction_id is not None
        resolved = await application.respond_interaction(
            pending.interaction_id,
            "trust",
        )
        assert resolved.ok is False
        assert resolved.error is not None
        assert resolved.error.code is ProductErrorCode.STATE_UNAVAILABLE
        assert resolved.error.data == {"diagnostic_code": "transaction_failed"}
        backend = cast(Any, application)._backend
        assert backend._runtime is None
        assert backend._skill_catalog is None
        assert quarantine.is_dir()
    finally:
        _unwrap(await application.shutdown())
