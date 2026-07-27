import type { LaunchIntent } from "./args.js";
import type { ConnectedSurface } from "../surface/controller.js";
import {
  respondStartupStateReset,
  respondStartupTrust,
  selectStartupThread,
  type StartupResult,
  type StartupThreadResult,
} from "../surface/startup.js";

type TrustRequiredStartup = Extract<
  StartupResult,
  { readonly kind: "trust_required" }
>;
type StateResetRequiredStartup = Extract<
  StartupResult,
  { readonly kind: "state_reset_required" }
>;
type ReadyStartup = Extract<StartupResult, { readonly kind: "ready" }>;

export type StartupSessionOutcome =
  | { readonly kind: "render"; readonly startup: StartupResult }
  | {
      readonly kind: "exit";
      readonly reason: "trust_denied" | "state_reset_denied";
    };

interface StartupSessionOperations {
  respondTrust(
    surface: ConnectedSurface,
    intent: LaunchIntent,
    interactionId: string,
    decision: "trust" | "deny",
  ): Promise<StartupResult>;
  respondStateReset(
    surface: ConnectedSurface,
    intent: LaunchIntent,
    interactionId: string,
    decision: "reset_state" | "deny",
  ): Promise<StartupResult>;
  selectThread(
    surface: ConnectedSurface,
    threadId: string,
  ): Promise<StartupThreadResult>;
}

const startupSessionOperations: StartupSessionOperations = {
  respondTrust: async (surface, intent, interactionId, decision) =>
    await respondStartupTrust(surface, intent, interactionId, decision),
  respondStateReset: async (surface, intent, interactionId, decision) =>
    await respondStartupStateReset(surface, intent, interactionId, decision),
  selectThread: async (surface, threadId) =>
    await selectStartupThread(surface, threadId),
};

export class StartupSessionController {
  constructor(
    private readonly surface: ConnectedSurface,
    private readonly intent: LaunchIntent,
    private readonly operations: StartupSessionOperations = startupSessionOperations,
  ) {}

  async respondTrust(
    current: TrustRequiredStartup,
    decision: "trust" | "deny",
  ): Promise<StartupSessionOutcome> {
    const startup = await this.operations.respondTrust(
      this.surface,
      this.intent,
      current.interactionId,
      decision,
    );
    return startup.kind === "denied"
      ? { kind: "exit", reason: "trust_denied" }
      : { kind: "render", startup };
  }

  async respondStateReset(
    current: StateResetRequiredStartup,
    decision: "reset_state" | "deny",
  ): Promise<StartupSessionOutcome> {
    const startup = await this.operations.respondStateReset(
      this.surface,
      this.intent,
      current.interactionId,
      decision,
    );
    return startup.kind === "denied"
      ? { kind: "exit", reason: "state_reset_denied" }
      : { kind: "render", startup };
  }

  async selectThread(
    current: ReadyStartup,
    threadId: string,
  ): Promise<ReadyStartup> {
    return {
      ...current,
      thread: await this.operations.selectThread(this.surface, threadId),
    };
  }
}
