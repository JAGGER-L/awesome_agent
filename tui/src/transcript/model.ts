export interface BlockBase {
  readonly key: string;
  readonly kind: string;
}

export interface UserBlock extends BlockBase {
  readonly kind: "user";
  readonly client_message_id: string;
  readonly status: "pending" | "accepted" | "persisted" | "failed";
  readonly text: string;
  readonly error_message?: string;
}
export interface CommandInputBlock extends BlockBase {
  readonly kind: "command_input";
  readonly submission_id: string;
  readonly text: string;
}
export interface AssistantBlock extends BlockBase {
  readonly kind: "assistant";
  readonly text: string;
}
export interface DirectCommandBlock extends BlockBase {
  readonly kind: "direct_command";
  readonly command: string;
}
export interface ToolItem {
  readonly call_id: string;
  readonly name: string;
  readonly verb: string;
  readonly target?: string;
  readonly outcome: "running" | "success" | "error" | "cancelled";
  readonly presentation_outcome?: string;
  readonly summary: string;
  readonly detail?: string;
  readonly duration_ms?: number;
  readonly error_code?: string;
}
export interface ToolGroupBlock extends BlockBase {
  readonly kind: "tools";
  readonly items: readonly ToolItem[];
}
export interface ChangeSummaryBlock extends BlockBase {
  readonly kind: "change";
  readonly change_set_id: string;
  readonly paths: readonly string[];
  readonly lifecycle: string;
  readonly reversibility: string;
}
export interface ReasoningMarkerBlock extends BlockBase {
  readonly kind: "reasoning_marker";
  readonly label: string;
}
export interface WarningBlock extends BlockBase {
  readonly kind: "warning";
  readonly code: string;
  readonly message: string;
}
export interface StatusBlock extends BlockBase {
  readonly kind: "status";
  readonly message: string;
}
export interface CommandResultBlock extends BlockBase {
  readonly kind: "command_result";
  readonly command: string;
  readonly presentation: import("../commands/presenters.js").CommandPresentation;
}
export interface ErrorBlock extends BlockBase {
  readonly kind: "error";
  readonly code: string;
  readonly message: string;
}
export interface OmittedHistoryBlock extends BlockBase {
  readonly kind: "omitted_history";
  readonly message: string;
}

export type TranscriptBlock =
  | UserBlock
  | CommandInputBlock
  | AssistantBlock
  | DirectCommandBlock
  | ToolGroupBlock
  | ChangeSummaryBlock
  | ReasoningMarkerBlock
  | StatusBlock
  | CommandResultBlock
  | WarningBlock
  | ErrorBlock
  | OmittedHistoryBlock;

export interface TranscriptProjection {
  readonly blocks: readonly TranscriptBlock[];
  readonly thread_id: string;
  readonly persisted: true;
}

export interface LiveTranscriptProjection {
  readonly blocks: readonly TranscriptBlock[];
  readonly operation_id?: string;
  readonly turn_id?: string;
  readonly reasoning_text: string;
  readonly usage?: Readonly<Record<string, number>>;
  readonly terminal: boolean;
}

export interface ReconciledTurn {
  readonly blocks: readonly TranscriptBlock[];
  readonly persisted: boolean;
}
