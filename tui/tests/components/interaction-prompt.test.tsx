import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { InteractionPrompt } from "../../src/components/InteractionPrompt.js";
import type { SurfaceState } from "../../src/state/model.js";
import { createSurfaceStore } from "../../src/state/store.js";

const interaction = {
  interaction_id: "interaction_1",
  interaction_kind: "execute_boundary" as const,
  prompt: "Run outside boundary?",
  choices: ["allow_once", "deny"],
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
  it("renders the controlled Event prompt and selection", () => {
    const frame =
      render(
        <InteractionPrompt interaction={interaction} selected={1} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Run outside boundary?");
    expect(frame).toContain("› deny");
    expect(frame).not.toContain("always allow");
  });

  it("routes arrows and Enter through App to one interaction response", async () => {
    const respond = vi.fn(async () => undefined);
    const seed: SurfaceState = {
      connection: "ready",
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
      event_sequence: 1,
      warnings: [],
      pending_interaction: interaction,
    };
    const view = render(<App store={createSurfaceStore(seed)} width={60} />);
    expect(view.lastFrame()).toContain("Run outside boundary?");
    expect(view.lastFrame()).not.toContain("Message");
  });
});
