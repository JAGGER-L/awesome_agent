import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import { CoreSpawnError, startCore, StderrRing } from "../../src/core/index.js";
import { startCoreProcess } from "../../src/core/process.js";
import { RpcClosedError } from "../../src/protocol/index.js";

const fixture = pathToFileURL(
  join(process.cwd(), "tests", "fixtures", "fake-core.mjs"),
).href;

async function options(extra: Record<string, string | undefined> = {}) {
  return {
    executable: process.execPath,
    cwd: await mkdtemp(join(tmpdir(), "awesome-core-cwd-")),
    env: extra,
  };
}

async function startFakeCore(extra: Record<string, string | undefined> = {}) {
  return await startCoreProcess(await options(extra), [fileURLToPath(fixture)]);
}

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    throw error;
  }
}

async function waitFor<T>(read: () => Promise<T | undefined>): Promise<T> {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const value = await read();
    if (value !== undefined) return value;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("Timed out waiting for fake Core process state");
}

async function readPid(path: string): Promise<number | undefined> {
  try {
    const pid = Number.parseInt(await readFile(path, "utf8"), 10);
    return Number.isSafeInteger(pid) && pid > 0 ? pid : undefined;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

async function waitForProcessExit(pid: number): Promise<void> {
  await waitFor(async () => (processExists(pid) ? undefined : true));
}

function forceKill(pid: number): void {
  if (!processExists(pid)) return;
  try {
    process.kill(pid, "SIGKILL");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
  }
}

describe("StderrRing", () => {
  it("keeps an exact byte tail and returns a copy", () => {
    const ring = new StderrRing(5);
    ring.append(Uint8Array.from([1, 2, 3]));
    ring.append(Uint8Array.from([4, 5, 6, 7]));
    const tail = ring.tail();
    expect([...tail]).toEqual([3, 4, 5, 6, 7]);
    tail[0] = 99;
    expect([...ring.tail()]).toEqual([3, 4, 5, 6, 7]);
  });

  it("truncates multibyte text as bytes", () => {
    const ring = new StderrRing(3);
    ring.append(new TextEncoder().encode("😀"));
    expect(ring.tail()).toHaveLength(3);
  });
});

describe("CoreProcess", () => {
  it("reports a missing executable and invalid cwd as spawn errors", async () => {
    await expect(
      startCore({
        executable: "awesome-core-does-not-exist",
        cwd: process.cwd(),
        env: {},
      }),
    ).rejects.toBeInstanceOf(CoreSpawnError);
    const value = await options();
    await expect(
      startCore({ ...value, cwd: join(value.cwd, "missing") }),
    ).rejects.toBeInstanceOf(CoreSpawnError);
  });

  it("propagates cwd/environment and handles fragmented stdout", async () => {
    const value = await options({
      AWESOME_FAKE_CORE_FRAGMENT: "1",
      AWESOME_FAKE_CORE_MARKER: "inherited",
    });
    const session = await startCoreProcess(value, [fileURLToPath(fixture)]);
    const initialized = await session.rpc.request("initialize", {
      protocol_version: 3,
      client_name: "awesome",
      client_version: "0.1.0",
    });
    expect(initialized).toMatchObject({
      ok: true,
      value: { workspace: { display_path: value.cwd, branch: "inherited" } },
    });
    await session.requestShutdown();
    await expect(session.exit).resolves.toMatchObject({
      code: 0,
      shutdown_requested: true,
    });
  });

  it("serializes concurrent stdin writes through RpcClient", async () => {
    const session = await startFakeCore();
    const first = session.rpc.request("operation.cancel", {
      operation_id: "operation_1",
    });
    const second = session.rpc.request("operation.cancel", {
      operation_id: "operation_2",
    });
    await expect(first).resolves.toMatchObject({
      ok: true,
      value: { operation_id: "operation_1" },
    });
    await expect(second).resolves.toMatchObject({
      ok: true,
      value: { operation_id: "operation_2" },
    });
    await session.requestShutdown();
  });

  it("keeps exactly the last 65,536 stderr bytes", async () => {
    const bytes = Uint8Array.from(
      { length: 70_000 },
      (_, index) => index % 251,
    );
    const session = await startFakeCore({
      AWESOME_FAKE_CORE_STDERR_BASE64: Buffer.from(bytes).toString("base64"),
    });
    await session.rpc.request("initialize", {
      protocol_version: 3,
      client_name: "awesome",
      client_version: "0.1.0",
    });
    expect(session.stderrTail()).toEqual(bytes.slice(bytes.length - 65_536));
    await session.requestShutdown();
  });

  it("records abnormal exit and terminates a hanging child", async () => {
    const abnormal = await startFakeCore({
      AWESOME_FAKE_CORE_MODE: "exit-before-handshake",
    });
    await expect(abnormal.exit).resolves.toMatchObject({
      code: 23,
      shutdown_requested: false,
    });

    const hanging = await startFakeCore({
      AWESOME_FAKE_CORE_MODE: "hang-after-shutdown",
    });
    await hanging.requestShutdown();
    await hanging.requestShutdown();
    await hanging.terminate();
    const exit = await hanging.exit;
    expect(exit.shutdown_requested).toBe(true);
    expect(exit.signal ?? exit.code).not.toBeNull();
  });

  it("terminates the Core process tree", async () => {
    const value = await options();
    const descendantPidFile = join(value.cwd, "descendant.pid");
    let descendantPid: number | undefined;
    let session: Awaited<ReturnType<typeof startCoreProcess>> | undefined;
    try {
      session = await startCoreProcess(
        {
          ...value,
          env: {
            ...value.env,
            AWESOME_FAKE_CORE_DESCENDANT_PID_FILE: descendantPidFile,
          },
        },
        [fileURLToPath(fixture)],
      );
      descendantPid = await waitFor(() => readPid(descendantPidFile));
      expect(processExists(descendantPid)).toBe(true);

      const termination = session.terminate();
      expect(session.terminate()).toBe(termination);
      await termination;
      expect(processExists(descendantPid)).toBe(false);
      await session.exit;
      const requestAfterTermination = session.rpc.request("operation.cancel", {
        operation_id: "operation_after_termination",
      });
      await expect(requestAfterTermination).rejects.toBeInstanceOf(
        RpcClosedError,
      );
      await expect(requestAfterTermination).rejects.toMatchObject({
        name: "RpcClosedError",
        message: "Core process terminated",
      });
    } finally {
      if (session) {
        await session.terminate().catch(() => undefined);
      }
      if (descendantPid !== undefined) {
        forceKill(descendantPid);
        await waitForProcessExit(descendantPid).catch(() => undefined);
      }
      await rm(value.cwd, {
        recursive: true,
        force: true,
        maxRetries: 20,
        retryDelay: 50,
      });
    }
  }, 15_000);

  it("terminates the original POSIX group after the Core root exits", async () => {
    if (process.platform === "win32") return;

    const value = await options();
    const descendantPidFile = join(value.cwd, "orphaned-descendant.pid");
    const source = [
      'const { spawn } = require("node:child_process")',
      'const { writeFileSync } = require("node:fs")',
      'const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" })',
      `writeFileSync(${JSON.stringify(descendantPidFile)}, String(child.pid), "utf8")`,
      "child.unref()",
      "process.exit(0)",
    ].join(";");
    let descendantPid: number | undefined;
    let session: Awaited<ReturnType<typeof startCoreProcess>> | undefined;
    try {
      session = await startCoreProcess(value, ["-e", source]);
      descendantPid = await waitFor(() => readPid(descendantPidFile));
      await session.exit;
      expect(processExists(descendantPid)).toBe(true);

      await session.terminate();

      expect(processExists(descendantPid)).toBe(false);
    } finally {
      if (session) await session.terminate().catch(() => undefined);
      if (descendantPid !== undefined) {
        forceKill(descendantPid);
        await waitForProcessExit(descendantPid).catch(() => undefined);
      }
      await rm(value.cwd, {
        recursive: true,
        force: true,
        maxRetries: 20,
        retryDelay: 50,
      });
    }
  }, 15_000);
});
