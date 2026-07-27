import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  startCoreProcess,
  type CoreLaunchOptions,
} from "../../src/core/process.js";
import { RpcClosedError } from "../../src/protocol/index.js";
import type { EventEnvelope } from "../../src/protocol/index.js";
import { applyThreadTransition } from "../../src/app/use-thread-transition.js";
import { connectSurface } from "../../src/surface/controller.js";
import { beginStartup, StartupError } from "../../src/surface/startup.js";

const fixture = fileURLToPath(
  new URL("../fixtures/fake-core.mjs", import.meta.url),
);

async function options(extra: Record<string, string | undefined> = {}) {
  const launch: CoreLaunchOptions = {
    executable: process.execPath,
    cwd: await mkdtemp(join(tmpdir(), "awesome-surface-cwd-")),
    env: extra,
  };
  return {
    ...launch,
    startSession: async (value: CoreLaunchOptions) =>
      await startCoreProcess(value, [fixture]),
  };
}

describe("connectSurface", () => {
  it("opens transport without reading trusted project state implicitly", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
      }),
    );
    expect(connected.store.getState()).toMatchObject({
      connection: "starting",
    });
    expect(connected.store.getState().application).toBeUndefined();
    expect(connected.store.getState().thread).toBeUndefined();
    await connected.close();
  });

  it("leaves trust resolution to the explicit startup controller", async () => {
    const connected = await connectSurface(
      await options({ AWESOME_FAKE_CORE_MODE: "trust-required" }),
    );
    await expect(
      beginStartup(connected, { kind: "new" }),
    ).resolves.toMatchObject({
      kind: "trust_required",
    });
    await connected.close();
  });

  it("retains an initialize Event racing the trust response", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_MODE: "trust-required",
        AWESOME_FAKE_CORE_EVENT_BEFORE_INIT: "1",
      }),
    );
    await expect(
      beginStartup(connected, { kind: "new" }),
    ).resolves.toMatchObject({
      kind: "trust_required",
      interactionId: "interaction_fake",
    });
    for (
      let attempt = 0;
      attempt < 20 && connected.store.getState().warnings.length === 0;
      attempt += 1
    ) {
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
    }
    expect(connected.store.getState().warnings).toEqual([
      {
        id: "warning:early:1",
        code: "early",
        message: "Early warning.",
        count: 1,
      },
    ]);
    await connected.close();
  });

  it("surfaces product handshake failure through startup", async () => {
    const connected = await connectSurface(
      await options({ AWESOME_FAKE_CORE_MODE: "handshake-failure" }),
    );
    await expect(
      beginStartup(connected, { kind: "new" }),
    ).rejects.toBeInstanceOf(StartupError);
    await connected.close();
  });

  it("passes requests through and rejects them after close", async () => {
    const connected = await connectSurface(await options());
    await expect(
      connected.request("operation.cancel", { operation_id: "operation_1" }),
    ).resolves.toMatchObject({ ok: true });
    await connected.close();
    await connected.close();
    await expect(connected.request("shutdown", {})).rejects.toBeInstanceOf(
      RpcClosedError,
    );
  });

  it("reads one bounded durable page for a terminal Operation", async () => {
    const connected = await connectSurface(
      await options({ AWESOME_FAKE_CORE_TERMINAL: "1" }),
    );
    await connected.request("operation.cancel", {
      operation_id: "operation_terminal",
    });
    for (
      let attempt = 0;
      attempt < 100 && !connected.store.getState().committed_transcript;
      attempt += 1
    ) {
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
    }
    expect(connected.store.getState()).toMatchObject({
      committed_transcript: expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "durable answer" }),
      ]),
    });
    expect(connected.store.getState().active_operation).toBeUndefined();
    const stderr = new TextDecoder().decode(connected.session.stderrTail());
    expect(stderr.match(/thread-read/g)).toHaveLength(1);
    await connected.close();
  });

  it("owns a pending terminal reconciliation during normal close", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_TERMINAL: "1",
        AWESOME_FAKE_CORE_THREAD_READ_DELAY_MS: "1000",
      }),
    );
    await connected.request("operation.cancel", {
      operation_id: "operation_terminal",
    });
    for (
      let attempt = 0;
      attempt < 100 &&
      !new TextDecoder()
        .decode(connected.session.stderrTail())
        .includes("thread-read");
      attempt += 1
    ) {
      await new Promise<void>((resolve) => setTimeout(resolve, 2));
    }

    const firstClose = connected.close();
    const repeatedClose = connected.close();
    expect(repeatedClose).toBe(firstClose);
    await firstClose;
    await connected.session.exit;

    expect(connected.store.getState()).toMatchObject({ connection: "closed" });
    expect(connected.store.getState().fatal).toBeUndefined();
    const stderr = new TextDecoder().decode(connected.session.stderrTail());
    expect(stderr.match(/thread-read/g)).toHaveLength(1);
  });

  it("surfaces a terminal reconciliation failure while still open", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_TERMINAL: "1",
        AWESOME_FAKE_CORE_REJECT_THREAD_READ: "1",
      }),
    );
    await connected.request("operation.cancel", {
      operation_id: "operation_terminal",
    });
    for (
      let attempt = 0;
      attempt < 100 &&
      connected.store.getState().fatal?.code !==
        "terminal_reconciliation_failed";
      attempt += 1
    ) {
      await new Promise<void>((resolve) => setTimeout(resolve, 2));
    }

    expect(connected.store.getState()).toMatchObject({
      connection: "fatal",
      fatal: { code: "terminal_reconciliation_failed" },
    });
    await expect(connected.request("shutdown", {})).rejects.toMatchObject({
      name: "RpcProtocolError",
    });
    await connected.close();
  });

  it("accepts an ordered Event emitted while shutdown is in flight", async () => {
    const connected = await connectSurface(
      await options({ AWESOME_FAKE_CORE_EVENT_DURING_SHUTDOWN: "1" }),
    );

    await connected.close();
    await connected.session.exit;

    expect(connected.store.getState()).toMatchObject({
      connection: "closed",
      warnings: [
        {
          code: "shutdown_notice",
          message: "Shutdown notice.",
          count: 1,
        },
      ],
    });
    expect(connected.store.getState().fatal).toBeUndefined();
  });

  it("drops a delayed reconciliation after an atomic thread replacement", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_TERMINAL: "1",
        AWESOME_FAKE_CORE_THREAD_READ_DELAY_MS: "100",
      }),
    );
    await connected.request("operation.cancel", {
      operation_id: "operation_terminal",
    });
    for (
      let attempt = 0;
      attempt < 100 &&
      !new TextDecoder()
        .decode(connected.session.stderrTail())
        .includes("thread-read");
      attempt += 1
    ) {
      await new Promise<void>((resolve) => setTimeout(resolve, 2));
    }

    connected.store.dispatch({
      type: "thread.replaced",
      application: { current_thread_id: "thread_new" } as never,
      thread: { view: { thread: { id: "thread_new" } } } as never,
      transcript: [],
    });
    await new Promise<void>((resolve) => setTimeout(resolve, 150));

    expect(connected.store.getState().thread_generation).toBe(1);
    expect(JSON.stringify(connected.store.getState())).not.toContain(
      "durable answer",
    );
    await connected.close();
  });

  it.each([
    "before",
    "after",
  ] as const)("installs the retry transition before replaying %s-response Events", async (order) => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: order,
      }),
    );
    await hydrateCurrentThread(connected);

    const response = await connected.request("command.execute", {
      name: "retry",
    });
    expect(response.ok).toBe(true);
    await new Promise<void>((resolve) => setTimeout(resolve, 10));
    expect(connected.store.getState()).toMatchObject({
      thread_generation: 0,
      application: { current_thread_id: "thread_fake" },
    });
    expect(connected.store.getState().active_operation).toBeUndefined();
    if (
      !response.ok ||
      response.value.kind !== "result" ||
      response.value.payload.kind !== "thread_retry"
    ) {
      throw new Error("Fake Core did not return a Thread retry");
    }
    const payload = response.value.payload;
    const replacement = applyThreadTransition({
      store: connected.store,
      transition: payload.transition,
      expectedGeneration: 0,
      effects: { resetCurrentFrame: () => undefined },
    });
    if (replacement.kind !== "replaced") {
      throw new Error("Retry transition was unexpectedly stale");
    }
    connected.activateThreadRetry?.(payload.operation, replacement.generation);

    await waitFor(() =>
      connected.store.getState().active_operation?.id ===
      payload.operation.operation_id
        ? true
        : undefined,
    );
    expect(connected.store.getState()).toMatchObject({
      thread_generation: 1,
      application: { current_thread_id: "thread_retry" },
      active_operation: {
        id: "operation_retry",
        status: "active",
        turn: { id: "turn_retry", status: "active" },
      },
    });
    expect(connected.store.getState().fatal).toBeUndefined();
    await connected.close();
  });

  it.each([
    "mismatch",
    "client_mismatch",
  ] as const)("rejects buffered retry Events whose %s identity disagrees with the accepted Operation", async (order) => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: order,
      }),
    );
    await hydrateCurrentThread(connected);
    const response = await connected.request("command.execute", {
      name: "retry",
    });
    if (
      !response.ok ||
      response.value.kind !== "result" ||
      response.value.payload.kind !== "thread_retry"
    ) {
      throw new Error("Fake Core did not return a Thread retry");
    }
    const payload = response.value.payload;
    const replacement = applyThreadTransition({
      store: connected.store,
      transition: payload.transition,
      expectedGeneration: 0,
      effects: { resetCurrentFrame: () => undefined },
    });
    if (replacement.kind !== "replaced") {
      throw new Error("Retry transition was unexpectedly stale");
    }
    try {
      connected.activateThreadRetry?.(
        payload.operation,
        replacement.generation,
      );
    } catch {
      // A pre-response Event is rejected synchronously when already buffered.
    }
    await waitFor(() => connected.store.getState().fatal);

    expect(connected.store.getState()).toMatchObject({
      connection: "fatal",
      fatal: { code: "thread_retry_identity_mismatch" },
    });
    expect(connected.store.getState().active_operation).toBeUndefined();
    await connected.close();
  });

  it("rejects a post-activation retry Event with a different client identity", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: "client_mismatch_after",
      }),
    );
    await hydrateCurrentThread(connected);
    const response = await connected.request("command.execute", {
      name: "retry",
    });
    if (
      !response.ok ||
      response.value.kind !== "result" ||
      response.value.payload.kind !== "thread_retry"
    ) {
      throw new Error("Fake Core did not return a Thread retry");
    }
    const payload = response.value.payload;
    const replacement = applyThreadTransition({
      store: connected.store,
      transition: payload.transition,
      expectedGeneration: 0,
      effects: { resetCurrentFrame: () => undefined },
    });
    if (replacement.kind !== "replaced") {
      throw new Error("Retry transition was unexpectedly stale");
    }
    connected.activateThreadRetry?.(payload.operation, replacement.generation);

    await waitFor(() => connected.store.getState().fatal);
    expect(connected.store.getState()).toMatchObject({
      connection: "fatal",
      fatal: { code: "thread_retry_identity_mismatch" },
    });
    await connected.close();
  });

  it("rejects a post-response buffered retry Event with a different client identity", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: "client_mismatch_after",
      }),
    );
    await hydrateCurrentThread(connected);
    const response = await connected.request("command.execute", {
      name: "retry",
    });
    if (
      !response.ok ||
      response.value.kind !== "result" ||
      response.value.payload.kind !== "thread_retry"
    ) {
      throw new Error("Fake Core did not return a Thread retry");
    }
    const payload = response.value.payload;
    const replacement = applyThreadTransition({
      store: connected.store,
      transition: payload.transition,
      expectedGeneration: 0,
      effects: { resetCurrentFrame: () => undefined },
    });
    if (replacement.kind !== "replaced") {
      throw new Error("Retry transition was unexpectedly stale");
    }

    await new Promise<void>((resolve) => setTimeout(resolve, 100));
    expect(connected.store.getState().fatal).toBeUndefined();
    expect(() =>
      connected.activateThreadRetry?.(
        payload.operation,
        replacement.generation,
      ),
    ).toThrow("Buffered retry Event identity");

    expect(connected.store.getState()).toMatchObject({
      connection: "fatal",
      fatal: { code: "thread_retry_identity_mismatch" },
    });
    await connected.close();
  });

  it("keeps ordinary Event stream faults classified as protocol_desynchronized", async () => {
    const connected = await connectSurface(
      await options({ AWESOME_FAKE_CORE_TERMINAL: "1" }),
    );
    await connected.request("operation.cancel", {
      operation_id: "operation_first",
    });
    await waitFor(() => connected.store.getState().committed_transcript);

    const repeatedSequence = connected
      .request("operation.cancel", { operation_id: "operation_second" })
      .catch(() => undefined);
    await waitFor(() => connected.store.getState().fatal);

    expect(connected.store.getState()).toMatchObject({
      connection: "fatal",
      fatal: { code: "protocol_desynchronized" },
    });
    await repeatedSequence;
    await connected.close();
  });

  it.each([
    "overflow_count",
    "overflow_bytes",
  ] as const)("fails closed when the retry Event gate exceeds its %s limit", async (order) => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: order,
      }),
    );
    await hydrateCurrentThread(connected);
    const request = connected
      .request("command.execute", { name: "retry" })
      .catch(() => undefined);

    await waitFor(() => connected.store.getState().fatal);
    expect(connected.store.getState()).toMatchObject({
      connection: "fatal",
      fatal: { code: "thread_retry_buffer_limit" },
    });
    await request;
    await connected.close();
  });

  it("replays an old terminal Event when a retry command is rejected", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_THREAD: "1",
        AWESOME_FAKE_CORE_RETRY_EVENTS: "failure_after_old_terminal",
      }),
    );
    await hydrateCurrentThread(connected);
    connected.store.dispatch({
      type: "event.received",
      event: oldOperationStart(),
      generation: 0,
    });

    const response = await connected.request("command.execute", {
      name: "retry",
    });
    expect(response).toMatchObject({
      ok: true,
      value: { kind: "error", code: "retry_not_available" },
    });
    expect(connected.store.getState()).toMatchObject({
      active_operation: { id: "operation_old", status: "completed" },
    });
    expect(connected.store.getState().fatal).toBeUndefined();
    await connected.close();
  });
});

