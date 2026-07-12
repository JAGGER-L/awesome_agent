import { Box, Text, useInput, useStdout } from "ink";
import { useCallback, useRef, useState, useSyncExternalStore } from "react";

import type {
  CommandController,
  CommandDispatchOutcome,
} from "../commands/controller.js";
import type {
  LocalCommandResult,
  LocalCommandService,
} from "../commands/local.js";
import type { CancellationSnapshot } from "../lifecycle/cancellation.js";
import type { ExitReason } from "../lifecycle/exit.js";
import type { CommandIntent } from "../commands/parser.js";
import { parseInput } from "../commands/parser.js";
import { CommandMenu } from "../components/CommandMenu.js";
import { Composer, type ComposerSubmitResult } from "../components/Composer.js";
import { Help } from "../components/Help.js";
import { InteractionPrompt } from "../components/InteractionPrompt.js";
import { Picker, type PickerSelection } from "../components/Picker.js";
import { ProviderSetupNotice } from "../components/ProviderSetupNotice.js";
import { SecretInput } from "../components/SecretInput.js";
import { StatusCommand } from "../components/StatusCommand.js";
import { StatusLine } from "../components/StatusLine.js";
import type { WelcomeProps } from "../components/Welcome.js";
import { ActiveTurn } from "../components/transcript/ActiveTurn.js";
import { Transcript } from "../components/transcript/Transcript.js";
import {
  statusSnapshotSchema,
  type StatusSnapshot,
} from "../protocol/commands.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";
import { projectLiveTurn } from "../transcript/live.js";
import { GlobalKeyController } from "./global-keys.js";

type PendingPicker =
  | {
      readonly kind: "command";
      readonly intent: CommandIntent;
      readonly selection: PickerSelection;
    }
  | { readonly kind: "local_theme"; readonly selection: PickerSelection };

type SecretPrompt = Extract<
  CommandDispatchOutcome,
  { kind: "secret" }
