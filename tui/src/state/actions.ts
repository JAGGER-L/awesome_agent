import type { CoreExit } from "../core/index.js";
import type { EventEnvelope, MethodValue } from "../protocol/index.js";
import type {
  CommandResultBlock,
  ReconciledTurn,
  TranscriptBlock,
} from "../transcript/model.js";
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
  | {
      readonly type: "thread.replaced";
      readonly application: MethodValue["application.getState"];
      readonly thread: MethodValue["thread.read"];
      readonly transcript: readonly TranscriptBlock[];
      readonly transcript_persisted: boolean;
    }
  | {
      readonly type: "event.received";
      readonly event: EventEnvelope;
      readonly generation: number;
    }
  | {
      readonly type: "delta.received";
      readonly delta: CoalescedDelta;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.reconciled";
      readonly result: ReconciledTurn;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.command_result";
      readonly block: CommandResultBlock;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.command.submitted";
      readonly submission_id: string;
      readonly text: string;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.command_result.replace";
      readonly block: CommandResultBlock;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.user.pending";
      readonly client_message_id: string;
      readonly text: string;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.user.accepted";
      readonly client_message_id: string;
      readonly generation: number;
    }
  | {
      readonly type: "transcript.user.failed";
      readonly client_message_id: string;
      readonly message: string;
      readonly generation: number;
    }
  | {
      readonly type: "protocol.fatal";
      readonly code: string;
      readonly message: string;
    }
  | { readonly type: "core.exited"; readonly exit: CoreExit }
  | { readonly type: "reconnect.reset" }
  | { readonly type: "surface.closed" };
