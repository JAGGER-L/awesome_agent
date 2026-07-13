import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { CommandController } from "../../src/commands/controller.js";
import { parseInput } from "../../src/commands/parser.js";
import type { LaunchIntent } from "../../src/cli/args.js";
import { runCli, type CliDependencies } from "../../src/cli/main.js";
import {
  connectSurface,
  type ConnectedSurface,
} from "../../src/surface/controller.js";
import {
  beginStartup,
  respondStartupTrust,
  type StartupResult,
} from "../../src/surface/startup.js";
import { createCoreWrapper } from "../fixtures/core-wrapper.js";

const temporary: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporary
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

describe("networkless candidate product flow", () => {
  it("configures a clean home through the credential RPC without leaking the key", async () => {
    const root = await mkdtemp(join(tmpdir(), "awesome-credential-"));
    temporary.push(root);
    const home = join(root, "home");
    const workspace = join(root, "workspace");
    const wrappers = join(root, "bin");
    const secret = "valid-test-key";
    await mkdir(workspace);
    await writeFile(join(workspace, "sample.txt"), "fixture source", "utf8");
    const wrapper = await createCoreWrapper({
      directory: wrappers,
      repository: resolve(".."),
    });
    const executable =
      process.platform === "win32"
        ? join(wrappers, "awesome-core.cmd")
        : "awesome-core";
    const surface = await connectSurface({
      executable,
      cwd: workspace,
      env: {
        ...wrapper.environment,
        AWESOME_HOME: home,
        AWESOME_WORKSPACE: workspace,
        AWESOME_FAKE_CREDENTIAL_FLOW: "1",
        PYTHONUNBUFFERED: "1",
      },
    });

    try {
      const ready = await trust(
        surface,
        { kind: "new" },
        await beginStartup(surface, { kind: "new" }),
      );
      expect(ready.readiness).toBe("diagnostics_ready");
      if (ready.thread.kind !== "ready") throw new Error("thread missing");
      const threadId = ready.thread.thread.view.thread.id;
      const commands = new CommandController(surface);
      const providers = await commands.submit(
        requiredInput("/model"),
        threadId,
      );
      expect(providers).toMatchObject({ kind: "selection" });
      if (providers.kind !== "selection")
        throw new Error("provider picker missing");
      const credential = await commands.select(
        providers.intent,
        "deepseek",
        threadId,
      );
      expect(credential).toMatchObject({ kind: "secret" });

      const saved = await commands.setCredential(
        "deepseek",
        "add",
        secret,
        false,
      );
      expect(saved).toMatchObject({
        kind: "credential",
        result: { status: "configured" },
      });
      const refreshed = await commands.refreshApplication();
      expect(refreshed).toMatchObject({
        ok: true,
        value: {
          provider_credentials: {
            deepseek: {
              awesome_configured: true,
              selected_source: "awesome",
            },
          },
        },
      });
      const models = await commands.submit(
        requiredInput("/model deepseek"),
        threadId,
      );
      expect(models).toMatchObject({ kind: "selection" });
      if (models.kind !== "selection") throw new Error("model picker missing");
      const selected = await commands.select(
        models.intent,
        "deepseek/deepseek-v4-flash",
        threadId,
      );
      expect(selected).toMatchObject({ kind: "result" });

      const turn = await commands.submit(
        requiredInput("inspect sample.txt"),
        threadId,
      );
      expect(turn.kind).toBe("accepted");
      if (turn.kind !== "accepted") throw new Error("turn not accepted");
      await terminal(surface, turn.operation.operation_id);

      const envFile = await readFile(join(home, ".env"), "utf8");
      expect(envFile).toContain(`DEEPSEEK_API_KEY='${secret}'`);
      const database = await readFile(join(home, "state", "application.db"));
      expect(database.includes(Buffer.from(secret))).toBe(false);
      expect(
        new TextDecoder().decode(surface.session.stderrTail()),
      ).not.toContain(secret);
      const thread = await surface.request("thread.read", {
        thread_id: threadId,
        limit: 50,
      });
      expect(JSON.stringify(thread)).not.toContain(secret);
    } finally {
      await closeSurface(surface);
    }
  }, 60_000);

  it.each([
    ["deepseek", "deepseek/deepseek-v4-flash"],
    ["kimi", "kimi/kimi-k2.6"],
  ] as const)(
    "runs the %s flow through CLI, controller, Python, and SQLite",
    async (provider, model) => {
      const root = await mkdtemp(join(tmpdir(), "awesome-product-"));
      temporary.push(root);
      const home = join(root, "home");
      const workspace = join(root, "workspace");
      const wrappers = join(root, "bin");
      await mkdir(workspace);
      await writeFile(join(workspace, "sample.txt"), "fixture source", "utf8");
      const wrapper = await createCoreWrapper({
        directory: wrappers,
        repository: resolve(".."),
      });
      const executable =
        process.platform === "win32"
          ? join(wrappers, "awesome-core.cmd")
          : "awesome-core";
      const environment = {
        ...wrapper.environment,
        AWESOME_HOME: home,
        AWESOME_WORKSPACE: workspace,
        AWESOME_FAKE_PROVIDER: provider,
        PYTHONUNBUFFERED: "1",
      };
      let threadId = "";
      let surfaceSeen: ConnectedSurface | undefined;
      let renderError: unknown;

      const dependencies: CliDependencies = {
        argv: [],
        cwd: () => workspace,
        env: environment,
        nodeVersion: "22.18.0",
        stdinIsTTY: true,
        stdoutIsTTY: true,
        stdoutColorDepth: 24,
        coreExecutable: executable,
        writeStdout: vi.fn(),
        writeStderr: vi.fn(),
        startSurface: async (options) =>
          await connectSurface({ ...options, executable }),
        startApplication: beginStartup,
        renderApplication: async ({ surface, intent, state }) => {
          try {
            surfaceSeen = surface;
            if (state.kind !== "startup") {
              throw new Error("unexpected startup failure");
            }
            const { startup } = state;
            const ready = await trust(surface, intent, startup);
            expect(ready.readiness).toBe("agent_ready");
            expect(ready.thread.kind).toBe("ready");
            if (ready.thread.kind !== "ready")
              throw new Error("thread missing");
            threadId = ready.thread.thread.view.thread.id;
            const commands = new CommandController(surface);

            const selected = await commands.submit(
              requiredInput(`/model ${provider} ${model}`),
              threadId,
            );
            expect(selected).toMatchObject({
              kind: "result",
              payload: { kind: "model", model },
            });

            const turn = await commands.submit(
              requiredInput("use tool to inspect @sample.txt"),
              threadId,
            );
            expect(turn.kind).toBe("accepted");
            if (turn.kind !== "accepted") throw new Error("turn not accepted");
            await terminal(surface, turn.operation.operation_id);

            const direct = await commands.submit(
              requiredInput("!echo direct-e2e"),
              threadId,
            );
            expect(direct.kind).toBe("accepted");
            if (direct.kind !== "accepted")
              throw new Error("direct not accepted");
            await terminal(surface, direct.operation.operation_id);

            const status = await commands.submit(
              requiredInput("/status"),
              threadId,
            );
            expect(status).toMatchObject({ kind: "result" });
            const read = await surface.request("thread.read", {
              thread_id: threadId,
              limit: 50,
            });
            expect(read.ok).toBe(true);
            if (!read.ok) throw new Error(read.error.message);
            expect(
              read.value.view.entries.some(
                (entry) => entry.kind === "direct_command",
              ),
            ).toBe(true);
            expect(
              read.value.view.tool_activities.some(
                (tool) => tool.tool_name === "read_file",
              ),
            ).toBe(true);

            const waiting = await commands.submit(
              requiredInput("wait forever"),
              threadId,
            );
            expect(waiting.kind).toBe("accepted");
            if (waiting.kind !== "accepted")
              throw new Error("wait not accepted");
            const cancelled = await surface.request("operation.cancel", {
              operation_id: waiting.operation.operation_id,
            });
            expect(cancelled).toMatchObject({
              ok: true,
              value: { cancelled: true },
            });
            await terminal(surface, waiting.operation.operation_id);

            await closeSurface(surface);
            return { kind: "quit", exitCode: 0 };
          } catch (error) {
            renderError = error;
            await closeSurface(surface).catch(() => undefined);
            throw error;
          }
        },
      };

      const exitCode = await runCli(dependencies);
      if (renderError) throw renderError;
      expect(exitCode).toBe(0);
      expect(threadId).toMatch(/^thread_[a-f0-9]+$/u);
      expect(surfaceSeen).toBeDefined();

      const restarted = await connectSurface({
        executable,
        cwd: workspace,
        env: environment,
      });
      try {
        const resumed = await beginStartup(restarted, {
          kind: "resume",
          threadId,
        });
        expect(resumed).toMatchObject({
          kind: "ready",
          thread: {
            kind: "ready",
            thread: { view: { thread: { id: threadId } } },
          },
        });
        expect(restarted.store.getState().event_sequence).toBe(0);
      } finally {
        await closeSurface(restarted);
      }
    },
    60_000,
  );
});

