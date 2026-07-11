import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { FatalScreen } from "../../src/components/FatalScreen.js";
import type { FatalState } from "../../src/lifecycle/fatal.js";

const fatal: FatalState = {
  kind: "core_exit",
  exit: { code: 23, signal: null, shutdown_requested: false },
  stderrLines: Array.from({ length: 20 }, (_, index) => `safe-${index}`),
};

describe("FatalScreen", () => {
  it("renders category, exit, bounded lines, and actionable choices", () => {
    const frame =
      render(
        <FatalScreen fatal={fatal} onReconnect={() => {}} onQuit={() => {}} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Core exited unexpectedly");
    expect(frame).toContain("Exit code 23");
    expect(frame).toContain("safe-0");
    expect(frame).toContain("safe-19");
    expect(frame).toContain("Reconnect");
    expect(frame).toContain("Quit");
    expect(frame).not.toContain("/details");
  });

  it("selects reconnect or quit once", async () => {
    const onReconnect = vi.fn();
    const onQuit = vi.fn();
    const view = render(
      <FatalScreen fatal={fatal} onReconnect={onReconnect} onQuit={onQuit} />,
    );
    view.stdin.write("\r");
    expect(onReconnect).toHaveBeenCalledOnce();
    view.stdin.write("\u001b[B");
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    view.stdin.write("\r");
    expect(onQuit).toHaveBeenCalledOnce();
  });

  it("renders runtime/version categories as configuration failures", () => {
    const runtime = render(
      <FatalScreen
        fatal={{ kind: "runtime_missing", executable: "awesome-core" }}
        onReconnect={() => {}}
        onQuit={() => {}}
      />,
    ).lastFrame();
    expect(runtime).toContain("awesome-core");
    expect(runtime).toContain("Exit code 2");
  });
});
