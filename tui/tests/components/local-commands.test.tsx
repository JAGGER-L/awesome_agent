import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { CommandController } from "../../src/commands/controller.js";
import { LocalCommandService } from "../../src/commands/local.js";
import type { MethodName, MethodParams } from "../../src/protocol/methods.js";
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

function localService() {
  return new LocalCommandService({
    clipboard: { writeText: async () => undefined },
    getThread: () => undefined,
    getTheme: () => "system",
    setTheme: () => {},
    saveTheme: async () => undefined,
  });
}

function controller() {
  return new CommandController({
    request: async <Method extends MethodName>(
      method: Method,
      _params: MethodParams[Method],
    ) => {
      if (method !== "command.execute") throw new Error("unexpected RPC");
      const command = (_params as { name?: string }).name;
      return {
        ok: true,
        value: {
          kind: "result",
          payload:
            command === "usage"
              ? {
                  kind: "usage",
                  usage: {
                    input_tokens: 0,
                    output_tokens: 0,
                    reasoning_tokens: 0,
                    cache_read_tokens: 0,
                    cache_write_tokens: 0,
                    model_calls: 0,
                    tool_calls: 0,
                    provider_retries: 0,
                    compressions: 0,
                    active_execution_seconds: 0,
                  },
                }
              : {
                  kind: "status",
                  snapshot: {
                    version: "0.1.0",
                    workspace_path: "E:\\projects\\awesome",
                    thread_title: "Feature auth",
                    thread_id: "thread_3f8a1c2d111122223333444455556666",
                    thread_display_id: "thread_3f8a1c2d",
                    model_identity: {
                      provider: "deepseek",
                      configured_model: "deepseek/deepseek-v4-flash",
                      effective_model: "deepseek/deepseek-v4-flash",
                      runtime_name: "Awesome Agent",
                      fallback_active: false,
                    },
                    model_status: "configured",
                    thinking_enabled: false,
                    skill_mode: "auto",
                    local_memory_enabled: false,
                    mem0_enabled: false,
                    mcp_ready: 0,
                    mcp_degraded: 0,
                    operation_status: "idle",
                    operation_id: null,
                    configuration_valid: true,
                    configuration_diagnostic_count: 0,
                    permission_mode: "request_approval",
                    context_used_tokens: 0,
                    context_budget_tokens: 262_144,
                  },
                },
        },
      } as never;
    },
  });
}

describe("App local command wiring", () => {
  it("renders help in transcript and immediately keeps Composer active", async () => {
    const view = render(
      <App
        store={createSurfaceStore()}
        controller={controller()}
        localCommands={localService()}
        reportFatal={() => undefined}
        width={60}
      />,
    );
    view.stdin.write("/help");
    view.stdin.write("\r");
    await eventually(() => expect(view.lastFrame()).toContain("Commands"));
    expect(view.lastFrame()).toContain("Message");
    expect(view.lastFrame()).toContain("/new");
  });

  it("renders visible usage feedback when no usage exists", async () => {
    const view = render(
      <App
        store={createSurfaceStore()}
        controller={controller()}
        localCommands={localService()}
        reportFatal={() => undefined}
        width={60}
      />,
    );
    view.stdin.write("/usage");
    view.stdin.write("\r");
    await eventually(() => expect(view.lastFrame()).toContain("Input tokens"));
    expect(view.lastFrame()).toContain("Message");
  });

  it("renders typed Python status data", async () => {
    const view = render(
      <App
        store={createSurfaceStore()}
        controller={controller()}
        localCommands={localService()}
        reportFatal={() => undefined}
        width={60}
      />,
    );
    view.stdin.write("/status");
    view.stdin.write("\r");
    await eventually(() => expect(view.lastFrame()).toContain("Thread"));
    expect(view.lastFrame()).toContain("thread_3f8a1c2d");
  });

  it("emits quit as a lifecycle intent", async () => {
    const requestExit = vi.fn(async () => ({
      reason: "quit_command" as const,
      exitCode: 0 as const,
      forced: false,
    }));
    const view = render(
      <App
        store={createSurfaceStore()}
        controller={controller()}
        localCommands={localService()}
        reportFatal={() => undefined}
        lifecycle={{
          cancelActiveOperation: async () => undefined,
          requestExit,
        }}
        width={60}
      />,
    );
    view.stdin.write("/quit");
    view.stdin.write("\r");
    await eventually(() =>
      expect(requestExit).toHaveBeenCalledWith("quit_command"),
    );
  });
});
