import type { MethodValue } from "../protocol/index.js";
import { hydrateThreadPage } from "./hydrate.js";
import type { LiveTranscriptProjection, ReconciledTurn } from "./model.js";

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
      persisted: false,
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
  return {
    ...durable,
    blocks: [
      ...durable.blocks,
      ...live.blocks.filter((block) => block.kind === "status"),
    ],
    persisted: true,
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
