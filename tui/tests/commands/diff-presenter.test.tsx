import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { presentCommandPayload } from "../../src/commands/presenters.js";
import { CommandResultView } from "../../src/components/CommandResultView.js";

describe("diff presentation", () => {
  it("renders an explicit empty state", () => {
    const presentation = presentCommandPayload("diff", {
      kind: "diff",
      content: "",
    });
    const frame =
      render(
        <CommandResultView presentation={presentation} width={80} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("No workspace changes");
    expect(frame).not.toContain("[object Object]");
  });

  it("renders a real bounded Diff with its ChangeSet identity", () => {
    const presentation = presentCommandPayload("diff", {
      kind: "diff",
      change_set_id: "change_123",
      content: "```diff\n--- a/a.py\n+++ b/a.py\n-old\n+new\n```",
    });
    const frame =
      render(
        <CommandResultView presentation={presentation} width={80} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("● Diff");
    expect(frame).toContain("change_123");
    expect(frame).toContain("--- a/a.py");
    expect(frame).toContain("+++ b/a.py");
    expect(frame).toContain("-old");
    expect(frame).toContain("+new");
  });
});
