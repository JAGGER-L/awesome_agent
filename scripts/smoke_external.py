from __future__ import annotations

import asyncio
from uuid import uuid4

from awesome_agent.memory.external import Mem0PlatformMemory
from awesome_agent.memory.models import MemoryCandidate, MemoryKind, MemorySource
from awesome_agent.providers.base import ModelRequest
from awesome_agent.providers.deepseek import DeepSeekProvider
from awesome_agent.settings import Settings


async def main() -> None:
    settings = Settings()
    if settings.deepseek_api_key is None:
        raise RuntimeError("AWESOME_AGENT_DEEPSEEK_API_KEY is required.")
    if settings.mem0_api_key is None:
        raise RuntimeError("AWESOME_AGENT_MEM0_API_KEY is required.")

    api_key = settings.deepseek_api_key.get_secret_value()
    for model in [settings.deepseek_pro_model, settings.deepseek_flash_model]:
        result = await DeepSeekProvider(
            api_key=api_key,
            model=model,
            base_url=settings.deepseek_base_url,
            thinking_enabled=settings.deepseek_thinking_enabled,
            reasoning_effort=settings.deepseek_reasoning_effort,
        ).generate(
            ModelRequest(
                system_prompt="Return a concise health-check response.",
                user_prompt="Reply with exactly OK.",
                max_output_tokens=256,
            )
        )
        if not result.text.strip():
            raise RuntimeError(f"{model} returned an empty response.")
        print(f"DeepSeek {model}: ok ({result.usage.output_tokens} output tokens)")

    marker = f"awesome-agent smoke {uuid4()}"
    memory = Mem0PlatformMemory(api_key=settings.mem0_api_key.get_secret_value())
    candidate = MemoryCandidate(
        kind=MemoryKind.OPERATIONAL,
        content=marker,
        source=MemorySource.AGENT_EXPERIENCE,
    )
    user_id = "awesome-agent-smoke"
    project_id = "awesome-agent"
    if not await memory.add(candidate, user_id=user_id, project_id=project_id):
        raise RuntimeError("Mem0 add failed.")

    results = []
    for _ in range(20):
        results = await memory.search(
            marker,
            user_id=user_id,
            project_id=project_id,
        )
        if results:
            break
        await asyncio.sleep(3)
    else:
        raise RuntimeError("Mem0 smoke memory was not found.")

    for record in results:
        if not await memory.delete(record.id):
            raise RuntimeError("Mem0 cleanup failed.")
    print(f"Mem0: ok ({len(results)} temporary memories deleted)")


if __name__ == "__main__":
    asyncio.run(main())
