import type { ConnectedSurface } from "../surface/controller.js";
import {
  beginStartup,
  type StartupResult,
  type StartupThreadResult,
} from "../surface/startup.js";
import type { SurfaceState } from "../state/model.js";
import { createClientMessageId } from "../transcript/identity.js";
import type { HeadlessRunIntent, LaunchIntent } from "./args.js";
import { StartupSessionController } from "./startup-session-controller.js";

export type HeadlessExitCode = 0 | 1 | 3 | 130;

export interface HeadlessIo {
  readonly writeStdout: (value: string) => void;
  readonly writeStderr: (value: string) => void;
}

export interface HeadlessDependencies {
  readonly subscribeInterrupt?: (listener: () => void) => () => void;
  readonly cancellationTimeoutMs?: number;
}

interface HeadlessResult {
  readonly exitCode: HeadlessExitCode;
  readonly stdout?: string;
  readonly stderr?: string;
}

interface ActiveOperation {
  value: string | undefined;
  pending: Promise<string | undefined> | undefined;
}

const DEFAULT_CANCELLATION_TIMEOUT_MS = 2_000;

type ReadyHeadlessStartup = Extract<
  StartupResult,
  { readonly kind: "ready" }
> & {
  readonly thread: Extract<StartupThreadResult, { readonly kind: "ready" }>;
};

type TerminalObservation =
  | { readonly kind: "terminal" }
  | {
      readonly kind: "interaction";
      readonly interaction: NonNullable<SurfaceState["pending_interaction"]>;
    }
  | { readonly kind: "failed"; readonly message: string };

export async function runHeadless(
  surface: ConnectedSurface,
  intent: HeadlessRunIntent,
  io: HeadlessIo,
  dependencies: HeadlessDependencies = {},
): Promise<HeadlessExitCode> {
  const activeOperation: ActiveOperation = {
    value: undefined,
    pending: undefined,
  };
  const cancellationTimeoutMs = normalizeCancellationTimeout(
    dependencies.cancellationTimeoutMs,
  );
  let interrupted = false;
  let resolveInterrupt!: () => void;
  const interruptSignal = new Promise<void>((resolve) => {
    resolveInterrupt = resolve;
  });
  const unsubscribe = (
    dependencies.subscribeInterrupt ?? subscribeProcessInterrupt
  )(() => {
    if (interrupted) return;
    interrupted = true;
    resolveInterrupt();
  });
  const workflow = executeHeadless(
    surface,
    intent,
    activeOperation,
    cancellationTimeoutMs,
  ).catch(
    (): HeadlessResult => failure("The headless run failed unexpectedly."),
  );
  const interruptOutcome = interruptSignal.then(
    async (): Promise<HeadlessResult> => {
      const cancellationError = await cancelActiveOperation(
        surface,
        activeOperation,
        cancellationTimeoutMs,
      );
      return {
        exitCode: 130,
        stderr: cancellationError
          ? `Interrupted; cancellation could not be confirmed: ${cancellationError}\n`
          : "Interrupted.\n",
      };
    },
  );

  try {
    let result = await Promise.race([workflow, interruptOutcome]);
    if (interrupted) result = await interruptOutcome;
    if (result.exitCode === 0) {
      io.writeStdout(result.stdout ?? "");
    } else if (result.stderr) {
      io.writeStderr(result.stderr);
    }
    return result.exitCode;
  } finally {
    unsubscribe();
  }
}

