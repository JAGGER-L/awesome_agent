import type { MethodValue } from "../protocol/index.js";
import type {
  LiveTranscriptProjection,
  TerminalTurnReconciliation,
  ToolItem,
  TranscriptBlock,
} from "./model.js";

export class ReconciliationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReconciliationError";
  }
}

export function reconcileTerminalTurn(
  live: LiveTranscriptProjection,
  page: MethodValue["thread.read"],
): TerminalTurnReconciliation {
  if (!live.operation_id || !live.turn_id) {
    throw new ReconciliationError(
      "Terminal reconciliation requires Operation and Turn identities.",
    );
  }
  const failure = validateDurableTurn(live, page);
  if (failure) {
    return {
      operation_id: live.operation_id,
      turn_id: live.turn_id,
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
    turn?.status !== "completed" ||
    (assistantEntry !== undefined &&
      liveAssistantText === assistantEntry.content);
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
  if (
    !activity.some((block) => block.kind === "tools") &&
    durableTools.size > 0
  ) {
    activity.push({
      key: `turn:${live.turn_id}:tools`,
      kind: "tools",
      items: [...durableTools.values()]
        .sort((left, right) => left.sequence - right.sequence)
        .map(durableToolItem),
    });
  }
  if (!retainAssistantSegments && assistantEntry) {
    activity.push({
      key: `entry:${assistantEntry.id}`,
      kind: "assistant",
      text: assistantEntry.content,
    });
  }
  if (turn?.status === "failed" || turn?.status === "cancelled") {
    activity.push({
      key: `turn:${live.turn_id}:error`,
      kind: "error",
      code: turn.error_code ?? turn.status,
      message: turn.termination_reason ?? `Turn ${turn.status}`,
    });
  }
  for (const change of page.change_sets.filter(
    (item) => item.turn_id === live.turn_id,
  )) {
    activity.push({
      key: `change:${change.change_set_id}`,
      kind: "change",
      change_set_id: change.change_set_id,
      paths: change.changed_paths,
      lifecycle: change.lifecycle,
      reversibility: change.reversibility,
    });
  }
  return {
    operation_id: live.operation_id,
    turn_id: live.turn_id,
    blocks: activity,
  };
}

function durableToolItem(
  tool: MethodValue["thread.read"]["view"]["tool_activities"][number],
): ToolItem {
  return {
    call_id: tool.call_id,
    name: tool.tool_name,
    verb: toolVerb(tool.tool_name),
    outcome: tool.outcome,
    started_at: tool.created_at,
    summary: tool.result_summary,
    duration_ms: tool.duration_ms,
    ...(tool.error_code === undefined ? {} : { error_code: tool.error_code }),
  };
}

function toolVerb(name: string): string {
  const known: Record<string, string> = {
    delete: "Delete",
    edit_file: "Edit",
    execute: "Run",
    glob: "Glob",
    grep: "Grep",
    ls: "List",
    read_file: "Read",
    write_file: "Write",
  };
  return known[name] ?? name;
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
