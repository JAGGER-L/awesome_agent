import type { MethodValue } from "../protocol/index.js";
import { hydrateThreadPage } from "./hydrate.js";
import type {
  LiveTranscriptProjection,
  ReconciledTurn,
  TranscriptBlock,
} from "./model.js";

export class ReconciliationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReconciliationError";
  }
}

export function reconcileCompletedTurn(
  live: LiveTranscriptProjection,
  page: MethodValue["thread.read"],
): ReconciledTurn {
  const failure = validateDurableTurn(live, page);
  if (failure) {
    return {
      blocks: [
        ...live.blocks,
        {
          key: `reconcile:error:${live.operation_id ?? live.turn_id ?? "unknown"}`,
          kind: "error",
          code: "transcript_not_reconciled",
          message: failure.message,
        },
      ],
    };
  }
  const durable = hydrateThreadPage(page);
  if (!live.turn_id) return durable;
  const turn = page.view.turns.find(
    (candidate) => candidate.id === live.turn_id,
  );
  const assistantEntry = page.view.entries.find(
    (entry) => entry.id === turn?.assistant_entry_id,
  );
  const durableTools = new Map(
    page.view.tool_activities
      .filter((tool) => tool.turn_id === live.turn_id)
      .map((tool) => [tool.call_id, tool]),
  );
  const liveAssistantText = live.blocks
    .filter((block) => block.kind === "assistant")
    .map((block) => block.text)
    .join("");
  const retainAssistantSegments =
    assistantEntry !== undefined &&
    liveAssistantText === assistantEntry.content;
  const activity: TranscriptBlock[] = [];
  for (const block of live.blocks) {
    if (
      block.kind !== "thinking" &&
      block.kind !== "tools" &&
      block.kind !== "assistant" &&
      block.kind !== "worked"
    )
      continue;
    if (block.kind === "assistant" && !retainAssistantSegments) continue;
    if (block.kind !== "tools") {
      activity.push(block);
      continue;
    }
    activity.push({
      ...block,
      items: block.items.map((item) => {
        const persisted = durableTools.get(item.call_id);
        if (!persisted) return item;
        const { error_code: _oldError, ...safeLive } = item;
        void _oldError;
        return {
          ...safeLive,
          outcome: persisted.outcome,
          summary: persisted.result_summary,
          duration_ms: persisted.duration_ms,
          ...(persisted.error_code === undefined
            ? {}
            : { error_code: persisted.error_code }),
        };
      }),
    });
  }
  if (!retainAssistantSegments && assistantEntry) {
    activity.push({
      key: `entry:${assistantEntry.id}`,
      kind: "assistant",
      text: assistantEntry.content,
    });
  }
  const replacedKeys = new Set([
    `turn:${live.turn_id}:tools`,
    ...(assistantEntry ? [`entry:${assistantEntry.id}`] : []),
  ]);
  const firstActivityIndex = durable.blocks.findIndex((block) =>
    replacedKeys.has(block.key),
  );
  const withoutActivity = durable.blocks.filter(
    (block) => !replacedKeys.has(block.key),
  );
  const insertion = Math.max(0, firstActivityIndex);
  return {
    blocks: [
      ...withoutActivity.slice(0, insertion),
      ...activity,
      ...withoutActivity.slice(insertion),
    ],
  };
}

function validateDurableTurn(
  live: LiveTranscriptProjection,
  page: MethodValue["thread.read"],
): ReconciliationError | undefined {
  if (!live.turn_id) return undefined;
  const turn = page.view.turns.find(
    (candidate) => candidate.id === live.turn_id,
  );
  if (!turn)
    return new ReconciliationError(
      "Completed Turn is missing from durable Thread state.",
    );
  if (turn.status === "in_progress") {
    return new ReconciliationError(
      "Durable Turn has not reached a terminal state.",
    );
  }
  if (turn.status === "completed") {
    if (!turn.assistant_entry_id) {
      return new ReconciliationError(
        "Completed Turn has no durable Assistant Entry identity.",
      );
    }
    if (
      !page.view.entries.some((entry) => entry.id === turn.assistant_entry_id)
    ) {
      return new ReconciliationError("Durable Assistant Entry is missing.");
    }
  }
  const liveCalls = live.blocks
    .filter((block) => block.kind === "tools")
    .flatMap((block) => block.items.map((item) => item.call_id));
  const durableCalls = new Set(
    page.view.tool_activities
      .filter((tool) => tool.turn_id === live.turn_id)
      .map((tool) => tool.call_id),
  );
  const missing = liveCalls.find((callId) => !durableCalls.has(callId));
  return missing
    ? new ReconciliationError(`Durable ToolActivity is missing for ${missing}.`)
    : undefined;
}
