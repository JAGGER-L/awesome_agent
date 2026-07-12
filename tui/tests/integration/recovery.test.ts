import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { startCoreProcess } from "../../src/core/process.js";
import { ReconnectController } from "../../src/lifecycle/reconnect.js";
import { connectSurface } from "../../src/surface/controller.js";

const fixture = fileURLToPath(
  new URL("../fixtures/fake-core.mjs", import.meta.url),
);

describe("networkless recovery", () => {
  it("creates fresh Sessions and rehydrates the same Thread without Welcome or Event replay", async () => {
    const cwd = await mkdtemp(join(tmpdir(), "awesome-reconnect-"));
    const controller = new ReconnectController({
      executable: process.execPath,
      env: { AWESOME_FAKE_CORE_THREAD: "1" },
      connect: async (options) =>
        await connectSurface({
          ...options,
          startSession: async (launch) =>
            await startCoreProcess(launch, [fixture]),
        }),
      committedBlocks: () => [],
    });
    const context = { cwd, threadId: "thread_fake" };
    const first = await controller.reconnect(context);
    expect(first.store.getState()).toMatchObject({
      application: { session_id: "session_fake" },
      thread: { view: { thread: { id: "thread_fake" } } },
      event_sequence: 0,
    });
    expect(
      first.store
        .getState()
        .committed_transcript?.some(
          (block) =>
            block.kind === "status" &&
            block.message.startsWith("Reconnected ·"),
        ),
    ).toBe(true);
    expect(JSON.stringify(first.store.getState())).not.toContain("Welcome");
    await first.close();

    controller.reset();
    const second = await controller.reconnect(context);
    expect(second).not.toBe(first);
    expect(second.store.getState().event_sequence).toBe(0);
    await second.close();
  });
});
