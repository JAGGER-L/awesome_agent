import { describe, expect, it } from "vitest";

import {
  initialTerminalUiState,
  terminalUiReducer,
} from "../../src/interaction/reducer.js";

describe("terminalUiReducer", () => {
  it("toggles one presentation-only tool detail state", () => {
    const initial = initialTerminalUiState();
    const expanded = terminalUiReducer(initial, {
      type: "details.toggle",
    });
    expect(expanded.detailsExpanded).toBe(true);
    expect(
      terminalUiReducer(expanded, { type: "details.toggle" }).detailsExpanded,
    ).toBe(false);
  });

  it("keeps exactly one active input owner", () => {
    const state = terminalUiReducer(initialTerminalUiState(), {
      type: "mode.open",
      mode: {
        kind: "picker",
        owner: { kind: "local_theme" },
        selected: 0,
        blocking: false,
        selection: {
          prompt: "Choose theme",
          options: [
            { value: "mint", label: "Mint", selected: true },
            { value: "mono", label: "Mono", selected: false },
          ],
        },
      },
    });

    expect(state.mode).toMatchObject({
      kind: "picker",
      owner: { kind: "local_theme" },
      selected: 0,
    });
  });

  it("keeps state reset submission and failure feedback in one modal", () => {
    const opened = terminalUiReducer(initialTerminalUiState(), {
      type: "mode.open",
      mode: { kind: "state_reset", selected: 0, submitting: false },
    });
    const moved = terminalUiReducer(opened, {
      type: "mode.select",
      delta: 1,
    });
    const submitting = terminalUiReducer(moved, {
      type: "mode.state_reset.submitting",
      submitting: true,
    });
    const failed = terminalUiReducer(
      terminalUiReducer(submitting, {
        type: "mode.state_reset.submitting",
        submitting: false,
      }),
      {
        type: "mode.state_reset.message",
        message: "State reset is busy.",
      },
    );

    expect(failed.mode).toEqual({
      kind: "state_reset",
      selected: 1,
      submitting: false,
      message: "State reset is busy.",
    });
  });

  it("restores the composer after cancelling a modal", () => {
    const opened = terminalUiReducer(initialTerminalUiState(), {
      type: "mode.open",
      mode: {
        kind: "secret",
        intent: { name: "auth" },
        prompt: {
          provider: "deepseek",
          action: "add",
          label: "DeepSeek API Key",
          environment_variable: "DEEPSEEK_API_KEY",
          help_url: "https://example.com",
        },
        value: "",
        submitting: false,
      },
    });

    expect(terminalUiReducer(opened, { type: "mode.cancel" }).mode).toEqual({
      kind: "composer",
    });
  });

  it("updates composer state without changing its input owner", () => {
    const state = terminalUiReducer(initialTerminalUiState(), {
      type: "composer.edit",
      action: { type: "insert", text: "hello" },
    });

    expect(state.mode).toEqual({ kind: "composer" });
    expect(state.composer.value).toBe("hello");
  });

  it("wraps selection across all commands and scrolls the viewport", () => {
    let state = terminalUiReducer(initialTerminalUiState(), {
      type: "composer.edit",
      action: { type: "insert", text: "/" },
    });
    for (let index = 0; index < 12; index += 1) {
      state = terminalUiReducer(state, { type: "mode.select", delta: 1 });
    }

    expect(state.mode).toMatchObject({
      kind: "command_menu",
      viewportStart: 3,
    });
  });
});
