from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Never

from awesome_agent.application.contracts import (
    ProductError,
    ProductErrorCode,
    SkillInstallRequest,
    SkillInstallResult,
    SkillListResult,
    SkillPackageSummary,
    SkillRemoveRequest,
    SkillRemoveResult,
)
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.extensions.skills import (
    SkillPackageAction,
    SkillPackageError,
    SkillPackageManager,
)


class SkillManagementService:
    """Async Application boundary over the synchronous local package manager."""

    def __init__(self, manager: SkillPackageManager) -> None:
        self._manager = manager

    async def recover(self) -> None:
        try:
            await _run_blocking(self._manager.recover)
        except SkillPackageError as error:
            _raise_package_failure(error, operation="recover")

    async def list(self) -> SkillListResult:
        try:
            installed = await _run_blocking(self._manager.list)
        except SkillPackageError as error:
            _raise_package_failure(error, operation="list")
        return SkillListResult(
            skills=tuple(
                SkillPackageSummary(
                    name=package.name,
                    description=package.description,
                )
                for package in sorted(installed, key=lambda package: package.name)
            )
        )

    async def install(self, request: SkillInstallRequest) -> SkillInstallResult:
        try:
            mutation = await _run_blocking(
                lambda: self._manager.install(
                    Path(request.source_path),
                    replace=request.replace,
                )
            )
        except SkillPackageError as error:
            _raise_package_failure(error, operation="install")
        if mutation.restart_required is not True:
            raise RuntimeError("Skill installation must require a new Core process.")
        status: Literal["installed", "replaced"]
        if mutation.action is SkillPackageAction.INSTALLED:
            status = "installed"
        elif mutation.action is SkillPackageAction.REPLACED:
            status = "replaced"
        else:
            raise RuntimeError("Skill installation returned an invalid action.")
        return SkillInstallResult(
            name=mutation.name,
            status=status,
        )

    async def remove(self, request: SkillRemoveRequest) -> SkillRemoveResult:
        try:
            mutation = await _run_blocking(
                lambda: self._manager.remove(request.name)
            )
        except SkillPackageError as error:
            _raise_package_failure(error, operation="remove")
        if mutation.restart_required is not True:
            raise RuntimeError("Skill removal must require a new Core process.")
        if mutation.action is not SkillPackageAction.REMOVED:
            raise RuntimeError("Skill removal returned an invalid action.")
        return SkillRemoveResult(
            name=mutation.name,
            status="removed",
        )


async def _run_blocking[ResultT](call: Callable[[], ResultT]) -> ResultT:
    result, cancellation = await finish_cancellation_safe(asyncio.to_thread(call))
    if cancellation is not None:
        raise cancellation
    return result


def _raise_package_failure(
    error: SkillPackageError,
    *,
    operation: Literal["recover", "list", "install", "remove"],
) -> Never:
    code = str(error.code)
    if code == "package_too_large" and operation == "list":
        product_code = ProductErrorCode.RESULT_TOO_LARGE
        message = "The installed Skill catalog exceeds the supported limit."
        retryable = False
    elif code == "package_busy":
        product_code = ProductErrorCode.OPERATION_BUSY
        message = "Skill package management is busy."
        retryable = True
    elif code == "transaction_failed":
        product_code = ProductErrorCode.STATE_UNAVAILABLE
        message = "The Skill package transaction could not be completed safely."
        retryable = True
    else:
        messages = {
            "invalid_source": "The Skill source is invalid or unavailable.",
            "invalid_package": "The Skill package is invalid.",
            "package_too_large": "The Skill package exceeds installation limits.",
            "package_exists": "The Skill is already installed.",
            "package_not_found": "The Skill is not installed.",
        }
        if code not in messages:
            raise RuntimeError(
                "Skill package manager returned an unknown error."
            ) from error
        product_code = ProductErrorCode.INVALID_ARGUMENTS
        retryable = False
        message = messages[code]
    raise ApplicationFailure(
        ProductError(
            code=product_code,
            message=message,
            retryable=retryable,
            data={"diagnostic_code": code},
        )
    ) from error


__all__ = ["SkillManagementService"]
