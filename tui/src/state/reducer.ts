import type { EventEnvelope } from "../protocol/index.js";
import {
  appendReasoningTail,
  reasoningElapsedMarker,
} from "../transcript/reasoning.js";
import type { SurfaceAction } from "./actions.js";
import type { SurfaceState, ToolProjection, TurnProjection } from "./model.js";

export function initialSurfaceState(): SurfaceState {
  return { connection: "idle", event_sequence: 0, warnings: [] };
}

function fatal(
  state: SurfaceState,
  code: string,
  message: string,
): SurfaceState {
  return { ...state, connection: "fatal", fatal: { code, message } };
}

function updateTurn(
  state: SurfaceState,
  update: (turn: TurnProjection) => TurnProjection,
): SurfaceState {
  const operation = state.active_operation;
  if (!operation?.turn)
    return fatal(state, "surface_invariant", "Turn projection is missing");
  return {
    ...state,
    active_operation: { ...operation, turn: update(operation.turn) },
  };
}

function reduceEvent(state: SurfaceState, event: EventEnvelope): SurfaceState {
  const next = { ...state, event_sequence: event.sequence };
  switch (event.payload.kind) {
    case "operation.started":
      if (state.active_operation?.status === "active") {
        return fatal(
          next,
          "operation_overlap",
          "Operation started while another is active",
        );
      }
      return {
        ...next,
        active_operation: { id: event.operation_id ?? "", status: "active" },
      };
    case "operation.completed":
    case "operation.failed":
    case "operation.cancelled": {
      const operation = state.active_operation;
      if (
        !operation ||
        operation.id !== event.operation_id ||
        operation.status !== "active"
      ) {
        return fatal(
          next,
          "operation_terminal_invalid",
          "Operation terminal has no active start",
        );
      }
      return {
        ...next,
        active_operation: {
          ...operation,
          status:
            event.payload.kind === "operation.completed"
              ? "completed"
              : event.payload.kind === "operation.failed"
                ? "failed"
                : "cancelled",
        },
      };
    }
    case "turn.started": {
      const operation = state.active_operation;
      if (
        operation?.status !== "active" ||
        operation.turn?.status === "active"
      ) {
        return fatal(
          next,
          "turn_start_invalid",
          "Turn start requires one active Operation",
        );
      }
      return {
        ...next,
        active_operation: {
          ...operation,
          turn: {
            id: event.turn_id ?? "",
            status: "active",
            started_at: event.timestamp,
            assistant_text: "",
            reasoning_text: "",
            reasoning_seen: false,
            tools: {},
            tool_order: [],
          },
        },
      };
    }
    case "turn.completed":
    case "turn.failed":
    case "turn.cancelled": {
      const turn = state.active_operation?.turn;
      if (!turn || turn.id !== event.turn_id || turn.status !== "active") {
        return fatal(
          next,
          "turn_terminal_invalid",
          "Turn terminal has no active start",
        );
      }
      return updateTurn(next, (turn) => {
        const elapsed =
          Date.parse(event.timestamp) - Date.parse(turn.started_at);
        return {
          ...turn,
          status:
            event.payload.kind === "turn.completed"
              ? "completed"
              : event.payload.kind === "turn.failed"
                ? "failed"
                : "cancelled",
          reasoning_text: "",
          ...(turn.reasoning_seen
            ? { reasoning_marker: reasoningElapsedMarker(elapsed) }
            : {}),
        };
      });
    }
    case "tool.started": {
      const payload = event.payload;
      const turn = state.active_operation?.turn;
      if (!turn || turn.tools[payload.call_id]) {
        return fatal(
          next,
          "tool_start_invalid",
          "Tool start is missing or duplicated",
        );
      }
      return updateTurn(next, (turn) => {
        const tool: ToolProjection = {
          call_id: payload.call_id,
          tool_name: payload.tool_name,
          status: "running",
          summary: "",
        };
        return {
          ...turn,
          tools: { ...turn.tools, [tool.call_id]: tool },
          tool_order: [...turn.tool_order, tool.call_id],
        };
      });
    }
    case "tool.completed":
    case "tool.failed":
    case "tool.cancelled": {
      const payload = event.payload;
      const current = state.active_operation?.turn?.tools[payload.call_id];
      if (current?.status !== "running") {
        return fatal(
          next,
          "tool_terminal_invalid",
          "Tool terminal has no active start",
        );
      }
      return updateTurn(next, (turn) => {
        const tool = turn.tools[payload.call_id];
        if (!tool) return turn;
        return {
          ...turn,
          tools: {
            ...turn.tools,
            [tool.call_id]: {
              ...tool,
              status:
                payload.kind === "tool.completed"
                  ? "completed"
                  : payload.kind === "tool.failed"
                    ? "failed"
                    : "cancelled",
              summary: payload.summary,
              ...(payload.error_code === undefined
                ? {}
                : { error_code: payload.error_code }),
            },
          },
        };
      });
    }
    case "usage.updated":
      return {
        ...next,
        usage: {
          input_tokens: event.payload.input_tokens,
          output_tokens: event.payload.output_tokens,
          reasoning_tokens: event.payload.reasoning_tokens,
          cache_read_tokens: event.payload.cache_read_tokens,
          cache_write_tokens: event.payload.cache_write_tokens,
        },
      };
    case "workspace.changed":
      return {
        ...next,
        latest_change: {
          change_set_id: event.payload.change_set_id,
          paths: event.payload.paths,
          reversibility: event.payload.reversibility,
        },
      };
    case "interaction.required":
      return {
        ...next,
        pending_interaction: {
          interaction_id: event.payload.interaction_id,
          interaction_kind: event.payload.interaction_kind,
          prompt: event.payload.prompt,
          operation: event.payload.operation,
          target: event.payload.target,
          ...(event.payload.capability === undefined
            ? {}
            : { capability: event.payload.capability }),
          choices: event.payload.choices,
        },
      };
    case "interaction.resolved": {
      const {
        pending_interaction: _pendingInteraction,
        ...withoutInteraction
      } = next;
      void _pendingInteraction;
      return withoutInteraction;
    }
    case "warning": {
      const payload = event.payload;
      return state.warnings.some((warning) => warning.code === payload.code)
        ? next
        : {
            ...next,
            warnings: [
              ...state.warnings,
              { code: payload.code, message: payload.message },
            ],
          };
    }
    default:
      return next;
  }
}

