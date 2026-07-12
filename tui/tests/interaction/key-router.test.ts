import { describe, expect, it } from "vitest";

import type { UiMode } from "../../src/interaction/model.js";
import {
  emptyTerminalKey,
  routeTerminalKey,
} from "../../src/interaction/key-router.js";
import { initialTerminalUiState } from "../../src/interaction/reducer.js";

function stateWithMode(mode: UiMode) {
  return { ...initialTerminalUiState(), mode };
}

describe("routeTerminalKey", () => {
  it("routes Enter only to an active approval", () => {
    const state = stateWithMode({
      kind: "approval",
      interaction: {
        interaction_id: "interaction_1",
        interaction_kind: "tool_approval",
        prompt: "Run command?",
        operation: "run",
        target: "pytest",
        capability: "shell.execute",
        choices: [
          { decision: "allow_once", label: "Yes" },
          { decision: "deny", label: "No" },
        ],
      },
      selected: 0,
      submitting: false,
    });

    expect(
      routeTerminalKey(state, "", { ...emptyTerminalKey(), return: true }),
    ).toEqual({ type: "selection.confirm" });
  });

  it("routes Escape to secret cancellation", () => {
    const state = stateWithMode({
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
    });

    expect(
      routeTerminalKey(state, "", { ...emptyTerminalKey(), escape: true }),
    ).toEqual({ type: "mode.cancel" });
  });

  it("routes command-menu Tab without submitting", () => {
    expect(
      routeTerminalKey(
        stateWithMode({ kind: "command_menu", selected: 0 }),
        "",
        { ...emptyTerminalKey(), tab: true },
      ),
    ).toEqual({ type: "command.complete" });
  });

  it("maps Composer editing and submission", () => {
    const state = initialTerminalUiState();
    expect(routeTerminalKey(state, "a", emptyTerminalKey())).toEqual({
      type: "composer.edit",
      action: { type: "insert", text: "a" },
    });
    expect(
      routeTerminalKey(state, "", {
        ...emptyTerminalKey(),
        return: true,
      }),
    ).toEqual({ type: "composer.submit" });
  });

  it("falls through unconsumed control keys to lifecycle handling", () => {
    expect(
      routeTerminalKey(initialTerminalUiState(), "c", {
        ...emptyTerminalKey(),
        ctrl: true,
      }),
    ).toEqual({ type: "lifecycle.evaluate", input: "c" });
  });

  it("moves picker selection and respects blocking Escape", () => {
    const picker = stateWithMode({
      kind: "picker",
      owner: { kind: "thread" },
      selected: 0,
      blocking: true,
      selection: {
        prompt: "Choose thread",
        options: [{ value: "thread_1", label: "Thread", selected: true }],
      },
    });
    expect(
      routeTerminalKey(picker, "", {
        ...emptyTerminalKey(),
        downArrow: true,
      }),
    ).toEqual({ type: "selection.move", delta: 1 });
    expect(
      routeTerminalKey(picker, "", {
        ...emptyTerminalKey(),
        escape: true,
      }),
    ).toBeUndefined();
  });

  it("routes workspace Trust numbers, arrows, Enter, and Escape", () => {
    const trust = stateWithMode({
      kind: "workspace_trust",
      workspacePath: "E:\\workspace",
      selected: 0,
      submitting: false,
    });
    expect(routeTerminalKey(trust, "2", emptyTerminalKey())).toEqual({
      type: "selection.set",
      selected: 1,
    });
    expect(
      routeTerminalKey(trust, "", {
        ...emptyTerminalKey(),
        downArrow: true,
      }),
    ).toEqual({ type: "selection.move", delta: 1 });
    expect(
      routeTerminalKey(trust, "", {
        ...emptyTerminalKey(),
        return: true,
      }),
    ).toEqual({ type: "selection.confirm" });
    expect(
      routeTerminalKey(trust, "", {
        ...emptyTerminalKey(),
        escape: true,
      }),
    ).toEqual({ type: "trust.deny" });
  });
});
