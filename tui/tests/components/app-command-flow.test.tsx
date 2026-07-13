import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import type { CommandController } from "../../src/commands/controller.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
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

describe("submitted slash command history", () => {
  it("records the exact command before the Core request resolves", async () => {
    let resolveRequest: ((value: never) => void) | undefined;
    const pending = new Promise<never>((resolve) => {
      resolveRequest = resolve;
    });
    const controller = {
      submit: vi.fn(async () => await pending),
    } as unknown as CommandController;
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: "thread_1" } as never,
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/status");
    view.stdin.write("\r");

    await eventually(() => expect(controller.submit).toHaveBeenCalledOnce());
    expect(store.getState().committed_transcript).toEqual([
      expect.objectContaining({ kind: "command_input", text: "/status" }),
    ]);
    resolveRequest?.({
      kind: "result",
      payload: { kind: "notice", message: "ok" },
    } as never);
  });

  it("retains invalid command input before the visible error", async () => {
    const store = createSurfaceStore();
    const view = render(
      <App store={store} reportFatal={() => undefined} width={80} />,
    );

    view.stdin.write("/unknown argument");
    view.stdin.write("\r");

    await eventually(() =>
      expect(
        store.getState().committed_transcript?.map((block) => block.kind),
      ).toEqual(["command_input", "command_result"]),
    );
    expect(store.getState().committed_transcript?.[0]).toMatchObject({
      text: "/unknown argument",
    });
  });

  it("renders status through the normal command transcript path", async () => {
    const controller = {
      submit: vi.fn(async () => ({
        kind: "result",
        payload: {
          kind: "status",
          snapshot: {
            version: "1.1.1",
            workspace_path: "E:\\workspace",
            thread_display_id: "thread_12345678",
            model_identity: { effective_model: "deepseek/deepseek-v4-flash" },
            permission_mode: "request_approval",
            context_used_tokens: 1024,
            context_budget_tokens: 262144,
          },
        },
      })),
    } as unknown as CommandController;
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: "thread_1" } as never,
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/status");
    view.stdin.write("\r");

    await eventually(() =>
      expect(
        store.getState().committed_transcript?.map((block) => block.kind),
      ).toEqual(["command_input", "command_result"]),
    );
    expect(view.lastFrame()).toContain("deepseek/deepseek-v4-flash");
  });
});
