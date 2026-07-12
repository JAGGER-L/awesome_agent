import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { DiagnosticsReady } from "../../src/components/DiagnosticsReady.js";

describe("DiagnosticsReady", () => {
  it.each([
    ["deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"],
    ["kimi/kimi-k2.6", "MOONSHOT_API_KEY"],
  ])("shows exact guidance for %s without collecting secrets", (model, variable) => {
    const view = render(
      <DiagnosticsReady
        model={model}
        environmentVariable={variable}
        diagnostics={[]}
      />,
    );
    expect(view.lastFrame()).toContain(model);
    expect(view.lastFrame()).toContain(variable);
    for (const command of [
      "/config",
      "/doctor",
      "/model",
      "/workspace",
      "/help",
      "/quit",
    ]) {
      expect(view.lastFrame()).toContain(command);
    }
    expect(view.lastFrame()).not.toMatch(/enter|paste|input.*key/iu);
  });
});
