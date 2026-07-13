import { render } from "ink-testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityLine } from "../../../src/components/activity/ActivityLine.js";
import { SHIMMER_INTERVAL_MS } from "../../../src/components/activity/ShimmerText.js";
import { ThemeProvider } from "../../../src/components/theme.js";
import { resolveTheme } from "../../../src/preferences/theme.js";

const startedAt = "2026-07-14T00:00:00.000Z";

describe("ActivityLine", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(startedAt));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses the confirmed 140ms shimmer cadence", () => {
    expect(SHIMMER_INTERVAL_MS).toBe(140);
  });

  it("updates active elapsed time and clears both local clocks on completion", async () => {
    const active = (
      <ThemeProvider value={resolveTheme("dark", "truecolor")}>
        <ActivityLine
          state="active"
          marker="✦"
          text="Working for"
          startedAt={startedAt}
          shimmer
        />
      </ThemeProvider>
    );
    const view = render(active);
    expect(view.lastFrame()).toContain("│ ✦ Working for 0.0 s");
    expect(vi.getTimerCount()).toBe(2);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(view.lastFrame()).toContain("│ ✦ Working for 1.0 s");

    view.rerender(
      <ThemeProvider value={resolveTheme("dark", "truecolor")}>
        <ActivityLine
          state="completed"
          marker="✻"
          text="Worked for"
          durationMs={1_250}
          shimmer={false}
        />
      </ThemeProvider>,
    );
    expect(view.lastFrame()).toContain("│ ✻ Worked for 1.3 s");
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["no color", resolveTheme("dark", "none"), true],
    ["reduced motion", resolveTheme("dark", "truecolor"), false],
  ] as const)("keeps %s output static without a shimmer timer", async (_name, theme, shimmer) => {
    const view = render(
      <ThemeProvider value={theme}>
        <ActivityLine
          state="active"
          marker="✦"
          text="Thinking for"
          startedAt={startedAt}
          shimmer={shimmer}
        />
      </ThemeProvider>,
    );
    expect(view.lastFrame()).toContain("│ ✦ Thinking for 0.0 s");
    expect(vi.getTimerCount()).toBe(1);
    view.unmount();
    await vi.runOnlyPendingTimersAsync();
    expect(vi.getTimerCount()).toBe(0);
  });
});
