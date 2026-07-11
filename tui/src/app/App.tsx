import { Box, useStdout } from "ink";
import { useCallback, useState, useSyncExternalStore } from "react";

import type { CommandIntent } from "../commands/parser.js";
import { parseInput } from "../commands/parser.js";
import type { CommandController } from "../commands/controller.js";
import { CommandMenu } from "../components/CommandMenu.js";
import { Composer, type ComposerSubmitResult } from "../components/Composer.js";
import { Picker, type PickerSelection } from "../components/Picker.js";
import { StatusLine } from "../components/StatusLine.js";
import type { WelcomeProps } from "../components/Welcome.js";
import { ActiveTurn } from "../components/transcript/ActiveTurn.js";
import { Transcript } from "../components/transcript/Transcript.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";
import { projectLiveTurn } from "../transcript/live.js";

type PendingPicker = {
  readonly intent: CommandIntent;
  readonly selection: PickerSelection;
};

export function App({
  store,
  controller,
  width,
  blockingSelection = false,
  welcome,
}: {
  store: SurfaceStore;
  controller?: CommandController;
  width?: number;
  blockingSelection?: boolean;
  welcome?: Omit<WelcomeProps, "width">;
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
  const historic =
    state.committed_transcript ??
    (state.thread ? hydrateThreadPage(state.thread).blocks : []);
  const live = projectLiveTurn(state);
  const submit = useCallback(
    async (value: string): Promise<ComposerSubmitResult> => {
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
        setPicker({ intent: outcome.intent, selection: outcome.selection });
      }
      if (outcome.kind === "error") {
        return {
          accepted: false,
          retryable: "error" in outcome ? outcome.error.retryable : true,
          message: "error" in outcome ? outcome.error.message : outcome.code,
        };
      }
      return { accepted: true };
    },
    [controller, state.application?.current_thread_id],
  );

  const select = useCallback(
    (value: string) => {
      if (!picker || !controller) return;
      const pending = picker;
      setPicker(undefined);
      void controller.select(
        pending.intent,
        value,
        state.application?.current_thread_id,
      );
    },
    [controller, picker, state.application?.current_thread_id],
  );

  return (
    <Box flexDirection="column">
      <Transcript blocks={historic} width={columns} welcome={welcome} />
      <ActiveTurn live={live} width={columns} />
      <CommandMenu query={picker ? "" : composerValue} />
      {picker ? (
        <Picker
          selection={picker.selection}
          onSelect={select}
          onClose={() => setPicker(undefined)}
          blocking={blockingSelection}
        />
      ) : (
        <Composer
          width={columns}
          onSubmit={submit}
          onValueChange={setComposerValue}
        />
      )}
      <StatusLine state={state} />
    </Box>
  );
}
