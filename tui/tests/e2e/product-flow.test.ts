import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { render } from "ink-testing-library";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { CommandController } from "../../src/commands/controller.js";
import { parseInput } from "../../src/commands/parser.js";
import type { LaunchIntent } from "../../src/cli/args.js";
import { runCli, type CliDependencies } from "../../src/cli/main.js";
import { startCoreProcess } from "../../src/core/process.js";
import { resolveTheme } from "../../src/preferences/theme.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";
import {
  connectSurface,
  type ConnectedSurface,
} from "../../src/surface/controller.js";
import { freshModelCatalog } from "../fixtures/model-catalog.js";
import {
  beginStartup,
  respondStartupStateReset,
  respondStartupTrust,
  type StartupResult,
} from "../../src/surface/startup.js";
import { createCoreWrapper } from "../fixtures/core-wrapper.js";
import { createCanonicalTemporaryRoot } from "../fixtures/temporary-root.js";

const temporary: string[] = [];
const fakeCore = fileURLToPath(
  new URL("../fixtures/fake-core.mjs", import.meta.url),
);

afterEach(async () => {
  await Promise.all(
    temporary
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  );
});

describe("networkless candidate product flow", () => {
  it("resets startup state and reaches Trust on the same Core process", async () => {
    const workspace = await mkdtemp(join(tmpdir(), "awesome-state-reset-"));
    temporary.push(workspace);
    const surface = await connectSurface({
      executable: process.execPath,
      cwd: workspace,
      env: { AWESOME_FAKE_CORE_MODE: "state-reset-required" },
      startSession: async (launch) =>
        await startCoreProcess(launch, [fakeCore]),
    });

    try {
      const reset = await beginStartup(surface, { kind: "new" });
      expect(reset).toEqual({
        kind: "state_reset_required",
        interactionId: "interaction_state_reset",
      });
      if (reset.kind !== "state_reset_required") {
        throw new Error("state reset was not requested");
      }

      const trustRequired = await respondStartupStateReset(
        surface,
        { kind: "new" },
        reset.interactionId,
        "reset_state",
      );
      expect(trustRequired).toMatchObject({ kind: "trust_required" });
      if (trustRequired.kind !== "trust_required") {
        throw new Error("workspace trust was not requested");
      }

      const ready = await respondStartupTrust(
        surface,
        { kind: "new" },
        trustRequired.interactionId,
        "trust",
      );
      expect(ready).toMatchObject({ kind: "ready" });
    } finally {
      await closeSurface(surface);
    }
  });

  it("replaces and resumes complete terminal conversations without duplicate Turn output", async () => {
    const oldThread = threadPage("thread_old", [
      entry("entry_old_user", "thread_old", 1, "user_message", "old question"),
      entry(
        "entry_old_assistant",
        "thread_old",
        2,
        "assistant_message",
        "old answer",
      ),
    ]);
    const newThread = threadPage("thread_new", []);
    const controller = {
      submit: vi.fn(async (routed: ReturnType<typeof parseInput>) => {
        if (routed?.kind !== "command") {
          throw new Error("expected command input");
        }
        const resumed = routed.intent.name === "resume";
        return {
          kind: "result",
          payload: {
            kind: "thread_transition",
            transition: {
              reason: resumed ? "resume" : "new",
              application: applicationState(
                resumed ? "thread_old" : "thread_new",
              ),
              thread: resumed ? oldThread : newThread,
            },
          },
        };
      }),
    } as unknown as CommandController;
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: applicationState("thread_old"),
      thread: oldThread,
      committed_transcript: [
        {
          key: "user:client_old",
          kind: "user",
          client_message_id: "client_old",
          status: "persisted",
          text: "old question",
        },
      ],
      active_operation: {
        id: "operation_old",
        status: "completed",
        turn: {
          id: "turn_old",
          status: "completed",
          started_at: "2026-07-13T00:00:00Z",
          thinking_sequence: 0,
          duration_ms: 1_200,
          timeline: [
            {
              kind: "assistant",
              id: "assistant:turn_old:1",
              text: "old answer",
            },
          ],
        },
      },
    });
    const resetCurrentFrame = vi.fn();
    const view = render(
      createElement(App, {
        store,
        controller,
        reportFatal: (error: unknown) => {
          throw error;
        },
        resetCurrentFrame,
        width: 80,
        welcome: {
          version: "1.3.0",
          workspacePath: "E:/awesome",
          thread: { kind: "new" },
          model: "deepseek/deepseek-v4-flash",
          thinkingEnabled: false,
          localMemoryEnabled: false,
          mem0Enabled: false,
          permissionMode: "request_approval",
          theme: resolveTheme("dark", "truecolor"),
        },
      }),
    );

    expect(view.lastFrame()?.match(/old answer/gu)).toHaveLength(1);
    expect(view.lastFrame()?.match(/Worked for/gu)).toHaveLength(1);
    store.dispatch({
      type: "transcript.reconciled",
      generation: 0,
      operation_id: "operation_old",
      turn_id: "turn_old",
      blocks: [
        {
          key: "user:client_old",
          kind: "user",
          client_message_id: "client_old",
          status: "persisted",
          text: "old question",
        },
        {
          key: "entry:entry_old_assistant",
          kind: "assistant",
          text: "old answer",
        },
        { key: "worked:turn_old", kind: "worked", duration_ms: 1_200 },
      ],
    });
    await eventually(() => {
      expect(view.lastFrame()?.match(/old answer/gu)).toHaveLength(1);
      expect(view.lastFrame()?.match(/Worked for/gu)).toHaveLength(1);
    });

    view.stdin.write("/new");
    view.stdin.write("\r");
    await eventually(() => expect(resetCurrentFrame).toHaveBeenCalledTimes(1));
    expect(view.lastFrame()).toContain("New conversation started");
    expect(view.lastFrame()).not.toContain("old question");
    expect(view.lastFrame()).not.toContain("old answer");

    store.dispatch({
      type: "transcript.reconciled",
      generation: 1,
      operation_id: "operation_new",
      turn_id: "turn_new",
      blocks: [
        {
          key: "user:client_new",
          kind: "user",
          client_message_id: "client_new",
          status: "persisted",
          text: "new question",
        },
        {
          key: "entry:entry_new_assistant",
          kind: "assistant",
          text: "new answer",
        },
      ],
    });
    await eventually(() => expect(view.lastFrame()).toContain("new answer"));

    view.stdin.write("/resume thread_old");
    view.stdin.write("\r");
    await eventually(() => expect(resetCurrentFrame).toHaveBeenCalledTimes(2));
    expect(view.lastFrame()).toContain("old question");
    expect(view.lastFrame()).toContain("old answer");
    expect(view.lastFrame()).not.toContain("new question");
    expect(view.lastFrame()).not.toContain("new answer");
  });

  it("installs a retry fork before consuming Events that raced its response", async () => {
    const workspace = await mkdtemp(join(tmpdir(), "awesome-retry-race-"));
    temporary.push(workspace);
    const surface = await connectSurface({
      executable: process.execPath,
      cwd: workspace,
      env: {
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: "before",
      },
      startSession: async (launch) =>
        await startCoreProcess(launch, [fakeCore]),
    });
    const reportFatal = vi.fn();
    const resetCurrentFrame = vi.fn();
    const application = await surface.request("application.getState", {});
    if (!application.ok || !application.value.current_thread_id) {
      throw new Error("Fake Core did not publish a current Thread");
    }
    surface.store.dispatch({
      type: "hydrate.application",
      application: application.value,
    });
    const thread = await surface.request("thread.read", {
      thread_id: application.value.current_thread_id,
    });
    if (!thread.ok) throw new Error("Fake Core did not publish a Thread page");
    surface.store.dispatch({ type: "hydrate.thread", thread: thread.value });
    const view = render(
      createElement(App, {
        store: surface.store,
        controller: new CommandController(surface),
        reportFatal,
        resetCurrentFrame,
        width: 80,
      }),
    );

    try {
      view.stdin.write("/retry");
      view.stdin.write("\r");
      await eventually(() => {
        expect(surface.store.getState()).toMatchObject({
          thread_generation: 1,
          application: { current_thread_id: "thread_retry" },
          active_operation: {
            id: "operation_retry",
            status: "active",
            turn: { id: "turn_retry", status: "active" },
          },
        });
      });
      expect(resetCurrentFrame).toHaveBeenCalledOnce();
      expect(view.lastFrame()).toContain("retry question");
      expect(reportFatal).not.toHaveBeenCalled();
    } finally {
      view.unmount();
      await closeSurface(surface);
    }
  });

  it("configures a clean home through the credential RPC without leaking the key", async () => {
    const root = await createCanonicalTemporaryRoot("awesome-credential-");
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
      const root = await createCanonicalTemporaryRoot("awesome-product-");
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

async function eventually(assertion: () => void): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      lastError = error;
      await new Promise<void>((resolvePromise) =>
        setTimeout(resolvePromise, 5),
      );
    }
  }
  throw lastError;
}

