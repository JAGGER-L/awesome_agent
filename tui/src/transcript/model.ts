export interface BlockBase {
  readonly key: string;
  readonly kind: string;
}

export interface UserBlock extends BlockBase {
  readonly kind: "user";
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
  readonly outcome: "running" | "success" | "error" | "cancelled";
  readonly summary: string;
  readonly duration_ms: number;
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
  | AssistantBlock
  | DirectCommandBlock
  | ToolGroupBlock
  | ChangeSummaryBlock
  | ReasoningMarkerBlock
  | StatusBlock
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