async function executeHeadless(
  surface: ConnectedSurface,
  intent: HeadlessRunIntent,
  activeOperation: ActiveOperation,
  cancellationTimeoutMs: number,
): Promise<HeadlessResult> {
  const launchIntent = toLaunchIntent(intent);
  const startupController = new StartupSessionController(surface, launchIntent);
  let startup: StartupResult;
  try {
    startup = await beginStartup(surface, launchIntent);
    if (startup.kind === "trust_required") {
      if (!intent.trustWorkspace)
        return unresolved("Workspace trust is required.");
      const continued = await startupController.respondTrust(startup, "trust");
      if (continued.kind === "exit") {
        return unresolved("Workspace trust was not granted.");
      }
      startup = continued.startup;
    }
  } catch (error) {
    const pending = await pendingInteractionResult(surface);
    if (pending) return pending;
    throw error;
  }
  const ready = requireReadyStartup(startup);
  if ("exitCode" in ready) return ready;
  if (ready.readiness === "diagnostics_ready") {
    return failure(startupDiagnostic(ready));
  }

  const startupInteraction =
    ready.application.pending_interaction_id !== undefined ||
    surface.store.getState().pending_interaction
      ? unresolved("Startup recovery requires interaction.")
      : undefined;
  if (startupInteraction) return startupInteraction;

  const threadId = ready.thread.thread.view.thread.id;
  if (intent.permissionMode) {
    const permission = await configurePermissionMode(
      surface,
      threadId,
      intent.permissionMode,
    );
    if (permission) return permission;
  }

  const pending = await pendingInteractionResult(surface);
  if (pending) return pending;

  const submission = surface.request("turn.submit", {
    thread_id: threadId,
    content: intent.prompt,
    client_message_id: createClientMessageId(),
  });
  activeOperation.pending = submission.then(
    (result) => (result.ok ? result.value.operation_id : undefined),
    () => undefined,
  );
  const submitted = await submission;
  activeOperation.pending = undefined;
  if (!submitted.ok) {
    return (
      (await pendingInteractionResult(surface)) ??
      failure(submitted.error.message)
    );
  }
  activeOperation.value = submitted.value.operation_id;

  let ignoredInteractionId: string | undefined;
  for (;;) {
    const observation = await waitForTerminal(
      surface,
      submitted.value.operation_id,
      submitted.value.turn_id,
      ignoredInteractionId,
    );
    ignoredInteractionId = undefined;
    if (observation.kind === "failed") return failure(observation.message);
    if (observation.kind === "terminal") break;
    if (
      intent.allowNetwork &&
      isExactNetworkApproval(
        observation.interaction,
        threadId,
        submitted.value.turn_id,
        submitted.value.operation_id,
      )
    ) {
      const response = await surface
        .request("interaction.respond", {
          interaction_id: observation.interaction.interaction_id,
          decision: "allow_once",
        })
        .catch(() => undefined);
      if (
        response?.ok &&
        response.value.accepted &&
        response.value.status === "resolved"
      ) {
        ignoredInteractionId = observation.interaction.interaction_id;
        continue;
      }
    }
    const cancellationError = await cancelActiveOperation(
      surface,
      activeOperation,
      cancellationTimeoutMs,
    );
    activeOperation.value = undefined;
    return cancellationError
      ? failure(
          `Interaction required; cancellation could not be confirmed: ${cancellationError}`,
        )
      : unresolved("Interaction required.");
  }
  activeOperation.value = undefined;

  const durable = await surface.request("thread.read", {
    thread_id: threadId,
    limit: 50,
  });
  if (!durable.ok) return failure(durable.error.message);
  const turn = durable.value.view.turns.find(
    (candidate) => candidate.id === submitted.value.turn_id,
  );
  if (!turn)
    return failure("The completed Turn was not found in durable state.");
  if (turn.status !== "completed") {
    return failure(
      turn.error_code ?? turn.termination_reason ?? `Turn ${turn.status}.`,
    );
  }
  const entry = durable.value.view.entries.find(
    (candidate) => candidate.id === turn.assistant_entry_id,
  );
  if (entry?.kind !== "assistant_message") {
    return failure("The completed Turn has no durable assistant answer.");
  }
  return {
    exitCode: 0,
    stdout:
      intent.format === "json"
        ? `${JSON.stringify({
            version: 2,
            type: "awesome.run.result",
            thread_id: threadId,
            turn_id: turn.id,
            text: entry.content,
            citations: entry.metadata.citations,
            termination_reason: turn.termination_reason ?? null,
            usage: turn.usage,
          })}\n`
        : normalizeTextOutput(entry.content),
  };
}

function toLaunchIntent(intent: HeadlessRunIntent): LaunchIntent {
  return intent.target.kind === "new"
    ? { kind: "new" }
    : { kind: "resume", threadId: intent.target.threadId };
}

