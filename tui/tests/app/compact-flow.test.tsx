import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import type {
  CommandController,
  CommandDispatchOutcome,
} from "../../src/commands/controller.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      assertion();
      return;
    } catch {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  assertion();
}

describe("compact command lifecycle", () => {
  it("replaces one pending block with success using the same identity", async () => {
    let complete: ((outcome: CommandDispatchOutcome) => void) | undefined;
    const controller = {
      submit: vi.fn(
        async () =>
          await new Promise<CommandDispatchOutcome>((resolve) => {
            complete = resolve;
          }),
      ),
    } as unknown as CommandController;
    const store = createSurfaceStore();
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );
    view.stdin.write("/compact");
    view.stdin.write("\r");

    await eventually(() =>
      expect(store.getState().committed_transcript).toHaveLength(2),
    );
    const pending = store.getState().committed_transcript?.[1];
    expect(pending).toMatchObject({
      kind: "command_result",
      presentation: { kind: "progress", message: "Compressing context..." },
    });
    const key = pending?.key;

    complete?.({
      kind: "result",
      payload: {
        kind: "compact",
        old_covered_entry_sequence: 0,
        new_covered_entry_sequence: 4,
        usage: {
          input_tokens: 10,
          output_tokens: 2,
          reasoning_tokens: 0,
          cache_read_tokens: 0,
          cache_write_tokens: 0,
          model_calls: 1,
          tool_calls: 0,
          provider_retries: 0,
          compressions: 1,
          web_requests: 0,
          active_execution_seconds: 0.2,
        },
      },
    });
    await eventually(() =>
      expect(view.lastFrame()).toContain("Context compressed"),
    );
    const terminal = store.getState().committed_transcript;
    expect(terminal).toHaveLength(2);
    expect(terminal?.[1]).toMatchObject({
      key,
      presentation: { kind: "progress", message: "Context compressed" },
    });
    expect(view.lastFrame()).not.toContain("Compressing context...");
  });

  it("replaces pending progress with the specific command error", async () => {
    const controller = {
      submit: vi.fn(async () => ({
        kind: "command_error",
        code: "nothing_to_compact",
        message: "Context is already compact.",
      })),
    } as unknown as CommandController;
    const store = createSurfaceStore();
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );
    view.stdin.write("/compact");
    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain(
        "Context compression failed · Context is already compact.",
      ),
    );
    expect(store.getState().committed_transcript).toHaveLength(2);
    expect(view.lastFrame()).not.toContain("Compressing context...");
  });
});
