import { Text } from "ink";
import { render } from "ink-testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useElapsedTime } from "../../../src/components/activity/use-elapsed-time.js";

const startedAt = "2026-07-14T00:00:00.000Z";

function Probe({ active }: { readonly active: boolean }) {
  const elapsed = useElapsedTime({
    active,
    startedAt,
    durationMs: 750,
    refreshMs: 100,
  });
  return <Text>{elapsed}</Text>;
}

describe("useElapsedTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(startedAt));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses local wall time while active and terminal duration afterwards", async () => {
    const view = render(<Probe active />);
    expect(view.lastFrame()).toBe("0");
    expect(vi.getTimerCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(300);
    expect(view.lastFrame()).toBe("300");

    view.rerender(<Probe active={false} />);
    expect(view.lastFrame()).toBe("750");
    expect(vi.getTimerCount()).toBe(0);
    view.unmount();
    await vi.runOnlyPendingTimersAsync();
    expect(vi.getTimerCount()).toBe(0);
  });
});
