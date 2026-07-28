from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, Field, field_validator

from awesome_agent.core.citations import Citation
from awesome_agent.core.tools.builtins._web import web_provider_failure
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolInvocationDescription,
    ToolOutput,
    ToolPresentation,
    ToolSpec,
)
from awesome_agent.core.tools.registry import (
    RegisteredTool,
    ToolReplaySafety,
)
from awesome_agent.core.tools.web_contracts import (
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from awesome_agent.core.tools.web_errors import (
    WebProviderError,
)

MAX_WEB_SEARCH_OUTPUT_CHARS = 28_000
WEB_SEARCH_TOOL_TIMEOUT_SECONDS = 20.0


class WebSearchArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=2_000)
    max_results: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        return WebSearchRequest(query=value).query


WEB_SEARCH_SPEC = ToolSpec(
    name="web_search",
    description="Search the public web for current information",
    input_schema=WebSearchArguments.model_json_schema(),
    capability="network.read",
    read_only=True,
    display_metadata={"verb": "Search"},
)


def create_web_search_handler(
    provider: WebSearchProvider,
    *,
    blocked_domains: tuple[str, ...] = (),
) -> ToolHandler:
    canonical_domains = WebSearchRequest(
        query="validation",
        blocked_domains=blocked_domains,
    ).blocked_domains

    async def web_search(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(WebSearchArguments, arguments)
        request = WebSearchRequest(
            query=options.query,
            max_results=options.max_results,
            blocked_domains=canonical_domains,
        )
        context.capability_quotas.consume("network.read")
        try:
            response = await provider.search(request)
        except WebProviderError as error:
            raise web_provider_failure(error) from None

        selected, output_truncated = _select_results(response)
        allocated = [
            context.citation_allocator.allocate(title=result.title, url=result.url)
            for result in selected
        ]
        content = _render_response(selected, allocated)
        citations = tuple(dict.fromkeys(allocated))
        rendered_count = len(selected)
        truncated = response.truncated or output_truncated
        return ToolOutput(
            content=content,
            metadata={
                "result_count": rendered_count,
                "truncated": truncated,
            },
            presentation=ToolPresentation(
                verb="Search",
                target="Web",
                outcome="Found",
                summary=(
                    f"{rendered_count} {'result' if rendered_count == 1 else 'results'}"
                ),
                detail_truncated_count=(
                    max(0, len(response.results) - rendered_count)
                    if truncated
                    else None
                ),
            ),
            citations=citations,
        )

    return web_search


def create_web_search_registration(
    provider: WebSearchProvider,
    *,
    blocked_domains: tuple[str, ...] = (),
) -> RegisteredTool:
    return RegisteredTool(
        spec=WEB_SEARCH_SPEC,
        input_model=WebSearchArguments,
        handler=create_web_search_handler(
            provider,
            blocked_domains=blocked_domains,
        ),
        describe=_describe_web_search,
        admit=_admit_web_search,
        replay_safety=ToolReplaySafety.NON_REPLAYABLE,
        timeout_resolver=_search_timeout,
    )


def _describe_web_search(arguments: BaseModel) -> ToolInvocationDescription:
    del arguments
    return ToolInvocationDescription(
        verb="Search",
        display_target="Web",
        approval_operation="send a search query to Tavily",
        approval_target=(
            "Tavily; the query is sent under the Tavily Privacy Policy "
            "(https://www.tavily.com/privacy) and Platform Terms "
            "(https://www.tavily.com/terms)"
        ),
    )


def _admit_web_search(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> None:
    del arguments
    context.capability_quotas.require_remaining("network.read")


def _search_timeout(arguments: BaseModel) -> float:
    del arguments
    return WEB_SEARCH_TOOL_TIMEOUT_SECONDS


def _select_results(
    response: WebSearchResponse,
) -> tuple[tuple[WebSearchResult, ...], bool]:
    selected: list[WebSearchResult] = []
    truncated = False
    for result in response.results:
        candidate = [
            *selected,
            result,
        ]
        content = json.dumps(
            {
                "results": [
                    {
                        "source_id": "S128",
                        "title": item.title,
                        "url": item.url,
                        "snippet": item.snippet,
                    }
                    for item in candidate
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(content) > MAX_WEB_SEARCH_OUTPUT_CHARS:
            truncated = True
            break
        selected.append(result)
    return tuple(selected), truncated


def _render_response(
    results: tuple[WebSearchResult, ...],
    citations: list[Citation],
) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "source_id": citation.id,
                    "title": result.title,
                    "url": result.url,
                    "snippet": result.snippet,
                }
                for result, citation in zip(results, citations, strict=True)
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


__all__ = [
    "MAX_WEB_SEARCH_OUTPUT_CHARS",
    "WEB_SEARCH_SPEC",
    "WebSearchArguments",
    "create_web_search_handler",
    "create_web_search_registration",
]
