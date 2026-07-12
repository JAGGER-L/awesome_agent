import type { EventEnvelope } from "../protocol/index.js";
import { mergeTranscriptBlocks } from "../transcript/merge.js";
import type { TranscriptBlock } from "../transcript/model.js";
import { appendReasoningTail } from "../transcript/reasoning.js";
import type { SurfaceAction } from "./actions.js";
import type { CoalescedDelta } from "./delta-batcher.js";
import type {
  SurfaceState,
  TimelineProjection,
  ToolProjection,
  TurnProjection,
} from "./model.js";

export function initialSurfaceState(): SurfaceState {
  return {
    connection: "idle",
    thread_generation: 0,
    event_sequence: 0,
    warnings: [],
  };
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

function closeThinking(
  timeline: readonly TimelineProjection[],
  endedAt: string,
): readonly TimelineProjection[] {
  const activeIndex = timeline.findLastIndex(
    (item) => item.kind === "thinking" && item.duration_ms === undefined,
  );
  if (activeIndex < 0) return timeline;
  return timeline.map((item, index) =>
    index === activeIndex && item.kind === "thinking"
      ? {
          ...item,
          duration_ms: Math.max(
            0,
            Date.parse(endedAt) - Date.parse(item.started_at),
          ),
        }
      : item,
  );
}

function projectDelta(
  turn: TurnProjection,
  delta: CoalescedDelta,
): TurnProjection {
  if (delta.delta_kind === "reasoning") {
    const hasActiveThinking = turn.timeline.some(
      (item) => item.kind === "thinking" && item.duration_ms === undefined,
    );
    return {
      ...turn,
      timeline: hasActiveThinking
        ? turn.timeline
        : [
            ...turn.timeline,
            {
              kind: "thinking",
              id: `thinking:${turn.thinking_sequence}`,
              started_at: delta.first_timestamp,
            },
          ],
      reasoning_text: appendReasoningTail(turn.reasoning_text, delta.text),
      thinking_sequence: hasActiveThinking
        ? turn.thinking_sequence
        : turn.thinking_sequence + 1,
    };
  }
  const timeline = closeThinking(turn.timeline, delta.first_timestamp);
  const last = timeline.at(-1);
  return {
    ...turn,
    timeline:
      last?.kind === "assistant"
        ? [...timeline.slice(0, -1), { ...last, text: last.text + delta.text }]
        : [
            ...timeline,
            {
              kind: "assistant",
              id: `assistant:${turn.id}:${timeline.filter((item) => item.kind === "assistant").length + 1}`,
              text: delta.text,
            },
          ],
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
            reasoning_text: "",
            timeline: [],
            thinking_sequence: 0,
          },
        },
      };
    }
    case "turn.completed":
    case "turn.failed":
    case "turn.cancelled": {
      const payload = event.payload;
      const turn = state.active_operation?.turn;
      if (!turn || turn.id !== event.turn_id || turn.status !== "active") {
        return fatal(
          next,
          "turn_terminal_invalid",
          "Turn terminal has no active start",
        );
      }
      return updateTurn(next, (turn) => {
        return {
          ...turn,
          status:
            payload.kind === "turn.completed"
              ? "completed"
              : payload.kind === "turn.failed"
                ? "failed"
                : "cancelled",
          reasoning_text: "",
          timeline: closeThinking(turn.timeline, event.timestamp),
          duration_ms: payload.duration_ms,
        };
      });
    }
    case "tool.started": {
      const payload = event.payload;
      const turn = state.active_operation?.turn;
      if (
        !turn ||
        turn.timeline.some(
          (item) => item.kind === "tool" && item.call_id === payload.call_id,
        )
      ) {
        return fatal(
          next,
          "tool_start_invalid",
          "Tool start is missing or duplicated",
        );
      }
      return updateTurn(next, (turn) => {
        const tool: ToolProjection = {
          kind: "tool",
          call_id: payload.call_id,
          tool_name: payload.tool_name,
          status: "running",
          verb: payload.verb,
          ...(payload.target === undefined ? {} : { target: payload.target }),
          summary: "",
        };
        return {
          ...turn,
          timeline: [...closeThinking(turn.timeline, event.timestamp), tool],
        };
      });
    }
    case "tool.completed":
    case "tool.failed":
    case "tool.cancelled": {
      const payload = event.payload;
      const current = state.active_operation?.turn?.timeline.find(
        (item) => item.kind === "tool" && item.call_id === payload.call_id,
      );
      if (current?.kind !== "tool" || current.status !== "running") {
        return fatal(
          next,
          "tool_terminal_invalid",
          "Tool terminal has no active start",
        );
      }
      return updateTurn(next, (turn) => {
        let matched = false;
        const timeline = turn.timeline.map((item) => {
          if (item.kind !== "tool" || item.call_id !== payload.call_id) {
            return item;
          }
          matched = true;
          return {
            ...item,
            status:
              payload.kind === "tool.completed"
                ? ("completed" as const)
                : payload.kind === "tool.failed"
                  ? ("failed" as const)
                  : ("cancelled" as const),
            verb: payload.verb,
            ...(payload.target === undefined ? {} : { target: payload.target }),
            outcome: payload.outcome,
            summary: payload.summary,
            ...(payload.detail === undefined ? {} : { detail: payload.detail }),
            duration_ms: payload.duration_ms,
            ...(payload.error_code === undefined
              ? {}
              : { error_code: payload.error_code }),
          };
        });
        if (!matched) return turn;
        return { ...turn, timeline };
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
    case "thread.replaced":
      return {
        connection: state.connection,
        event_sequence: state.event_sequence,
        thread_generation: state.thread_generation + 1,
        application: action.application,
        thread: action.thread,
        warnings: [],
        committed_transcript: action.transcript,
        transcript_persisted: true,
      };
    case "event.received":
      return action.generation === state.thread_generation
        ? reduceEvent(state, action.event)
        : {
            ...state,
            event_sequence: Math.max(
              state.event_sequence,
              action.event.sequence,
            ),
          };
    case "delta.received":
      if (action.generation !== state.thread_generation) {
        return {
          ...state,
          event_sequence: Math.max(
            state.event_sequence,
            action.delta.last_sequence,
          ),
        };
      }
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
        (turn) => projectDelta(turn, action.delta),
      );
    case "transcript.reconciled":
      if (action.generation !== state.thread_generation) return state;
      return {
        ...state,
        committed_transcript: action.result.blocks,
        transcript_persisted: action.result.persisted,
      };
    case "transcript.command_result":
      if (action.generation !== state.thread_generation) return state;
      return {
        ...state,
        committed_transcript: mergeTranscriptBlocks(
          state.committed_transcript ?? [],
          [action.block],
        ),
      };
    case "transcript.command_result.replace":
      if (action.generation !== state.thread_generation) return state;
      return {
        ...state,
        committed_transcript: (state.committed_transcript ?? []).map((block) =>
          block.key === action.block.key ? action.block : block,
        ),
      };
    case "transcript.user.pending":
      if (action.generation !== state.thread_generation) return state;
      return {
        ...state,
        committed_transcript: mergeTranscriptBlocks(
          state.committed_transcript ?? [],
          [
            {
              key: `user:${action.client_message_id}`,
              kind: "user",
              client_message_id: action.client_message_id,
              status: "pending",
              text: action.text,
            },
          ],
        ),
        transcript_persisted: false,
      };
    case "transcript.user.accepted":
      if (action.generation !== state.thread_generation) return state;
      return updateUserMessage(state, action.client_message_id, (block) => ({
        ...block,
        status: "accepted",
      }));
    case "transcript.user.failed":
      if (action.generation !== state.thread_generation) return state;
      return updateUserMessage(state, action.client_message_id, (block) => ({
        ...block,
        status: "failed",
        error_message: action.message,
      }));
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

function updateUserMessage(
  state: SurfaceState,
  clientMessageId: string,
  update: (
    block: Extract<TranscriptBlock, { kind: "user" }>,
  ) => Extract<TranscriptBlock, { kind: "user" }>,
): SurfaceState {
  const blocks = state.committed_transcript ?? [];
  let matched = false;
  const committed = blocks.map((block) => {
    if (block.kind !== "user" || block.client_message_id !== clientMessageId) {
      return block;
    }
    matched = true;
    return update(block);
  });
  return matched ? { ...state, committed_transcript: committed } : state;
}
