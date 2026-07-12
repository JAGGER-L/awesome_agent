import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { TrustPrompt } from "../../src/components/TrustPrompt.js";

describe("TrustPrompt", () => {
  it("shows only the canonical path and explicit trust choices", () => {
    const view = render(
      <TrustPrompt
        workspacePath={"E:\\projects\\awesome"}
        onDecision={() => {}}
      />,
    );
    expect(view.lastFrame()).toContain("E:\\projects\\awesome");
    expect(view.lastFrame()).toContain("Trust workspace");
    expect(view.lastFrame()).toContain("Deny and exit");
    expect(view.lastFrame()).not.toContain("branch");
  });

  it("cannot be dismissed with Esc", async () => {
    const onDecision = vi.fn();
    const view = render(
      <TrustPrompt workspacePath="/workspace" onDecision={onDecision} />,
    );
    view.stdin.write("\u001b");
    await new Promise<void>((resolve) => setTimeout(resolve, 25));
    expect(onDecision).not.toHaveBeenCalled();
  });

  it("selects trust or denial explicitly", async () => {
    const onDecision = vi.fn();
    const view = render(
      <TrustPrompt workspacePath="/workspace" onDecision={onDecision} />,
    );
    view.stdin.write("\r");
    expect(onDecision).toHaveBeenCalledWith("trust");
    view.stdin.write("\u001b[B");
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    view.stdin.write("\r");
    expect(onDecision).toHaveBeenCalledWith("deny");
  });
});
