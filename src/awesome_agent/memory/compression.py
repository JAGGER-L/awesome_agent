from awesome_agent.memory.models import ContextItem, ContextSummary
from awesome_agent.providers.base import ModelProvider, ModelRequest


class ContextCompressor:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def compress(self, items: list[ContextItem]) -> ContextSummary:
        source = "\n\n".join(f"[{item.event_id}] {item.content}" for item in items)
        result = await self._provider.generate(
            ModelRequest(
                system_prompt=(
                    "Summarize the execution context without inventing facts. "
                    "Preserve decisions, failures, evidence, blockers, and "
                    "next actions."
                ),
                user_prompt=source,
                max_output_tokens=1200,
            )
        )
        return ContextSummary(
            text=result.text,
            source_event_ids=[item.event_id for item in items],
        )
