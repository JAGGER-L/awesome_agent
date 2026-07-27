from types import MappingProxyType

import pytest

from awesome_agent.core.tools.context import CapabilityQuotaLedger
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure


def test_capability_quota_consumption_exposes_stable_usage_snapshot() -> None:
    ledger = CapabilityQuotaLedger(
        {"network.read": 3},
        used_counts={"network.read": 1},
    )

    assert ledger.remaining("network.read") == 2
    assert ledger.consume("network.read") == 2
    snapshot = ledger.used_counts

    assert isinstance(snapshot, MappingProxyType)
    assert snapshot == {"network.read": 2}
    assert ledger.remaining("network.read") == 1

    ledger.consume("network.read")

    assert snapshot == {"network.read": 2}
    assert ledger.used_counts == {"network.read": 3}


def test_capability_quota_fails_closed_without_limit_and_never_overdraws() -> None:
    missing = CapabilityQuotaLedger()

    with pytest.raises(ExpectedToolFailure) as missing_error:
        missing.require_remaining("network.read")

    assert missing_error.value.code is ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED
    assert missing.used_counts == {}

    ledger = CapabilityQuotaLedger({"network.read": 1})
    ledger.consume("network.read")

    with pytest.raises(ExpectedToolFailure) as exhausted_error:
        ledger.consume("network.read")

    assert exhausted_error.value.code is ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED
    assert exhausted_error.value.metadata == {"capability": "network.read"}
    assert ledger.used_counts == {"network.read": 1}


@pytest.mark.parametrize(
    ("limits", "used_counts"),
    [
        ({"network.read": -1}, None),
        ({"network.read": 1}, {"network.read": 2}),
        ({}, {"network.read": 0}),
    ],
)
def test_capability_quota_rejects_invalid_initial_state(
    limits: dict[str, int],
    used_counts: dict[str, int] | None,
) -> None:
    with pytest.raises(ValueError):
        CapabilityQuotaLedger(limits, used_counts=used_counts)
