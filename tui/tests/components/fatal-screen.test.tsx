import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { FatalScreen } from "../../src/components/FatalScreen.js";
import type { FatalState } from "../../src/lifecycle/fatal.js";

const fatal: FatalState = {
  kind: "core_exit",
  exit: { code: 23, signal: null, shutdown_requested: false },
  stderrLines: Array.from({ length: 20 }, (_, index) => `safe-${index}`),
};

describe("FatalScreen", () => {
  it("renders category, exit, bounded lines, and actionable choices", () => {
    const frame = render(<FatalScreen fatal={fatal} />).lastFrame() ?? "";
    expect(frame).toContain("Core exited unexpectedly");
    expect(frame).toContain("Exit code 23");
    expect(frame).toContain("safe-0");
    expect(frame).toContain("safe-19");
    expect(frame).toContain("Reconnect");
    expect(frame).toContain("Quit");
    expect(frame).not.toContain("/details");
  });

  it("renders the selection owned by the root terminal controller", () => {
    const frame =
      render(<FatalScreen fatal={fatal} selected={1} />).lastFrame() ?? "";
    expect(frame).toContain("› Quit");
  });

  it("renders runtime/version categories as configuration failures", () => {
    const runtime = render(
      <FatalScreen
        fatal={{ kind: "runtime_missing", executable: "awesome-core" }}
      />,
    ).lastFrame();
    expect(runtime).toContain("awesome-core");
    expect(runtime).toContain("Exit code 2");
  });
});
