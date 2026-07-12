import { Box, Text, useStdout } from "ink";
import {
  useCallback,
  type Dispatch,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import type {
  CommandController,
  CommandDispatchOutcome,
} from "../commands/controller.js";
import type {
  LocalCommandResult,
  LocalCommandService,
} from "../commands/local.js";
import type { CommandIntent } from "../commands/parser.js";
import { parseInput } from "../commands/parser.js";
import { CommandMenu } from "../components/CommandMenu.js";
import { Composer } from "../components/Composer.js";
import { Help } from "../components/Help.js";
import { InteractionPrompt } from "../components/InteractionPrompt.js";
import { Picker } from "../components/Picker.js";
import { ProviderSetupNotice } from "../components/ProviderSetupNotice.js";
import { SecretInput } from "../components/SecretInput.js";
import { StatusCommand } from "../components/StatusCommand.js";
import { StatusLine } from "../components/StatusLine.js";
import type { WelcomeProps } from "../components/Welcome.js";
import { ActiveTurn } from "../components/transcript/ActiveTurn.js";
import { Transcript } from "../components/transcript/Transcript.js";
import {
  routeTerminalKey,
  type TerminalIntent,
  type TerminalKey,
} from "../interaction/key-router.js";
import type {
  PickerOwner,
  PickerSelection,
  SecretPrompt,
  TerminalUiAction,
  TerminalUiState,
} from "../interaction/model.js";
import { initialTerminalUiState } from "../interaction/reducer.js";
import { TerminalInput } from "../interaction/TerminalInput.js";
import { useTerminalUi } from "../interaction/use-terminal-ui.js";
import type { CancellationSnapshot } from "../lifecycle/cancellation.js";
import type { ExitReason } from "../lifecycle/exit.js";
import {
  statusSnapshotSchema,
  type StatusSnapshot,
} from "../protocol/commands.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";
import { projectLiveTurn } from "../transcript/live.js";
import { GlobalKeyController } from "./global-keys.js";

interface ComposerSubmitResult {
  readonly accepted: boolean;
  readonly retryable?: boolean;
  readonly message?: string;
}

export interface AppLifecycle {
  cancelActiveOperation(): Promise<void>;
  requestExit(reason: ExitReason): Promise<unknown>;
}

export function App({
  store,
  controller,
  width,
  blockingSelection = false,
  welcome,
  localCommands,
  cancellation = { status: "idle" },
  lifecycle,
  interactionResponder,
  providerSetupRequired = false,
}: {
  store: SurfaceStore;
  controller?: CommandController;
  width?: number;
  blockingSelection?: boolean;
  welcome?: Omit<WelcomeProps, "width">;
  localCommands?: LocalCommandService;
  cancellation?: CancellationSnapshot;
  lifecycle?: AppLifecycle;
  interactionResponder?: { respond(decision: string): Promise<void> };
  providerSetupRequired?: boolean;
}) {
  const state = useSyncExternalStore(
    store.subscribe,
    store.getState,
    store.getState,
  );
  const { stdout } = useStdout();
  const columns = width ?? stdout.columns ?? 80;
  const terminal = useTerminalUi(initialRuntimeUi(state.pending_interaction));
  const ui = terminal.state;
  const uiRef = terminal.current;
  const dispatch = terminal.dispatch;
  const [status, setStatus] = useState<StatusSnapshot>();
  const globalKeys = useRef(new GlobalKeyController()).current;
  const historic =
    state.committed_transcript ??
    (state.thread ? hydrateThreadPage(state.thread).blocks : []);
  const live = projectLiveTurn(state);
  const cancelling = cancellation.status === "requested";
  const providerSetupVisible =
    providerSetupRequired &&
    state.application?.provider_credentials.deepseek.source === "missing" &&
    state.application.provider_credentials.kimi.source === "missing";
  const exceptionalInteraction =
    state.pending_interaction?.interaction_kind === "workspace_trust"
      ? undefined
      : state.pending_interaction;

  useEffect(() => {
    dispatch({
      type: "composer.edit",
      action: { type: "resize", width: columns },
    });
  }, [columns, dispatch]);

  useEffect(() => {
    if (exceptionalInteraction) {
      if (
        ui.mode.kind !== "approval" ||
        ui.mode.interaction.interaction_id !==
          exceptionalInteraction.interaction_id
      ) {
        dispatch({
          type: "mode.open",
          mode: {
            kind: "approval",
            interaction: exceptionalInteraction,
            selected: 0,
            submitting: false,
          },
        });
      }
    } else if (ui.mode.kind === "approval") {
      dispatch({ type: "mode.cancel" });
    }
  }, [dispatch, exceptionalInteraction, ui.mode]);

  const applyLocalResult = useCallback(
    (result: LocalCommandResult): ComposerSubmitResult => {
      switch (result.kind) {
        case "help":
          dispatch({
            type: "mode.open",
            mode: {
              kind: "help",
              ...(result.command === undefined
                ? {}
                : { command: result.command }),
            },
          });
          return { accepted: true };
        case "picker":
          dispatch({
            type: "mode.open",
            mode: pickerMode({ kind: "local_theme" }, result.selection, false),
          });
          return { accepted: true };
        case "notice":
        case "warning":
          dispatch({ type: "notice.set", message: result.message });
          return { accepted: true };
        case "shutdown":
          void lifecycle?.requestExit("quit_command");
          return { accepted: true };
      }
    },
    [dispatch, lifecycle],
  );

  const applyCommandOutcome = useCallback(
    async (
      outcome: CommandDispatchOutcome,
      intent?: CommandIntent,
    ): Promise<ComposerSubmitResult> => {
      if (outcome.kind === "picker") {
        dispatch({
          type: "mode.open",
          mode: pickerMode(
            { kind: "command", intent: outcome.intent },
            outcome.selection,
            blockingSelection,
          ),
        });
        return { accepted: true };
      }
      if (outcome.kind === "secret") {
        dispatch({
          type: "mode.open",
          mode: secretMode(outcome.intent, outcome.prompt),
        });
        return { accepted: true };
      }
      if (outcome.kind === "error") {
        return {
          accepted: false,
          retryable: "error" in outcome ? outcome.error.retryable : true,
          message: "error" in outcome ? outcome.error.message : outcome.code,
        };
      }
      if (outcome.kind === "local") {
        if (!localCommands) {
          return {
            accepted: false,
            retryable: true,
            message: "local_commands_unavailable",
          };
        }
        return applyLocalResult(await localCommands.execute(outcome.intent));
      }
      if (outcome.kind === "result") {
        if (outcome.result.status === "error") {
          return {
            accepted: false,
            retryable: true,
            message: outcome.result.content || "command_failed",
          };
        }
        if (intent?.name === "status") {
          const snapshot = statusSnapshotSchema.safeParse(outcome.result.data);
          if (!snapshot.success) {
            return {
              accepted: false,
              retryable: true,
              message: "invalid_status_snapshot",
            };
          }
          setStatus(snapshot.data);
        }
        if (
          intent?.name === "permissions" &&
          outcome.result.status === "success" &&
          controller
        ) {
          const refreshed = await controller.refreshApplication();
          if (refreshed.ok) {
            store.dispatch({
              type: "hydrate.application",
              application: refreshed.value,
            });
          }
        }
        return {
          accepted: true,
          ...(outcome.result.content
            ? { message: outcome.result.content }
            : {}),
        };
      }
      return { accepted: true };
    },
    [
      applyLocalResult,
      blockingSelection,
      controller,
      dispatch,
      localCommands,
      store,
    ],
  );

  const submit = useCallback(
    async (value: string): Promise<ComposerSubmitResult> => {
      setStatus(undefined);
      dispatch({ type: "notice.clear" });
      const routed = parseInput(value);
      if (!routed) return { accepted: true };
      if (routed.kind === "invalid") {
        return { accepted: false, retryable: true, message: routed.code };
      }
      if (!controller) {
        return {
          accepted: false,
          retryable: true,
          message: "surface_not_connected",
        };
      }
      const outcome = await controller.submit(
        routed,
        state.application?.current_thread_id,
      );
      return await applyCommandOutcome(
        outcome,
        routed.kind === "command" ? routed.intent : undefined,
      );
    },
    [
      applyCommandOutcome,
      controller,
      dispatch,
      state.application?.current_thread_id,
    ],
  );

  const submitComposer = useCallback(async () => {
    const value = uiRef.current.composer.value;
    if (value.trim().length === 0) {
      if (providerSetupVisible) {
        const intent: CommandIntent = { name: "model" };
        if (controller) {
          const outcome = await controller.submit(
            { kind: "command", intent },
            state.application?.current_thread_id,
          );
          await applyCommandOutcome(outcome, intent);
        }
      }
      return;
    }
    dispatch({ type: "composer.submitting", submitting: true });
    dispatch({ type: "composer.message" });
    try {
      const result = await submit(value);
      if (result.accepted) {
        dispatch({
          type: "composer.edit",
          action: { type: "submit_history", value },
        });
        dispatch({
          type: "composer.edit",
          action: { type: "replace", value: "" },
        });
      }
      dispatch({ type: "composer.message", message: result.message });
    } finally {
      dispatch({ type: "composer.submitting", submitting: false });
    }
  }, [
    applyCommandOutcome,
    controller,
    dispatch,
    providerSetupVisible,
    state.application?.current_thread_id,
    submit,
    uiRef,
  ]);

  const submitCredential = useCallback(
    async (
      intent: CommandIntent,
      prompt: SecretPrompt,
      secret: string,
      allowUnverified = false,
    ) => {
      if (!controller) return;
      if (!allowUnverified) {
        dispatch({ type: "mode.secret.submitting", submitting: true });
      }
      const outcome = await controller.setCredential(
        prompt.provider,
        secret,
        allowUnverified,
      );
      if (outcome.kind === "error") {
        dispatch({
          type: "mode.open",
          mode: secretMode(intent, prompt, outcome.error.message),
        });
        return;
      }
      if (outcome.result.status === "invalid") {
        dispatch({
          type: "mode.open",
          mode: secretMode(
            intent,
            prompt,
            "The API key was rejected. Try another key.",
          ),
        });
        return;
      }
      if (outcome.result.status === "confirm_unverified") {
        dispatch({
          type: "mode.open",
          mode: pickerMode(
            { kind: "credential_confirm", intent, prompt, secret },
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
      if (!refreshed.ok) {
        dispatch({
          type: "mode.open",
          mode: secretMode(intent, prompt, refreshed.error.message),
        });
        return;
      }
      store.dispatch({
        type: "hydrate.application",
        application: refreshed.value,
      });
      const resumed = await controller.submit(
        { kind: "command", intent },
        refreshed.value.current_thread_id,
      );
      dispatch({ type: "mode.cancel" });
      await applyCommandOutcome(resumed, intent);
    },
    [applyCommandOutcome, controller, dispatch, store],
  );

  const respondApproval = useCallback(
    async (decision: string) => {
      dispatch({ type: "mode.approval.submitting", submitting: true });
      try {
        await interactionResponder?.respond(decision);
        if (controller) {
          const refreshed = await controller.refreshApplication();
          if (refreshed.ok) {
            store.dispatch({
              type: "hydrate.application",
              application: refreshed.value,
            });
          }
        }
      } catch (error) {
        dispatch({
          type: "mode.approval.submitting",
          submitting: false,
        });
        dispatch({
          type: "mode.approval.message",
          message:
            error instanceof Error ? error.message : "Interaction failed.",
        });
      }
    },
    [controller, dispatch, interactionResponder, store],
  );

  const selectCurrent = useCallback(async () => {
    const mode = uiRef.current.mode;
    if (mode.kind === "approval") {
      const choice = mode.interaction.choices[mode.selected];
      if (!choice) return;
      await respondApproval(choice.decision);
      return;
    }
    if (mode.kind !== "picker") return;
    const option = mode.selection.options[mode.selected];
    if (!option) return;
    const owner = mode.owner;
    if (owner.kind === "local_theme") {
      dispatch({ type: "mode.cancel" });
      if (localCommands) {
        applyLocalResult(
          await localCommands.execute({
            name: "theme",
            arguments: [option.value],
          }),
        );
      }
      return;
    }
    if (owner.kind === "command") {
      dispatch({ type: "mode.cancel" });
      if (controller) {
        const outcome = await controller.select(
          owner.intent,
          option.value,
          state.application?.current_thread_id,
        );
        await applyCommandOutcome(outcome, owner.intent);
      }
      return;
    }
    if (owner.kind === "credential_confirm") {
      if (option.value === "save") {
        await submitCredential(owner.intent, owner.prompt, owner.secret, true);
      } else {
        dispatch({
          type: "mode.open",
          mode: secretMode(owner.intent, owner.prompt),
        });
      }
    }
  }, [
    applyCommandOutcome,
    applyLocalResult,
    controller,
    dispatch,
    localCommands,
    state.application?.current_thread_id,
    submitCredential,
    respondApproval,
    uiRef,
  ]);

  const handleLifecycle = useCallback(
    (input: string, key: TerminalKey) => {
      const action = globalKeys.handle({
        input,
        key,
        activeOperation: state.active_operation?.status === "active",
        composerEmpty: uiRef.current.composer.value.length === 0,
      });
      if (!action) return;
      switch (action.kind) {
        case "cancel":
          void lifecycle?.cancelActiveOperation();
          break;
        case "clear_composer":
          dispatch({
            type: "composer.edit",
            action: { type: "replace", value: "" },
          });
          dispatch({ type: "composer.message" });
          dispatch({ type: "notice.clear" });
          break;
        case "exit_hint":
          dispatch({
            type: "notice.set",
            message: "Press Ctrl+C again to quit",
          });
          break;
        case "exit":
          void lifecycle?.requestExit(action.reason);
          break;
      }
    },
    [dispatch, globalKeys, lifecycle, state.active_operation?.status, uiRef],
  );

  const handleTerminalInput = useCallback(
    (input: string, key: TerminalKey) => {
      const routed = routeTerminalKey(uiRef.current, input, key);
      if (!routed) return;
      handleTerminalIntent(routed, input, key, {
        dispatch,
        onSubmit: () => void submitComposer(),
        onSelect: () => void selectCurrent(),
        onDeny: () => {
          const mode = uiRef.current.mode;
          if (mode.kind !== "approval") return;
          void respondApproval("deny");
        },
        onSecretSubmit: () => {
          if (uiRef.current.mode.kind !== "secret") return;
          const { intent, prompt, value } = uiRef.current.mode;
          void submitCredential(intent, prompt, value);
        },
        onLifecycle: handleLifecycle,
      });
    },
    [
      handleLifecycle,
      dispatch,
      respondApproval,
      selectCurrent,
      submitComposer,
      submitCredential,
      uiRef,
    ],
  );

  return (
    <Box flexDirection="column">
      <TerminalInput active={!cancelling} onInput={handleTerminalInput} />
      <Transcript blocks={historic} width={columns} welcome={welcome} />
      <ActiveTurn live={live} width={columns} />
      {status ? <StatusCommand snapshot={status} /> : null}
      {ui.notice ? <Text>{ui.notice}</Text> : null}
      {providerSetupVisible && ui.mode.kind !== "secret" ? (
        <ProviderSetupNotice />
      ) : null}
      {!cancelling && ui.mode.kind === "composer" ? (
        <CommandMenu query={ui.composer.value} />
      ) : null}
      {cancelling ? null : ui.mode.kind === "approval" ? (
        <InteractionPrompt
          interaction={ui.mode.interaction}
          selected={ui.mode.selected}
          submitting={ui.mode.submitting}
          {...(ui.mode.message === undefined
            ? {}
            : { message: ui.mode.message })}
        />
      ) : ui.mode.kind === "secret" ? (
        <SecretInput
          label={ui.mode.prompt.label}
          value={ui.mode.value}
          submitting={ui.mode.submitting}
          {...(ui.mode.message === undefined
            ? {}
            : { message: ui.mode.message })}
        />
      ) : ui.mode.kind === "picker" ? (
        <Picker selection={ui.mode.selection} selected={ui.mode.selected} />
      ) : ui.mode.kind === "help" ? (
        <Help
          {...(ui.mode.command === undefined
            ? {}
            : { command: ui.mode.command })}
        />
      ) : (
        <Composer
          state={ui.composer}
          submitting={ui.composerSubmitting}
          {...(ui.composerMessage === undefined
            ? {}
            : { message: ui.composerMessage })}
        />
      )}
      <StatusLine state={state} cancellation={cancellation} />
    </Box>
  );
}

function pickerMode(
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

function secretMode(
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

function handleTerminalIntent(
  intent: TerminalIntent,
  input: string,
  key: TerminalKey,
  handlers: {
    readonly dispatch: Dispatch<TerminalUiAction>;
    readonly onSubmit: () => void;
    readonly onSelect: () => void;
    readonly onDeny: () => void;
    readonly onSecretSubmit: () => void;
    readonly onLifecycle: (input: string, key: TerminalKey) => void;
  },
): void {
  switch (intent.type) {
    case "mode.cancel":
      handlers.dispatch({ type: "mode.cancel" });
      break;
    case "selection.move":
      handlers.dispatch({ type: "mode.select", delta: intent.delta });
      break;
    case "selection.set":
      handlers.dispatch({ type: "mode.set", selected: intent.selected });
      break;
    case "selection.confirm":
      handlers.onSelect();
      break;
    case "approval.deny":
      handlers.onDeny();
      break;
    case "trust.deny":
      break;
    case "command.complete":
      break;
    case "secret.insert":
      handlers.dispatch({ type: "mode.secret.insert", text: intent.text });
      break;
    case "secret.backspace":
      handlers.dispatch({ type: "mode.secret.backspace" });
      break;
    case "secret.submit":
      handlers.onSecretSubmit();
      break;
    case "composer.edit":
      handlers.dispatch({ type: "composer.edit", action: intent.action });
      handlers.dispatch({ type: "composer.message" });
      break;
    case "composer.submit":
      handlers.onSubmit();
      break;
    case "lifecycle.evaluate":
      handlers.onLifecycle(input, key);
      break;
  }
}

function initialRuntimeUi(
  interaction: ReturnType<SurfaceStore["getState"]>["pending_interaction"],
): TerminalUiState {
  const initial = initialTerminalUiState();
  return interaction?.interaction_kind === "workspace_trust"
    ? initial
    : interaction
      ? {
          ...initial,
          mode: {
            kind: "approval",
            interaction,
            selected: 0,
            submitting: false,
          },
        }
      : initial;
}
