import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { COMMAND_CATALOG } from "../../src/commands/catalog.js";
import {
  formatTokenCount,
  presentCommandResult,
} from "../../src/commands/presenters.js";
import { CommandResultView } from "../../src/components/CommandResultView.js";

describe("command presenters", () => {
  it("gives every public command a visible success, empty, and error result", () => {
    for (const { name } of COMMAND_CATALOG) {
      const success = presentCommandResult(name, {
        status: "success",
        content: "",
        data: {},
      });
      expect(JSON.stringify(success).length).toBeGreaterThan(20);
      const error = presentCommandResult(name, {
        status: "error",
        content: "Unavailable",
        data: { error_code: "unavailable" },
      });
      expect(error).toEqual({
        kind: "error",
        title: `/${name}`,
        message: "Unavailable",
      });
    }
  });

  it.each([
    80, 100, 120,
  ])("renders aligned usage rows at %i columns", (width) => {
    const presentation = presentCommandResult("usage", {
      status: "success",
      content: "",
      data: {
        input_tokens: 18_204,
        output_tokens: 1_024,
        total_tokens: 1_048_576,
      },
    });
    const frame = render(
      <CommandResultView presentation={presentation} width={width} />,
    ).lastFrame();
    expect(frame).toContain("Input tokens   17.8K");
    expect(frame).toContain("Output tokens  1K");
    expect(frame).toContain("Total tokens   1M");
  });

  it("uses binary token units", () => {
    expect(formatTokenCount(1_024)).toBe("1K");
    expect(formatTokenCount(262_144)).toBe("256K");
    expect(formatTokenCount(1_048_576)).toBe("1M");
  });

  it("renders one tool per row and explicit empty diff state", () => {
    const tools = presentCommandResult("tools", {
      status: "success",
      content: "",
      data: {
        tools: [
          { name: "read_file", description: "Read a file" },
          { name: "execute", description: "Run shell commands" },
        ],
      },
    });
    const frame = render(
      <CommandResultView presentation={tools} width={100} />,
    ).lastFrame();
    expect(frame).toContain("read_file  Read a file");
    expect(frame).toContain("execute    Run shell commands");

    expect(
      presentCommandResult("diff", {
        status: "success",
        content: "",
        data: {},
      }),
    ).toMatchObject({ rows: [{ value: "No workspace changes" }] });
  });
});