type Connected = Awaited<ReturnType<typeof connectSurface>>;

async function hydrateCurrentThread(connected: Connected): Promise<void> {
  const application = await connected.request("application.getState", {});
  if (!application.ok || !application.value.current_thread_id) {
    throw new Error("Fake Core did not publish a current Thread");
  }
  connected.store.dispatch({
    type: "hydrate.application",
    application: application.value,
  });
  const thread = await connected.request("thread.read", {
    thread_id: application.value.current_thread_id,
  });
  if (!thread.ok) throw new Error("Fake Core did not publish a Thread page");
  connected.store.dispatch({ type: "hydrate.thread", thread: thread.value });
}

async function waitFor<Value>(read: () => Value | undefined): Promise<Value> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const value = read();
    if (value !== undefined) return value;
    await new Promise<void>((resolve) => setTimeout(resolve, 2));
  }
  throw new Error("Timed out waiting for Surface state");
}

function oldOperationStart(): EventEnvelope {
  return {
    version: 1,
    event_id: "event_oldstart",
    sequence: 1,
    session_id: "session_fake",
    workspace_key: "workspace_fake",
    thread_id: "thread_fake",
    turn_id: undefined,
    operation_id: "operation_old",
    client_message_id: undefined,
    event_type: "operation.started",
    timestamp: "2026-07-11T08:00:00Z",
    payload: { kind: "operation.started", message: "" },
  };
}
