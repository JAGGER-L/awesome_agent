import { describe, expect, it } from "vitest";

import { InteractionController } from "../../src/lifecycle/interactions.js";
import type { SurfaceState } from "../../src/state/model.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function pendingState(): SurfaceState {
  return {
    connection: "ready",
    event_sequence: 1,
    warnings: [],
    pending_interaction: {
      interaction_id: "interaction_1",
      interaction_kind: "tool_approval",
      prompt: "Do you want to run pytest?",
      operation: "run",
      target: "pytest",
      capability: "shell.execute",
      choices: [
        { decision: "allow_once", label: "Yes" },
        { decision: "deny", label: "No" },
      ],
    },
  };
}

function harness(response: Promise<unknown>) {
  let state = pendingState();
  const listeners = new Set<() => void>();
  const calls: unknown[] = [];
  const controller = new InteractionController({
    getState: () => state,
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
      state = next;
      for (const listener of listeners) listener();
    },
  };
}

describe("InteractionController", () => {
  it("sends one exact protocol choice and waits for resolved Event", async () => {
    const response = deferred<unknown>();
    const value = harness(response.promise);
    const first = value.controller.respond("allow_once");
    const second = value.controller.respond("allow_once");
    expect(second).toBe(first);
    expect(value.calls).toEqual([
      { interaction_id: "interaction_1", decision: "allow_once" },
    ]);
    response.resolve({
      ok: true,
      value: { accepted: true, status: "resolved" },
    });
    await first;
    expect(value.controller.snapshot().status).toBe("responding");
    const { pending_interaction: _pending, ...resolved } = pendingState();
    void _pending;
    value.setState(resolved);
    expect(value.controller.snapshot().status).toBe("resolved");
  });

  it("rejects a choice not supplied by the Event", async () => {
    const value = harness(Promise.resolve({ ok: true }));
    await expect(value.controller.respond("trust")).rejects.toThrow(
      "not available",
    );
    expect(value.calls).toEqual([]);
  });

  it.each([
    {
      ok: false,
      error: {
        code: "operation_busy",
        message: "busy",
        retryable: true,
        data: {},
      },
    },
    { ok: true, value: { accepted: false, status: "rejected" } },
  ])("records product or rejected response failure", async (response) => {
    const value = harness(Promise.resolve(response));
    await value.controller.respond("deny");
    expect(value.controller.snapshot().status).toBe("failed");
  });

  it("fails when Core exits while a response is pending", () => {
    const response = deferred<unknown>();
    const value = harness(response.promise);
    void value.controller.respond("deny");
    value.setState({
      ...pendingState(),
      core_exit: { code: 1, signal: null },
    });
    expect(value.controller.snapshot().status).toBe("failed");
    response.resolve({
      ok: true,
      value: { accepted: true, status: "resolved" },
    });
  });
});
