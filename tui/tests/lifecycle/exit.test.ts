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
  const requestShutdown = vi.fn(async () => undefined);
  const terminate = vi.fn(async () => undefined);
  const disableInput = vi.fn();
  const cleanupTerminal = vi.fn(async () => undefined);
  const controller = new ExitController(
    { exit: exit.promise, requestShutdown, terminate },
    { clock, disableInput, cleanupTerminal },
  );
  return {
    clock,
    controller,
    disableInput,
    exit,
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
  ] as const)("uses one graceful path for %s", async (reason) => {
    const value = harness();
    const pending = value.controller.requestExit(reason);
    expect(value.disableInput).toHaveBeenCalledOnce();
    expect(value.requestShutdown).toHaveBeenCalledOnce();
    value.exit.resolve({ code: 0, signal: null, shutdown_requested: true });
    await expect(pending).resolves.toEqual({
      reason,
      exitCode: 0,
      forced: false,
    });
    expect(value.terminate).not.toHaveBeenCalled();
    expect(value.cleanupTerminal).toHaveBeenCalledOnce();
  });

  it("shares one idempotent exit Promise", () => {
    const value = harness();
    const first = value.controller.requestExit("quit_command");
    const second = value.controller.requestExit("ctrl_d");
    expect(second).toBe(first);
    expect(value.requestShutdown).toHaveBeenCalledOnce();
    value.exit.resolve({ code: 0, signal: null, shutdown_requested: true });
    return first;
  });

  it("waits five seconds then terminates once with a local warning", async () => {
    const value = harness();
    const pending = value.controller.requestExit("quit_command");
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
      { clock, disableInput: () => {}, cleanupTerminal },
    );
    const pending = controller.requestExit("quit_command");
    await Promise.resolve();
    clock.advance(5_000);
    await expect(pending).rejects.toThrow("terminate failed");
    expect(cleanupTerminal).toHaveBeenCalledOnce();
  });
});
