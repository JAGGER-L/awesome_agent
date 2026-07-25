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

function pendingState(interactionId = "interaction_1"): SurfaceState {
  return {
    connection: "ready",
    thread_generation: 0,
    event_sequence: 1,
    warnings: [],
    pending_interaction: {
      interaction_id: interactionId,
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
  it.each([
    "retry",
    "abort",
  ] as const)("sends the typed recovery decision %s", async (decision) => {
    const response = Promise.resolve({
      ok: true,
      value: { accepted: true, status: "resolved" },
    });
    const state: SurfaceState = {
      ...pendingState(),
      pending_interaction: {
        interaction_id: "interaction_recovery",
        interaction_kind: "recovery_decision",
        prompt: "Resume the unfinished Turn?",
        operation: "recover",
        target: "unfinished Turn",
        choices: [
          { decision: "retry", label: "Retry" },
          { decision: "abort", label: "Abort" },
        ],
      },
    };
    const calls: unknown[] = [];
    const controller = new InteractionController({
      getState: () => state,
      subscribe: () => () => undefined,
      request(params) {
        calls.push(params);
        return response as never;
      },
    });

    await controller.respond(decision);

    expect(calls).toEqual([
      { interaction_id: "interaction_recovery", decision },
    ]);
  });

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
  ])("reports product or rejected response failure to the caller", async (response) => {
    const value = harness(Promise.resolve(response));
    await expect(value.controller.respond("deny")).rejects.toThrow();
    expect(value.controller.snapshot().status).toBe("failed");
  });

  it("accepts a later interaction after the previous one resolves", async () => {
    const responses = [
      Promise.resolve({
        ok: true,
        value: { accepted: true, status: "resolved" },
      }),
      Promise.resolve({
        ok: true,
        value: { accepted: true, status: "resolved" },
      }),
    ];
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
        return responses.shift() as never;
      },
    });

    await controller.respond("deny");
    const { pending_interaction: _first, ...withoutFirst } = state;
    void _first;
    state = withoutFirst;
    for (const listener of listeners) listener();
    state = pendingState("interaction_2");
    for (const listener of listeners) listener();
    await controller.respond("deny");

    expect(calls).toEqual([
      { interaction_id: "interaction_1", decision: "deny" },
      { interaction_id: "interaction_2", decision: "deny" },
    ]);
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
