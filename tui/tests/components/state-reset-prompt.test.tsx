import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { StateResetPrompt } from "../../src/components/StateResetPrompt.js";

describe("StateResetPrompt", () => {
  it("explains the exact reset boundary without exposing a state path", () => {
    const frame = render(<StateResetPrompt selected={0} />).lastFrame() ?? "";

    expect(frame).toContain("Awesome needs to reset local state");
    expect(frame).toContain("This version uses a new local data format.");
    expect(frame).toContain("Conversations and threads");
    expect(frame).toContain("Workspace trust");
    expect(frame).toContain("Checkpoints and undo history");
    expect(frame).toContain("API keys and configuration");
    expect(frame).toContain("Skills");
    expect(frame).toContain("Local and Cloud Memory settings");
    expect(frame).toContain("Reset local state and continue");
    expect(frame).toContain("Exit");
    expect(frame).toContain("Enter confirm");
    expect(frame).toContain("Esc cancel");
    expect(frame).not.toContain("schema 6");
    expect(frame).not.toContain("\\state");
  });

  it("renders the current selection without owning terminal input", () => {
    const view = render(<StateResetPrompt selected={1} />);
    view.stdin.write("\u001b[A\r");

    expect(view.lastFrame()).toContain("Exit");
  });

  it("keeps a retryable failure on the same submitting surface", () => {
    const frame =
      render(
        <StateResetPrompt
          selected={0}
          submitting={false}
          message="Close other Awesome sessions before resetting local state."
        />,
      ).lastFrame() ?? "";

    expect(frame).toContain("Awesome needs to reset local state");
    expect(frame).toContain(
      "Close other Awesome sessions before resetting local state.",
    );
  });
});
