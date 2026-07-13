import { useCallback } from "react";

import type {
  CommandController,
  CommandDispatchOutcome,
} from "../commands/controller.js";
import type {
  LocalCommandResult,
  LocalCommandService,
} from "../commands/local.js";
import type { CommandIntent } from "../commands/parser.js";
import type {
  PickerOwner,
  PickerSelection,
  SecretPrompt,
  TerminalUiAction,
  TerminalUiState,
} from "../interaction/model.js";
import type { SurfaceStore } from "../state/index.js";

interface InteractionControllerOptions {
  readonly controller?: CommandController | undefined;
  readonly store: SurfaceStore;
  readonly dispatch: (action: TerminalUiAction) => void;
  readonly uiRef: { readonly current: TerminalUiState };
  readonly currentThreadId?: string | undefined;
  readonly localCommands?: LocalCommandService | undefined;
  readonly interactionResponder?:
    | { respond(decision: string): Promise<void> }
    | undefined;
  readonly appendCommandResult: (
    command: string,
    tone: "info" | "warning" | "error",
    content: string,
    generation: number,
  ) => void;
  readonly applyLocalResult: (
    result: LocalCommandResult,
    generation: number,
  ) => unknown;
  readonly applyOutcome: (
    outcome: CommandDispatchOutcome,
    intent?: CommandIntent,
    generation?: number,
  ) => Promise<unknown>;
}

