from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.core.citations import Citation
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolOutput,
    ToolResult,
    ToolStatus,
)


def test_citation_accepts_https_source_locator_with_fragment() -> None:
    citation = Citation(
        id="S1",
        title="Architecture reference",
        url="https://example.com/reference?q=agent#runtime",
    )

    assert citation.url.endswith("#runtime")


def test_citation_accepts_largest_source_id() -> None:
    citation = Citation(
        id="S999999",
        title="Source",
        url="https://example.com",
    )

    assert citation.id == "S999999"


def test_citation_value_does_not_apply_network_address_policy() -> None:
    citation = Citation(
        id="S1",
        title="Local test source",
        url="https://127.0.0.1/reference",
    )

    assert citation.url == "https://127.0.0.1/reference"


@pytest.mark.parametrize(
    "citation_id",
    ["S0", "S01", "S1000000", "s1", "1", "S-1"],
)
def test_citation_rejects_invalid_source_id(citation_id: str) -> None:
    with pytest.raises(ValidationError):
        Citation(id=citation_id, title="Source", url="https://example.com")


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "line one\nline two",
        "line one\u2028line two",
        "source\x00",
        "source\x80",
        "x" * 501,
    ],
)
def test_citation_rejects_invalid_title(title: str) -> None:
    with pytest.raises(ValidationError):
        Citation(id="S1", title=title, url="https://example.com")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "example.com/source",
        "https:///source",
        "https://user@example.com/source",
        "https://user:secret@example.com/source",
        "https://example.com/source\nleak",
        "https://example.com/source path",
        "https://example.com\\source",
        "https://%40example.com/source",
        "https://example.com:99999/source",
        f"https://example.com/{'x' * 8_000}",
    ],
)
def test_citation_rejects_invalid_url(url: str) -> None:
    with pytest.raises(ValidationError):
        Citation(id="S1", title="Source", url=url)


def test_citation_is_strict() -> None:
    with pytest.raises(ValidationError, match="string_type"):
        Citation.model_validate(
            {
                "id": 1,
                "title": "Source",
                "url": "https://example.com",
            }
        )


def test_citation_is_closed() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Citation.model_validate(
            {
                "id": "S1",
                "title": "Source",
                "url": "https://example.com",
                "unknown": True,
            }
        )


def test_citation_is_frozen() -> None:
    citation = Citation(id="S1", title="Source", url="https://example.com")
    with pytest.raises(ValidationError):
        citation.title = "Changed"


def test_tool_contracts_default_citations_for_legacy_payloads() -> None:
    output = ToolOutput.model_validate({"content": "legacy"})
    result = ToolResult.model_validate(
        {
            "call_id": "call_1",
            "tool_name": "read_file",
            "status": "success",
            "content": "legacy",
        }
    )

    assert output.citations == ()
    assert result.citations == ()
    assert result.status is ToolStatus.SUCCESS


def test_tool_result_round_trips_citations() -> None:
    citation = Citation(
        id="S1",
        title="Source",
        url="https://example.com/reference#section",
    )
    result = ToolResult(
        call_id="call_1",
        tool_name="web_search",
        status=ToolStatus.SUCCESS,
        content="result",
        citations=(citation,),
    )

    restored = ToolResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.citations == (citation,)


def test_tool_error_result_rejects_citations() -> None:
    citation = Citation(
        id="S1",
        title="Source",
        url="https://example.com/reference",
    )

    with pytest.raises(ValidationError, match="must not include citations"):
        ToolResult(
            call_id="call_1",
            tool_name="web_search",
            status=ToolStatus.ERROR,
            content="request failed",
            error=ToolError(
                code=ToolErrorCode.EXECUTION_FAILED,
                message="request failed",
            ),
            citations=(citation,),
        )


@pytest.mark.parametrize("contract", [ToolOutput, ToolResult])
def test_tool_contracts_bound_citation_count(
    contract: type[ToolOutput | ToolResult],
) -> None:
    citation = Citation(id="S1", title="Source", url="https://example.com")
    payload: dict[str, object] = {
        "content": "result",
        "citations": (citation,) * 129,
    }
    if contract is ToolResult:
        payload.update(
            call_id="call_1",
            tool_name="web_search",
            status=ToolStatus.SUCCESS,
        )

    with pytest.raises(ValidationError, match="too_long"):
        contract.model_validate(payload)
