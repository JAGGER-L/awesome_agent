import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { Worked } from "../../src/components/transcript/Worked.js";
import { ThemeProvider } from "../../src/components/theme.js";
import { resolveTheme } from "../../src/preferences/theme.js";

describe("Worked", () => {
  it("uses a distinct local-duration status treatment with color", () => {
    const frame = render(
      <ThemeProvider value={resolveTheme("dark", "truecolor")}>
        <Worked durationMs={2200} />
      </ThemeProvider>,
    ).lastFrame();
    expect(frame).toContain("✻ Worked for 2.2 s");
  });

  it("uses an explicit text marker without color", () => {
    const frame = render(
      <ThemeProvider value={resolveTheme("dark", "none")}>
        <Worked durationMs={2200} />
      </ThemeProvider>,
    ).lastFrame();
    expect(frame).toContain("[Worked] 2.2 s");
  });
});
