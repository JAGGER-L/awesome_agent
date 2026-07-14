import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { CommandController } from "../../src/commands/controller.js";
import { RpcProtocolError } from "../../src/protocol/client.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 100; attempt += 1) {
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

describe("request failure containment", () => {
  it("keeps a rejected Turn visible and accepts a later command", async () => {
    const request = vi.fn(
      async <Method extends MethodName>(
        method: Method,
        _params: MethodParams[Method],
      ) => {
        if (method === "turn.submit") {
          throw new RpcProtocolError(-32603, "Internal error", {
            diagnostic_code: "core_request_failed",
          });
        }
        if (method === "command.execute") {
          return {
            ok: true,
            value: {
              status: "success",
              content: "",
              data: {},
            },
          } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    );
    const reportFatal = vi.fn();
    const controller = new CommandController({ request });
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: "thread_1" } as never,
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={reportFatal}
        width={60}
      />,
    );

    view.stdin.write("hi");
    view.stdin.write("\r");

    await eventually(() =>
      expect(view.lastFrame()).toContain(
        "Awesome could not complete this request. You can retry.",
      ),
    );
    const failedFrame = view.lastFrame() ?? "";
    expect(failedFrame.match(/❯ hi/gu)).toHaveLength(2);
    expect(failedFrame).toContain("◇ Request approval");
    expect(failedFrame).not.toContain("Sending…");
    expect(reportFatal).not.toHaveBeenCalled();

    view.stdin.write("\u0003");
    view.stdin.write("/status");
    view.stdin.write("\r");

    await eventually(() =>
      expect(request).toHaveBeenCalledWith("command.execute", {
        name: "status",
      }),
    );
  });

  it("escalates an unexpected action failure exactly once", async () => {
    const failure = new Error("private unexpected failure");
    const controller = new CommandController({
      request: async () => {
        throw failure;
      },
    });
    const reportFatal = vi.fn();
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: "thread_1" } as never,
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={reportFatal}
        width={60}
      />,
    );

    view.stdin.write("hi");
    view.stdin.write("\r");

    await eventually(() => expect(reportFatal).toHaveBeenCalledOnce());
    expect(reportFatal).toHaveBeenCalledWith(failure);
    expect(view.lastFrame()).not.toContain("private unexpected failure");
  });
});
