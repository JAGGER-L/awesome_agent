import { describe, expect, it } from "vitest";

import { parseLaunchIntent } from "../../src/cli/args.js";
import {
  beginStartup,
  runStartup,
  SAFE_DIAGNOSTIC_COMMANDS,
  selectStartupThread,
  respondStartupTrust,
  StartupError,
} from "../../src/surface/startup.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../../src/protocol/methods.js";

type Call = { method: MethodName; params: MethodParams[MethodName] };

const thread = (id: string): MethodValue["thread.list"]["threads"][number] => ({
  id,
  workspace_key: "workspace_1",
  title: `Title ${id}`,
  current_model: "deepseek/deepseek-chat",
  thinking_enabled: false,
  skill_mode: "auto",
  created_at: "2026-07-11T00:00:00Z",
  updated_at: "2026-07-11T01:00:00Z",
});

const threadPage = (id: string): MethodValue["thread.read"] => ({
  view: {
    thread: thread(id),
    entries: [],
    turns: [],
    tool_activities: [],
  },
  change_sets: [],
  has_more: false,
});

function harness({
  recent = [thread("thread_recent")],
  resumeSelection,
  commandFailure,
}: {
  recent?: MethodValue["thread.list"]["threads"];
  resumeSelection?: MethodValue["command.execute"]["selection"];
  commandFailure?: boolean;
} = {}) {
  const calls: Call[] = [];
  return {
    calls,
    surface: {
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        calls.push({ method, params } as Call);
        if (method === "thread.list") {
          return {
            ok: true,
            value: { threads: recent, has_more: false },
          } as never;
        }
        if (method === "thread.read") {
          return {
            ok: true,
            value: threadPage((params as { thread_id: string }).thread_id),
          } as never;
        }
        if (method === "command.execute") {
          if (commandFailure) {
            return {
              ok: false,
              error: {
                code: "thread_not_found",
                message: "missing",
                retryable: false,
                data: {},
              },
            } as never;
          }
          const intent = params as MethodParams["command.execute"];
          if (intent.name === "resume" && resumeSelection) {
            return {
              ok: true,
              value: {
                status: "success",
                content: "",
                data: {},
                selection: resumeSelection,
              },
            } as never;
          }
          const id =
            intent.name === "new"
              ? "thread_new"
              : (intent.arguments?.[0] ?? "thread_recent");
          return {
            ok: true,
            value: {
              status: "success",
              content: "",
              data: { thread_id: id, title: `Title ${id}` },
            },
          } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    },
  };
}

describe("parseLaunchIntent", () => {
  it.each([
    [[], { kind: "new" }],
    [["--continue"], { kind: "continue" }],
    [["--resume"], { kind: "resume-picker" }],
    [
      ["--resume", "thread_abcd1234"],
      { kind: "resume", threadId: "thread_abcd1234" },
    ],
  ] as const)("parses %j", (argv, expected) => {
    expect(parseLaunchIntent([...argv])).toEqual(expected);
  });

  it.each([
    ["--continue", "--resume"],
    ["--resume", "a", "b"],
    ["--unknown"],
  ])("rejects invalid launch arguments %j", (...argv) => {
    expect(() => parseLaunchIntent(argv)).toThrow();
  });
});

describe("runStartup", () => {
  it("creates exactly one new thread and hydrates it", async () => {
    const { calls, surface } = harness();
    await expect(runStartup(surface, { kind: "new" })).resolves.toMatchObject({
      kind: "ready",
      thread: { view: { thread: { id: "thread_new" } } },
    });
    expect(calls).toEqual([
      { method: "command.execute", params: { name: "new" } },
      { method: "thread.read", params: { thread_id: "thread_new", limit: 50 } },
    ]);
  });

  it("continues the first recent thread through Python ownership", async () => {
    const { calls, surface } = harness();
    await runStartup(surface, { kind: "continue" });
    expect(calls.map(({ method }) => method)).toEqual([
      "thread.list",
      "command.execute",
      "thread.read",
    ]);
    expect(calls[1]).toEqual({
      method: "command.execute",
      params: { name: "resume", arguments: ["thread_recent"] },
    });
  });

  it("creates one thread when continue has an empty workspace", async () => {
    const { calls, surface } = harness({ recent: [] });
    await runStartup(surface, { kind: "continue" });
    expect(calls.filter(({ method }) => method === "command.execute")).toEqual([
      { method: "command.execute", params: { name: "new" } },
    ]);
  });

  it.each([
    "thread_full_identifier",
    "thread_abcd1234",
  ])("resumes exact or prefix identity %s", async (threadId) => {
    const { calls, surface } = harness();
    await runStartup(surface, { kind: "resume", threadId });
    expect(calls[0]).toEqual({
      method: "command.execute",
      params: { name: "resume", arguments: [threadId] },
    });
  });

  it("returns an ambiguous resume selection without hydrating arbitrarily", async () => {
    const selection = {
      prompt: "Select",
      options: [
        { value: "thread_a", label: "A", selected: false },
        { value: "thread_b", label: "B", selected: false },
      ],
    };
    const { calls, surface } = harness({ resumeSelection: selection });
    await expect(
      runStartup(surface, { kind: "resume", threadId: "thread_abcd1234" }),
    ).resolves.toEqual({ kind: "selection_required", selection });
    expect(calls.some(({ method }) => method === "thread.read")).toBe(false);
  });

  it("opens the recent picker and creates one thread when it is empty", async () => {
    const selection = {
      prompt: "Select",
      options: [{ value: "thread_a", label: "A", selected: false }],
    };
    const picker = harness({ resumeSelection: selection });
    await expect(
      runStartup(picker.surface, { kind: "resume-picker" }),
    ).resolves.toEqual({ kind: "selection_required", selection });

    const empty = harness({ recent: [] });
    empty.surface.request = async (method, params) => {
      empty.calls.push({ method, params } as Call);
      if (
        method === "command.execute" &&
        (params as { name: string }).name === "resume"
      ) {
        return {
          ok: true,
          value: { status: "success", content: "", data: { threads: [] } },
        } as never;
      }
      if (method === "command.execute") {
        return {
          ok: true,
          value: {
            status: "success",
            content: "",
            data: { thread_id: "thread_new" },
          },
        } as never;
      }
      return { ok: true, value: threadPage("thread_new") } as never;
    };
    await runStartup(empty.surface, { kind: "resume-picker" });
    expect(
      empty.calls.filter(({ method }) => method === "command.execute"),
    ).toHaveLength(2);
  });

  it("hydrates a selected option through a fresh resume command", async () => {
    const { calls, surface } = harness();
    await selectStartupThread(surface, "thread_selected");
    expect(calls).toEqual([
      {
        method: "command.execute",
        params: { name: "resume", arguments: ["thread_selected"] },
      },
      {
        method: "thread.read",
        params: { thread_id: "thread_selected", limit: 50 },
      },
    ]);
  });

  it("propagates typed thread product failures", async () => {
    const { surface } = harness({ commandFailure: true });
    await expect(
      runStartup(surface, { kind: "resume", threadId: "thread_missing" }),
    ).rejects.toBeInstanceOf(StartupError);
  });
});

function modelIdentity(model: string) {
  return {
    provider: model.startsWith("kimi/")
      ? ("kimi" as const)
      : ("deepseek" as const),
    configured_model: model,
    effective_model: model,
    runtime_name: "Awesome Agent" as const,
    fallback_active: false,
  };
}

function applicationState({
  model = "deepseek/deepseek-v4-flash",
  deepseek = true,
  kimi = true,
  valid = true,
}: {
  model?: string | null;
  deepseek?: boolean;
  kimi?: boolean;
  valid?: boolean;
} = {}): MethodValue["application.getState"] {
  return {
    initialized: true,
    session_id: "session_1",
    workspace_key: "workspace_1",
    workspace: { display_path: "E:\\projects\\awesome", branch: "main" },
    workspace_trusted: true,
    ...(model === null ? {} : { model_identity: modelIdentity(model) }),
    thinking_enabled: false,
    skill_mode: "auto",
    permission_mode: "request_approval",
    configuration_valid: valid,
    secret_status: {
      deepseek_api_key: deepseek,
      moonshot_api_key: kimi,
      mem0_api_key: false,
    },
    provider_credentials: {
      deepseek: {
        provider: "deepseek",
        environment_variable: "DEEPSEEK_API_KEY",
        environment_configured: deepseek,
        awesome_configured: false,
        selected_source: deepseek ? "environment" : null,
      },
      kimi: {
        provider: "kimi",
        environment_variable: "MOONSHOT_API_KEY",
        environment_configured: kimi,
        awesome_configured: false,
        selected_source: kimi ? "environment" : null,
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
    configuration_diagnostics: valid ? [] : ["invalid config"],
  };
}

function startupHarness({
  trustRequired = false,
  application = applicationState(),
}: {
  trustRequired?: boolean;
  application?: MethodValue["application.getState"];
} = {}) {
  const calls: Call[] = [];
  let trusted = !trustRequired;
  let selectedThreadId: string | undefined;
  return {
    calls,
    surface: {
      request: async <Method extends MethodName>(
        method: Method,
        params: MethodParams[Method],
      ) => {
        calls.push({ method, params } as Call);
        if (method === "initialize") {
          return {
            ok: true,
            value: {
              product_version: "0.1.0",
              protocol_version: 1,
              status: trusted ? "ready" : "trust_required",
              session_id: "session_1",
              ...(trusted ? {} : { interaction_id: "interaction_response" }),
              workspace: { display_path: "E:\\projects\\awesome" },
              capabilities: [],
            },
          } as never;
        }
        if (method === "interaction.respond") {
          const decision = (params as MethodParams["interaction.respond"])
            .decision;
          trusted = decision === "trust";
          return {
            ok: true,
            value: { accepted: true, status: decision },
          } as never;
        }
        if (method === "application.getState") {
          return {
            ok: true,
            value: selectedThreadId
              ? {
                  ...application,
                  current_thread_id: selectedThreadId,
                  model_identity:
                    application.model_identity ??
                    modelIdentity("deepseek/deepseek-v4-flash"),
                }
              : application,
          } as never;
        }
        if (method === "command.execute") {
          selectedThreadId = "thread_new";
          return {
            ok: true,
            value: {
              status: "success",
              content: "",
              data: { thread_id: "thread_new" },
            },
          } as never;
        }
        if (method === "thread.read") {
          return { ok: true, value: threadPage("thread_new") } as never;
        }
        if (method === "shutdown") {
          return { ok: true, value: { stopped: true } } as never;
        }
        throw new Error(`Unexpected method ${method}`);
      },
    },
  };
}

describe("trusted startup state machine", () => {
  it("stops before project state when initialize requires trust", async () => {
    const { calls, surface } = startupHarness({ trustRequired: true });
    await expect(beginStartup(surface, { kind: "new" })).resolves.toEqual({
      kind: "trust_required",
      interactionId: "interaction_response",
      workspacePath: "E:\\projects\\awesome",
    });
    expect(calls.map(({ method }) => method)).toEqual(["initialize"]);
  });

  it("trusts the response interaction then reinitializes before project state", async () => {
    const { calls, surface } = startupHarness({ trustRequired: true });
    const pending = await beginStartup(surface, { kind: "new" });
    if (pending.kind !== "trust_required") throw new Error("expected trust");
    await expect(
      respondStartupTrust(
        surface,
        { kind: "new" },
        pending.interactionId,
        "trust",
      ),
    ).resolves.toMatchObject({ kind: "ready", readiness: "agent_ready" });
    expect(calls.map(({ method }) => method)).toEqual([
      "initialize",
      "interaction.respond",
      "initialize",
      "application.getState",
      "command.execute",
      "thread.read",
      "application.getState",
    ]);
  });

  it("denies normally without reading project state", async () => {
    const { calls, surface } = startupHarness({ trustRequired: true });
    await expect(
      respondStartupTrust(
        surface,
        { kind: "new" },
        "interaction_response",
        "deny",
      ),
    ).resolves.toEqual({ kind: "denied" });
    expect(calls.map(({ method }) => method)).toEqual([
      "interaction.respond",
      "shutdown",
    ]);
  });

  it("starts an already trusted workspace directly", async () => {
    const { calls, surface } = startupHarness();
    await expect(beginStartup(surface, { kind: "new" })).resolves.toMatchObject(
      {
        kind: "ready",
        readiness: "agent_ready",
      },
    );
    expect(calls[1]?.method).toBe("application.getState");
  });

  it("hydrates application and the single selected thread into the surface store", async () => {
    const { surface } = startupHarness();
    const actions: { type: string }[] = [];
    Object.assign(surface, {
      store: { dispatch: (action: { type: string }) => actions.push(action) },
    });
    await beginStartup(surface, { kind: "new" });
    expect(actions.map(({ type }) => type)).toEqual([
      "connection.handshaking",
      "handshake.ready",
      "hydrate.application",
      "hydrate.thread",
      "hydrate.application",
    ]);
  });

  it("binds the selected Thread and model into the startup application state", async () => {
    const { surface } = startupHarness({
      application: applicationState({ model: null }),
    });
    const result = await beginStartup(surface, { kind: "new" });
    expect(result).toMatchObject({
      kind: "ready",
      application: {
        current_thread_id: "thread_new",
        model_identity: modelIdentity("deepseek/deepseek-v4-flash"),
      },
    });
  });

  it.each([
    ["deepseek/deepseek-v4-flash", false, true, "DEEPSEEK_API_KEY"],
    ["kimi/kimi-k2.6", true, false, "MOONSHOT_API_KEY"],
  ] as const)("keeps %s diagnostics-ready when its credential is missing", async (model, deepseek, kimi, environmentVariable) => {
    const { surface } = startupHarness({
      application: applicationState({ model, deepseek, kimi }),
    });
    await expect(beginStartup(surface, { kind: "new" })).resolves.toMatchObject(
      {
        kind: "ready",
        readiness: "diagnostics_ready",
        diagnostic: { model, environmentVariable },
        safeCommands: SAFE_DIAGNOSTIC_COMMANDS,
      },
    );
  });

  it("keeps invalid configuration diagnostics-ready", async () => {
    const { surface } = startupHarness({
      application: applicationState({ valid: false }),
    });
    await expect(beginStartup(surface, { kind: "new" })).resolves.toMatchObject(
      {
        kind: "ready",
        readiness: "diagnostics_ready",
        diagnostic: { code: "configuration_invalid" },
      },
    );
  });
});
