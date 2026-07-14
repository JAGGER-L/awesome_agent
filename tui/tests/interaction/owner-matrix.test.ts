import { describe, expect, it } from "vitest";

import type { UiMode } from "../../src/interaction/model.js";
import {
  emptyTerminalKey,
  routeTerminalKey,
  type TerminalIntent,
} from "../../src/interaction/key-router.js";
import { initialTerminalUiState } from "../../src/interaction/reducer.js";

const selection = {
  prompt: "Choose",
  options: [{ value: "one", label: "One", selected: true }],
};

const modes: readonly [string, UiMode, TerminalIntent][] = [
  [
    "fatal recovery",
    { kind: "fatal", selected: 0 },
    { type: "selection.confirm" },
  ],
  [
    "workspace trust",
    {
      kind: "workspace_trust",
      workspacePath: "E:\\workspace",
      selected: 0,
      submitting: false,
    },
    { type: "selection.confirm" },
  ],
  [
    "approval",
    {
      kind: "approval",
      selected: 0,
      submitting: false,
      interaction: {
        interaction_id: "interaction_1",
        interaction_kind: "tool_approval",
        prompt: "Run?",
        operation: "run",
        target: "pytest",
        choices: [{ decision: "allow_once", label: "Yes" }],
      },
    },
    { type: "selection.confirm" },
  ],
  [
    "secret",
    {
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
    { type: "secret.submit" },
  ],
  [
    "picker",
    {
      kind: "picker",
      owner: { kind: "command", intent: { name: "memory" } },
      selection,
      selected: 0,
      blocking: false,
    },
    { type: "selection.confirm" },
  ],
  [
    "slash menu",
    {
      kind: "command_menu",
      query: "/",
      selectedCommand: "new",
      viewportStart: 0,
    },
    { type: "selection.confirm" },
  ],
  ["composer", { kind: "composer" }, { type: "composer.submit" }],
];

describe("terminal input ownership matrix", () => {
  it.each(
    modes,
  )("routes Enter to exactly one %s owner", (_name, mode, expected) => {
    const state = { ...initialTerminalUiState(), mode };
    expect(
      routeTerminalKey(state, "", { ...emptyTerminalKey(), return: true }),
    ).toEqual(expected);
  });

  it("keeps global detail and lifecycle keys behind the active owner", () => {
    const composer = initialTerminalUiState();
    expect(
      routeTerminalKey(composer, "o", { ...emptyTerminalKey(), ctrl: true }),
    ).toEqual({ type: "details.toggle" });
    expect(
      routeTerminalKey(composer, "c", { ...emptyTerminalKey(), ctrl: true }),
    ).toEqual({ type: "lifecycle.evaluate", input: "c" });
  });
});
