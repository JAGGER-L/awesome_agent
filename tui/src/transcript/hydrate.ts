import type { MethodValue } from "../protocol/index.js";
import type { TranscriptBlock, TranscriptProjection } from "./model.js";

type ThreadPage = MethodValue["thread.read"];

export function hydrateThreadPage(page: ThreadPage): TranscriptProjection {
  const blocks: TranscriptBlock[] = [];
  if (page.has_more) {
    blocks.push({
      key: "history:omitted",
      kind: "omitted_history",
      message:
        "Earlier transcript omitted · conversation summary remains available to Agent",
    });
  }

  const turnsByAssistant = new Map(
    page.view.turns
      .filter((turn) => turn.assistant_entry_id)
      .map((turn) => [turn.assistant_entry_id as string, turn]),
  );
  const toolsByTurn = groupTools(
    page.view.tool_activities.filter((tool) => tool.turn_id),
    (tool) => tool.turn_id as string,
  );
  const toolsByOperation = groupTools(
    page.view.tool_activities,
    (tool) => tool.operation_id,
  );

  for (const entry of [...page.view.entries].sort(
    (left, right) => left.sequence - right.sequence,
  )) {
    if (entry.kind === "user_message") {
      if (!entry.client_message_id) {
        throw new Error("Durable user message has no client identity.");
      }
      blocks.push({
        key: `user:${entry.client_message_id}`,
        kind: "user",
        client_message_id: entry.client_message_id,
        status: "persisted",
        text: entry.content,
      });
    } else if (entry.kind === "assistant_message") {
      const turn = turnsByAssistant.get(entry.id);
      if (turn)
        appendTools(blocks, `turn:${turn.id}`, toolsByTurn.get(turn.id) ?? []);
      if (turn?.status === "failed" || turn?.status === "cancelled") {
        blocks.push({
          key: `turn:${turn.id}:error`,
          kind: "error",
          code: turn.error_code ?? turn.status,
          message: turn.termination_reason ?? `Turn ${turn.status}`,
        });
      }
      appendChanges(blocks, page, turn?.id, undefined);
      blocks.push({
        key: `entry:${entry.id}`,
        kind: "assistant",
        text: entry.content,
        citations: entry.metadata.citations,
      });
    } else {
      blocks.push({
        key: `entry:${entry.id}`,
        kind: "direct_command",
        command: entry.content,
      });
      const operationId =
        typeof entry.metadata.operation_id === "string"
          ? entry.metadata.operation_id
          : undefined;
      if (operationId) {
        appendTools(
          blocks,
          `operation:${operationId}`,
          toolsByOperation.get(operationId) ?? [],
        );
        appendChanges(blocks, page, undefined, operationId);
      }
    }
  }
  return { blocks, thread_id: page.view.thread.id, persisted: true };
}

function groupTools(
  tools: ThreadPage["view"]["tool_activities"],
  key: (tool: ThreadPage["view"]["tool_activities"][number]) => string,
): Map<string, ThreadPage["view"]["tool_activities"][number][]> {
  const grouped = new Map<
    string,
    ThreadPage["view"]["tool_activities"][number][]
  >();
  for (const tool of tools)
    grouped.set(key(tool), [...(grouped.get(key(tool)) ?? []), tool]);
  return grouped;
}

function appendTools(
  blocks: TranscriptBlock[],
  key: string,
  tools: ThreadPage["view"]["tool_activities"],
): void {
  if (tools.length === 0) return;
  blocks.push({
    key: `${key}:tools`,
    kind: "tools",
    items: [...tools]
      .sort((left, right) => left.sequence - right.sequence)
      .map((tool) => ({
        call_id: tool.call_id,
        name: tool.tool_name,
        verb: toolVerb(tool.tool_name),
        outcome: tool.outcome,
        started_at: tool.created_at,
        summary: tool.result_summary,
        duration_ms: tool.duration_ms,
        ...(tool.error_code === undefined
          ? {}
          : { error_code: tool.error_code }),
      })),
  });
}

function appendChanges(
  blocks: TranscriptBlock[],
  page: ThreadPage,
  turnId: string | undefined,
  operationId: string | undefined,
): void {
  for (const change of page.change_sets.filter(
    (item) => item.turn_id === turnId && item.operation_id === operationId,
  )) {
    if (change.changes.length === 0) continue;
    blocks.push({
      key: `change:${change.change_set_id}`,
      kind: "change",
      change_set_id: change.change_set_id,
      lifecycle: change.lifecycle,
      changes: change.changes,
    });
  }
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
