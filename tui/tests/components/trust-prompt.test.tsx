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
    expect(frame).toContain("Is this a project you created or trust?");
    expect(frame).toContain("File changes and shell commands");
    expect(frame).toContain("1. Yes, I trust this folder");
    expect(frame).toContain("2. No, exit");
    expect(frame).toContain("Enter Confirm · Esc Exit");
    expect(frame).not.toContain("branch");
  });

  it("renders the selected trust decision without owning input", () => {
    const view = render(
      <TrustPrompt workspacePath="/workspace" selected={1} />,
    );
    view.stdin.write("\u001b[A\r");
    expect(view.lastFrame()).toContain("❯ 2. No, exit");
  });

  it("renders submitting and recoverable error feedback", () => {
    const frame =
      render(
        <TrustPrompt
          workspacePath="/workspace"
          selected={0}
          submitting
          message="Unable to save trust."
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Saving trust…");
    expect(frame).toContain("Unable to save trust.");
  });
});
