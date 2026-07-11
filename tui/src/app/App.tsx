import { Box, useStdout } from "ink";
import { useSyncExternalStore } from "react";

import { StatusLine } from "../components/StatusLine.js";
import { ActiveTurn } from "../components/transcript/ActiveTurn.js";
import { Transcript } from "../components/transcript/Transcript.js";
import type { SurfaceStore } from "../state/index.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";
import { projectLiveTurn } from "../transcript/live.js";

export function App({ store, width }: { store: SurfaceStore; width?: number }) {
  const state = useSyncExternalStore(
    store.subscribe,
    store.getState,
    store.getState,
  );
  const { stdout } = useStdout();
  const columns = width ?? stdout.columns ?? 80;
  const historic =
    state.committed_transcript ??
    (state.thread ? hydrateThreadPage(state.thread).blocks : []);
  const live = projectLiveTurn(state);
  return (
    <Box flexDirection="column">
      <Transcript blocks={historic} width={columns} />
      <ActiveTurn live={live} width={columns} />
      <StatusLine state={state} />
    </Box>
  );
}
