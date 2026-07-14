import { describe, expect, it, vi } from "vitest";

import { ExitController, type ExitClock } from "../../src/lifecycle/exit.js";
import type { CoreExit } from "../../src/core/process.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

async function nextTurn(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

class FakeClock implements ExitClock {
  #tasks: { at: number; callback: () => void; cancelled: boolean }[] = [];
  #now = 0;

  schedule(callback: () => void, delay: number) {
    const task = { at: this.#now + delay, callback, cancelled: false };
    this.#tasks.push(task);
    return { cancel: () => (task.cancelled = true) };
  }

  advance(milliseconds: number) {
    this.#now += milliseconds;
    for (const task of this.#tasks) {
      if (!task.cancelled && task.at <= this.#now) {
        task.cancelled = true;
        task.callback();
      }
    }
  }
}

function harness() {
  const exit = deferred<CoreExit>();
  const clock = new FakeClock();
  const calls: string[] = [];
  const requestShutdown = vi.fn(async () => {
    calls.push("shutdown");
  });
  const terminate = vi.fn(async () => {
    calls.push("terminate");
  });
  const disableInput = vi.fn(() => calls.push("disable"));
  const flushTerminal = vi.fn(async () => {
    calls.push("flush");
  });
  const cleanupTerminal = vi.fn(async () => {
    calls.push("cleanup");
  });
  const controller = new ExitController(
    { exit: exit.promise, requestShutdown, terminate },
    { clock, disableInput, flushTerminal, cleanupTerminal },
  );
  return {
    calls,
    clock,
    controller,
    disableInput,
    exit,
    flushTerminal,
    cleanupTerminal,
    requestShutdown,
    terminate,
  };
}

describe("ExitController", () => {
  it.each([
    "quit_command",
    "ctrl_d",
    "double_ctrl_c",
    "trust_denied",
    "state_reset_denied",
  ] as const)("uses one graceful path for %s", async (reason) => {
    const value = harness();
    const pending = value.controller.requestExit(reason);
    expect(value.disableInput).toHaveBeenCalledOnce();
    await Promise.resolve();
    expect(value.requestShutdown).toHaveBeenCalledOnce();
    value.exit.resolve({ code: 0, signal: null, shutdown_requested: true });
    await expect(pending).resolves.toEqual({
      reason,
      exitCode: 0,
      forced: false,
    });
    expect(value.terminate).not.toHaveBeenCalled();
    expect(value.cleanupTerminal).toHaveBeenCalledOnce();
    expect(value.calls).toEqual(["disable", "flush", "shutdown", "cleanup"]);
  });

  it("shares one idempotent exit Promise", async () => {
    const value = harness();
    const first = value.controller.requestExit("quit_command");
    const second = value.controller.requestExit("ctrl_d");
    expect(second).toBe(first);
    await Promise.resolve();
    expect(value.requestShutdown).toHaveBeenCalledOnce();
    value.exit.resolve({ code: 0, signal: null, shutdown_requested: true });
    await first;
  });

  it("waits for the final presentation flush before requesting shutdown", async () => {
    const exit = deferred<CoreExit>();
    const flushed = deferred<void>();
    const calls: string[] = [];
    const requestShutdown = vi.fn(async () => {
      calls.push("shutdown");
    });
    const cleanupTerminal = vi.fn(async () => {
      calls.push("cleanup");
    });
    const controller = new ExitController(
      {
        exit: exit.promise,
        requestShutdown,
        terminate: async () => {
          calls.push("terminate");
        },
      },
      {
        disableInput: () => calls.push("disable"),
        flushTerminal: async () => {
          calls.push("flush");
          await flushed.promise;
        },
        cleanupTerminal,
      },
    );

    const pending = controller.requestExit("quit_command");
    expect(calls).toEqual(["disable", "flush"]);
    expect(requestShutdown).not.toHaveBeenCalled();

    flushed.resolve();
    await nextTurn();
    expect(calls).toEqual(["disable", "flush", "shutdown"]);

    exit.resolve({ code: 0, signal: null, shutdown_requested: true });
    await expect(pending).resolves.toMatchObject({ forced: false });
    expect(calls).toEqual(["disable", "flush", "shutdown", "cleanup"]);
  });

  it("shuts down and cleans up before reporting a presentation flush failure", async () => {
    const exit = deferred<CoreExit>();
    const requestShutdown = vi.fn(async () => undefined);
    const cleanupTerminal = vi.fn(async () => undefined);
    const controller = new ExitController(
      {
        exit: exit.promise,
        requestShutdown,
        terminate: async () => undefined,
      },
      {
        disableInput: () => undefined,
        flushTerminal: async () => {
          throw new Error("render flush failed");
        },
        cleanupTerminal,
      },
    );

    const pending = controller.requestExit("quit_command");
    await Promise.resolve();
    expect(requestShutdown).toHaveBeenCalledOnce();
    exit.resolve({ code: 0, signal: null, shutdown_requested: true });

    await expect(pending).rejects.toThrow("render flush failed");
    expect(cleanupTerminal).toHaveBeenCalledOnce();
  });

  it("waits five seconds then terminates once with a local warning", async () => {
    const value = harness();
    const pending = value.controller.requestExit("quit_command");
    await nextTurn();
    value.clock.advance(4_999);
    expect(value.terminate).not.toHaveBeenCalled();
    value.clock.advance(1);
    await expect(pending).resolves.toEqual({
      reason: "quit_command",
      exitCode: 0,
      forced: true,
      warning: "Core did not exit within 5 seconds and was terminated.",
    });
    expect(value.terminate).toHaveBeenCalledOnce();
    expect(value.cleanupTerminal).toHaveBeenCalledOnce();
  });

  it("restores terminal state when shutdown and termination fail", async () => {
    const exit = deferred<CoreExit>();
    const clock = new FakeClock();
    const cleanupTerminal = vi.fn(async () => undefined);
    const controller = new ExitController(
      {
        exit: exit.promise,
        requestShutdown: async () => {
          throw new Error("shutdown failed");
        },
        terminate: async () => {
          throw new Error("terminate failed");
        },
      },
      {
        clock,
        disableInput: () => {},
        flushTerminal: async () => undefined,
        cleanupTerminal,
      },
    );
    const pending = controller.requestExit("quit_command");
    await Promise.resolve();
    clock.advance(5_000);
    await expect(pending).rejects.toThrow("terminate failed");
    expect(cleanupTerminal).toHaveBeenCalledOnce();
  });
});
