from pathlib import Path

from awesome_agent.domain.enums import AgentKind, AgentStatus, ExecutionKind, RunIntent
from awesome_agent.domain.models import Agent, Run
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


async def test_conversation_run_can_persist_working_directory() -> None:
    repository = InMemoryRuntimeRepository()
    run = Run(
        goal="hello",
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route="conversation-turn",
        working_directory=Path("E:/project"),
        graph_thread_id="conversation:run-id",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )

    await repository.create_run(run, leader)

    loaded = await repository.get_run(run.id)
    assert loaded.intent is RunIntent.CONVERSATION
    assert loaded.execution_kind is ExecutionKind.CONVERSATION
    assert loaded.working_directory == Path("E:/project")
