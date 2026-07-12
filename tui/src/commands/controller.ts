import type { ProductError } from "../protocol/base.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../protocol/methods.js";
import { findCommand } from "./catalog.js";
import type {
  CommandIntent,
  LocalCommandIntent,
  RoutedInput,
} from "./parser.js";
import { createClientMessageId } from "../transcript/identity.js";

interface CommandRpc {
  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<
    | { readonly ok: true; readonly value: MethodValue[Method] }
    | { readonly ok: false; readonly error: ProductError }
  >;
}

type OperationAccepted =
  | MethodValue["turn.submit"]
  | MethodValue["direct.execute"];
type CommandResult = MethodValue["command.execute"];
type CommandSelection = NonNullable<CommandResult["selection"]>;
type CommandSecretPrompt = NonNullable<CommandResult["secret_prompt"]>;
type ProviderCredentialSetResult = MethodValue["provider.credential.set"];

export type CommandDispatchOutcome =
  | { readonly kind: "accepted"; readonly operation: OperationAccepted }
  | { readonly kind: "result"; readonly result: CommandResult }
  | {
      readonly kind: "picker";
      readonly intent: CommandIntent;
      readonly selection: CommandSelection;
    }
  | {
      readonly kind: "secret";
      readonly intent: CommandIntent;
      readonly prompt: CommandSecretPrompt;
    }
  | { readonly kind: "local"; readonly intent: LocalCommandIntent }
  | { readonly kind: "error"; readonly error: ProductError }
  | {
      readonly kind: "error";
      readonly code: "thread_required" | "unknown_command";
    };

export type ThreadReplacementOutcome =
  | {
      readonly kind: "replacement";
      readonly application: MethodValue["application.getState"];
      readonly thread: MethodValue["thread.read"];
    }
  | { readonly kind: "error"; readonly error: ProductError };

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
      const result =
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
      return result.ok
        ? { kind: "accepted", operation: result.value }
        : { kind: "error", error: result.error };
    }

    if (!findCommand(routed.intent.name)) {
      return { kind: "error", code: "unknown_command" };
    }
    const result = await this.rpc.request("command.execute", {
      name: routed.intent.name,
      ...(routed.intent.arguments
        ? { arguments: [...routed.intent.arguments] }
        : {}),
    });
    if (!result.ok) return { kind: "error", error: result.error };
    if (result.value.selection) {
      return {
        kind: "picker",
        intent: routed.intent,
        selection: result.value.selection,
      };
    }
    if (result.value.secret_prompt) {
      return {
        kind: "secret",
        intent: routed.intent,
        prompt: result.value.secret_prompt,
      };
    }
    return { kind: "result", result: result.value };
  }

  async setCredential(
    provider: "deepseek" | "kimi",
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
    const result = await this.rpc.request("provider.credential.set", {
      provider,
      action,
      ...(apiKey === undefined ? {} : { api_key: apiKey }),
      ...(action === "delete" ? {} : { allow_unverified: allowUnverified }),
    });
    return result.ok
      ? { kind: "credential", result: result.value }
      : { kind: "error", error: result.error };
  }

  async refreshApplication() {
    return await this.rpc.request("application.getState", {});
  }

  async loadThreadReplacement(
    threadId: string,
  ): Promise<ThreadReplacementOutcome> {
    const application = await this.rpc.request("application.getState", {});
    if (!application.ok) return { kind: "error", error: application.error };
    const thread = await this.rpc.request("thread.read", {
      thread_id: threadId,
      limit: 100,
    });
    return thread.ok
      ? {
          kind: "replacement",
          application: application.value,
          thread: thread.value,
        }
      : { kind: "error", error: thread.error };
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
