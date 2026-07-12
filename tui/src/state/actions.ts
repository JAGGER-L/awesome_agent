import type { CoreExit } from "../core/index.js";
import type { EventEnvelope, MethodValue } from "../protocol/index.js";
import type { ReconciledTurn } from "../transcript/model.js";
import type { CoalescedDelta } from "./delta-batcher.js";

export type SurfaceAction =
  | { readonly type: "connection.start" }
  | { readonly type: "connection.handshaking" }
  | { readonly type: "handshake.trust_required" }
  | { readonly type: "handshake.ready" }
  | {
      readonly type: "hydrate.application";
      readonly application: MethodValue["application.getState"];
    }
  | {
      readonly type: "hydrate.thread";
      readonly thread: MethodValue["thread.read"];
    }
  | { readonly type: "event.received"; readonly event: EventEnvelope }
  | { readonly type: "delta.received"; readonly delta: CoalescedDelta }
  | { readonly type: "transcript.reconciled"; readonly result: ReconciledTurn }
  | {
      readonly type: "protocol.fatal";
      readonly code: string;
      readonly message: string;
    }
  | { readonly type: "core.exited"; readonly exit: CoreExit }
  | { readonly type: "reconnect.reset" }
  | { readonly type: "surface.closed" };