function requiredInput(value: string) {
  const routed = parseInput(value);
  if (!routed || routed.kind === "invalid") {
    throw new Error(`Unable to route ${value}`);
  }
  return routed;
}

async function trust(
  surface: ConnectedSurface,
  intent: LaunchIntent,
  startup: StartupResult,
): Promise<Extract<StartupResult, { readonly kind: "ready" }>> {
  const result =
    startup.kind === "trust_required"
      ? await respondStartupTrust(
          surface,
          intent,
          startup.interactionId,
          "trust",
        )
      : startup;
  if (result.kind !== "ready") throw new Error("workspace not ready");
  return result;
}

async function terminal(
  surface: ConnectedSurface,
  operationId: string,
): Promise<void> {
  if (isTerminal(surface, operationId)) return;
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => {
      unsubscribe();
      reject(new Error(`Operation ${operationId} did not finish`));
    }, 20_000);
    const unsubscribe = surface.store.subscribe(() => {
      if (!isTerminal(surface, operationId)) return;
      clearTimeout(timeout);
      unsubscribe();
      resolve();
    });
  });
}

function isTerminal(surface: ConnectedSurface, operationId: string): boolean {
  const operation = surface.store.getState().active_operation;
  return operation?.id === operationId && operation.status !== "active";
}

async function closeSurface(surface: ConnectedSurface): Promise<void> {
  await surface.close();
  await surface.session.exit;
}
