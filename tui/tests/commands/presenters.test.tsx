import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import {
  formatTokenCount,
  presentCommandPayload,
} from "../../src/commands/presenters.js";
import { CommandResultView } from "../../src/components/CommandResultView.js";
import { terminalDisplayWidth } from "../../src/layout/width.js";

function renderedText(frame: string | undefined): string {
  return (frame ?? "")
    .replace(/[╭╮╰╯─│]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
}

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

  it("renders complete active and unavailable tool metadata", () => {
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
      unavailable_tools: [
        {
          name: "extension.lookup",
          description: "Look up extension records",
          read_only: true,
          reason_code: "extension_offline",
          reason: "The extension is offline.",
          hint: "Reconnect the extension and try again.",
        },
      ],
    });
    const frame = render(
      <CommandResultView presentation={tools} width={100} />,
    ).lastFrame();
    const text = renderedText(frame);
    expect(text).toContain("read_file");
    expect(text).toContain("Available · Read-only — Read a file");
    expect(text).toContain("execute");
    expect(text).toContain(
      "Approval required · May have side effects — Run shell commands",
    );
    expect(text).toContain("extension.lookup");
    expect(text).toContain(
      "Unavailable · Read-only — Look up extension records",
    );
    expect(text).toContain("Reason: The extension is offline.");
    expect(text).toContain("Hint: Reconnect the extension and try again.");
  });

  it("wraps complete unavailable tool metadata at narrow widths", () => {
    const tools = presentCommandPayload("tools", {
      kind: "tools",
      permission_mode: "request_approval",
      tools: [],
      unavailable_tools: [
        {
          name: "extension.lookup",
          description: "Look up 模型😀 records",
          read_only: false,
          reason_code: "extension_offline",
          reason: "扩展 is offline.",
          hint: "Reconnect 后 try again.",
        },
      ],
    });
    const frame = render(
      <CommandResultView presentation={tools} width={36} />,
    ).lastFrame();
    const text = renderedText(frame);

    expect(text).toContain("extension.lookup");
    expect(text).toContain("Look up 模型😀 records");
    expect(text).toContain("May have side effects");
    expect(text).toContain("Unavailable");
    expect(text).toContain("Reason: 扩展 is offline.");
    expect(text).toContain("Hint: Reconnect 后 try again.");
    expect(
      frame?.split("\n").every((line) => terminalDisplayWidth(line) <= 36),
    ).toBe(true);
  });

  it("renders an explicit empty diff", () => {
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

  it("renders deterministic export results and their change set", () => {
    const presentation = presentCommandPayload("export", {
      kind: "thread_export",
      thread_id: "thread_123",
      path: "reports/thread.md",
      format: "markdown",
      write_status: "updated",
      byte_count: 2_048,
      change_set_id: "change_123",
    });
    const frame = render(
      <CommandResultView presentation={presentation} width={100} />,
    ).lastFrame();
    expect(frame).toContain("reports/thread.md");
    expect(frame).toContain("Updated");
    expect(frame).toContain("2048");
    expect(frame).toContain("change_123");
  });
});
