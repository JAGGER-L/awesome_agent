from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from awesome_agent.application.composition import compose_local_application
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelRequest,
    ModelTurn,
    SelectedModel,
    StopReason,
    TextDelta,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.protocol.stdio import (
    JsonLineWriter,
    ProtocolEventSink,
    serve_stdio,
)


class _Stdout:
    async def write(self, data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


class FakeGateway:
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.calls = 0

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        self.calls += 1
        supplied = "\n".join(message.content for message in request.messages)
        if "wait forever" in supplied:
            await asyncio.sleep(3_600)
        if "use tool" in supplied and self.calls == 1:
            yield TurnCompleted(
                turn=ModelTurn(
                    provider=selected.provider,
                    model=selected.model,
                    assistant=AssistantMessage(
                        tool_calls=(
                            ToolCall(
                                call_id="call_read",
                                name="read_file",
                                arguments_json='{"path":"sample.txt"}',
                            ),
                        )
                    ),
                    stop_reason=StopReason.TOOL_CALLS,
                )
            )
            return
        yield TextDelta(text="fixture done")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="fixture done"),
                stop_reason=StopReason.COMPLETED,
            )
        )

    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn:
        completed = [
            event.turn
            async for event in self.stream(selected, request)
            if isinstance(event, TurnCompleted)
        ]
        return completed[0]


async def run() -> None:
    provider = os.environ.get("AWESOME_FAKE_PROVIDER", "deepseek")
    print("fixture core log", file=sys.stderr, flush=True)
    home = Path(os.environ["AWESOME_HOME"])
    workspace = Path(os.environ["AWESOME_WORKSPACE"])
    writer = JsonLineWriter(_Stdout())

    def gateway_factory(selected_provider: str, selected_model: str) -> object:
        return cast(object, FakeGateway(selected_provider, selected_model))

    secret_name = "DEEPSEEK_API_KEY" if provider == "deepseek" else "MOONSHOT_API_KEY"
    facade = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=ProtocolEventSink(writer),
        environ={secret_name: "fake-key"},
        gateway_factory=cast(Any, gateway_factory),
    )
    await serve_stdio(facade, writer=writer)


if __name__ == "__main__":
    asyncio.run(run())
