import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { App } from "../../src/app/App.js";
import type { SurfaceState } from "../../src/state/model.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
    }
  }
  throw last;
}

const interaction = {
  interaction_id: "interaction_exit",
  interaction_kind: "tool_approval" as const,
  prompt: "Do you want to edit src/main.py?",
  operation: "edit",
  target: "src/main.py",
  capability: "filesystem.write",
  choices: [
    { decision: "allow_once", label: "Yes" },
    { decision: "deny", label: "No" },
  ],
};

function exitStore(overrides: Partial<SurfaceState> = {}) {
  const store = createSurfaceStore({
    connection: "ready",
    thread_generation: 0,
    event_sequence: 0,
    warnings: [],
    committed_transcript: [
      {
        key: "assistant:previous",
        kind: "assistant",
        text: "Previous response remains visible.",
      },
    ],
    ...overrides,
  });
  store.dispatch({
    type: "transcript.command.submitted",
    submission_id: "command_11111111111111111111111111111111",
    text: "/quit",
    generation: 0,
  });
  return store;
}

describe("App exit presentation", () => {
  it("preserves history while removing Composer, notices, and command menu", async () => {
    const store = exitStore();
    const view = render(
      <App
        store={store}
        reportFatal={() => undefined}
        providerSetupRequired
        width={72}
      />,
    );

    expect(view.lastFrame()).toContain("Previous response remains visible.");
    expect(view.lastFrame()).toContain("❯ /quit");
    expect(view.lastFrame()).toContain("Message");
    expect(view.lastFrame()).toContain("Choose a model Provider");

    view.stdin.write("/");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Start a new thread"),
    );

    view.rerender(
      <App
        store={store}
        reportFatal={() => undefined}
        providerSetupRequired
        width={72}
        exiting
      />,
    );

    const frame = view.lastFrame() ?? "";
    expect(frame).toContain("Previous response remains visible.");
    expect(frame).toContain("❯ /quit");
    expect(frame).not.toContain("Message");
    expect(frame).not.toContain("Choose a model Provider");
    expect(frame).not.toContain("Start a new thread");
    expect(frame).not.toContain("Request approval");
  });

  it("removes an active approval prompt from the final frame", () => {
    const store = exitStore({ pending_interaction: interaction });
    const view = render(
      <App store={store} reportFatal={() => undefined} width={72} />,
    );

    expect(view.lastFrame()).toContain("Do you want to edit src/main.py?");

    view.rerender(
      <App store={store} reportFatal={() => undefined} width={72} exiting />,
    );

    const frame = view.lastFrame() ?? "";
    expect(frame).toContain("Previous response remains visible.");
    expect(frame).toContain("❯ /quit");
    expect(frame).not.toContain("Do you want to edit src/main.py?");
    expect(frame).not.toContain("Message");
    expect(frame).not.toContain("Request approval");
  });
});
