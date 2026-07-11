import type { MethodValue } from "../protocol/index.js";

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
  readonly call_id: string;
  readonly tool_name: string;
  readonly status: "running" | "completed" | "failed" | "cancelled";
  readonly summary: string;
  readonly error_code?: string;
}

export interface TurnProjection {
  readonly id: string;
  readonly status: "active" | "completed" | "failed" | "cancelled";
  readonly assistant_text: string;
  readonly reasoning_text: string;
  readonly tools: Readonly<Record<string, ToolProjection>>;
  readonly tool_order: readonly string[];
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
      | "execute_boundary"
      | "recovery_decision";
    readonly prompt: string;
    readonly choices: readonly string[];
  };
  readonly warnings: readonly SurfaceWarning[];
  readonly fatal?: FatalState;
  readonly core_exit?: {
    readonly code: number | null;
    readonly signal: string | null;
  };
}
