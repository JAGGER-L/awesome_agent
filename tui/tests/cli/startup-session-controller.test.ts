import { describe, expect, it, vi } from "vitest";

import { StartupSessionController } from "../../src/cli/startup-session-controller.js";
import type { ConnectedSurface } from "../../src/surface/controller.js";
import type {
  StartupResult,
  StartupThreadResult,
} from "../../src/surface/startup.js";
import { freshModelCatalog } from "../fixtures/model-catalog.js";

describe("StartupSessionController", () => {
  it("continues Trust with the exact interaction identity", async () => {
    const values = harness();
    const current = trustRequired("interaction_trust");
    values.respondTrust.mockResolvedValueOnce(ready());

    await expect(
      values.controller.respondTrust(current, "trust"),
    ).resolves.toMatchObject({
      kind: "render",
      startup: { kind: "ready" },
    });
    expect(values.respondTrust).toHaveBeenCalledWith(
      values.surface,
      { kind: "new" },
      "interaction_trust",
      "trust",
    );
  });

  it("maps Trust denial to the CLI exit reason without another transition", async () => {
    const values = harness();
    values.respondTrust.mockResolvedValueOnce({ kind: "denied" });

    await expect(
      values.controller.respondTrust(
        trustRequired("interaction_trust"),
        "deny",
      ),
    ).resolves.toEqual({ kind: "exit", reason: "trust_denied" });
    expect(values.respondStateReset).not.toHaveBeenCalled();
    expect(values.selectThread).not.toHaveBeenCalled();
  });

  it("continues State Reset with the exact interaction identity", async () => {
    const values = harness();
    values.respondStateReset.mockResolvedValueOnce(
      trustRequired("interaction_after_reset"),
    );

    await expect(
      values.controller.respondStateReset(
        stateResetRequired("interaction_reset"),
        "reset_state",
      ),
    ).resolves.toEqual({
      kind: "render",
      startup: trustRequired("interaction_after_reset"),
    });
    expect(values.respondStateReset).toHaveBeenCalledWith(
      values.surface,
      { kind: "new" },
      "interaction_reset",
      "reset_state",
    );
  });

  it("maps State Reset denial to its distinct CLI exit reason", async () => {
    const values = harness();
    values.respondStateReset.mockResolvedValueOnce({ kind: "denied" });

    await expect(
      values.controller.respondStateReset(
        stateResetRequired("interaction_reset"),
        "deny",
      ),
    ).resolves.toEqual({ kind: "exit", reason: "state_reset_denied" });
  });

  it("preserves typed continuation failures for the current modal", async () => {
    const values = harness();
    const error = Object.assign(new Error("Close other sessions."), {
      code: "state_reset_busy",
    });
    values.respondStateReset.mockRejectedValueOnce(error);

    await expect(
      values.controller.respondStateReset(
        stateResetRequired("interaction_reset"),
        "reset_state",
      ),
    ).rejects.toBe(error);
  });

  it("merges an exact Thread selection into the current ready result", async () => {
    const values = harness();
    const current = ready();
    const selected = readyThread("thread_selected");
    values.selectThread.mockResolvedValueOnce(selected);

    const result = await values.controller.selectThread(
      current,
      "thread_selected",
    );

    expect(values.selectThread).toHaveBeenCalledWith(
      values.surface,
      "thread_selected",
    );
    expect(result).toEqual({ ...current, thread: selected });
    expect(result.application).toBe(current.application);
    expect(result.readiness).toBe(current.readiness);
  });
});

function harness() {
  const surface = {} as ConnectedSurface;
  const respondTrust =
    vi.fn<
      (
        surface: ConnectedSurface,
        intent: { readonly kind: "new" },
        interactionId: string,
        decision: "trust" | "deny",
      ) => Promise<StartupResult>
    >();
  const respondStateReset =
    vi.fn<
      (
        surface: ConnectedSurface,
        intent: { readonly kind: "new" },
        interactionId: string,
        decision: "reset_state" | "deny",
      ) => Promise<StartupResult>
    >();
  const selectThread =
    vi.fn<
      (
        surface: ConnectedSurface,
        threadId: string,
      ) => Promise<StartupThreadResult>
    >();
  return {
    surface,
    respondTrust,
    respondStateReset,
    selectThread,
    controller: new StartupSessionController(
      surface,
      { kind: "new" },
      { respondTrust, respondStateReset, selectThread },
    ),
  };
}

function trustRequired(interactionId: string) {
  return {
    kind: "trust_required",
    interactionId,
    workspacePath: "E:\\projects\\awesome",
  } as const;
}

function stateResetRequired(interactionId: string) {
  return { kind: "state_reset_required", interactionId } as const;
}

function ready(): Extract<StartupResult, { readonly kind: "ready" }> {
  return {
    kind: "ready",
    readiness: "agent_ready",
    application: applicationState("thread_current"),
    thread: readyThread("thread_current"),
  };
}

function readyThread(threadId: string): StartupThreadResult {
  return {
    kind: "ready",
    application: applicationState(threadId),
    thread: {
      has_more: false,
      view: {
        thread: {
          id: threadId,
          workspace_key: "workspace_1",
          title: "Conversation",
          thinking_enabled: false,
          skill_mode: "auto",
          created_at: "2026-07-14T00:00:00Z",
          updated_at: "2026-07-14T00:00:00Z",
        },
        entries: [],
        turns: [],
        tool_activities: [],
      },
      change_sets: [],
    } as never,
  };
}

function applicationState(threadId: string) {
  return {
    current_thread_id: threadId,
    model_catalog: freshModelCatalog(),
    permission_mode: "request_approval",
    provider_credentials: {},
  } as never;
}
