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
      { code: "early", message: "Early warning." },
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
      transcript_persisted: true,
      committed_transcript: expect.arrayContaining([
        expect.objectContaining({ kind: "assistant", text: "durable answer" }),
      ]),
    });
    const stderr = new TextDecoder().decode(connected.session.stderrTail());
    expect(stderr.match(/thread-read/g)).toHaveLength(1);
    await connected.close();
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
      transcript_persisted: true,
    });
    await new Promise<void>((resolve) => setTimeout(resolve, 150));

    expect(connected.store.getState().thread_generation).toBe(1);
    expect(JSON.stringify(connected.store.getState())).not.toContain(
      "durable answer",
    );
    await connected.close();
  });
});
