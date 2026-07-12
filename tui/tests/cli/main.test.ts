import { describe, expect, it, vi } from "vitest";

import { CoreSpawnError } from "../../src/core/errors.js";
import { runCli, type CliDependencies } from "../../src/cli/main.js";
import type { ConnectedSurface } from "../../src/surface/controller.js";
import type { StartupResult } from "../../src/surface/startup.js";

const ready: StartupResult = {
  kind: "ready",
  readiness: "agent_ready",
  application: {
    initialized: true,
    session_id: "session_1",
    workspace_key: "workspace_1",
    workspace: { display_path: "E:\\workspace" },
    workspace_trusted: true,
    current_model: "deepseek/deepseek-v4-flash",
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
        source: "missing",
        mutable: true,
      },
      kimi: {
        provider: "kimi",
        environment_variable: "MOONSHOT_API_KEY",
        source: "missing",
        mutable: true,
      },
    },
    memory_status: {},
    mcp_status: [],
    usage: {},
    configuration_diagnostics: [],
  },
  thread: {
    kind: "ready",
    thread: {
      view: {
        thread: {
          id: "thread_1",
          workspace_key: "workspace_1",
          title: "New thread",
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
  } as unknown as ConnectedSurface;
  const dependencies: CliDependencies = {
    argv: [],
    cwd: () => "E:\\workspace",
    env: {},
    nodeVersion: "22.18.0",
    stdinIsTTY: true,
    stdoutIsTTY: true,
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
  it.each([
    ["--version"],
    ["-V"],
  ])("prints only the product version for %s without starting Core", async (flag) => {
    const value = harness({ argv: [flag] });
    await expect(runCli(value.dependencies)).resolves.toBe(0);
    expect(value.stdout.join("")).toBe("1.0.0\n");
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

  it("rejects Node versions older than 22", async () => {
    const value = harness({ nodeVersion: "20.19.0" });
    await expect(runCli(value.dependencies)).resolves.toBe(2);
    expect(value.stderr.join("")).toContain("Node.js 22 or newer");
    expect(value.dependencies.startSurface).not.toHaveBeenCalled();
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
});