function applicationState(threadId: string) {
  const credential = (
    provider: "deepseek" | "kimi" | "mem0" | "tavily",
    variable: string,
  ) => ({
    provider,
    environment_variable: variable,
    environment_configured: false,
    awesome_configured: true,
    selected_source: "awesome" as const,
  });
  return {
    initialized: true,
    session_id: "session_test",
    workspace_key: "workspace_test",
    workspace: { display_path: "E:/awesome" },
    workspace_trusted: true,
    current_thread_id: threadId,
    model_catalog: freshModelCatalog(),
    model_identity: {
      provider: "deepseek",
      configured_model: "deepseek/deepseek-v4-flash",
      effective_model: "deepseek/deepseek-v4-flash",
      runtime_name: "Awesome Agent",
      fallback_active: false,
    },
    thinking_enabled: false,
    skill_mode: "auto",
    permission_mode: "request_approval" as const,
    configuration_valid: true,
    secret_status: {
      deepseek_api_key: true,
      moonshot_api_key: false,
      mem0_api_key: false,
    },
    provider_credentials: {
      deepseek: credential("deepseek", "DEEPSEEK_API_KEY"),
      kimi: credential("kimi", "MOONSHOT_API_KEY"),
      mem0: credential("mem0", "MEM0_API_KEY"),
      tavily: credential("tavily", "TAVILY_API_KEY"),
    },
    memory_status: {},
    mcp_status: [],
    usage: {},
    configuration_diagnostics: [],
  } as never;
}

function threadPage(threadId: string, entries: readonly unknown[]) {
  return {
    has_more: false,
    view: {
      thread: {
        id: threadId,
        workspace_key: "workspace_test",
        title:
          threadId === "thread_old" ? "Old conversation" : "New conversation",
        thinking_enabled: false,
        skill_mode: "auto",
        created_at: "2026-07-13T00:00:00Z",
        updated_at: "2026-07-13T00:00:00Z",
      },
      entries,
      turns: [],
      tool_activities: [],
    },
    change_sets: [],
  } as never;
}

function entry(
  id: string,
  threadId: string,
  sequence: number,
  kind: "user_message" | "assistant_message",
  content: string,
) {
  return {
    id,
    thread_id: threadId,
    sequence,
    kind,
    content,
    ...(kind === "user_message" ? { client_message_id: "client_old" } : {}),
    metadata: kind === "assistant_message" ? { citations: [] } : {},
    created_at: "2026-07-13T00:00:00Z",
  };
}

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