>["prompt"];
type CredentialFlow =
  | {
      readonly kind: "input";
      readonly intent: CommandIntent;
      readonly prompt: SecretPrompt;
      readonly message?: string;
      readonly submitting?: boolean;
    }
  | {
      readonly kind: "confirm";
      readonly intent: CommandIntent;
      readonly prompt: SecretPrompt;
      readonly secret: string;
    };

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
  const [composerValue, setComposerValue] = useState("");
  const [picker, setPicker] = useState<PendingPicker>();
  const [credentialFlow, setCredentialFlow] = useState<CredentialFlow>();
  const [helpCommand, setHelpCommand] = useState<string | null>();
  const [status, setStatus] = useState<StatusSnapshot>();
  const [localNotice, setLocalNotice] = useState<string>();
  const [clearRevision, setClearRevision] = useState(0);
  const globalKeys = useRef(new GlobalKeyController()).current;
  const commandInputBlocked = useRef(false);
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
  const applyLocalResult = useCallback(
    (result: LocalCommandResult): ComposerSubmitResult => {
      switch (result.kind) {
        case "help":
          setHelpCommand(result.command ?? null);
          return { accepted: true };
        case "picker":
          setPicker({ kind: "local_theme", selection: result.selection });
          return { accepted: true };
        case "notice":
        case "warning":
          setLocalNotice(result.message);
          return { accepted: true };
        case "shutdown":
          void lifecycle?.requestExit("quit_command");
          return { accepted: true };
      }
    },
    [lifecycle],
  );

  useInput(
    (input, key) => {
      const action = globalKeys.handle({
        input,
        key,
        activeOperation: state.active_operation?.status === "active",
        composerEmpty: composerValue.length === 0,
      });
      if (!action) return;
      switch (action.kind) {
        case "cancel":
          void lifecycle?.cancelActiveOperation();
          break;
        case "clear_composer":
          setClearRevision((value) => value + 1);
          setLocalNotice(undefined);
          break;
        case "exit_hint":
          setLocalNotice("Press Ctrl+C again to quit");
          break;
        case "exit":
          void lifecycle?.requestExit(action.reason);
          break;
      }
    },
    { isActive: credentialFlow === undefined },
  );

  const applyCommandOutcome = useCallback(
    async (
      outcome: CommandDispatchOutcome,
      intent?: CommandIntent,
    ): Promise<ComposerSubmitResult> => {
      if (outcome.kind === "picker") {
        commandInputBlocked.current = true;
        setPicker({
          kind: "command",
          intent: outcome.intent,
          selection: outcome.selection,
        });
        return { accepted: true };
      }
      if (outcome.kind === "secret") {
        commandInputBlocked.current = true;
        setCredentialFlow({
          kind: "input",
          intent: outcome.intent,
          prompt: outcome.prompt,
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
        commandInputBlocked.current = false;
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
        return {
          accepted: true,
          ...(outcome.result.content
            ? { message: outcome.result.content }
            : {}),
        };
      }
      return { accepted: true };
    },
    [applyLocalResult, localCommands],
  );
  const submit = useCallback(
    async (value: string): Promise<ComposerSubmitResult> => {
      if (commandInputBlocked.current) return { accepted: false };
      setStatus(undefined);
      setLocalNotice(undefined);
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
    [applyCommandOutcome, controller, state.application?.current_thread_id],
  );

  const select = useCallback(
    async (value: string) => {
      if (!picker) return;
      const pending = picker;
      setPicker(undefined);
      if (pending.kind === "local_theme") {
        if (!localCommands) return;
        applyLocalResult(
          await localCommands.execute({ name: "theme", arguments: [value] }),
        );
        return;
      }
      if (!controller) return;
      const outcome = await controller.select(
        pending.intent,
        value,
        state.application?.current_thread_id,
      );
      await applyCommandOutcome(outcome, pending.intent);
    },
    [
      applyLocalResult,
      applyCommandOutcome,
      controller,
      localCommands,
      picker,
      state.application?.current_thread_id,
    ],
  );

  const submitCredential = useCallback(
    async (secret: string, allowUnverified = false) => {
      if (!controller || !credentialFlow) return;
      const current = credentialFlow;
      setCredentialFlow(
        current.kind === "input" ? { ...current, submitting: true } : current,
      );
      const outcome = await controller.setCredential(
        current.prompt.provider,
        secret,
        allowUnverified,
      );
      if (outcome.kind === "error") {
        setCredentialFlow({
          kind: "input",
          intent: current.intent,
          prompt: current.prompt,
          message: outcome.error.message,
        });
        return;
      }
      if (outcome.result.status === "invalid") {
        setCredentialFlow({
          kind: "input",
          intent: current.intent,
          prompt: current.prompt,
          message: "The API key was rejected. Try another key.",
        });
        return;
      }
      if (outcome.result.status === "confirm_unverified") {
        setCredentialFlow({
          kind: "confirm",
          intent: current.intent,
          prompt: current.prompt,
          secret,
        });
        return;
      }
      const refreshed = await controller.refreshApplication();
      if (!refreshed.ok) {
        setCredentialFlow({
          kind: "input",
          intent: current.intent,
          prompt: current.prompt,
          message: refreshed.error.message,
        });
        return;
      }
      store.dispatch({
        type: "hydrate.application",
        application: refreshed.value,
      });
      const resumed = await controller.submit(
        { kind: "command", intent: current.intent },
        refreshed.value.current_thread_id,
      );
      await applyCommandOutcome(resumed, current.intent);
      setCredentialFlow(undefined);
    },
    [applyCommandOutcome, controller, credentialFlow, store],
  );

  const openProviderSetup = useCallback(() => {
    if (!controller) return;
    const intent: CommandIntent = { name: "model" };
    void controller
      .submit({ kind: "command", intent }, state.application?.current_thread_id)
      .then((outcome) => applyCommandOutcome(outcome, intent));
  }, [applyCommandOutcome, controller, state.application?.current_thread_id]);

  return (
    <Box flexDirection="column">
      <Transcript blocks={historic} width={columns} welcome={welcome} />
      <ActiveTurn live={live} width={columns} />
      {status ? <StatusCommand snapshot={status} /> : null}
      {localNotice ? <Text>{localNotice}</Text> : null}
      {providerSetupVisible && !credentialFlow ? <ProviderSetupNotice /> : null}
      {!cancelling && !exceptionalInteraction ? (
        <CommandMenu
          query={
            picker || credentialFlow || helpCommand !== undefined
              ? ""
              : composerValue
          }
        />
      ) : null}
      {exceptionalInteraction ? (
        <InteractionPrompt
          interaction={exceptionalInteraction}
          onRespond={(decision) => {
            void interactionResponder?.respond(decision);
          }}
        />
      ) : cancelling ? null : credentialFlow?.kind === "input" ? (
        <SecretInput
          label={credentialFlow.prompt.label}
          {...(credentialFlow.submitting === undefined
            ? {}
            : { submitting: credentialFlow.submitting })}
          {...(credentialFlow.message === undefined
            ? {}
            : { message: credentialFlow.message })}
          onSubmit={(value) => void submitCredential(value)}
          onCancel={() => {
            commandInputBlocked.current = false;
            setCredentialFlow(undefined);
          }}
        />
      ) : credentialFlow?.kind === "confirm" ? (
        <Picker
          selection={{
            prompt: "The Provider could not be reached. Save this key anyway?",
            options: [
              { value: "back", label: "Back", selected: true },
              { value: "save", label: "Save anyway", selected: false },
            ],
          }}
          onSelect={(value) => {
            if (value === "save") {
              void submitCredential(credentialFlow.secret, true);
            } else {
              setCredentialFlow({
                kind: "input",
                intent: credentialFlow.intent,
                prompt: credentialFlow.prompt,
              });
            }
          }}
          onClose={() => {
            commandInputBlocked.current = false;
            setCredentialFlow(undefined);
          }}
        />
      ) : picker ? (
        <Picker
          selection={picker.selection}
          onSelect={select}
          onClose={() => {
            commandInputBlocked.current = false;
            setPicker(undefined);
          }}
          blocking={blockingSelection}
        />
      ) : helpCommand !== undefined ? (
        <Help
          {...(helpCommand === null ? {} : { command: helpCommand })}
          onClose={() => setHelpCommand(undefined)}
        />
      ) : (
        <Composer
          width={columns}
          clearRevision={clearRevision}
          onSubmit={submit}
          onValueChange={setComposerValue}
          {...(providerSetupVisible
            ? { onEmptySubmit: openProviderSetup }
            : {})}
        />
      )}
      <StatusLine state={state} cancellation={cancellation} />
    </Box>
  );
}
