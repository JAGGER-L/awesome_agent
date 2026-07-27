import { describe, expect, it, vi } from "vitest";

import { CoreSpawnError } from "../../src/core/errors.js";
import {
  clearCurrentInkFrame,
  closeHeadlessSurface,
  executeFatalRecoverySelection,
  flushCurrentInkFrame,
  reconnectAndReplaceSurface,
  runCli,
  unmountInkApplication,
  type CliDependencies,
} from "../../src/cli/main.js";
import { RpcProtocolError } from "../../src/protocol/client.js";
import type { ConnectedSurface } from "../../src/surface/controller.js";
import type { StartupResult } from "../../src/surface/startup.js";
import { StartupProductError } from "../../src/surface/startup.js";

type ReadyApplication = Extract<
  StartupResult,
  { readonly kind: "ready" }
>["application"];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

const readyApplication: ReadyApplication = {
  initialized: true,
  session_id: "session_1",
  workspace_key: "workspace_1",
  workspace: { display_path: "E:\\workspace" },
  workspace_trusted: true,
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
    moonshot_api_key: true,
    mem0_api_key: false,
  },
  provider_credentials: {
    deepseek: {
      provider: "deepseek",
      environment_variable: "DEEPSEEK_API_KEY",
      environment_configured: false,
      awesome_configured: false,
      selected_source: null,
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

const ready: StartupResult = {
  kind: "ready",
  readiness: "agent_ready",
  application: readyApplication,
  thread: {
    kind: "ready",
    application: readyApplication,
    thread: {
      view: {
        thread: {
          id: "thread_1",
          workspace_key: "workspace_1",
          title: "New thread",
          title_source: "automatic",
          current_model: "deepseek/deepseek-v4-flash",
          thinking_enabled: false,
          skill_mode: "auto",
          created_at: "2026-07-11T00:00:00Z",
          updated_at: "2026-07-11T00:00:00Z",
        },
        entries: [],
        turns: [],
        tool_activities: [],
      },
      change_sets: [],
      has_more: false,
    },
  },
};

function harness(overrides: Partial<CliDependencies> = {}) {
  const stdout: string[] = [];
  const stderr: string[] = [];
  const surface = {
    close: vi.fn(async () => undefined),
    session: {
      exit: Promise.resolve({
        code: 0,
        signal: null,
        shutdown_requested: true,
      }),
    },
  } as unknown as ConnectedSurface;
  const dependencies: CliDependencies = {
    argv: [],
    cwd: () => "E:\\workspace",
    env: {},
    nodeVersion: "22.18.0",
    stdinIsTTY: true,
    stdoutIsTTY: true,
    stdoutColorDepth: 24,
    coreExecutable: "awesome-core",
    writeStdout: (value) => stdout.push(value),
    writeStderr: (value) => stderr.push(value),
    startSurface: vi.fn(async () => surface),
    startApplication: vi.fn(async () => ready),
    renderApplication: vi.fn(
      async () => ({ kind: "quit", exitCode: 0 }) as const,
    ),
    ...overrides,
  };
  return { dependencies, stdout, stderr, surface };
}

describe("runCli", () => {
  it("clears only the current Ink frame through the render host", () => {
    const clear = vi.fn();

    clearCurrentInkFrame({ clear });

    expect(clear).toHaveBeenCalledOnce();
  });

  it("awaits the current Ink frame flush", async () => {
    const flushed = deferred<void>();
    const waitUntilRenderFlush = vi.fn(async () => {
      await flushed.promise;
    });
    let complete = false;

    const pending = flushCurrentInkFrame({ waitUntilRenderFlush }).then(() => {
      complete = true;
    });

    expect(waitUntilRenderFlush).toHaveBeenCalledOnce();
    expect(complete).toBe(false);
    flushed.resolve();
    await pending;
    expect(complete).toBe(true);
  });

  it("unmounts Ink and waits for all teardown writes", async () => {
    const exited = deferred<void>();
    const unmount = vi.fn();
    const waitUntilExit = vi.fn(async () => {
      await exited.promise;
    });
    let complete = false;

    const pending = unmountInkApplication({ unmount, waitUntilExit }).then(
      () => {
        complete = true;
      },
    );

    expect(unmount).toHaveBeenCalledOnce();
    expect(waitUntilExit).toHaveBeenCalledOnce();
    expect(complete).toBe(false);
    exited.resolve();
    await pending;
    expect(complete).toBe(true);
  });

  it("treats a missing Ink instance as already cleaned up", async () => {
    await expect(unmountInkApplication(undefined)).resolves.toBeUndefined();
    await expect(flushCurrentInkFrame(undefined)).resolves.toBeUndefined();
  });
  it.each([
    ["--version"],
    ["-V"],
  ])("prints only the product version for %s without starting Core", async (flag) => {
    const value = harness({ argv: [flag] });
    await expect(runCli(value.dependencies)).resolves.toBe(0);
    expect(value.stdout.join("")).toBe("1.3.0\n");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
  });

  it.each([
    ["--help"],
    ["-h"],
  ])("prints help for %s without checking TTY, Node, or Core", async (flag) => {
    const value = harness({
      argv: [flag],
      nodeVersion: "invalid",
      stdinIsTTY: false,
      stdoutIsTTY: false,
    });
    await expect(runCli(value.dependencies)).resolves.toBe(0);
    expect(value.stdout.join("")).toContain("Usage: awesome");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
  });

  it("rejects version with extra arguments before starting Core", async () => {
    const value = harness({ argv: ["--version", "extra"] });
    await expect(runCli(value.dependencies)).resolves.toBe(2);
    expect(value.stderr.join("")).toContain("Usage: awesome");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
  });

  it.each([
    [[], { kind: "new" }],
    [["--continue"], { kind: "continue" }],
    [["--resume"], { kind: "resume-picker" }],
    [
      ["--resume", "thread_12345678"],
      { kind: "resume", threadId: "thread_12345678" },
    ],
  ] as const)("starts the accepted launch intent %j", async (argv, intent) => {
    const value = harness({ argv: [...argv] });
    await expect(runCli(value.dependencies)).resolves.toBe(0);
    expect(value.dependencies.startSurface).toHaveBeenCalledWith({
      executable: "awesome-core",
      cwd: "E:\\workspace",
      env: {},
    });
    expect(value.dependencies.startApplication).toHaveBeenCalledWith(
      value.surface,
      intent,
    );
  });

  it("renders state reset as a recoverable startup mode rather than fatal", async () => {
    const reset: StartupResult = {
      kind: "state_reset_required",
      interactionId: "interaction_state_reset",
    };
    const renderApplication = vi.fn(
      async () => ({ kind: "quit", exitCode: 0 }) as const,
    );
    const value = harness({
      startApplication: vi.fn(async () => reset),
      renderApplication,
    });

    await expect(runCli(value.dependencies)).resolves.toBe(0);
    expect(renderApplication).toHaveBeenCalledWith(
      expect.objectContaining({
        state: { kind: "startup", startup: reset },
      }),
    );
  });

  it("rejects invalid combinations before starting Core", async () => {
    const value = harness({ argv: ["--continue", "--resume"] });
    await expect(runCli(value.dependencies)).resolves.toBe(2);
    expect(value.stderr.join("")).toContain("Usage: awesome");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
  });

  it("rejects a non-interactive terminal before starting Core", async () => {
    const value = harness({ stdoutIsTTY: false });
    await expect(runCli(value.dependencies)).resolves.toBe(2);
    expect(value.stderr.join("")).toContain("interactive terminal");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
  });

  it("runs headless without TTY or Ink and closes the same Surface", async () => {
    const runHeadlessApplication = vi.fn(async (_surface, _intent, io) => {
      io.writeStdout("durable answer\n");
      return 0 as const;
    });
    const value = harness({
      argv: ["run", "do the work"],
      stdinIsTTY: false,
      stdoutIsTTY: false,
      runHeadlessApplication,
    });

    await expect(runCli(value.dependencies)).resolves.toBe(0);

    expect(runHeadlessApplication).toHaveBeenCalledWith(
      value.surface,
      expect.objectContaining({
        kind: "run",
        prompt: "do the work",
        target: { kind: "new" },
      }),
      expect.objectContaining({
        writeStdout: expect.any(Function),
        writeStderr: expect.any(Function),
      }),
    );
    expect(value.dependencies.startApplication).not.toHaveBeenCalled();
    expect(value.dependencies.renderApplication).not.toHaveBeenCalled();
    expect(value.surface.close).toHaveBeenCalledOnce();
    expect(value.stdout.join("")).toBe("durable answer\n");
  });

  it("keeps unresolved headless interaction paths off stdout", async () => {
    const value = harness({
      argv: ["run", "do the work"],
      stdoutIsTTY: false,
      runHeadlessApplication: vi.fn(async () => 3 as const),
    });

    await expect(runCli(value.dependencies)).resolves.toBe(3);

    expect(value.stdout).toEqual([]);
    expect(value.surface.close).toHaveBeenCalledOnce();
  });

  it("suppresses successful output when Core shutdown cannot be confirmed", async () => {
    const value = harness({
      argv: ["run", "do the work"],
      runHeadlessApplication: vi.fn(async (_surface, _intent, io) => {
        io.writeStdout("must not escape\n");
        return 0 as const;
      }),
      closeHeadlessApplication: vi.fn(async () => false),
    });

    await expect(runCli(value.dependencies)).resolves.toBe(1);

    expect(value.stdout).toEqual([]);
    expect(value.stderr.join("")).toContain("did not shut down cleanly");
  });

  it("closes Core after the headless SIGINT path requests cancellation", async () => {
    const order: string[] = [];
    const surface = {
      close: vi.fn(async () => {
        order.push("shutdown");
      }),
      session: {
        exit: Promise.resolve({
          code: 0,
          signal: null,
          shutdown_requested: true,
        }),
      },
    } as unknown as ConnectedSurface;
    const value = harness({
      argv: ["run", "do the work"],
      startSurface: vi.fn(async () => surface),
      runHeadlessApplication: vi.fn(async () => {
        order.push("cancel");
        return 130 as const;
      }),
    });

    await expect(runCli(value.dependencies)).resolves.toBe(130);

    expect(order).toEqual(["cancel", "shutdown"]);
    expect(value.stdout).toEqual([]);
  });

  it("forces termination when graceful headless close stalls", async () => {
    const exit = deferred<{
      code: number | null;
      signal: NodeJS.Signals | null;
      shutdown_requested: boolean;
    }>();
    const surface = {
      close: vi.fn(async () => await new Promise<never>(() => undefined)),
      session: {
        exit: exit.promise,
        terminate: vi.fn(async () => {
          exit.resolve({
            code: null,
            signal: "SIGTERM",
            shutdown_requested: true,
          });
        }),
      },
    } as unknown as ConnectedSurface;

    await expect(
      closeHeadlessSurface(surface, {
        gracefulTimeoutMs: 2,
        totalTimeoutMs: 50,
      }),
    ).resolves.toBe(true);
    expect(surface.session.terminate).toHaveBeenCalledOnce();
  });

  it("returns from headless close when termination and exit both stall", async () => {
    const never = new Promise<never>(() => undefined);
    const surface = {
      close: vi.fn(async () => await never),
      session: {
        exit: never,
        terminate: vi.fn(async () => await never),
      },
    } as unknown as ConnectedSurface;

    await expect(
      closeHeadlessSurface(surface, {
        gracefulTimeoutMs: 2,
        totalTimeoutMs: 10,
      }),
    ).resolves.toBe(false);
  });

  it("rejects Node versions older than 22", async () => {
    const value = harness({ nodeVersion: "20.19.0" });
    await expect(runCli(value.dependencies)).resolves.toBe(2);
    expect(value.stderr.join("")).toContain("Node.js 22 or newer");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
  });

  it("passes observed stdout color capability to the renderer", async () => {
    const renderApplication = vi.fn(
      async () => ({ kind: "quit", exitCode: 0 }) as const,
    );
    const value = harness({
      env: {},
      stdoutIsTTY: true,
      stdoutColorDepth: 24,
      renderApplication,
    });

    await expect(runCli(value.dependencies)).resolves.toBe(0);
    expect(renderApplication).toHaveBeenCalledWith(
      expect.objectContaining({
        stdoutIsTTY: true,
        stdoutColorDepth: 24,
      }),
    );
  });

  it("reports a missing Core safely with exit code 2", async () => {
    const value = harness({
      startSurface: vi.fn(async () => {
        throw new CoreSpawnError(
          "Unable to spawn Core executable: awesome-core",
        );
      }),
    });
    await expect(runCli(value.dependencies)).resolves.toBe(2);
    expect(value.stderr.join("")).toContain("Awesome Core");
    expect(value.stderr.join("")).not.toContain("awesome-core");
    expect(value.stderr.join("")).not.toContain("stack");
  });

  it.each([
    [{ kind: "quit", exitCode: 0 } as const, 0],
    [{ kind: "trust_denied", exitCode: 0 } as const, 0],
    [{ kind: "fatal", exitCode: 1 } as const, 1],
  ])("maps the renderer outcome %j", async (outcome, exitCode) => {
    const value = harness({ renderApplication: vi.fn(async () => outcome) });
    await expect(runCli(value.dependencies)).resolves.toBe(exitCode);
  });

  it("maps render failures to a bounded fatal error", async () => {
    const value = harness({
      renderApplication: vi.fn(async () => {
        throw new Error("private render details");
      }),
    });
    await expect(runCli(value.dependencies)).resolves.toBe(1);
    expect(value.stderr.join("")).toContain("terminal interface failed");
    expect(value.stderr.join("")).not.toContain("private render details");
  });

  it("renders a startup rejection through the fatal surface", async () => {
    const renderApplication = vi.fn(
      async () => ({ kind: "fatal", exitCode: 1 }) as const,
    );
    const value = harness({
      startApplication: vi.fn(async () => {
        throw new RpcProtocolError(-32603, "Internal error", {
          diagnostic_code: "core_request_failed",
        });
      }),
      renderApplication,
    });

    await expect(runCli(value.dependencies)).resolves.toBe(1);
    expect(renderApplication).toHaveBeenCalledOnce();
    expect(renderApplication).toHaveBeenCalledWith(
      expect.objectContaining({
        state: {
          kind: "fatal",
          fatal: {
            kind: "protocol",
            message: "Internal error",
            diagnosticCode: "core_request_failed",
          },
        },
      }),
    );
    expect(value.stderr.join("")).not.toContain(
      "The terminal interface failed unexpectedly.",
    );
  });

  it("renders newer state as a non-destructive version failure", async () => {
    const renderApplication = vi.fn(
      async () => ({ kind: "fatal", exitCode: 1 }) as const,
    );
    const value = harness({
      startApplication: vi.fn(async () => {
        throw new StartupProductError({
          code: "state_created_by_newer_version",
          message:
            "Local state was created by a newer Awesome version. Upgrade Awesome to continue.",
          retryable: false,
          data: {
            found_schema: 8,
            expected_schema: 7,
            state_directory: "E:\\awesome_agent\\.awesome-dev\\home\\state",
          },
        });
      }),
      renderApplication,
    });

    await expect(runCli(value.dependencies)).resolves.toBe(1);
    expect(renderApplication).toHaveBeenCalledWith(
      expect.objectContaining({
        state: {
          kind: "fatal",
          fatal: {
            kind: "version_incompatible",
            message:
              "Local state was created by a newer Awesome version. Upgrade Awesome to continue.",
          },
        },
      }),
    );
  });
});

describe("fatal recovery selection", () => {
  it("runs Reconnect for the first recovery option", async () => {
    const reconnect = vi.fn(async () => undefined);
    const quit = vi.fn(async () => undefined);

    await executeFatalRecoverySelection(0, { reconnect, quit });

    expect(reconnect).toHaveBeenCalledOnce();
    expect(quit).not.toHaveBeenCalled();
  });

  it("closes the stale Surface before replacing its subscriptions", async () => {
    const close = vi.fn(async () => undefined);
    const connected = {} as ConnectedSurface;
    const reconnect = vi.fn(async () => connected);
    const replace = vi.fn();

    await expect(
      reconnectAndReplaceSurface(
        { close },
        { reconnect },
        { cwd: "E:\\workspace", threadId: "thread_1" },
        replace,
      ),
    ).resolves.toBe(connected);

    expect(reconnect).toHaveBeenCalledWith({
      cwd: "E:\\workspace",
      threadId: "thread_1",
    });
    expect(close).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith(connected);
    expect(close.mock.invocationCallOrder[0]).toBeLessThan(
      replace.mock.invocationCallOrder[0] ?? Number.POSITIVE_INFINITY,
    );
  });

  it("closes the recovered Surface and preserves the current one when cleanup fails", async () => {
    const cleanupError = new Error("stale surface cleanup failed");
    const closeCurrent = vi.fn(async () => {
      throw cleanupError;
    });
    const closeRecovered = vi.fn(async () => undefined);
    const connected = { close: closeRecovered } as unknown as ConnectedSurface;
    const replace = vi.fn();

    await expect(
      reconnectAndReplaceSurface(
        { close: closeCurrent },
        { reconnect: async () => connected },
        { cwd: "E:\\workspace", threadId: "thread_1" },
        replace,
      ),
    ).rejects.toBe(cleanupError);

    expect(closeRecovered).toHaveBeenCalledOnce();
    expect(replace).not.toHaveBeenCalled();
  });

  it("runs Quit for the second recovery option", async () => {
    const reconnect = vi.fn(async () => undefined);
    const quit = vi.fn(async () => undefined);

    await executeFatalRecoverySelection(1, { reconnect, quit });

    expect(quit).toHaveBeenCalledOnce();
    expect(reconnect).not.toHaveBeenCalled();
  });
});
