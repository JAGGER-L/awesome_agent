import type { SurfaceState } from "../state/index.js";
import type {
  LiveTranscriptProjection,
  ToolItem,
  TranscriptBlock,
} from "./model.js";
import { formatDuration, reasoningElapsedMarker } from "./reasoning.js";

export function projectLiveTurn(state: SurfaceState): LiveTranscriptProjection {
  const blocks: TranscriptBlock[] = [];
  const operation = state.active_operation;
  const turn = operation?.turn;
  if (turn) {
    let pendingTools: ToolItem[] = [];
    const flushTools = () => {
      if (pendingTools.length === 0) return;
      blocks.push({
        key: `live:${turn.id}:tools:${pendingTools[0]?.call_id ?? "sequence"}`,
        kind: "tools",
        items: pendingTools,
      });
      pendingTools = [];
    };
    for (const item of turn.timeline) {
      if (item.kind === "thinking" && item.duration_ms !== undefined) {
        blocks.push({
          key: `live:${item.id}`,
          kind: "reasoning_marker",
          label: reasoningElapsedMarker(item.duration_ms),
        });
      } else if (item.kind === "assistant") {
        flushTools();
        blocks.push({
          key: `live:${item.id}`,
          kind: "assistant",
          text: item.text,
        });
      } else if (item.kind === "tool") {
        const tool: ToolItem = {
          call_id: item.call_id,
          name: item.tool_name,
          verb: item.verb,
          ...(item.target === undefined ? {} : { target: item.target }),
          outcome:
            item.status === "completed"
              ? "success"
              : item.status === "failed"
                ? "error"
                : item.status === "cancelled"
                  ? "cancelled"
                  : "running",
          ...(item.outcome === undefined
            ? {}
            : { presentation_outcome: item.outcome }),
          summary: item.status === "running" ? "Running…" : item.summary,
          ...(item.detail === undefined ? {} : { detail: item.detail }),
          ...(item.duration_ms === undefined
            ? {}
            : { duration_ms: item.duration_ms }),
          ...(item.error_code === undefined
            ? {}
            : { error_code: item.error_code }),
        };
        pendingTools.push(tool);
      }
    }
    flushTools();
    if (turn.duration_ms !== undefined) {
      blocks.push({
        key: `live:${turn.id}:duration`,
        kind: "status",
        message: `Worked for ${formatDuration(turn.duration_ms)}`,
      });
    }
  }
  if (state.latest_change) {
    blocks.push({
      key: `live:change:${state.latest_change.change_set_id}`,
      kind: "change",
      change_set_id: state.latest_change.change_set_id,
      paths: state.latest_change.paths,
      lifecycle: "live",
      reversibility: state.latest_change.reversibility,
    });
  }
  for (const warning of state.warnings) {
    blocks.push({
      key: `live:warning:${warning.code}`,
      kind: "warning",
      code: warning.code,
      message: warning.message,
    });
  }
  return {
    blocks,
    ...(operation === undefined ? {} : { operation_id: operation.id }),
    ...(turn === undefined ? {} : { turn_id: turn.id }),
    reasoning_text: turn?.reasoning_text ?? "",
    ...(state.usage === undefined ? {} : { usage: state.usage }),
    terminal: operation !== undefined && operation.status !== "active",
  };
}
