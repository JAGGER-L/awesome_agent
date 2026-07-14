import { useCallback, useRef } from "react";

import type { CommandPresentation } from "../commands/presenters.js";
import type { SurfaceStore } from "../state/index.js";

export function useCommandExecution(store: SurfaceStore) {
  const sequence = useRef(0);

  const appendPresentation = useCallback(
    (
      command: string,
      presentation: CommandPresentation,
      generation: number,
    ) => {
      sequence.current += 1;
      store.dispatch({
        type: "transcript.command_result",
        generation,
        block: {
          key: `command_result_${sequence.current}`,
          kind: "command_result",
          command,
          presentation,
        },
      });
    },
    [store],
  );

  const appendTextResult = useCallback(
    (
      command: string,
      tone: "info" | "warning" | "error",
      content: string,
      generation: number,
    ) => {
      appendPresentation(
        command,
        tone === "error"
          ? { kind: "error", title: `/${command}`, message: content }
          : {
              kind: "notice",
              message: content,
              tone,
            },
        generation,
      );
    },
    [appendPresentation],
  );

  const beginProgress = useCallback(
    (command: string, message: string, generation: number) => {
      sequence.current += 1;
      const key = `command_result_${sequence.current}`;
      store.dispatch({
        type: "transcript.command_result",
        generation,
        block: {
          key,
          kind: "command_result",
          command,
          presentation: {
            kind: "progress",
            message,
            tone: "info",
          },
        },
      });
      return (presentation: CommandPresentation) => {
        store.dispatch({
          type: "transcript.command_result.replace",
          generation,
          block: { key, kind: "command_result", command, presentation },
        });
      };
    },
    [store],
  );

  return { appendPresentation, appendTextResult, beginProgress } as const;
}
