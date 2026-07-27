import type { ProductError } from "../protocol/base.js";
import type {
  CommandPayload,
  CommandSecretPrompt,
  CommandSelection,
  ThreadRetryOperation,
} from "../protocol/commands.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../protocol/methods.js";
import { createClientMessageId } from "../transcript/identity.js";
import { findCommand } from "./catalog.js";
import type {
  CommandIntent,
  LocalCommandIntent,
  RoutedInput,
} from "./parser.js";

interface CommandRpc {
  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<
    | { readonly ok: true; readonly value: MethodValue[Method] }
    | { readonly ok: false; readonly error: ProductError }
  >;
  activateThreadRetry?(
    operation: ThreadRetryOperation,
    generation: number,
  ): void;
  rejectThreadRetry?(message: string): never;
}

type OperationAccepted =
  | MethodValue["turn.submit"]
  | MethodValue["direct.execute"];
type ProviderCredentialSetResult = MethodValue["provider.credential.set"];

export type CommandDispatchOutcome =
  | { readonly kind: "accepted"; readonly operation: OperationAccepted }
  | { readonly kind: "result"; readonly payload: CommandPayload }
  | {
      readonly kind: "selection";
      readonly intent: CommandIntent;
      readonly selection: CommandSelection;
      readonly context?: CommandPayload;
    }
  | {
      readonly kind: "secret";
      readonly intent: CommandIntent;
      readonly prompt: CommandSecretPrompt;
    }
  | {
      readonly kind: "application_interaction";
      readonly interactionId: string;
    }
  | {
      readonly kind: "command_error";
      readonly code: string;
      readonly message: string;
    }
  | { readonly kind: "local"; readonly intent: LocalCommandIntent }
  | { readonly kind: "error"; readonly error: ProductError }
  | {
      readonly kind: "error";
      readonly code: "thread_required" | "unknown_command";
    };

export function isOperationBusyOutcome(
  outcome: CommandDispatchOutcome,
): boolean {
  if (outcome.kind === "command_error") {
    return outcome.code === "operation_busy" || outcome.code === "turn_busy";
  }
  return (
    outcome.kind === "error" &&
    "error" in outcome &&
    (outcome.error.code === "operation_busy" ||
      outcome.error.code === "turn_busy")
  );
}

export class CommandController {
  constructor(private readonly rpc: CommandRpc) {}

  async submit(
    routed: RoutedInput,
    threadId: string | undefined,
    clientMessageId?: string,
  ): Promise<CommandDispatchOutcome> {
    if (routed.kind === "local") {
      return { kind: "local", intent: routed.intent };
    }
    if (routed.kind === "turn" || routed.kind === "direct") {
      if (!threadId) return { kind: "error", code: "thread_required" };
      const response =
        routed.kind === "turn"
          ? await this.rpc.request("turn.submit", {
              thread_id: threadId,
              content: routed.content,
              client_message_id: clientMessageId ?? createClientMessageId(),
            })
          : await this.rpc.request("direct.execute", {
              thread_id: threadId,
              command: routed.command,
            });
      return response.ok
        ? { kind: "accepted", operation: response.value }
        : { kind: "error", error: response.error };
    }
    if (!findCommand(routed.intent.name)) {
      return { kind: "error", code: "unknown_command" };
    }
    const response = await this.rpc.request("command.execute", {
      name: routed.intent.name,
      ...(routed.intent.arguments
        ? { arguments: [...routed.intent.arguments] }
        : {}),
    });
    if (!response.ok) return { kind: "error", error: response.error };
    const outcome = response.value;
    switch (outcome.kind) {
      case "result":
        return { kind: "result", payload: outcome.payload };
      case "error":
        return {
          kind: "command_error",
          code: outcome.code,
          message: outcome.message,
        };
      case "interaction":
        switch (outcome.interaction.kind) {
          case "selection":
            return {
              kind: "selection",
              intent: routed.intent,
              selection: outcome.interaction,
              ...(outcome.context ? { context: outcome.context } : {}),
            };
          case "secret":
            return {
              kind: "secret",
              intent: routed.intent,
              prompt: outcome.interaction,
            };
          case "application":
            return {
              kind: "application_interaction",
              interactionId: outcome.interaction.interaction_id,
            };
        }
    }
  }

  async setCredential(
    provider: "deepseek" | "kimi" | "mem0",
    action: "add" | "replace" | "delete",
    apiKey: string | undefined,
    allowUnverified: boolean,
  ): Promise<
    | {
        readonly kind: "credential";
        readonly result: ProviderCredentialSetResult;
      }
    | { readonly kind: "error"; readonly error: ProductError }
  > {
    const response = await this.rpc.request("provider.credential.set", {
      provider,
      action,
      ...(apiKey === undefined ? {} : { api_key: apiKey }),
      ...(action === "delete" ? {} : { allow_unverified: allowUnverified }),
    });
    return response.ok
      ? { kind: "credential", result: response.value }
      : { kind: "error", error: response.error };
  }

  async refreshApplication() {
    return await this.rpc.request("application.getState", {});
  }

  activateThreadRetry(
    operation: ThreadRetryOperation,
    generation: number,
  ): void {
    if (!this.rpc.activateThreadRetry) {
      throw new Error("Thread retry activation is unavailable");
    }
    this.rpc.activateThreadRetry(operation, generation);
  }

  rejectThreadRetry(message: string): never {
    if (!this.rpc.rejectThreadRetry) {
      throw new Error(message);
    }
    return this.rpc.rejectThreadRetry(message);
  }

  async select(
    intent: CommandIntent,
    value: string,
    threadId: string | undefined,
  ): Promise<CommandDispatchOutcome> {
    return await this.submit(
      {
        kind: "command",
        intent: {
          name: intent.name,
          arguments: [...(intent.arguments ?? []), value],
        },
      },
      threadId,
    );
  }
}
