import { describe, expect, it } from "vitest";

import {
  initialTerminalUiState,
  terminalUiReducer,
} from "../../src/interaction/reducer.js";

describe("terminalUiReducer", () => {
  it("toggles one presentation-only tool detail state", () => {
    const initial = initialTerminalUiState();
    const expanded = terminalUiReducer(initial, {
      type: "tool_details.toggle",
    });
    expect(expanded.toolDetailsExpanded).toBe(true);
    expect(
      terminalUiReducer(expanded, { type: "tool_details.toggle" })
        .toolDetailsExpanded,
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
});
