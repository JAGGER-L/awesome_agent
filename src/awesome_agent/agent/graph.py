from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from awesome_agent.agent.context import AgentRuntimeContext
from awesome_agent.agent.nodes import (
    call_model,
    compress_context,
    execute_one_tool,
    finalize,
    prepare_context,
    route_after_compression,
    route_after_model,
    route_after_prepare,
    route_after_tool,
)
from awesome_agent.agent.state import AgentState


def compile_agent_graph(
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph[
    AgentState,
    AgentRuntimeContext,
    AgentState,
    AgentState,
]:
    builder = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    builder.add_node("prepare_context", prepare_context)
    builder.add_node("call_model", call_model)
    builder.add_node("compress_context", compress_context)
    builder.add_node("execute_one_tool", execute_one_tool)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "prepare_context")
    builder.add_conditional_edges(
        "prepare_context",
        route_after_prepare,
        {
            "compress_context": "compress_context",
            "call_model": "call_model",
        },
    )
    builder.add_conditional_edges(
        "compress_context",
        route_after_compression,
        {"call_model": "call_model", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "call_model",
        route_after_model,
        {
            "compress_context": "compress_context",
            "execute_one_tool": "execute_one_tool",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "execute_one_tool",
        route_after_tool,
        {
            "execute_one_tool": "execute_one_tool",
            "call_model": "call_model",
        },
    )
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
