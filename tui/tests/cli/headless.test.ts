import { describe, expect, it, vi } from "vitest";

import type { HeadlessRunIntent } from "../../src/cli/args.js";
import { runHeadless } from "../../src/cli/headless.js";
import type { ConnectedSurface } from "../../src/surface/controller.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import type { SurfaceState } from "../../src/state/model.js";

describe("runHeadless", () => {
  it("prints only the durable assistant answer in text mode", async () => {
    const values = fixture();

    await expect(
      runHeadless(values.surface, runIntent(), values.io),
    ).resolves.toBe(0);

    expect(values.stdout.join("")).toBe("durable answer\n");
    expect(values.stderr).toEqual([]);
    expect(values.methods).toEqual([
      "initialize",
      "application.getState",
      "command.execute",
      "application.getState",
      "turn.submit",
      "thread.read",
    ]);
  });

  it("emits one versioned JSON document", async () => {
    const values = fixture();

    await expect(
      runHeadless(values.surface, runIntent({ format: "json" }), values.io),
    ).resolves.toBe(0);

    const output: unknown = JSON.parse(values.stdout.join(""));
    expect(output).toMatchObject({
      version: 1,
      type: "awesome.run.result",
      thread_id: "thread_1",
      turn_id: "turn_1",
      text: "durable answer\n\n",
      termination_reason: "stop",
    });
    expect(values.stdout).toHaveLength(1);
  });

  it("selects the exact requested Thread through the existing command path", async () => {
    const values = fixture();

    await expect(
      runHeadless(
        values.surface,
        runIntent({
          target: { kind: "thread", threadId: "thread_existing" },
        }),
        values.io,
      ),
    ).resolves.toBe(0);

    expect(values.commands[0]).toEqual({
      name: "resume",
      arguments: ["thread_existing"],
    });
  });

  it("returns exit 3 without submitting when Workspace trust is unresolved", async () => {
    const values = fixture({ startup: "trust" });

    await expect(
      runHeadless(values.surface, runIntent(), values.io),
    ).resolves.toBe(3);

    expect(values.stdout).toEqual([]);
    expect(values.stderr.join("")).toContain("Workspace trust is required");
    expect(values.methods).toEqual(["initialize"]);
  });

  it("resolves only the exact startup trust identity when requested", async () => {
    const values = fixture({ startup: "trust" });

    await expect(
      runHeadless(
        values.surface,
        runIntent({ trustWorkspace: true }),
        values.io,
      ),
    ).resolves.toBe(0);

    expect(values.interactions).toContainEqual({
      interaction_id: "interaction_trust",
      decision: "trust",
    });
    expect(
      values.methods.filter((method) => method === "initialize"),
    ).toHaveLength(2);
  });

  it("leaves State Reset to an interactive client", async () => {
    const values = fixture({ startup: "reset" });

    await expect(
      runHeadless(
        values.surface,
        runIntent({ trustWorkspace: true }),
        values.io,
      ),
    ).resolves.toBe(3);

    expect(values.stdout).toEqual([]);
    expect(values.methods).toEqual(["initialize"]);
  });

  it("confirms only the full-access interaction returned by its command", async () => {
    const values = fixture({ fullAccessConfirmation: true });

    await expect(
      runHeadless(
        values.surface,
        runIntent({ permissionMode: "full_access" }),
        values.io,
      ),
    ).resolves.toBe(0);

    expect(values.interactions).toContainEqual({
      interaction_id: "interaction_full_access",
      decision: "enable_full_access",
    });
  });

  it("cancels the accepted operation before returning an unresolved interaction", async () => {
    const values = fixture({ terminal: "interaction" });

    await expect(
      runHeadless(values.surface, runIntent(), values.io),
    ).resolves.toBe(3);

    expect(values.stdout).toEqual([]);
    expect(values.cancellations).toEqual(["operation_1"]);
    expect(values.methods.at(-1)).toBe("operation.cancel");
  });

  it("lets SIGINT win stdout and requests cancellation first", async () => {
    const values = fixture({ terminal: "pending" });
    let interrupt: () => void = () => undefined;
    const running = runHeadless(values.surface, runIntent(), values.io, {
      subscribeInterrupt(listener) {
        interrupt = listener;
        return () => undefined;
      },
    });
    await values.turnSubmitted;

    interrupt();
    interrupt();

    await expect(running).resolves.toBe(130);
    expect(values.stdout).toEqual([]);
    expect(values.cancellations).toEqual(["operation_1"]);
    expect(values.stderr.join("")).toContain("Interrupted");
  });

  it("waits for an in-flight submission identity before SIGINT cancellation", async () => {
    const values = fixture({ terminal: "pending", submission: "pending" });
    let interrupt: () => void = () => undefined;
    const running = runHeadless(values.surface, runIntent(), values.io, {
      subscribeInterrupt(listener) {
        interrupt = listener;
        return () => undefined;
      },
      cancellationTimeoutMs: 100,
    });
    await values.turnSubmitted;

    interrupt();
    values.releaseSubmission();

    await expect(running).resolves.toBe(130);
    expect(values.stdout).toEqual([]);
    expect(values.cancellations).toEqual(["operation_1"]);
  });

  it.each([
    "rejected",
    "wrong_id",
  ] as const)("does not claim cancellation when Core returns %s confirmation", async (cancelResult) => {
    const values = fixture({ terminal: "pending", cancelResult });
    let interrupt: () => void = () => undefined;
    const running = runHeadless(values.surface, runIntent(), values.io, {
      subscribeInterrupt(listener) {
        interrupt = listener;
        return () => undefined;
      },
      cancellationTimeoutMs: 50,
    });
    await values.turnSubmitted;

    interrupt();

    await expect(running).resolves.toBe(130);
    expect(values.stderr.join("")).toContain(
      "cancellation could not be confirmed",
    );
  });

  it("bounds a cancellation request that never returns", async () => {
    const values = fixture({ terminal: "pending", cancelResult: "pending" });
    let interrupt: () => void = () => undefined;
    const running = runHeadless(values.surface, runIntent(), values.io, {
      subscribeInterrupt(listener) {
        interrupt = listener;
        return () => undefined;
      },
      cancellationTimeoutMs: 5,
    });
    await values.turnSubmitted;

    interrupt();

    await expect(running).resolves.toBe(130);
    expect(values.stdout).toEqual([]);
    expect(values.stderr.join("")).toContain(
      "cancellation could not be confirmed",
    );
  });

  it("returns exit 3 for startup recovery without submitting a new Turn", async () => {
    const values = fixture({ startupPendingInteraction: true });

    await expect(
      runHeadless(values.surface, runIntent(), values.io),
    ).resolves.toBe(3);

    expect(values.stdout).toEqual([]);
    expect(values.stderr.join("")).toContain("interaction");
    expect(values.methods).not.toContain("turn.submit");
  });

  it("keeps failed durable Turns off stdout", async () => {
    const values = fixture({ terminal: "failed" });

    await expect(
      runHeadless(values.surface, runIntent(), values.io),
    ).resolves.toBe(1);

    expect(values.stdout).toEqual([]);
    expect(values.stderr.join("")).toContain("model_failed");
  });

  it("does not echo arbitrary transport details, prompts, or secrets", async () => {
    const values = fixture({ startupFailure: true });

    await expect(
      runHeadless(
        values.surface,
        runIntent({ prompt: "private prompt" }),
        values.io,
      ),
    ).resolves.toBe(1);

    const diagnostics = values.stderr.join("");
    expect(values.stdout).toEqual([]);
    expect(diagnostics).not.toContain("private prompt");
    expect(diagnostics).not.toContain("secret-value");
    expect(diagnostics).not.toContain("jsonrpc");
  });
});

