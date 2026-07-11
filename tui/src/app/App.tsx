import { Box, Text, useInput, useStdout } from "ink";
import { useCallback, useRef, useState, useSyncExternalStore } from "react";

import type { CommandController } from "../commands/controller.js";
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
import { Picker, type PickerSelection } from "../components/Picker.js";
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
}: {
  store: SurfaceStore;
  controller?: CommandController;
  width?: number;
  blockingSelection?: boolean;
  welcome?: Omit<WelcomeProps, "width">;
  localCommands?: LocalCommandService;
  cancellation?: CancellationSnapshot;
  lifecycle?: AppLifecycle;
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
  const [helpCommand, setHelpCommand] = useState<string | null>();
  const [status, setStatus] = useState<StatusSnapshot>();
  const [localNotice, setLocalNotice] = useState<string>();
  const [clearRevision, setClearRevision] = useState(0);
  const globalKeys = useRef(new GlobalKeyController()).current;
  const historic =
    state.committed_transcript ??
    (state.thread ? hydrateThreadPage(state.thread).blocks : []);
  const live = projectLiveTurn(state);
  const cancelling = cancellation.status === "requested";
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

  useInput((input, key) => {
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
  });
  const submit = useCallback(
    async (value: string): Promise<ComposerSubmitResult> => {
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
      if (outcome.kind === "picker") {
        setPicker({
          kind: "command",
          intent: outcome.intent,
          selection: outcome.selection,
        });
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
        if (routed.kind === "command" && routed.intent.name === "status") {
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
    [
      applyLocalResult,
      controller,
      localCommands,
      state.application?.current_thread_id,
    ],
  );

  const select = useCallback(
    (value: string) => {
      if (!picker) return;
      const pending = picker;
      setPicker(undefined);
      if (pending.kind === "local_theme") {
        if (!localCommands) return;
        void localCommands
          .execute({ name: "theme", arguments: [value] })
          .then(applyLocalResult);
        return;
      }
      if (!controller) return;
      void controller.select(
        pending.intent,
        value,
        state.application?.current_thread_id,
      );
    },
    [
      applyLocalResult,
      controller,
      localCommands,
      picker,
      state.application?.current_thread_id,
    ],
  );

  return (
    <Box flexDirection="column">
      <Transcript blocks={historic} width={columns} welcome={welcome} />
      <ActiveTurn live={live} width={columns} />
      {status ? <StatusCommand snapshot={status} /> : null}
      {localNotice ? <Text>{localNotice}</Text> : null}
      {!cancelling ? (
        <CommandMenu
          query={picker || helpCommand !== undefined ? "" : composerValue}
        />
      ) : null}
      {cancelling ? null : picker ? (
        <Picker
          selection={picker.selection}
          onSelect={select}
          onClose={() => setPicker(undefined)}
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
        />
      )}
      <StatusLine state={state} cancellation={cancellation} />
    </Box>
  );
}
