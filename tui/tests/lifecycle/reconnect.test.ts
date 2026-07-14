import { describe, expect, it, vi } from "vitest";

import {
  ReconnectController,
  ReconnectError,
} from "../../src/lifecycle/reconnect.js";
import { DefaultLifecycleCoordinator } from "../../src/lifecycle/coordinator.js";
import type { ConnectedSurface } from "../../src/surface/controller.js";
import type { StartupResult } from "../../src/surface/startup.js";
import { createSurfaceStore } from "../../src/state/store.js";
import type { TranscriptBlock } from "../../src/transcript/model.js";

function ready(title = "Feature auth"): StartupResult {
  const now = "2026-07-11T00:00:00Z";
  const application: Extract<
    StartupResult,
    { readonly kind: "ready" }
  >["application"] = {
    initialized: true,
    session_id: "session_new",
    workspace_key: "workspace_1",
    workspace: { display_path: "/workspace" },
    workspace_trusted: true,
    current_thread_id: "thread_full",
    model_identity: {
      provider: "deepseek",
      configured_model: "deepseek/deepseek-v4-flash",
      effective_model: "deepseek/deepseek-v4-flash",
      runtime_name: "Awesome Agent",
      fallback_active: false,
    },
    thinking_enabled: false,
    skill_mode: "auto",
    permission_mode: "request_approval",
    configuration_valid: true,
    secret_status: {
      deepseek_api_key: true,
      moonshot_api_key: false,
      mem0_api_key: false,
    },
    provider_credentials: {
      deepseek: {
        provider: "deepseek",
        environment_variable: "DEEPSEEK_API_KEY",
        environment_configured: false,
        awesome_configured: true,
        selected_source: "awesome",
      },
      kimi: {
        provider: "kimi",
        environment_variable: "MOONSHOT_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: null,
      },
      mem0: {
        provider: "mem0",
        environment_variable: "MEM0_API_KEY",
        environment_configured: false,
        awesome_configured: false,
        selected_source: null,
      },
    },
    memory_status: {},
    mcp_status: [],
    usage: {},
    configuration_diagnostics: [],
  };
  return {
    kind: "ready",
    readiness: "agent_ready",
    application,
    thread: {
      kind: "ready",
      application,
      thread: {
        view: {
          thread: {
            id: "thread_full",
            workspace_key: "workspace_1",
            title,
            title_source: "automatic",
            current_model: "deepseek/deepseek-v4-flash",
            thinking_enabled: false,
            skill_mode: "auto",
            created_at: now,
            updated_at: now,
          },
          entries: [
            {
              id: "entry_1",
              thread_id: "thread_full",
              sequence: 1,
              kind: "assistant_message",
              content: "durable after crash",
              metadata: {},
              created_at: now,
            },
          ],
          turns: [],
          tool_activities: [],
        },
        change_sets: [],
        has_more: false,
      },
    },
  };
}

function surface(): ConnectedSurface {
  return {
    store: createSurfaceStore(),
    session: {} as ConnectedSurface["session"],
    request: async () => {
      throw new Error("startup seam owns requests");
    },
    close: vi.fn(async () => undefined),
  };
}

function harness(startup: () => Promise<StartupResult> = async () => ready()) {
  const surfaces: ConnectedSurface[] = [];
  const connect = vi.fn(async () => {
    const next = surface();
    surfaces.push(next);
    return next;
  });
  const oldBlocks: TranscriptBlock[] = [
    { key: "entry:old", kind: "assistant", text: "already committed" },
  ];
  const startupCall = vi.fn(startup);
  const controller = new ReconnectController({
    executable: "awesome-core",
    env: { SAFE: "1" },
    connect,
    startup: startupCall,
    committedBlocks: () => oldBlocks,
  });
  return { connect, controller, startupCall, surfaces };
}

describe("ReconnectController", () => {
  it("starts a new surface and resumes the same full Thread", async () => {
    const value = harness();
    const connected = await value.controller.reconnect({
      cwd: "/workspace",
      threadId: "thread_full",
    });
    expect(value.connect).toHaveBeenCalledWith({
      executable: "awesome-core",
      cwd: "/workspace",
      env: { SAFE: "1" },
    });
    expect(value.startupCall).toHaveBeenCalledWith(connected, {
      kind: "resume",
      threadId: "thread_full",
    });
    expect(connected.store.getState()).toMatchObject({
      connection: "idle",
      committed_transcript: [
        expect.objectContaining({ key: "entry:old" }),
        expect.objectContaining({
          kind: "status",
          message: "Reconnected · Feature auth",
        }),
        expect.objectContaining({ key: "entry:entry_1" }),
      ],
    });
  });

  it("shares a repeated reconnect click while connecting", () => {
    let resolve!: (result: StartupResult) => void;
    const startup = new Promise<StartupResult>((done) => {
      resolve = done;
    });
    const value = harness(async () => await startup);
    const context = { cwd: "/workspace", threadId: "thread_full" };
    const first = value.controller.reconnect(context);
    const second = value.controller.reconnect(context);
    expect(second).toBe(first);
    expect(value.connect).toHaveBeenCalledOnce();
    resolve(ready());
    return first;
  });

  it.each([
    new ReconnectError("Thread was not found", "thread_not_found"),
    new ReconnectError("Version mismatch", "version_incompatible"),
  ])("closes a failed new surface and permits another explicit attempt", async (error) => {
    let attempt = 0;
    const value = harness(async () => {
      attempt += 1;
      if (attempt === 1) throw error;
      return ready("Recovered");
    });
    const context = { cwd: "/workspace", threadId: "thread_full" };
    await expect(value.controller.reconnect(context)).rejects.toBe(error);
    expect(value.surfaces[0]?.close).toHaveBeenCalledOnce();
    await expect(value.controller.reconnect(context)).resolves.toBe(
      value.surfaces[1],
    );
    expect(value.connect).toHaveBeenCalledTimes(2);
  });

  it("can reset after a successful second crash without replaying Events", async () => {
    const value = harness();
    const context = { cwd: "/workspace", threadId: "thread_full" };
    const first = await value.controller.reconnect(context);
    value.controller.reset();
    const second = await value.controller.reconnect(context);
    expect(second).not.toBe(first);
    expect(value.startupCall).toHaveBeenCalledTimes(2);
  });
});

describe("DefaultLifecycleCoordinator", () => {
  it("composes cancel, exit, and the fixed reconnect context", async () => {
    const cancelActiveOperation = vi.fn(async () => undefined);
    const requestExit = vi.fn(async () => ({
      reason: "quit_command" as const,
      exitCode: 0 as const,
      forced: false,
    }));
    const connected = surface();
    const reconnect = vi.fn(async () => connected);
    const coordinator = new DefaultLifecycleCoordinator(
      { cancelActiveOperation },
      { requestExit },
      { reconnect },
      { cwd: "/workspace", threadId: "thread_full" },
    );
    await coordinator.cancelActiveOperation();
    await coordinator.requestExit("quit_command");
    await expect(coordinator.reconnect()).resolves.toBe(connected);
    expect(cancelActiveOperation).toHaveBeenCalledOnce();
    expect(requestExit).toHaveBeenCalledWith("quit_command");
    expect(reconnect).toHaveBeenCalledWith({
      cwd: "/workspace",
      threadId: "thread_full",
    });
  });
});
