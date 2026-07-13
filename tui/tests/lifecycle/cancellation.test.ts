import { describe, expect, it } from "vitest";

import { CancellationController } from "../../src/lifecycle/cancellation.js";
import type { SurfaceState } from "../../src/state/model.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function state(operation: "turn" | "direct" | "none" = "turn"): SurfaceState {
  return {
    connection: "ready",
    thread_generation: 0,
    event_sequence: 0,
    warnings: [],
    ...(operation === "none"
      ? {}
      : {
          active_operation: {
            id: "operation_1",
            status: "active" as const,
            ...(operation === "turn"
              ? {
                  turn: {
                    id: "turn_1",
                    status: "active" as const,
                    started_at: "2026-07-11T00:00:00Z",
                    timeline: [],
                    thinking_sequence: 0,
                  },
                }
              : {}),
          },
        }),
  };
}

function harness(
  initial = state(),
  response: Promise<unknown> = Promise.resolve({
    ok: true,
    value: { operation_id: "operation_1", cancelled: true },
  }),
) {
  let current = initial;
  const listeners = new Set<() => void>();
  const calls: unknown[] = [];
  const controller = new CancellationController({
    getState: () => current,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    request(params) {
      calls.push(params);
      return response as never;
    },
  });
  return {
    calls,
    controller,
    setState(next: SurfaceState) {
      current = next;
      for (const listener of listeners) listener();
    },
  };
}

describe("CancellationController", () => {
  it("does nothing without an active Operation", async () => {
    const { calls, controller } = harness(state("none"));
    await controller.cancelActiveOperation();
    expect(calls).toEqual([]);
    expect(controller.snapshot()).toEqual({ status: "idle" });
  });

  it.each([
    "turn",
    "direct",
  ] as const)("cancels one active %s operation", async (kind) => {
    const { calls, controller } = harness(state(kind));
    await controller.cancelActiveOperation();
    expect(calls).toEqual([{ operation_id: "operation_1" }]);
    expect(controller.snapshot()).toEqual({
      status: "requested",
      operationId: "operation_1",
    });
  });

  it("returns the same pending request and sends no repeat", () => {
    const pending = deferred<unknown>();
    const { calls, controller } = harness(state(), pending.promise);
    const first = controller.cancelActiveOperation();
    const second = controller.cancelActiveOperation();
    expect(second).toBe(first);
    expect(calls).toHaveLength(1);
    pending.resolve({
      ok: true,
      value: { operation_id: "operation_1", cancelled: true },
    });
    return first;
  });

  it("records product failure and allows an explicit retry", async () => {
    const { calls, controller } = harness(
      state(),
      Promise.resolve({
        ok: false,
        error: {
          code: "operation_busy",
          message: "busy",
          retryable: true,
          data: {},
        },
      }),
    );
    await controller.cancelActiveOperation();
    expect(controller.snapshot()).toMatchObject({
      status: "failed",
      message: "busy",
    });
    await controller.cancelActiveOperation();
    expect(calls).toHaveLength(2);
  });

  it("records a false cancel response as failed", async () => {
    const { controller } = harness(
      state(),
      Promise.resolve({
        ok: true,
        value: { operation_id: "operation_1", cancelled: false },
      }),
    );
    await controller.cancelActiveOperation();
    expect(controller.snapshot().status).toBe("failed");
  });

  it("lets a terminal Operation win a race with the cancel response", async () => {
    const pending = deferred<unknown>();
    const { controller, setState } = harness(state(), pending.promise);
    const request = controller.cancelActiveOperation();
    const terminal = state();
    if (!terminal.active_operation) throw new Error("expected operation");
    setState({
      ...terminal,
      active_operation: {
        ...terminal.active_operation,
        status: "cancelled",
      },
    });
    expect(controller.snapshot().status).toBe("confirmed");
    pending.resolve({
      ok: true,
      value: { operation_id: "operation_1", cancelled: true },
    });
    await request;
    expect(controller.snapshot().status).toBe("confirmed");
  });

  it("can cancel the next Operation after a previous cancellation completed", async () => {
    const { calls, controller, setState } = harness(state());
    const first = controller.cancelActiveOperation();
    const terminal = state();
    if (!terminal.active_operation) throw new Error("expected operation");
    setState({
      ...terminal,
      active_operation: { ...terminal.active_operation, status: "cancelled" },
    });
    await first;

    const next = state();
    if (!next.active_operation) throw new Error("expected operation");
    setState({
      ...next,
      active_operation: { ...next.active_operation, id: "operation_2" },
    });
    await controller.cancelActiveOperation();
    expect(calls).toEqual([
      { operation_id: "operation_1" },
      { operation_id: "operation_2" },
    ]);
  });

  it("fails pending cancellation on Core exit and resets for reconnect", () => {
    const pending = deferred<unknown>();
    const { controller, setState } = harness(state(), pending.promise);
    void controller.cancelActiveOperation();
    setState({ ...state(), core_exit: { code: 1, signal: null } });
    expect(controller.snapshot().status).toBe("failed");
    controller.reset();
    expect(controller.snapshot()).toEqual({ status: "idle" });
    pending.resolve({
      ok: true,
      value: { operation_id: "operation_1", cancelled: true },
    });
  });
});
