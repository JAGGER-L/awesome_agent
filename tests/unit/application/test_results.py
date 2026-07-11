from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from awesome_agent.application.contracts import (
    ApplicationResult,
    ProductError,
    ProductErrorCode,
    ShutdownResult,
)
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.application.facade import LocalApplication


def _error(code: ProductErrorCode) -> ProductError:
    return ProductError(code=code, message="Safe product message.")


def test_application_result_serializes_one_success_or_failure_branch() -> None:
    success = ApplicationResult[ShutdownResult].success(ShutdownResult(stopped=True))
    failure = ApplicationResult[ShutdownResult].failure(
        _error(ProductErrorCode.OPERATION_BUSY)
    )

    assert success.model_dump(mode="json", exclude_none=True) == {
        "ok": True,
        "value": {"stopped": True},
    }
    assert failure.model_dump(mode="json", exclude_none=True) == {
        "ok": False,
        "error": {
            "code": "operation_busy",
            "message": "Safe product message.",
            "retryable": False,
            "data": {},
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": False},
        {"ok": True, "error": _error(ProductErrorCode.INTERNAL_ERROR)},
        {
            "ok": False,
            "value": ShutdownResult(stopped=True),
        },
        {
            "ok": True,
            "value": ShutdownResult(stopped=True),
            "error": _error(ProductErrorCode.INTERNAL_ERROR),
        },
    ],
)
def test_application_result_rejects_inconsistent_branches(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ApplicationResult[ShutdownResult].model_validate(payload)


def test_product_error_codes_cover_expected_application_failures() -> None:
    assert {
        ProductErrorCode.OPERATION_BUSY,
        ProductErrorCode.THREAD_NOT_FOUND,
        ProductErrorCode.WORKSPACE_NOT_TRUSTED,
        ProductErrorCode.PROVIDER_NOT_CONFIGURED,
        ProductErrorCode.INVALID_ARGUMENTS,
        ProductErrorCode.CLIENT_VERSION_INCOMPATIBLE,
        ProductErrorCode.PROTOCOL_VERSION_INCOMPATIBLE,
        ProductErrorCode.INTERNAL_ERROR,
    } <= set(ProductErrorCode)


def test_application_failure_exposes_only_a_validated_safe_error() -> None:
    failure = ApplicationFailure(
        ProductError(
            code=ProductErrorCode.INTERNAL_ERROR,
            message="Internal application error.",
        )
    )

    assert failure.error.code is ProductErrorCode.INTERNAL_ERROR
    assert "secret-provider-token" not in str(failure)
    assert failure.args == ()


class _FailingBackend:
    async def initialize_application(self) -> object:
        raise ApplicationFailure(_error(ProductErrorCode.WORKSPACE_NOT_TRUSTED))

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Unexpected backend member access: {name}")


@pytest.mark.asyncio
async def test_facade_converts_only_typed_application_failures() -> None:
    facade = LocalApplication(_FailingBackend())  # type: ignore[arg-type]

    result = await facade.initialize()

    assert result == ApplicationResult.failure(
        _error(ProductErrorCode.WORKSPACE_NOT_TRUSTED)
    )


class _CrashingBackend:
    async def initialize_application(self) -> object:
        raise RuntimeError("secret-provider-token")

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Unexpected backend member access: {name}")


@pytest.mark.asyncio
async def test_facade_does_not_convert_unexpected_invariant_failures() -> None:
    facade = LocalApplication(_CrashingBackend())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="secret-provider-token"):
        await facade.initialize()
