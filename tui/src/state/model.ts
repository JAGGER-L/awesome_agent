import type { MethodValue } from "../protocol/index.js";
import type { TranscriptBlock } from "../transcript/model.js";

export type ConnectionState =
  | "idle"
  | "starting"
  | "handshaking"
  | "trust_required"
  | "ready"
  | "fatal"
  | "closed";

export interface SurfaceWarning {
  readonly code: string;
  readonly message: string;
}

export interface ToolProjection {
  readonly kind: "tool";
  readonly call_id: string;
  readonly tool_name: string;
  readonly status: "running" | "completed" | "failed" | "cancelled";
  readonly verb: string;
  readonly target?: string;
  readonly outcome?: string;
  readonly summary: string;
  readonly detail?: string;
  readonly duration_ms?: number;
  readonly error_code?: string;
}

export interface ThinkingProjection {
  readonly kind: "thinking";
  readonly id: string;
  readonly started_at: string;
  readonly duration_ms?: number;
}

export interface AssistantProjection {
  readonly kind: "assistant";
  readonly id: string;
  readonly text: string;
}

export type TimelineProjection =
  | ThinkingProjection
  | ToolProjection
  | AssistantProjection;

export interface TurnProjection {
  readonly id: string;
  readonly status: "active" | "completed" | "failed" | "cancelled";
  readonly started_at: string;
  readonly reasoning_text: string;
  readonly timeline: readonly TimelineProjection[];
  readonly thinking_sequence: number;
  readonly duration_ms?: number;
}

export interface OperationProjection {
  readonly id: string;
  readonly status: "active" | "completed" | "failed" | "cancelled";
  readonly turn?: TurnProjection;
}

export interface FatalState {
  readonly code: string;
  readonly message: string;
}

export interface SurfaceState {
  readonly connection: ConnectionState;
  readonly thread_generation: number;
  readonly application?: MethodValue["application.getState"];
  readonly thread?: MethodValue["thread.read"];
  readonly active_operation?: OperationProjection;
  readonly event_sequence: number;
  readonly usage?: Record<string, number>;
  readonly latest_change?: {
    readonly change_set_id: string;
    readonly paths: readonly string[];
    readonly reversibility: "full" | "partial" | "none";
  };
  readonly pending_interaction?: {
    readonly interaction_id: string;
    readonly interaction_kind:
      | "workspace_trust"
      | "tool_approval"
      | "full_access_confirmation"
      | "recovery_decision";
    readonly prompt: string;
    readonly operation: string;
    readonly target: string;
    readonly capability?: string;
    readonly choices: readonly {
      readonly decision: string;
      readonly label: string;
      readonly description?: string | undefined;
    }[];
  };
  readonly warnings: readonly SurfaceWarning[];
  readonly committed_transcript?: readonly TranscriptBlock[];
  readonly transcript_persisted?: boolean;
  readonly fatal?: FatalState;
  readonly core_exit?: {
    readonly code: number | null;
    readonly signal: string | null;
  };
}
