import { Box, Text, useStdout } from "ink";
import {
  useCallback,
  type Dispatch,
  useEffect,
  useRef,
  useSyncExternalStore,
} from "react";

import type {
  CommandController,
  CommandDispatchOutcome,
} from "../commands/controller.js";
import { findCommand } from "../commands/catalog.js";
import {
  presentCommandPayload,
  presentHelpResult,
} from "../commands/presenters.js";
import type {
  LocalCommandResult,
  LocalCommandService,
} from "../commands/local.js";
import type { CommandIntent } from "../commands/parser.js";
import { parseInput } from "../commands/parser.js";
import { searchCommands } from "../commands/search.js";
import { CommandMenu } from "../components/CommandMenu.js";
import { AuthPicker } from "../components/AuthPicker.js";
import { Composer } from "../components/Composer.js";
import { InteractionPrompt } from "../components/InteractionPrompt.js";
import { Picker } from "../components/Picker.js";
import { ProviderSetupNotice } from "../components/ProviderSetupNotice.js";
import { SecretInput } from "../components/SecretInput.js";
import { StatusLine } from "../components/StatusLine.js";
import { Welcome, type WelcomeProps } from "../components/Welcome.js";
import { ActiveTurn } from "../components/transcript/ActiveTurn.js";
import { Transcript } from "../components/transcript/Transcript.js";
import { classifyTerminalActionError } from "../interaction/action-errors.js";
import {
  routeTerminalKey,
  type TerminalIntent,
  type TerminalKey,
} from "../interaction/key-router.js";
import type {
  TerminalUiAction,
  TerminalUiState,
} from "../interaction/model.js";
import { initialTerminalUiState } from "../interaction/reducer.js";
import { TerminalInput } from "../interaction/TerminalInput.js";
import { useTerminalUi } from "../interaction/use-terminal-ui.js";
import type { CancellationSnapshot } from "../lifecycle/cancellation.js";
import type { ExitReason } from "../lifecycle/exit.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";
import {
  createClientMessageId,
  createCommandSubmissionId,
} from "../transcript/identity.js";
import { projectLiveTurn } from "../transcript/live.js";
import { GlobalKeyController } from "./global-keys.js";
import { useCommandExecution } from "./use-command-execution.js";
import {
  pickerMode,
  useInteractionController,
} from "./use-interaction-controller.js";
import {
  ThreadReplacementError,
  useThreadReplacement,
} from "./use-thread-replacement.js";
import { isAuthPicker } from "./use-interaction-flow.js";
import { threadViewportKey } from "./use-thread-viewport.js";

interface ComposerSubmitResult {
  readonly accepted: boolean;
  readonly retryable?: boolean;
  readonly message?: string;
}

export interface AppLifecycle {
  cancelActiveOperation(): Promise<void>;
  requestExit(reason: ExitReason): Promise<unknown>;
  resetThreadScope?(): void;
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
  reportFatal,
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
  reportFatal: (error: unknown) => void;
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
  const applyOutcomeRef = useRef<
    (
      outcome: CommandDispatchOutcome,
      intent?: CommandIntent,
      generation?: number,
    ) => Promise<ComposerSubmitResult>
  >(async () => ({ accepted: true }));
  const {
    appendPresentation,
    appendTextResult: appendCommandResult,
    beginProgress,
  } = useCommandExecution(store);
  const globalKeys = useRef(new GlobalKeyController()).current;
  const replaceThread = useThreadReplacement({
    store,
    controller,
    resetThreadScope: lifecycle?.resetThreadScope,
  });
  const historic =
    state.committed_transcript ??
    (state.thread ? hydrateThreadPage(state.thread).blocks : []);
  const live = projectLiveTurn(state);
  const cancelling = cancellation.status === "requested";
  const liveWelcome = welcome
    ? {
        ...welcome,
        workspacePath:
          state.application?.workspace.display_path ?? welcome.workspacePath,
        model:
          state.application?.model_identity?.effective_model ?? welcome.model,
        thinkingEnabled:
          state.application?.thinking_enabled ?? welcome.thinkingEnabled,
        permissionMode:
          state.application?.permission_mode ?? welcome.permissionMode,
        localMemoryEnabled: memoryStateEnabled(
          state.application?.memory_status,
          "local",
          welcome.localMemoryEnabled,
        ),
        mem0Enabled: memoryStateEnabled(
          state.application?.memory_status,
          "mem0",
          welcome.mem0Enabled,
        ),
      }
    : undefined;
  const providerSetupVisible =
    providerSetupRequired &&
    !credentialConfigured(state.application?.provider_credentials.deepseek) &&
    !credentialConfigured(state.application?.provider_credentials.kimi);