export function surfaceReducer(
  state: SurfaceState,
  action: SurfaceAction,
): SurfaceState {
  switch (action.type) {
    case "connection.start":
      return { ...state, connection: "starting" };
    case "connection.handshaking":
      return { ...state, connection: "handshaking" };
    case "handshake.trust_required":
      return { ...state, connection: "trust_required" };
    case "handshake.ready":
      return { ...state, connection: "ready" };
    case "hydrate.application":
      return { ...state, application: action.application };
    case "hydrate.thread":
      return { ...state, thread: action.thread };
    case "event.received":
      return reduceEvent(state, action.event);
    case "delta.received":
      if (
        state.active_operation?.status !== "active" ||
        state.active_operation.id !== action.delta.operation_id ||
        state.active_operation.turn?.status !== "active" ||
        state.active_operation.turn.id !== action.delta.turn_id
      ) {
        return fatal(
          state,
          "delta_identity_invalid",
          "Delta identity has no active Turn",
        );
      }
      return updateTurn(
        { ...state, event_sequence: action.delta.last_sequence },
        (turn) => ({
          ...turn,
          assistant_text:
            action.delta.delta_kind === "text"
              ? turn.assistant_text + action.delta.text
              : turn.assistant_text,
          reasoning_text:
            action.delta.delta_kind === "reasoning"
              ? appendReasoningTail(turn.reasoning_text, action.delta.text)
              : turn.reasoning_text,
          reasoning_seen:
            action.delta.delta_kind === "reasoning"
              ? true
              : turn.reasoning_seen,
        }),
      );
    case "transcript.reconciled":
      return {
        ...state,
        committed_transcript: action.result.blocks,
        transcript_persisted: action.result.persisted,
      };
    case "protocol.fatal":
      return fatal(state, action.code, action.message);
    case "core.exited":
      return {
        ...state,
        core_exit: { code: action.exit.code, signal: action.exit.signal },
      };
    case "reconnect.reset":
      return initialSurfaceState();
    case "surface.closed":
      return { ...state, connection: "closed" };
  }
}
