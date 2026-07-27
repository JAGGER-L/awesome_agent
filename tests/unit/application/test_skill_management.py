from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from awesome_agent.application.contracts import (
    ProductErrorCode,
    SkillInstallRequest,
    SkillListResult,
    SkillPackageSummary,
    SkillRemoveRequest,
)
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.application.skill_management import SkillManagementService
from awesome_agent.extensions.skills import (
    InstalledSkillPackage,
    SkillPackageAction,
    SkillPackageError,
    SkillPackageManager,
    SkillPackageMutation,
)


class _Manager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.thread_ids: list[int] = []
        self.error_code: str | None = None
        self.started: threading.Event | None = None
        self.release: threading.Event | None = None

    def _enter(self, operation: str, value: object) -> None:
        self.calls.append((operation, value))
        self.thread_ids.append(threading.get_ident())
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        if self.error_code is not None:
            raise SkillPackageError(self.error_code, "private manager detail")

    def list(self) -> tuple[InstalledSkillPackage, ...]:
        self._enter("list", None)
        return (
            InstalledSkillPackage(
                name="test",
                description="Test code",
                allowed_tools=("execute",),
            ),
            InstalledSkillPackage(name="review", description="Review code"),
        )

    def recover(self) -> None:
        self._enter("recover", None)

    def install(self, source: Path, *, replace: bool = False) -> SkillPackageMutation:
        self._enter("install", (source, replace))
        return SkillPackageMutation(
            name="review",
            action=(
                SkillPackageAction.REPLACED
                if replace
                else SkillPackageAction.INSTALLED
            ),
        )

    def remove(self, name: str) -> SkillPackageMutation:
        self._enter("remove", name)
        return SkillPackageMutation(name=name, action=SkillPackageAction.REMOVED)


def _service(manager: _Manager) -> SkillManagementService:
    return SkillManagementService(cast(SkillPackageManager, manager))


@pytest.mark.asyncio
async def test_service_runs_manager_off_loop_and_returns_minimal_results() -> None:
    manager = _Manager()
    service = _service(manager)
    event_loop_thread = threading.get_ident()

    await service.recover()
    listed = await service.list()
    installed = await service.install(
        SkillInstallRequest(source_path="C:\\private\\review.zip", replace=True)
    )
    removed = await service.remove(SkillRemoveRequest(name="review"))

    assert [skill.name for skill in listed.skills] == ["review", "test"]
    assert listed.model_dump(mode="json") == {
        "skills": [
            {"name": "review", "description": "Review code"},
            {"name": "test", "description": "Test code"},
        ]
    }
    assert installed.model_dump(mode="json") == {
        "name": "review",
        "status": "replaced",
    }
    assert removed.model_dump(mode="json") == {
        "name": "review",
        "status": "removed",
    }
    assert all(thread_id != event_loop_thread for thread_id in manager.thread_ids)
    assert manager.calls[2] == (
        "install",
        (Path("C:\\private\\review.zip"), True),
    )


@pytest.mark.asyncio
async def test_recovery_runs_off_loop_and_maps_failures_closed() -> None:
    manager = _Manager()
    manager.error_code = "transaction_failed"
    event_loop_thread = threading.get_ident()

    with pytest.raises(ApplicationFailure) as raised:
        await _service(manager).recover()

    assert raised.value.error.code is ProductErrorCode.STATE_UNAVAILABLE
    assert raised.value.error.data == {"diagnostic_code": "transaction_failed"}
    assert manager.calls == [("recover", None)]
    assert manager.thread_ids[0] != event_loop_thread


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manager_code", "product_code", "retryable"),
    [
        ("invalid_source", ProductErrorCode.INVALID_ARGUMENTS, False),
        ("invalid_package", ProductErrorCode.INVALID_ARGUMENTS, False),
        ("package_too_large", ProductErrorCode.RESULT_TOO_LARGE, False),
        ("package_exists", ProductErrorCode.INVALID_ARGUMENTS, False),
        ("package_not_found", ProductErrorCode.INVALID_ARGUMENTS, False),
        ("package_busy", ProductErrorCode.OPERATION_BUSY, True),
        ("transaction_failed", ProductErrorCode.STATE_UNAVAILABLE, True),
    ],
)
async def test_service_maps_closed_manager_errors_without_private_details(
    manager_code: str,
    product_code: ProductErrorCode,
    retryable: bool,
) -> None:
    manager = _Manager()
    manager.error_code = manager_code

    with pytest.raises(ApplicationFailure) as raised:
        await _service(manager).list()

    assert raised.value.error.code is product_code
    assert raised.value.error.retryable is retryable
    assert raised.value.error.data == {"diagnostic_code": manager_code}
    assert "private manager detail" not in raised.value.error.model_dump_json()
    if manager_code == "package_too_large":
        assert "installation" not in raised.value.error.message.lower()


@pytest.mark.asyncio
async def test_install_package_size_error_remains_an_input_failure() -> None:
    manager = _Manager()
    manager.error_code = "package_too_large"

    with pytest.raises(ApplicationFailure) as raised:
        await _service(manager).install(SkillInstallRequest(source_path="review"))

    assert raised.value.error.code is ProductErrorCode.INVALID_ARGUMENTS
    assert "installation limits" in raised.value.error.message
    assert raised.value.error.data == {"diagnostic_code": "package_too_large"}


@pytest.mark.asyncio
async def test_service_preserves_cancellation_until_blocking_transaction_finishes() -> (
    None
):
    manager = _Manager()
    manager.started = threading.Event()
    manager.release = threading.Event()
    task = asyncio.create_task(
        _service(manager).install(SkillInstallRequest(source_path="review"))
    )
    while not manager.started.is_set():
        await asyncio.sleep(0)

    task.cancel("cancel-skill-install")
    await asyncio.sleep(0)
    assert task.done() is False
    manager.release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(task, timeout=1)
    assert cancelled.value.args == ("cancel-skill-install",)


def test_skill_management_contracts_are_strict_bounded_and_ordered() -> None:
    with pytest.raises(ValidationError):
        SkillInstallRequest.model_validate(
            {"source_path": "review", "replace": 1}
        )
    with pytest.raises(ValidationError):
        SkillInstallRequest(source_path=" review ")
    with pytest.raises(ValidationError):
        SkillRemoveRequest(name="../review")
    with pytest.raises(ValidationError):
        SkillListResult(
            skills=(
                SkillPackageSummary(name="test", description="Test"),
                SkillPackageSummary(name="review", description="Review"),
            )
        )
