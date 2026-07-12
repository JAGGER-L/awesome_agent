import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import type { CommandController } from "../../src/commands/controller.js";
import {
  composerReducer,
  initialComposerState,
} from "../../src/composer/reducer.js";
import { CommandMenu } from "../../src/components/CommandMenu.js";
import { Composer } from "../../src/components/Composer.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  throw last;
}

function composerState(value: string, width: number) {
  return composerReducer(
    composerReducer(initialComposerState(), { type: "resize", width }),
    { type: "replace", value },
  );
}

function controllerReturning(outcome: unknown): CommandController {
  return {
    submit: vi.fn(async () => outcome),
  } as unknown as CommandController;
}

describe("Composer", () => {
  it.each([
    40, 60, 120,
  ])("renders controlled multiline input and a cursor at %i columns", (width) => {
    const view = render(
      <Composer state={composerState("first\nsecond", width)} />,
    );
    expect(view.lastFrame()).toContain("first");
    expect(view.lastFrame()).toContain("second");
    expect(view.lastFrame()).toContain("▌");
  });

  it("renders the cursor at the controlled grapheme position", () => {
    const state = composerReducer(composerState("a😀c", 40), { type: "left" });
    expect(render(<Composer state={state} />).lastFrame()).toContain("a😀▌c");
  });

  it("renders controlled submission and error states", () => {
    const frame =
      render(
        <Composer
          state={composerState("retry me", 40)}
          submitting
          message="busy"
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Sending…");
    expect(frame).toContain("retry me");
    expect(frame).toContain("busy");
  });
});

describe("CommandMenu", () => {
  it("renders controlled entries and highlights the selected command", () => {
    const commands = [
      {
        name: "thinking" as const,
        owner: "application" as const,
        usage: "/thinking [on|off]",
        description: "Show or choose thinking mode",
        examples: ["/thinking [on|off]"],
      },
      {
        name: "theme" as const,
        owner: "ink" as const,
        usage: "/theme [system|dark|light]",
        description: "Show or choose the color theme",
        examples: ["/theme [system|dark|light]"],
      },
    ];
    const view = render(
      <CommandMenu commands={commands} selectedCommand="theme" />,
    );
    expect(view.lastFrame()).toContain("/thinking");
    expect(view.lastFrame()).toContain("/theme");
    expect(view.lastFrame()).toContain("› /theme");
  });
});

describe("App composer integration", () => {
  it("routes pasted input and Enter through the root terminal owner", async () => {
    const controller = controllerReturning({
      kind: "accepted",
      operation: { operation_id: "operation_1", thread_id: "thread_1" },
    });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={40} />,
    );
    view.stdin.write("hello\nworld");
    await eventually(() => expect(view.lastFrame()).toContain("hello"));
    view.stdin.write("\r");
    await eventually(() => expect(controller.submit).toHaveBeenCalledOnce());
    await eventually(() => expect(view.lastFrame()).not.toContain("hello"));
  });

  it("retains a retryable draft after an immediate product error", async () => {
    const controller = controllerReturning({
      kind: "error",
      error: { code: "operation_busy", message: "busy", retryable: true },
    });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={40} />,
    );
    view.stdin.write("retry me");
    view.stdin.write("\r");
    await eventually(() => expect(view.lastFrame()).toContain("busy"));
    expect(view.lastFrame()).toContain("retry me");
  });

  it("selects slash commands with arrows and executes Enter exactly once", async () => {
    const controller = controllerReturning({
      kind: "result",
      result: { status: "success", content: "status ok", data: {} },
    });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={60} />,
    );

    view.stdin.write("/s");
    await eventually(() => expect(view.lastFrame()).toContain("/status"));
    view.stdin.write("\u001b[B");
    view.stdin.write("\u001b[B");
    view.stdin.write("\r");

    await eventually(() => expect(controller.submit).toHaveBeenCalledOnce());
    expect(controller.submit).toHaveBeenCalledWith(
      { kind: "command", intent: { name: "status" } },
      undefined,
    );
  });

  it("completes with Tab without execution and Esc keeps the draft", async () => {
    const controller = controllerReturning({ kind: "result" });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={60} />,
    );

    view.stdin.write("/th");
    view.stdin.write("\t");
    await eventually(() =>
      expect(view.lastFrame()).toContain("/thinking [on|off]"),
    );
    expect(controller.submit).not.toHaveBeenCalled();

    view.stdin.write("\u001b");
    expect(view.lastFrame()).toContain("/thinking [on|off]");
    expect(controller.submit).not.toHaveBeenCalled();
  });

  it("shows feedback for an unmatched slash command", async () => {
    const controller = controllerReturning({ kind: "result" });
    const view = render(
      <App store={createSurfaceStore()} controller={controller} width={60} />,
    );

    view.stdin.write("/definitely-not-a-command");
    view.stdin.write("\r");

    await eventually(() =>
      expect(view.lastFrame()).toContain("unknown_command"),
    );
    expect(controller.submit).not.toHaveBeenCalled();
  });

  it("atomically replaces the old projection after /new", async () => {
    const store = createSurfaceStore();
    store.dispatch({
      type: "transcript.command_result",
      generation: 0,
      block: {
        key: "old",
        kind: "command_result",
        command: "old",
        tone: "info",
        content: "old transcript",
      },
    });
    const resetThreadScope = vi.fn();
    const controller = {
      submit: vi.fn(async () => ({
        kind: "result",
        result: {
          status: "success",
          content: "",
          data: { thread_id: "thread_new" },
        },
      })),
      loadThreadReplacement: vi.fn(async () => ({
        kind: "replacement",
        application: { current_thread_id: "thread_new" },
        thread: {
          view: {
            thread: {
              id: "thread_new",
              workspace_key: "workspace_1",
              title: "New Thread",
              thinking_enabled: false,
              skill_mode: "auto",
              created_at: "2026-07-12T00:00:00Z",
              updated_at: "2026-07-12T00:00:00Z",
            },
            entries: [],
            turns: [],
            tool_activities: [],
          },
          change_sets: [],
          has_more: false,
        },
      })),
    } as unknown as CommandController;
    const view = render(
      <App
        store={store}
        controller={controller}
        lifecycle={{
          cancelActiveOperation: async () => undefined,
          requestExit: async () => undefined,
          resetThreadScope,
        }}
        width={60}
      />,
    );

    view.stdin.write("/new");
    view.stdin.write("\r");
    await eventually(() => expect(store.getState().thread_generation).toBe(1));

    expect(store.getState()).toMatchObject({
      application: { current_thread_id: "thread_new" },
      thread: { view: { thread: { id: "thread_new" } } },
      committed_transcript: [],
    });
    expect(JSON.stringify(store.getState())).not.toContain("old transcript");
    expect(resetThreadScope).toHaveBeenCalledOnce();
  });
});