export function useInteractionController(
  options: InteractionControllerOptions,
) {
  const mutateCredential = useCallback(
    async (
      intent: CommandIntent,
      provider: "deepseek" | "kimi" | "mem0",
      action: "add" | "replace" | "delete",
      secret?: string,
      prompt?: SecretPrompt,
      allowUnverified = false,
    ) => {
      const { controller, dispatch, store } = options;
      if (!controller) return;
      const generation = store.getState().thread_generation;
      if (action !== "delete" && !secret) {
        dispatch({
          type: "mode.secret.message",
          message: "API key is required.",
        });
        return;
      }
      if (prompt && !allowUnverified) {
        dispatch({ type: "mode.secret.submitting", submitting: true });
      }
      const outcome = await controller.setCredential(
        provider,
        action,
        secret,
        allowUnverified,
      );
      if (store.getState().thread_generation !== generation) return;
      if (outcome.kind === "error") {
        if (prompt) {
          dispatch({
            type: "mode.open",
            mode: secretMode(intent, prompt, outcome.error.message),
          });
        } else {
          dispatch({ type: "mode.cancel" });
          options.appendCommandResult(
            "auth",
            "error",
            outcome.error.message,
            generation,
          );
        }
        return;
      }
      if (outcome.result.status === "invalid") {
        if (prompt) {
          dispatch({
            type: "mode.open",
            mode: secretMode(
              intent,
              prompt,
              "The API key was rejected. Try another key.",
            ),
          });
        }
        return;
      }
      if (outcome.result.status === "confirm_unverified") {
        if (!prompt || !secret) return;
        dispatch({
          type: "mode.open",
          mode: pickerMode(
            { kind: "credential_unverified", intent, prompt, secret },
            {
              prompt:
                "The Provider could not be reached. Save this key anyway?",
              options: [
                { value: "back", label: "Back", selected: true },
                { value: "save", label: "Save anyway", selected: false },
              ],
            },
            false,
          ),
        });
        return;
      }
      const refreshed = await controller.refreshApplication();
      if (store.getState().thread_generation !== generation) return;
      if (!refreshed.ok) {
        if (prompt) {
          dispatch({
            type: "mode.open",
            mode: secretMode(intent, prompt, refreshed.error.message),
          });
        } else {
          dispatch({ type: "mode.cancel" });
          options.appendCommandResult(
            "auth",
            "error",
            refreshed.error.message,
            generation,
          );
        }
        return;
      }
      store.dispatch({
        type: "hydrate.application",
        application: refreshed.value,
      });
      dispatch({ type: "mode.cancel" });
      if (outcome.result.status === "deleted") {
        options.appendCommandResult(
          "auth",
          "info",
          `${providerLabel(provider)} credential deleted. Choose a credential source to continue.`,
          generation,
        );
        return;
      }
      if (intent.name !== "model") {
        options.appendCommandResult(
          "auth",
          "info",
          `${providerLabel(provider)} credential configured.`,
          generation,
        );
        return;
      }
      const resumed = await controller.submit(
        { kind: "command", intent },
        refreshed.value.current_thread_id,
      );
      await options.applyOutcome(resumed, intent, generation);
    },
    [options],
  );

  const respondApproval = useCallback(
    async (decision: string) => {
      options.dispatch({ type: "mode.approval.submitting", submitting: true });
      await options.interactionResponder?.respond(decision);
      if (!options.controller) return;
      const refreshed = await options.controller.refreshApplication();
      if (refreshed.ok) {
        options.store.dispatch({
          type: "hydrate.application",
          application: refreshed.value,
        });
      }
    },
    [options],
  );

  const openOutcome = useCallback(
    (outcome: CommandDispatchOutcome, blocking: boolean): boolean => {
      if (outcome.kind === "selection") {
        options.dispatch({
          type: "mode.open",
          mode: pickerMode(
            commandPickerOwner(outcome.intent),
            outcome.selection,
            blocking,
          ),
        });
        return true;
      }
      if (outcome.kind === "secret") {
        options.dispatch({
          type: "mode.open",
          mode: secretMode(outcome.intent, outcome.prompt),
        });
        return true;
      }
      return outcome.kind === "application_interaction";
    },
    [options],
  );

  const confirmSelection = useCallback(async () => {
    const mode = options.uiRef.current.mode;
    if (mode.kind === "approval") {
      const choice = mode.interaction.choices[mode.selected];
      if (choice) await respondApproval(choice.decision);
      return;
    }
    if (mode.kind !== "picker" || mode.submitting) return;
    const selected = mode.selection.options[mode.selected];
    if (!selected || selected.disabled) return;
    const owner = mode.owner;
    if (owner.kind === "local_theme") {
      const generation = options.store.getState().thread_generation;
      options.dispatch({ type: "mode.cancel" });
      if (options.localCommands) {
        options.applyLocalResult(
          await options.localCommands.execute({
            name: "theme",
            arguments: [selected.value],
          }),
          generation,
        );
      }
      return;
    }
    if (owner.kind === "command") {
      const generation = options.store.getState().thread_generation;
      options.dispatch({ type: "mode.cancel" });
      if (options.controller) {
        const outcome = await options.controller.select(
          owner.intent,
          selected.value,
          options.currentThreadId,
        );
        await options.applyOutcome(outcome, owner.intent, generation);
      }
      return;
    }
    if (owner.kind === "credential_delete") {
      if (selected.value === "confirm") {
        await mutateCredential(owner.intent, owner.provider, "delete");
      } else {
        options.dispatch({ type: "mode.cancel" });
      }
      return;
    }
    if (owner.kind === "thread") return;
    if (selected.value === "save") {
      await mutateCredential(
        owner.intent,
        owner.prompt.provider,
        owner.prompt.action,
        owner.secret,
        owner.prompt,
        true,
      );
    } else {
      options.dispatch({
        type: "mode.open",
        mode: secretMode(owner.intent, owner.prompt),
      });
    }
  }, [mutateCredential, options, respondApproval]);

  const submitSecret = useCallback(async () => {
    const mode = options.uiRef.current.mode;
    if (mode.kind !== "secret") return;
    await mutateCredential(
      mode.intent,
      mode.prompt.provider,
      mode.prompt.action,
      mode.value,
      mode.prompt,
    );
  }, [mutateCredential, options]);

  const cancelCurrent = useCallback(() => {
    options.dispatch({ type: "mode.cancel" });
  }, [options]);

  return {
    openOutcome,
    confirmSelection,
    submitSecret,
    respondApproval,
    cancelCurrent,
  } as const;
}

export function commandPickerOwner(intent: CommandIntent): PickerOwner {
  const provider = intent.arguments?.[0];
  if (
    intent.name === "auth" &&
    intent.arguments?.at(-1) === "delete" &&
    (provider === "deepseek" || provider === "kimi" || provider === "mem0")
  ) {
    return { kind: "credential_delete", intent, provider };
  }
  return { kind: "command", intent };
}

export function pickerMode(
  owner: PickerOwner,
  selection: NonNullable<PickerSelection>,
  blocking: boolean,
): Extract<TerminalUiState["mode"], { kind: "picker" }> {
  return {
    kind: "picker",
    owner,
    selection,
    selected: Math.max(
      0,
      selection.options.findIndex((option) => option.selected),
    ),
    blocking,
  };
}

export function secretMode(
  intent: CommandIntent,
  prompt: SecretPrompt,
  message?: string,
): Extract<TerminalUiState["mode"], { kind: "secret" }> {
  return {
    kind: "secret",
    intent,
    prompt,
    value: "",
    submitting: false,
    ...(message === undefined ? {} : { message }),
  };
}

function providerLabel(provider: "deepseek" | "kimi" | "mem0"): string {
  return provider === "deepseek"
    ? "DeepSeek"
    : provider === "kimi"
      ? "Kimi"
      : "Mem0 Cloud";
}
