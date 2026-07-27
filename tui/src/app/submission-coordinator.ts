import type {
  CommandController,
  CommandDispatchOutcome,
} from "../commands/controller.js";
import { isOperationBusyOutcome } from "../commands/controller.js";
import type { CommandPresentation } from "../commands/presenters.js";
import { presentCommandPayload } from "../commands/presenters.js";
import type { CommandIntent } from "../commands/parser.js";
import { parseInput } from "../commands/parser.js";
import { classifyTerminalActionError } from "../interaction/action-errors.js";
import type { PendingInput } from "../pending-input/model.js";
import type { SurfaceStore } from "../state/index.js";
import {
  createClientMessageId,
  createCommandSubmissionId,
} from "../transcript/identity.js";

export interface SubmissionResult {
  readonly accepted: boolean;
  readonly retryable?: boolean;
  readonly message?: string;
  readonly operationBusy?: boolean;
  readonly operationId?: string;
}

export interface SubmissionEffects {
  clearNotice(): void;
  appendInputError(command: string, message: string, generation: number): void;
  beginProgress(
    command: string,
    message: string,
    generation: number,
  ): (presentation: CommandPresentation) => void;
  applyOutcome(
    outcome: CommandDispatchOutcome,
    intent: CommandIntent | undefined,
    generation: number,
  ): Promise<SubmissionResult>;
}

export class SubmissionCoordinator {
  constructor(
    private readonly store: SurfaceStore,
    private readonly controller: CommandController | undefined,
    private readonly effects: SubmissionEffects,
  ) {}

  async submit(
    value: string,
    pendingInput?: PendingInput,
  ): Promise<SubmissionResult> {
    this.effects.clearNotice();
    const submitted = value.trimStart();
    if (submitted.startsWith("/") && pendingInput === undefined) {
      this.store.dispatch({
        type: "transcript.command.submitted",
        submission_id: createCommandSubmissionId(),
        text: submitted,
        generation: this.store.getState().thread_generation,
      });
    }
    const routed = parseInput(value);
    if (!routed) return { accepted: true };
    if (routed.kind === "invalid") {
      if (submitted.startsWith("/")) {
        this.effects.appendInputError(
          submitted.slice(1).split(/\s/u, 1)[0] || "command",
          routed.code,
          this.store.getState().thread_generation,
        );
        return { accepted: true };
      }
      return { accepted: false, retryable: true, message: routed.code };
    }
    if (!this.controller) {
      return {
        accepted: false,
        retryable: true,
        message: "surface_not_connected",
      };
    }

    const generation = this.store.getState().thread_generation;
    const threadId = this.store.getState().application?.current_thread_id;
    const optimisticMessage =
      routed.kind === "turn"
        ? {
            id: pendingInput?.clientMessageId ?? createClientMessageId(),
            text: routed.content,
          }
        : undefined;
    if (optimisticMessage && pendingInput === undefined) {
      this.store.dispatch({
        type: "transcript.user.pending",
        client_message_id: optimisticMessage.id,
        text: optimisticMessage.text,
        generation,
      });
    }

    const compact =
      routed.kind === "command" && routed.intent.name === "compact";
    let finishProgress =
      compact && pendingInput === undefined
        ? this.effects.beginProgress(
            "compact",
            "Compressing context...",
            generation,
          )
        : undefined;
    let outcome: CommandDispatchOutcome;
    try {
      outcome = optimisticMessage
        ? await this.controller.submit(routed, threadId, optimisticMessage.id)
        : await this.controller.submit(routed, threadId);
    } catch (error) {
      const failure = classifyTerminalActionError(error);
      finishProgress?.({
        kind: "progress",
        message: `Context compression failed · ${failure.kind === "request" ? failure.message : "Protocol failure"}`,
        tone: "danger",
      });
      if (
        failure.kind === "request" &&
        optimisticMessage &&
        this.store.getState().thread_generation === generation
      ) {
        this.store.dispatch({
          type: "transcript.user.failed",
          client_message_id: optimisticMessage.id,
          message: failure.message,
          generation,
        });
        return {
          accepted: false,
          retryable: true,
          message: failure.message,
        };
      }
      throw failure.kind === "fatal" ? failure.error : error;
    }

    if (pendingInput && isOperationBusyOutcome(outcome)) {
      return {
        accepted: false,
        retryable: true,
        operationBusy: true,
      };
    }
    if (pendingInput && submitted.startsWith("/")) {
      this.store.dispatch({
        type: "transcript.command.submitted",
        submission_id: createCommandSubmissionId(),
        text: submitted,
        generation,
      });
    }
    if (pendingInput && optimisticMessage) {
      this.store.dispatch({
        type: "transcript.user.pending",
        client_message_id: optimisticMessage.id,
        text: optimisticMessage.text,
        generation,
      });
    }
    if (compact && pendingInput) {
      finishProgress = this.effects.beginProgress(
        "compact",
        "Compressing context...",
        generation,
      );
    }
    if (finishProgress) {
      if (outcome.kind === "result") {
        finishProgress(presentCommandPayload("compact", outcome.payload));
      } else if (outcome.kind === "error" || outcome.kind === "command_error") {
        const message =
          outcome.kind === "command_error"
            ? outcome.message
            : "error" in outcome
              ? outcome.error.message
              : outcome.code;
        finishProgress({
          kind: "progress",
          message: `Context compression failed · ${message}`,
          tone: "danger",
        });
      }
      return { accepted: true };
    }

    if (
      optimisticMessage &&
      this.store.getState().thread_generation === generation
    ) {
      if (
        outcome.kind === "accepted" &&
        outcome.operation.client_message_id === optimisticMessage.id
      ) {
        this.store.dispatch({
          type: "transcript.user.accepted",
          client_message_id: optimisticMessage.id,
          generation,
        });
      } else {
        this.store.dispatch({
          type: "transcript.user.failed",
          client_message_id: optimisticMessage.id,
          message:
            outcome.kind === "error"
              ? "error" in outcome
                ? outcome.error.message
                : outcome.code
              : "Turn acceptance identity did not match the submitted message.",
          generation,
        });
      }
    }
    return await this.effects.applyOutcome(
      outcome,
      routed.kind === "command" ? routed.intent : undefined,
      generation,
    );
  }
}
