from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NotRequired, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from awesome_agent.domain.models import Run
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
)
from awesome_agent.runtime.graphs import RUNTIME_PROBE_GRAPH, RUNTIME_PROBE_VERSION

__all__ = [
    "RUNTIME_PROBE_GRAPH",
    "RUNTIME_PROBE_VERSION",
    "RuntimeProbeGraph",
    "RuntimeProbeState",
]


class RuntimeProbeState(TypedDict):
    run_id: str
    graph_name: str
    graph_version: int
    phase: str
    completed_steps: list[str]
    result_summary: NotRequired[str]


FaultHook = Callable[[str, RuntimeProbeState], Awaitable[None]]


class RuntimeProbeGraph:
    def __init__(
        self,
        saver: AsyncPostgresSaver,
        *,
        fault_hook: FaultHook | None = None,
    ) -> None:
        self.saver = saver
        self.fault_hook = fault_hook
        builder = StateGraph(RuntimeProbeState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("checkpoint_probe", self._checkpoint_probe)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "checkpoint_probe")
        builder.add_edge("checkpoint_probe", "finalize")
        builder.add_edge("finalize", END)
        self.graph = builder.compile(checkpointer=saver, name=RUNTIME_PROBE_GRAPH)

    async def execute(self, run: Run) -> tuple[RuntimeProbeState, bool]:
        self._validate_run(run)
        config: RunnableConfig = {
            "configurable": {
                "thread_id": run.graph_thread_id,
                "checkpoint_ns": "",
            }
        }
        checkpoint = await self.saver.aget_tuple(config)
        if checkpoint is None:
            result = await self.graph.ainvoke(
                {
                    "run_id": str(run.id),
                    "graph_name": RUNTIME_PROBE_GRAPH,
                    "graph_version": RUNTIME_PROBE_VERSION,
                    "phase": "created",
                    "completed_steps": [],
                },
                config,
                durability="sync",
            )
            return _state(result), False

        snapshot = await self.graph.aget_state(config)
        if not snapshot.next:
            return _state(snapshot.values), True
        result = await self.graph.ainvoke(
            None,
            config,
            durability="sync",
        )
        return _state(result), True

    def _validate_run(self, run: Run) -> None:
        if (
            run.graph_name != RUNTIME_PROBE_GRAPH
            or run.graph_version != RUNTIME_PROBE_VERSION
        ):
            raise IncompatibleGraphError(
                f"Unsupported graph identity: {run.graph_name}@{run.graph_version}"
            )
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")
        if run.workspace_path is None or not run.workspace_path.is_dir():
            raise CorruptRuntimeStateError("Run workspace is unavailable.")

    async def _initialize(self, state: RuntimeProbeState) -> RuntimeProbeState:
        await self._fault("initialize", state)
        return {
            **state,
            "phase": "initialized",
            "completed_steps": [*state["completed_steps"], "initialize"],
        }

    async def _checkpoint_probe(
        self,
        state: RuntimeProbeState,
    ) -> RuntimeProbeState:
        await self._fault("checkpoint_probe", state)
        return {
            **state,
            "phase": "checkpointed",
            "completed_steps": [
                *state["completed_steps"],
                "checkpoint_probe",
            ],
        }

    async def _finalize(self, state: RuntimeProbeState) -> RuntimeProbeState:
        await self._fault("finalize", state)
        return {
            **state,
            "phase": "completed",
            "completed_steps": [*state["completed_steps"], "finalize"],
            "result_summary": "Durable runtime probe completed.",
        }

    async def _fault(self, node: str, state: RuntimeProbeState) -> None:
        if self.fault_hook is not None:
            await self.fault_hook(node, state)


def _state(value: object) -> RuntimeProbeState:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Runtime probe returned invalid state.")
    required = {
        "run_id",
        "graph_name",
        "graph_version",
        "phase",
        "completed_steps",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Runtime probe state is incomplete.")
    return cast(RuntimeProbeState, value)
