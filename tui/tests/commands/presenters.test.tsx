import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import {
  formatTokenCount,
  presentCommandPayload,
} from "../../src/commands/presenters.js";
import { CommandResultView } from "../../src/components/CommandResultView.js";

describe("command presenters", () => {
  it("renders typed context categories without object coercion", () => {
    const presentation = presentCommandPayload("context", {
      kind: "context",
      categories: [
        { name: "instructions", estimated_tokens: 1_024 },
        { name: "conversation", estimated_tokens: 18_204 },
        { name: "files", estimated_tokens: 0 },
        { name: "memory", estimated_tokens: 0 },
      ],
      total_tokens: 19_228,
      budget_tokens: 262_144,
    });
    const frame = render(
      <CommandResultView presentation={presentation} width={100} />,
    ).lastFrame();
    expect(frame).toContain("Instructions");
    expect(frame).toContain("1K");
    expect(frame).toContain("Conversation");
    expect(frame).not.toContain("[object Object]");
  });

  it("renders one typed tool per row and an explicit empty diff", () => {
    const tools = presentCommandPayload("tools", {
      kind: "tools",
      permission_mode: "request_approval",
      tools: [
        {
          name: "read_file",
          description: "Read a file",
          read_only: true,
          approval_required: false,
        },
        {
          name: "execute",
          description: "Run shell commands",
          read_only: false,
          approval_required: true,
        },
      ],
    });
    const frame = render(
      <CommandResultView presentation={tools} width={100} />,
    ).lastFrame();
    expect(frame).toContain("read_file");
    expect(frame).toContain("Enabled");
    expect(frame).toContain("execute");
    expect(frame).toContain("Approval required");
    expect(
      presentCommandPayload("diff", { kind: "diff", content: "" }),
    ).toMatchObject({
      kind: "empty",
      message: "No workspace changes",
    });
  });

  it("uses binary token units", () => {
    expect(formatTokenCount(1_024)).toBe("1K");
    expect(formatTokenCount(262_144)).toBe("256K");
    expect(formatTokenCount(1_048_576)).toBe("1M");
  });
});