type StartupMode = "ready" | "trust" | "reset";
type TerminalMode = "completed" | "failed" | "interaction" | "pending";
type SubmissionMode = "immediate" | "pending";
type CancelMode = "accepted" | "rejected" | "wrong_id" | "pending";

function fixture({
  startup = "ready",
  terminal = "completed",
  fullAccessConfirmation = false,
  startupFailure = false,
  startupPendingInteraction = false,
  submission = "immediate",
  cancelResult = "accepted",
}: {
  readonly startup?: StartupMode;
  readonly terminal?: TerminalMode;
  readonly fullAccessConfirmation?: boolean;
  readonly startupFailure?: boolean;
  readonly startupPendingInteraction?: boolean;
  readonly submission?: SubmissionMode;
  readonly cancelResult?: CancelMode;
} = {}) {
  let state: SurfaceState = initialSurfaceState();
  const listeners = new Set<() => void>();
  const methods: string[] = [];
  const commands: object[] = [];
  const interactions: object[] = [];
  const cancellations: string[] = [];
  const stdout: string[] = [];
  const stderr: string[] = [];
  let trusted = startup !== "trust";
  let permission = "request_approval";
  let resolveTurnSubmitted!: () => void;
  const turnSubmitted = new Promise<void>((resolve) => {
    resolveTurnSubmitted = resolve;
  });
  let resolveSubmission!: () => void;
  const submissionReleased = new Promise<void>((resolve) => {
    resolveSubmission = resolve;
  });
  const setState = (next: Partial<SurfaceState>) => {
    state = { ...state, ...next };
    for (const listener of [...listeners]) listener();
  };
  const request = vi.fn(async (method: string, params: object) => {
    methods.push(method);
    switch (method) {
      case "initialize":
        if (startupFailure) {
          throw new Error('secret-value private prompt {"jsonrpc":"2.0"}');
        }
        if (startup === "reset") {
          return ok({
            status: "state_reset_required",
            interaction_id: "interaction_reset",
          });
        }
        if (!trusted) {
          return ok({
            status: "trust_required",
            interaction_id: "interaction_trust",
            workspace: { display_path: "E:\\workspace" },
          });
        }
        return ok({ status: "ready" });
      case "application.getState":
        return ok(
          applicationState(
            permission,
            startupPendingInteraction ? "interaction_recovery" : undefined,
          ),
        );
      case "command.execute": {
        const command = params as { name: string; arguments?: string[] };
        commands.push(command);
        if (command.name === "permissions") {
          if (
            fullAccessConfirmation &&
            command.arguments?.[0] === "full_access"
          ) {
            return ok({
              kind: "interaction",
              interaction: {
                kind: "application",
                interaction_id: "interaction_full_access",
              },
            });
          }
          permission = command.arguments?.[0] ?? permission;
          return ok({
            kind: "result",
            payload: { kind: "permissions", mode: permission },
          });
        }
        if (startupPendingInteraction) {
          return ok({
            kind: "error",
            code: "operation_busy",
            message: "Resolve recovery first.",
          });
        }
        return ok({
          kind: "result",
          payload: {
            kind: "thread_transition",
            transition: {
              application: applicationState(permission),
              thread: threadPage("in_progress"),
            },
          },
        });
      }
      case "interaction.respond": {
        const interaction = params as {
          interaction_id: string;
          decision: string;
        };
        interactions.push(interaction);
        if (
          interaction.interaction_id === "interaction_trust" &&
          interaction.decision === "trust"
        ) {
          trusted = true;
        }
        if (
          interaction.interaction_id === "interaction_full_access" &&
          interaction.decision === "enable_full_access"
        ) {
          permission = "full_access";
        }
        return ok({ accepted: true, status: "resolved" });
      }
      case "turn.submit":
        resolveTurnSubmitted();
        if (submission === "pending") await submissionReleased;
        if (terminal === "interaction") {
          setState({
            pending_interaction: {
              interaction_id: "interaction_tool",
              interaction_kind: "tool_approval",
              prompt: "Approve?",
              operation: "run",
              target: "tool",
              choices: [],
            },
          });
        } else if (terminal !== "pending") {
          setState({
            active_operation: {
              id: "operation_1",
              status: terminal === "completed" ? "completed" : "failed",
            },
          });
        }
        return ok({
          operation_id: "operation_1",
          thread_id: "thread_1",
          turn_id: "turn_1",
          client_message_id: "client_fixture",
        });
      case "thread.read":
        return ok(threadPage(terminal === "failed" ? "failed" : "completed"));
      case "operation.cancel":
        cancellations.push((params as { operation_id: string }).operation_id);
        if (cancelResult === "pending") {
          return await new Promise<never>(() => undefined);
        }
        return ok({
          operation_id:
            cancelResult === "wrong_id" ? "operation_other" : "operation_1",
          cancelled: cancelResult !== "rejected",
        });
      default:
        throw new Error(`Unexpected method: ${method}`);
    }
  });
  const surface = {
    store: {
      getState: () => state,
      dispatch: () => undefined,
      subscribe(listener: () => void) {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
    request,
    close: vi.fn(async () => undefined),
    session: {},
  } as unknown as ConnectedSurface;
  return {
    surface,
    methods,
    commands,
    interactions,
    cancellations,
    stdout,
    stderr,
    turnSubmitted,
    releaseSubmission: resolveSubmission,
    io: {
      writeStdout: (value: string) => stdout.push(value),
      writeStderr: (value: string) => stderr.push(value),
    },
  };
}

function runIntent(
  overrides: Partial<HeadlessRunIntent> = {},
): HeadlessRunIntent {
  return {
    kind: "run",
    prompt: "do the work",
    target: { kind: "new" },
    format: "text",
    trustWorkspace: false,
    allowNetwork: false,
    ...overrides,
  };
}

function ok(value: object) {
  return { ok: true, value } as const;
}

function applicationState(
  permissionMode: string,
  pendingInteractionId?: string,
) {
  return {
    initialized: true,
    session_id: "session_1",
    workspace_key: "workspace_1",
    workspace: { display_path: "E:\\workspace" },
    workspace_trusted: true,
    current_thread_id: "thread_1",
    ...(pendingInteractionId === undefined
      ? {}
      : { pending_interaction_id: pendingInteractionId }),
    model_identity: { effective_model: "deepseek/deepseek-v4-flash" },
    thinking_enabled: false,
    skill_mode: "auto",
    permission_mode: permissionMode,
    configuration_valid: true,
    configuration_diagnostics: [],
    secret_status: { deepseek_api_key: true },
    provider_credentials: {
      deepseek: {
        selected_source: "environment",
        environment_configured: true,
        awesome_configured: false,
      },
      kimi: {
        selected_source: null,
        environment_configured: false,
        awesome_configured: false,
      },
    },
  };
}

function threadPage(status: "in_progress" | "completed" | "failed") {
  const completed = status !== "in_progress";
  return {
    view: {
      thread: { id: "thread_1" },
      entries: completed
        ? [
            {
              id: "entry_assistant",
              kind: "assistant_message",
              content: "durable answer\n\n",
            },
          ]
        : [],
      turns: [
        {
          id: "turn_1",
          status,
          ...(status === "completed"
            ? {
                assistant_entry_id: "entry_assistant",
                termination_reason: "stop",
              }
            : {}),
          ...(status === "failed" ? { error_code: "model_failed" } : {}),
          usage: {
            input_tokens: 3,
            output_tokens: 2,
            reasoning_tokens: 0,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            model_calls: 1,
            tool_calls: 0,
            provider_retries: 0,
            compressions: 0,
            active_execution_seconds: 0.1,
          },
        },
      ],
      tool_activities: [],
    },
    change_sets: [],
    has_more: false,
  };
}