  const runTerminalAction = useCallback(
    (action: () => Promise<void>) => {
      void action().catch((error: unknown) => {
        const failure = classifyTerminalActionError(error);
        if (failure.kind === "fatal") {
          reportFatal(failure.error);
          return;
        }
        const mode = uiRef.current.mode;
        if (mode.kind === "secret") {
          dispatch({ type: "mode.secret.submitting", submitting: false });
          dispatch({ type: "mode.secret.message", message: failure.message });
          return;
        }
        if (mode.kind === "approval") {
          dispatch({ type: "mode.approval.submitting", submitting: false });
          dispatch({ type: "mode.approval.message", message: failure.message });
          return;
        }
        dispatch({ type: "composer.submitting", submitting: false });
        dispatch({ type: "notice.set", message: failure.message });
      });
    },
    [dispatch, reportFatal, uiRef],
  );
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
    (result: LocalCommandResult, generation: number): ComposerSubmitResult => {
      if (store.getState().thread_generation !== generation) {
        return { accepted: true };
      }
      switch (result.kind) {
        case "help":
          appendPresentation("help", presentHelpResult(result), generation);
          return { accepted: true };
        case "result":
          appendCommandResult(
            result.command,
            result.tone,
            result.content,
            generation,
          );
          return { accepted: true };
        case "picker":
          dispatch({
            type: "mode.open",
            mode: pickerMode({ kind: "local_theme" }, result.selection, false),
          });
          return { accepted: true };
        case "shutdown":
          runTerminalAction(async () => {
            await lifecycle?.requestExit("quit_command");
          });
          return { accepted: true };
      }
    },
    [
      appendCommandResult,
      appendPresentation,
      dispatch,
      lifecycle,
      runTerminalAction,
      store,
    ],
  );

  const interactions = useInteractionController({
    controller,
    store,
    dispatch,
    uiRef,
    currentThreadId: state.application?.current_thread_id,
    localCommands,
    interactionResponder,
    appendCommandResult,
    applyLocalResult,
    applyOutcome: async (outcome, intent, generation) =>
      await applyOutcomeRef.current(outcome, intent, generation),
  });

  const applyCommandOutcome = useCallback(
    async (
      outcome: CommandDispatchOutcome,
      intent?: CommandIntent,
      generation = store.getState().thread_generation,
    ): Promise<ComposerSubmitResult> => {
      if (store.getState().thread_generation !== generation) {
        return { accepted: true };
      }
      switch (outcome.kind) {
        case "selection":
        case "secret":
        case "application_interaction":
          interactions.openOutcome(outcome, blockingSelection);
          return { accepted: true };
        case "command_error":
          if (intent) {
            appendCommandResult(
              intent.name,
              "error",
              outcome.message,
              generation,
            );
            return { accepted: true };
          }
          return { accepted: false, retryable: true, message: outcome.message };
        case "error": {
          const message =
            "error" in outcome ? outcome.error.message : outcome.code;
          if (intent) {
            appendCommandResult(intent.name, "error", message, generation);
            return { accepted: true };
          }
          return {
            accepted: false,
            retryable: "error" in outcome ? outcome.error.retryable : true,
            message,
          };
        }
        case "local":
          if (!localCommands) {
            return {
              accepted: false,
              retryable: true,
              message: "local_commands_unavailable",
            };
          }
          return applyLocalResult(
            await localCommands.execute(outcome.intent),
            generation,
          );
        case "result": {
          const payload = outcome.payload;
          if (payload.kind === "thread") {
            try {
              await replaceThread({
                threadId: payload.thread_id,
                expectedGeneration: generation,
                reason: payload.action === "created" ? "new" : "resume",
              });
            } catch (error) {
              if (!(error instanceof ThreadReplacementError)) throw error;
              appendCommandResult(
                intent?.name ?? "resume",
                "error",
                error.message,
                generation,
              );
            }
          } else if (intent) {
            appendPresentation(
              intent.name,
              presentCommandPayload(intent.name, payload),
              generation,
            );
          }
          if (payload.kind === "permissions" && controller) {
            const refreshed = await controller.refreshApplication();
            if (
              refreshed.ok &&
              store.getState().thread_generation === generation
            ) {
              store.dispatch({
                type: "hydrate.application",
                application: refreshed.value,
              });
            }
          }
          return {
            accepted: true,
            ...(payload.kind === "notice" ? { message: payload.message } : {}),
          };
        }
        case "accepted":
          return { accepted: true };
      }
    },
    [
      applyLocalResult,
      appendCommandResult,
      appendPresentation,
      blockingSelection,
      controller,
      localCommands,
      replaceThread,
      store,
      interactions,
    ],
  );
  applyOutcomeRef.current = applyCommandOutcome;
  const submit = useCallback(
    async (value: string): Promise<ComposerSubmitResult> => {
      dispatch({ type: "notice.clear" });
      const submitted = value.trimStart();
      if (submitted.startsWith("/")) {
        store.dispatch({
          type: "transcript.command.submitted",
          submission_id: createCommandSubmissionId(),
          text: submitted,
          generation: store.getState().thread_generation,
        });
      }
      const routed = parseInput(value);
      if (!routed) return { accepted: true };
      if (routed.kind === "invalid") {
        if (value.trimStart().startsWith("/")) {
          appendCommandResult(
            value.trimStart().slice(1).split(/\s/u, 1)[0] || "command",
            "error",
            routed.code,
            store.getState().thread_generation,
          );
          return { accepted: true };
        }
        return { accepted: false, retryable: true, message: routed.code };
      }
      if (!controller) {
        return {
          accepted: false,
          retryable: true,
          message: "surface_not_connected",
        };
      }
      const generation = store.getState().thread_generation;
      const threadId = state.application?.current_thread_id;
      const optimisticMessage =
        routed.kind === "turn"
          ? { id: createClientMessageId(), text: routed.content }
          : undefined;
      if (optimisticMessage) {
        store.dispatch({
          type: "transcript.user.pending",
          client_message_id: optimisticMessage.id,
          text: optimisticMessage.text,
          generation,
        });
      }
      let outcome: CommandDispatchOutcome;
      const finishProgress =
        routed.kind === "command" && routed.intent.name === "compact"
          ? beginProgress("compact", "Compressing context...", generation)
          : undefined;
      try {
        outcome = optimisticMessage
          ? await controller.submit(routed, threadId, optimisticMessage.id)
          : await controller.submit(routed, threadId);
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
          store.getState().thread_generation === generation
        ) {
          store.dispatch({
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
      if (finishProgress) {
        if (outcome.kind === "result") {
          finishProgress(presentCommandPayload("compact", outcome.payload));
        } else if (
          outcome.kind === "error" ||
          outcome.kind === "command_error"
        ) {
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
        store.getState().thread_generation === generation
      ) {
        if (
          outcome.kind === "accepted" &&
          outcome.operation.client_message_id === optimisticMessage.id
        ) {
          store.dispatch({
            type: "transcript.user.accepted",
            client_message_id: optimisticMessage.id,
            generation,
          });
        } else {
          store.dispatch({
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
      return await applyCommandOutcome(
        outcome,
        routed.kind === "command" ? routed.intent : undefined,
        generation,
      );
    },
    [
      applyCommandOutcome,
      appendCommandResult,
      beginProgress,
      controller,
      dispatch,
      state.application?.current_thread_id,
      store,
    ],
  );

  const submitValue = useCallback(
    async (value: string) => {
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
    },
    [dispatch, submit],
  );

  const submitComposer = useCallback(async () => {
    const value = uiRef.current.composer.value;
    if (value.trim().length === 0) {
      if (providerSetupVisible) {
        const intent: CommandIntent = { name: "model" };
        if (controller) {
          const generation = store.getState().thread_generation;
          const outcome = await controller.submit(
            { kind: "command", intent },
            state.application?.current_thread_id,
          );
          await applyCommandOutcome(outcome, intent, generation);
        }
      }
      return;
    }
    await submitValue(value);
  }, [
    applyCommandOutcome,
    controller,
    providerSetupVisible,
    state.application?.current_thread_id,
    store,
    submitValue,
    uiRef,
  ]);

  const selectCurrent = useCallback(async () => {
    const mode = uiRef.current.mode;
    if (mode.kind === "command_menu") {
      const command = mode.selectedCommand
        ? findCommand(mode.selectedCommand)
        : undefined;
      if (command) {
        dispatch({ type: "mode.cancel" });
        await submitValue(command.completion);
        return;
      }
      const value = uiRef.current.composer.value;
      dispatch({ type: "mode.cancel" });
      await submitValue(value);
      return;
    }
    await interactions.confirmSelection();
  }, [dispatch, interactions, submitValue, uiRef]);
  const completeCommand = useCallback(() => {
    const mode = uiRef.current.mode;
    if (mode.kind !== "command_menu" || !mode.selectedCommand) return;
    const command = findCommand(mode.selectedCommand);
    if (!command) return;
    dispatch({
      type: "composer.edit",
      action: { type: "replace", value: command.completion },
    });
    dispatch({ type: "mode.cancel" });
  }, [dispatch, uiRef]);

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
          runTerminalAction(async () => {
            await lifecycle?.cancelActiveOperation();
          });
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
          runTerminalAction(async () => {
            await lifecycle?.requestExit(action.reason);
          });
          break;
      }
    },
    [
      dispatch,
      globalKeys,
      lifecycle,
      runTerminalAction,
      state.active_operation?.status,
      uiRef,
    ],
  );

  const handleTerminalInput = useCallback(
    (input: string, key: TerminalKey) => {
      const routed = routeTerminalKey(uiRef.current, input, key);
      if (!routed) return;
      handleTerminalIntent(routed, input, key, {
        dispatch,
        onSubmit: () => runTerminalAction(submitComposer),
        onSelect: () => runTerminalAction(selectCurrent),
        onCommandComplete: completeCommand,
        onDeny: () => {
          const mode = uiRef.current.mode;
          if (mode.kind !== "approval") return;
          runTerminalAction(async () => {
            await interactions.respondApproval("deny");
          });
        },
        onSecretSubmit: () => {
          if (uiRef.current.mode.kind !== "secret") return;
          runTerminalAction(async () => {
            await interactions.submitSecret();
          });
        },
        onLifecycle: handleLifecycle,
      });
    },
    [
      handleLifecycle,
      completeCommand,
      dispatch,
      interactions,
      selectCurrent,
      submitComposer,
      runTerminalAction,
      uiRef,
    ],
  );

  return (
    <Box flexDirection="column">
      <TerminalInput active={!cancelling} onInput={handleTerminalInput} />
      {liveWelcome ? <Welcome {...liveWelcome} width={columns} /> : null}
      <Transcript
        key={threadViewportKey(state)}
        blocks={historic}
        width={columns}
        detailsExpanded={ui.detailsExpanded}
      />
      <ActiveTurn
        live={live}
        width={columns}
        detailsExpanded={ui.detailsExpanded}
      />
      {ui.notice ? <Text>{ui.notice}</Text> : null}
      {providerSetupVisible && ui.mode.kind !== "secret" ? (
        <ProviderSetupNotice />
      ) : null}
      {!cancelling && ui.mode.kind === "command_menu" ? (
        <CommandMenu
          commands={searchCommands(ui.mode.query)}
          {...(ui.mode.selectedCommand === undefined
            ? {}
            : { selectedCommand: ui.mode.selectedCommand })}
          viewportStart={ui.mode.viewportStart}
        />
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
      ) : isAuthPicker(ui.mode) ? (
        <AuthPicker
          selection={ui.mode.selection}
          selected={ui.mode.selected}
          width={columns}
        />
      ) : ui.mode.kind === "picker" ? (
        <Picker selection={ui.mode.selection} selected={ui.mode.selected} />
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

function credentialConfigured(
  status:
    | {
        selected_source?: "environment" | "awesome" | null | undefined;
        environment_configured: boolean;
        awesome_configured: boolean;
      }
    | undefined,
): boolean {
  return status?.selected_source === "environment"
    ? status.environment_configured
    : status?.selected_source === "awesome"
      ? status.awesome_configured
      : false;
}

function memoryStateEnabled(
  status: Readonly<Record<string, unknown>> | undefined,
  key: string,
  fallback: boolean,
): boolean {
  const value = status?.[key];
  if (typeof value === "boolean") return value;
  if (typeof value === "object" && value !== null && "enabled" in value) {
    return value.enabled === true;
  }
  return fallback;
}

function handleTerminalIntent(
  intent: TerminalIntent,
  input: string,
  key: TerminalKey,
  handlers: {
    readonly dispatch: Dispatch<TerminalUiAction>;
    readonly onSubmit: () => void;
    readonly onSelect: () => void;
    readonly onCommandComplete: () => void;
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
      handlers.onCommandComplete();
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
    case "details.toggle":
      handlers.dispatch({ type: "details.toggle" });
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