function requireReadyStartup(
  startup: StartupResult,
): ReadyHeadlessStartup | HeadlessResult {
  if (startup.kind === "state_reset_required") {
    return unresolved("Application state reset requires interaction.");
  }
  if (startup.kind === "trust_required") {
    return unresolved("Workspace trust is still required.");
  }
  if (startup.kind === "denied") {
    return unresolved("Startup interaction was denied.");
  }
  if (startup.thread.kind === "selection_required") {
    return unresolved("Thread selection requires interaction.");
  }
  return startup as ReadyHeadlessStartup;
}

function startupDiagnostic(
  startup: Extract<StartupResult, { readonly kind: "ready" }>,
): string {
  const diagnostic = startup.diagnostic;
  if (!diagnostic) return "Awesome is not ready to run an Agent Turn.";
  const details = diagnostic.messages.filter(
    (message) => message.trim().length > 0,
  );
  return [
    `Awesome is not ready: ${diagnostic.code}.`,
    ...(diagnostic.environmentVariable
      ? [`Configure ${diagnostic.environmentVariable}.`]
      : []),
    ...details,
  ].join("\n");
}

async function configurePermissionMode(
  surface: ConnectedSurface,
  threadId: string,
  mode: NonNullable<HeadlessRunIntent["permissionMode"]>,
): Promise<HeadlessResult | undefined> {
  const response = await surface.request("command.execute", {
    name: "permissions",
    arguments: [mode],
  });
  if (!response.ok) {
    return (
      (await pendingInteractionResult(surface)) ??
      failure(response.error.message)
    );
  }
  const outcome = response.value;
  if (
    outcome.kind === "result" &&
    outcome.payload.kind === "permissions" &&
    outcome.payload.mode === mode
  ) {
    return undefined;
  }
  if (
    mode === "full_access" &&
    outcome.kind === "interaction" &&
    outcome.interaction.kind === "application"
  ) {
    const resolved = await surface.request("interaction.respond", {
      interaction_id: outcome.interaction.interaction_id,
      decision: "enable_full_access",
    });
    if (!resolved.ok) return failure(resolved.error.message);
    if (!resolved.value.accepted || resolved.value.status !== "resolved") {
      return unresolved("Full access confirmation was not accepted.");
    }
    const state = await surface.request("application.getState", {});
    if (!state.ok) return failure(state.error.message);
    if (
      state.value.current_thread_id !== threadId ||
      state.value.permission_mode !== "full_access"
    ) {
      return failure(
        "Full access confirmation did not update the selected Thread.",
      );
    }
    return undefined;
  }
  if (outcome.kind === "interaction") {
    return unresolved("Permission mode requires interaction.");
  }
  if (outcome.kind === "error") {
    return (
      (await pendingInteractionResult(surface)) ?? failure(outcome.message)
    );
  }
  return failure("Permission mode returned an unexpected result.");
}

async function pendingInteractionResult(
  surface: ConnectedSurface,
): Promise<HeadlessResult | undefined> {
  if (surface.store.getState().pending_interaction) {
    return unresolved("An interaction must be resolved before running.");
  }
  try {
    const state = await surface.request("application.getState", {});
    if (state.ok && state.value.pending_interaction_id !== undefined) {
      return unresolved("An interaction must be resolved before running.");
    }
  } catch {
    // The caller reports the primary startup or operation failure.
  }
  return undefined;
}

async function waitForTerminal(
  surface: ConnectedSurface,
  operationId: string,
  turnId: string,
  ignoredInteractionId?: string,
): Promise<TerminalObservation> {
  const inspect = (): TerminalObservation | undefined =>
    inspectTerminalState(
      surface.store.getState(),
      operationId,
      turnId,
      ignoredInteractionId,
    );
  const immediate = inspect();
  if (immediate) return immediate;
  return await new Promise<TerminalObservation>((resolve) => {
    let unsubscribe: () => void = () => undefined;
    const finish = (value: TerminalObservation) => {
      unsubscribe();
      resolve(value);
    };
    unsubscribe = surface.store.subscribe(() => {
      const observed = inspect();
      if (observed) finish(observed);
    });
    const raced = inspect();
    if (raced) finish(raced);
  });
}

