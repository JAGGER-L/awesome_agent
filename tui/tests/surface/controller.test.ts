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
import {
  connectSurface,
  SurfaceConnectionError,
} from "../../src/surface/controller.js";

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
  it("hydrates state/Thread and retains Events racing initialize", async () => {
    const connected = await connectSurface(
      await options({
        AWESOME_FAKE_CORE_EVENT_BEFORE_INIT: "1",
        AWESOME_FAKE_CORE_THREAD: "1",
      }),
    );
    expect(connected.store.getState()).toMatchObject({
      connection: "ready",
      application: { current_thread_id: "thread_fake" },
      thread: { view: { thread: { title: "Fake Thread" } } },
      warnings: [{ code: "early" }],
    });
    await connected.close();
  });

  it("represents trust-required without inventing product decisions", async () => {
    const connected = await connectSurface(
      await options({ AWESOME_FAKE_CORE_MODE: "trust-required" }),
    );
    expect(connected.store.getState()).toMatchObject({
      connection: "trust_required",
      application: { workspace_trusted: false },
    });
    await connected.close();
  });

  it("closes cleanly on product handshake failure", async () => {
    await expect(
      connectSurface(
        await options({ AWESOME_FAKE_CORE_MODE: "handshake-failure" }),
      ),
    ).rejects.toBeInstanceOf(SurfaceConnectionError);
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
});
