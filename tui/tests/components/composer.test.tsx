import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import type { CommandController } from "../../src/commands/controller.js";
import {
  composerReducer,
  initialComposerState,
} from "../../src/composer/reducer.js";
import { CommandMenu } from "../../src/components/CommandMenu.js";
import { Composer } from "../../src/components/Composer.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  throw last;
}

function composerState(value: string, width: number) {
  return composerReducer(
    composerReducer(initialComposerState(), { type: "resize", width }),
    { type: "replace", value },
  );
}

function controllerReturning(outcome: unknown): CommandController {
  return {
    submit: vi.fn(async () => outcome),
  } as unknown as CommandController;
}

describe("Composer", () => {
  it.each([
    40, 60, 120,
  ])("renders controlled multiline input and a cursor at %i columns", (width) => {
    const view = render(
      <Composer state={composerState("first\nsecond", width)} />,
    );
    expect(view.lastFrame()).toContain("first");
    expect(view.lastFrame()).toContain("second");
    expect(view.lastFrame()).toContain("▌");
  });

  it("renders the cursor at the controlled grapheme position", () => {
    const state = composerReducer(composerState("a😀c", 40), { type: "left" });
    expect(render(<Composer state={state} />).lastFrame()).toContain("a😀▌c");
  });

  it("renders controlled submission and error states", () => {
    const frame =
      render(
        <Composer
          state={composerState("retry me", 40)}
          submitting
          message="busy"
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Sending…");
    expect(frame).toContain("retry me");
    expect(frame).toContain("busy");
  });
});

describe("CommandMenu", () => {
  it("opens for slash input, filters, and disappears for ordinary text", () => {
    const view = render(<CommandMenu query="/th" />);
    expect(view.lastFrame()).toContain("/thinking");
    expect(view.lastFrame()).toContain("/theme");
    view.rerender(<CommandMenu query="hello" />);
    expect(view.lastFrame()).toBe("");
  });
});

describe("App composer integration", () => {
  it("routes pasted input and Enter through the root terminal owner", async () => {
    const controller = controllerReturning({
      kind: "accepted",
      operation: { operation_id: "operation_1", thread_id: "thread_1" },
    });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={40} />,
    );
    view.stdin.write("hello\nworld");
    await eventually(() => expect(view.lastFrame()).toContain("hello"));
    view.stdin.write("\r");
    await eventually(() => expect(controller.submit).toHaveBeenCalledOnce());
    await eventually(() => expect(view.lastFrame()).not.toContain("hello"));
  });

  it("retains a retryable draft after an immediate product error", async () => {
    const controller = controllerReturning({
      kind: "error",
      error: { code: "operation_busy", message: "busy", retryable: true },
    });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={40} />,
    );
    view.stdin.write("retry me");
    view.stdin.write("\r");
    await eventually(() => expect(view.lastFrame()).toContain("busy"));
    expect(view.lastFrame()).toContain("retry me");
  });
});