function inspectTerminalState(
  state: SurfaceState,
  operationId: string,
  turnId: string,
  ignoredInteractionId?: string,
): TerminalObservation | undefined {
  if (
    state.pending_interaction &&
    state.pending_interaction.interaction_id !== ignoredInteractionId
  ) {
    return { kind: "interaction", interaction: state.pending_interaction };
  }
  if (state.fatal) return { kind: "failed", message: state.fatal.message };
  if (state.core_exit) {
    return {
      kind: "failed",
      message: "Awesome Core exited before completion.",
    };
  }
  const durableTurn = state.thread?.view.turns.find(
    (candidate) => candidate.id === turnId,
  );
  if (durableTurn && durableTurn.status !== "in_progress") {
    return { kind: "terminal" };
  }
  const operation = state.active_operation;
  if (operation?.id === operationId && operation.status !== "active") {
    return { kind: "terminal" };
  }
  return undefined;
}

function isExactNetworkApproval(
  interaction: NonNullable<SurfaceState["pending_interaction"]>,
  threadId: string,
  turnId: string,
  operationId: string,
): boolean {
  return (
    interaction.interaction_kind === "tool_approval" &&
    interaction.capability === "network.read" &&
    interaction.thread_id === threadId &&
    interaction.turn_id === turnId &&
    interaction.operation_id === operationId &&
    interaction.choices.some((choice) => choice.decision === "allow_once")
  );
}

async function cancelActiveOperation(
  surface: ConnectedSurface,
  activeOperation: ActiveOperation,
  timeoutMs: number,
): Promise<string | undefined> {
  const deadline = Date.now() + timeoutMs;
  let operationId = activeOperation.value;
  if (!operationId && activeOperation.pending) {
    const pending = await settleBefore(activeOperation.pending, deadline);
    if (pending.kind === "timeout") {
      return "Operation identity was not available before the cancellation deadline.";
    }
    if (pending.kind === "resolved") operationId = pending.value;
  }
  if (!operationId) return undefined;
  const result = await settleBefore(
    surface.request("operation.cancel", { operation_id: operationId }),
    deadline,
  );
  if (result.kind === "timeout") {
    return "Cancellation was not confirmed before the deadline.";
  }
  if (result.kind === "rejected" || !result.value.ok) {
    return "Cancellation request failed.";
  }
  if (
    result.value.value.operation_id !== operationId ||
    !result.value.value.cancelled
  ) {
    return "Cancellation was not confirmed for the active operation.";
  }
  return undefined;
}

type DeadlineResult<Value> =
  | { readonly kind: "resolved"; readonly value: Value }
  | { readonly kind: "rejected" }
  | { readonly kind: "timeout" };

async function settleBefore<Value>(
  promise: Promise<Value>,
  deadline: number,
): Promise<DeadlineResult<Value>> {
  const remaining = deadline - Date.now();
  if (remaining <= 0) return { kind: "timeout" };
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const settled = promise.then<DeadlineResult<Value>, DeadlineResult<Value>>(
    (value) => ({ kind: "resolved", value }),
    () => ({ kind: "rejected" }),
  );
  const result = await Promise.race([
    settled,
    new Promise<DeadlineResult<Value>>((resolve) => {
      timeout = setTimeout(() => resolve({ kind: "timeout" }), remaining);
    }),
  ]);
  if (timeout) clearTimeout(timeout);
  return result;
}

function normalizeCancellationTimeout(value: number | undefined): number {
  if (value === undefined) return DEFAULT_CANCELLATION_TIMEOUT_MS;
  return Number.isFinite(value) && value >= 1
    ? Math.min(Math.floor(value), 10_000)
    : DEFAULT_CANCELLATION_TIMEOUT_MS;
}

function failure(message: string): HeadlessResult {
  return { exitCode: 1, stderr: `Awesome run failed: ${message}\n` };
}

function unresolved(message: string): HeadlessResult {
  return { exitCode: 3, stderr: `${message}\n` };
}

function normalizeTextOutput(value: string): string {
  return `${value.replace(/(?:\r?\n)+$/u, "")}\n`;
}

function subscribeProcessInterrupt(listener: () => void): () => void {
  process.on("SIGINT", listener);
  return () => process.off("SIGINT", listener);
}
