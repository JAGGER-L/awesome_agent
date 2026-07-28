from __future__ import annotations

import json
from typing import cast
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from awesome_agent.core.tools.builtins._web import web_provider_failure
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolInvocationDescription,
    ToolOutput,
    ToolPresentation,
    ToolSpec,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import RegisteredTool, ToolReplaySafety
from awesome_agent.core.tools.web_contracts import (
    MAX_WEB_FETCH_CONTENT_CHARACTERS,
    WebFetchProvider,
    WebFetchRequest,
    WebSearchRequest,
    web_fetch_urls_equivalent,
)
from awesome_agent.core.tools.web_errors import WebProviderError, WebProviderErrorCode

MAX_WEB_FETCH_CONTENT_CHARS = MAX_WEB_FETCH_CONTENT_CHARACTERS
MAX_WEB_FETCH_OUTPUT_CHARS = 28_000
WEB_FETCH_TOOL_TIMEOUT_SECONDS = 20.0


class WebFetchArguments(ToolArguments):
    url: str = Field(min_length=1, max_length=8_000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return WebFetchRequest(url=value).url


WEB_FETCH_SPEC = ToolSpec(
    name="web_fetch",
    description="Fetch readable content from one public HTTPS URL",
    input_schema=WebFetchArguments.model_json_schema(),
    capability="network.read",
    read_only=True,
    display_metadata={"verb": "Fetch"},
)


def create_web_fetch_handler(
    provider: WebFetchProvider,
    *,
    blocked_domains: tuple[str, ...] = (),
) -> ToolHandler:
    canonical_domains = _canonical_domains(blocked_domains)

    async def web_fetch(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(WebFetchArguments, arguments)
        request = _fetch_request(options, blocked_domains=canonical_domains)
        context.capability_quotas.consume("network.read")
        try:
            response = await provider.fetch(request)
        except WebProviderError as error:
            raise web_provider_failure(error) from None

        if not web_fetch_urls_equivalent(request.url, response.url):
            raise web_provider_failure(
                WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE)
            ) from None

        citation = context.citation_allocator.allocate(
            title=_citation_title(request.url),
            url=request.url,
        )
        body, content, truncated = _render_bounded_response(
            source_id=citation.id,
            url=request.url,
            body=response.content,
            provider_truncated=response.truncated,
        )
        return ToolOutput(
            content=content,
            metadata={
                "content_chars": len(body),
                "truncated": truncated,
            },
            presentation=ToolPresentation(
                verb="Fetch",
                target="Web",
                outcome="Fetched",
                summary=f"{len(body)} characters",
            ),
            citations=(citation,),
        )

    return web_fetch


def create_web_fetch_registration(
    provider: WebFetchProvider,
    *,
    blocked_domains: tuple[str, ...] = (),
) -> RegisteredTool:
    canonical_domains = _canonical_domains(blocked_domains)
    return RegisteredTool(
        spec=WEB_FETCH_SPEC,
        input_model=WebFetchArguments,
        handler=create_web_fetch_handler(
            provider,
            blocked_domains=canonical_domains,
        ),
        describe=_describe_web_fetch,
        admit=lambda arguments, context: _admit_web_fetch(
            arguments,
            context,
            blocked_domains=canonical_domains,
        ),
        replay_safety=ToolReplaySafety.NON_REPLAYABLE,
        timeout_resolver=_fetch_timeout,
    )


def _describe_web_fetch(arguments: BaseModel) -> ToolInvocationDescription:
    del arguments
    return ToolInvocationDescription(
        verb="Fetch",
        display_target="Web",
        approval_operation="send a requested URL to Tavily",
        approval_target=(
            "Tavily; the URL is sent under the Tavily Privacy Policy "
            "(https://www.tavily.com/privacy) and Platform Terms "
            "(https://www.tavily.com/terms)"
        ),
    )


def _admit_web_fetch(
    arguments: BaseModel,
    context: ToolExecutionContext,
    *,
    blocked_domains: tuple[str, ...],
) -> None:
    _fetch_request(
        cast(WebFetchArguments, arguments),
        blocked_domains=blocked_domains,
    )
    context.capability_quotas.require_remaining("network.read")


def _fetch_timeout(arguments: BaseModel) -> float:
    del arguments
    return WEB_FETCH_TOOL_TIMEOUT_SECONDS


def _canonical_domains(blocked_domains: tuple[str, ...]) -> tuple[str, ...]:
    return WebSearchRequest(
        query="validation",
        blocked_domains=blocked_domains,
    ).blocked_domains


def _fetch_request(
    arguments: WebFetchArguments,
    *,
    blocked_domains: tuple[str, ...],
) -> WebFetchRequest:
    request = WebFetchRequest(url=arguments.url)
    canonical_host = _canonical_hostname(request.url)
    if any(
        canonical_host == blocked or canonical_host.endswith(f".{blocked}")
        for blocked in blocked_domains
    ):
        raise ExpectedToolFailure(
            ToolErrorCode.WEB_REQUEST_REJECTED,
            "This URL is blocked by Web configuration.",
        )
    return request


def _citation_title(url: str) -> str:
    return f"Fetched content from {_canonical_hostname(url)}"


def _render_bounded_response(
    *,
    source_id: str,
    url: str,
    body: str,
    provider_truncated: bool,
) -> tuple[str, str, bool]:
    bounded_body = body[:MAX_WEB_FETCH_CONTENT_CHARS]
    truncated = provider_truncated or len(bounded_body) < len(body)
    rendered = _render_response(
        source_id=source_id,
        url=url,
        body=bounded_body,
        truncated=truncated,
    )
    if len(rendered) <= MAX_WEB_FETCH_OUTPUT_CHARS:
        return bounded_body, rendered, truncated

    truncated = True
    lower = 0
    upper = len(bounded_body)
    while lower < upper:
        candidate_length = (lower + upper + 1) // 2
        candidate = _render_response(
            source_id=source_id,
            url=url,
            body=bounded_body[:candidate_length],
            truncated=truncated,
        )
        if len(candidate) <= MAX_WEB_FETCH_OUTPUT_CHARS:
            lower = candidate_length
        else:
            upper = candidate_length - 1
    bounded_body = bounded_body[:lower]
    return (
        bounded_body,
        _render_response(
            source_id=source_id,
            url=url,
            body=bounded_body,
            truncated=truncated,
        ),
        truncated,
    )


def _render_response(
    *,
    source_id: str,
    url: str,
    body: str,
    truncated: bool,
) -> str:
    return json.dumps(
        {
            "source_id": source_id,
            "url": url,
            "content": body,
            "truncated": truncated,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _canonical_hostname(url: str) -> str:
    hostname = urlsplit(url).hostname
    if hostname is None:
        raise RuntimeError("Validated Web URL has no hostname")
    return hostname.lower().rstrip(".").encode("idna").decode("ascii")


__all__ = [
    "MAX_WEB_FETCH_CONTENT_CHARS",
    "MAX_WEB_FETCH_OUTPUT_CHARS",
    "WEB_FETCH_SPEC",
    "WEB_FETCH_TOOL_TIMEOUT_SECONDS",
    "WebFetchArguments",
    "create_web_fetch_handler",
    "create_web_fetch_registration",
]
