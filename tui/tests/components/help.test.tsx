import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { Help } from "../../src/components/Help.js";

describe("Help", () => {
  it("renders command groups as a controlled view", () => {
    const frame = render(<Help />).lastFrame() ?? "";
    expect(frame).toContain("Commands");
    expect(frame).toContain("/new");
    expect(frame).toContain("/help");
  });

  it("renders one command", () => {
    const frame = render(<Help command="status" />).lastFrame() ?? "";
    expect(frame).toContain("/status");
    expect(frame).toContain("Owner: application");
  });

  it("renders an unknown-command warning", () => {
    expect(render(<Help command="missing" />).lastFrame()).toContain(
      "No command named /missing.",
    );
  });
});
