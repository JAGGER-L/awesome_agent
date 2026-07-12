import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
    }
  }
  throw last;
}

function lifecycle() {
  return {
    cancelActiveOperation: vi.fn(async () => undefined),
    requestExit: vi.fn(async () => ({
      reason: "double_ctrl_c" as const,
      exitCode: 0 as const,
      forced: false,
    })),
  };
}

describe("App global keys", () => {
  it("clears non-empty idle Composer input with Ctrl+C", async () => {
    const view = render(
      <App store={createSurfaceStore()} lifecycle={lifecycle()} width={60} />,
    );
    view.stdin.write("draft");
    await eventually(() => expect(view.lastFrame()).toContain("draft"));
    view.stdin.write("\x03");
    await eventually(() => expect(view.lastFrame()).not.toContain("draft"));
    expect(view.lastFrame()).toContain("Message");
  });

  it("exits on the second empty Ctrl+C within the window", async () => {
    const actions = lifecycle();
    const view = render(
      <App store={createSurfaceStore()} lifecycle={actions} width={60} />,
    );
    view.stdin.write("\x03");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Press Ctrl+C again to quit"),
    );
    view.stdin.write("\x03");
    await eventually(() =>
      expect(actions.requestExit).toHaveBeenCalledWith("double_ctrl_c"),
    );
  });

  it("exits on Ctrl+D only with empty input", async () => {
    const actions = lifecycle();
    const view = render(
      <App store={createSurfaceStore()} lifecycle={actions} width={60} />,
    );
    view.stdin.write("\x04");
    await eventually(() =>
      expect(actions.requestExit).toHaveBeenCalledWith("ctrl_d"),
    );
  });
});
