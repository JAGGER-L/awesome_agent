import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { TrustPrompt } from "../../src/components/TrustPrompt.js";

describe("TrustPrompt", () => {
  it("shows only the canonical path and explicit trust choices", () => {
    const frame =
      render(
        <TrustPrompt workspacePath={"E:\\projects\\awesome"} selected={0} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("E:\\projects\\awesome");
    expect(frame).toContain("Trust workspace");
    expect(frame).toContain("Deny and exit");
    expect(frame).not.toContain("branch");
  });

  it("renders the selected trust decision without owning input", () => {
    const view = render(
      <TrustPrompt workspacePath="/workspace" selected={1} />,
    );
    view.stdin.write("\u001b[A\r");
    expect(view.lastFrame()).toContain("› Deny and exit");
  });
});
