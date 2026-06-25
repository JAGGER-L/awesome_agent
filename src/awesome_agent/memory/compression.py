from awesome_agent.memory.models import ContextItem, ContextSummary
from awesome_agent.modeling import (
    ModelProvider,
    ModelRequest,
    SystemMessage,
    UserMessage,
)


class ContextCompressor:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def compress(self, items: list[ContextItem]) -> ContextSummary:
        source = "\n\n".join(f"[{item.event_id}] {item.content}" for item in items)
        turn = await self._provider.complete(
            ModelRequest(
                messages=[
                    SystemMessage(
                        content=(
                            "Summarize the execution context without inventing "
                            "facts. Preserve decisions, failures, evidence, "
                            "blockers, and next actions."
                        )
                    ),
                    UserMessage(content=source),
                ],
                max_output_tokens=1200,
            )
        )
        return ContextSummary(
            text=turn.assistant.content,
            source_event_ids=[item.event_id for item in items],
        )
