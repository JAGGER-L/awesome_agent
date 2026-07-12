import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { InteractionPrompt } from "../../src/components/InteractionPrompt.js";
import type { SurfaceState } from "../../src/state/model.js";
import { createSurfaceStore } from "../../src/state/store.js";

const interaction = {
  interaction_id: "interaction_1",
  interaction_kind: "tool_approval" as const,
  prompt: "Do you want to run pytest?",
  operation: "run",
  target: "pytest",
  capability: "shell.execute",
  choices: [
    { decision: "allow_once", label: "Yes" },
    { decision: "deny", label: "No" },
  ],
};

async function eventually(assertion: () => void): Promise<void> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  assertion();
}

describe("InteractionPrompt", () => {
  it("renders structured labels without exposing raw decisions", () => {
    const frame =
      render(
        <InteractionPrompt interaction={interaction} selected={1} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Do you want to run pytest?");
    expect(frame).toContain("No");
    expect(frame).not.toContain("allow_once");
  });

  it("routes arrows and Enter through App to one interaction response", async () => {
    const respond = vi.fn(async () => undefined);
    const seed: SurfaceState = {
      connection: "ready",
      thread_generation: 0,
      event_sequence: 1,
      warnings: [],
      pending_interaction: interaction,
    };
    const view = render(
      <App
        store={createSurfaceStore(seed)}
        interactionResponder={{ respond }}
        width={60}
      />,
    );
    view.stdin.write("\u001b[B");
    view.stdin.write("\r");
    await eventually(() => expect(respond).toHaveBeenCalledTimes(1));
    expect(respond).toHaveBeenCalledWith("deny");
  });

  it("enters submitting state before sending an Escape rejection", async () => {
    const respond = vi.fn(async () => await new Promise<void>(() => undefined));
    const seed: SurfaceState = {
      connection: "ready",
      thread_generation: 0,
      event_sequence: 1,
      warnings: [],
      pending_interaction: interaction,
    };
    const view = render(
      <App
        store={createSurfaceStore(seed)}
        interactionResponder={{ respond }}
        width={60}
      />,
    );
    view.stdin.write("\u001b");
    await eventually(() => expect(view.lastFrame()).toContain("Submitting…"));
    await eventually(() => expect(respond).toHaveBeenCalledOnce());
    expect(respond).toHaveBeenCalledWith("deny");
  });

  it("removes Composer input while interaction is active", () => {
    const seed: SurfaceState = {
      connection: "ready",
      thread_generation: 0,
      event_sequence: 1,
      warnings: [],
      pending_interaction: interaction,
    };
    const view = render(<App store={createSurfaceStore(seed)} width={60} />);
    expect(view.lastFrame()).toContain("Do you want to run pytest?");
    expect(view.lastFrame()).not.toContain("Message");
  });

  it("shows an RPC failure, permits retry, and restores Composer after resolution", async () => {
    const store = createSurfaceStore({
      connection: "ready",
      thread_generation: 0,
      event_sequence: 1,
      warnings: [],
      pending_interaction: interaction,
    });
    let attempts = 0;
    const respond = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("Approval request failed.");
    });
    const view = render(
      <App store={store} interactionResponder={{ respond }} width={60} />,
    );

    view.stdin.write("\r");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Approval request failed."),
    );
    view.stdin.write("\r");
    await eventually(() => expect(respond).toHaveBeenCalledTimes(2));

    store.dispatch({
      type: "event.received",
      generation: 0,
      event: {
        version: 1,
        event_id: "event_2",
        sequence: 2,
        session_id: "session_1",
        workspace_key: "workspace_1",
        thread_id: "thread_1",
        turn_id: undefined,
        operation_id: "operation_1",
        client_message_id: undefined,
        event_type: "interaction.resolved",
        timestamp: "2026-07-12T00:00:01Z",
        payload: {
          kind: "interaction.resolved",
          interaction_id: "interaction_1",
          decision: "allow_once",
        },
      },
    });
    await eventually(() => expect(view.lastFrame()).toContain("Message"));
    expect(view.lastFrame()).not.toContain("Do you want to run pytest?");
  });
});
