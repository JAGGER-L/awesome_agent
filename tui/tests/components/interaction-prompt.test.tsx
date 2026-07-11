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

describe("InteractionPrompt", () => {
  it("renders exact Event prompt and choices", () => {
    const frame =
      render(
        <InteractionPrompt interaction={interaction} onRespond={() => {}} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Run outside boundary?");
    expect(frame).toContain("allow_once");
    expect(frame).toContain("deny");
    expect(frame).not.toContain("always allow");
  });

  it("supports arrows/Enter and maps Esc to deny", async () => {
    const onRespond = vi.fn();
    const view = render(
      <InteractionPrompt interaction={interaction} onRespond={onRespond} />,
    );
    view.stdin.write("\u001b[B");
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    view.stdin.write("\r");
    expect(onRespond).toHaveBeenCalledWith("deny");
    view.stdin.write("\u001b");
    await new Promise<void>((resolve) => setTimeout(resolve, 25));
    expect(onRespond).toHaveBeenLastCalledWith("deny");
  });

  it("removes Composer input while interaction is active", () => {
    const seed: SurfaceState = {
      connection: "ready",
      event_sequence: 1,
      warnings: [],
      pending_interaction: interaction,
    };
    const view = render(
      <App
        store={createSurfaceStore(seed)}
        interactionResponder={{ respond: async () => undefined }}
        width={60}
      />,
    );
    expect(view.lastFrame()).toContain("Run outside boundary?");
    expect(view.lastFrame()).not.toContain("Message");
  });
});
