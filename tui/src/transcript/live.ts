import type { SurfaceState } from "../state/index.js";
import type {
  LiveTranscriptProjection,
  ToolItem,
  TranscriptBlock,
} from "./model.js";

export function projectLiveTurn(state: SurfaceState): LiveTranscriptProjection {
  const blocks: TranscriptBlock[] = [];
  const operation = state.active_operation;
  const turn = operation?.turn;
  if (turn?.assistant_text) {
    blocks.push({
      key: `live:${turn.id}:assistant`,
      kind: "assistant",
      text: turn.assistant_text,
    });
  }
  if (turn && turn.tool_order.length > 0) {
    const items = [...new Set(turn.tool_order)].flatMap(
      (callId): ToolItem[] => {
        const tool = turn.tools[callId];
        if (!tool) return [];
        return [
          {
            call_id: tool.call_id,
            name: tool.tool_name,
            outcome:
              tool.status === "completed"
                ? "success"
                : tool.status === "failed"
                  ? "error"
                  : tool.status === "cancelled"
                    ? "cancelled"
                    : "running",
            summary: tool.status === "running" ? "Running…" : tool.summary,
            duration_ms: 0,
            ...(tool.error_code === undefined
              ? {}
              : { error_code: tool.error_code }),
          },
        ];
      },
    );
    blocks.push({ key: `live:${turn.id}:tools`, kind: "tools", items });
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
