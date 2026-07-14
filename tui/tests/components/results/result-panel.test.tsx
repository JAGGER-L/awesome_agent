import { render } from "ink-testing-library";
import { Text } from "ink";
import { describe, expect, it } from "vitest";

import {
  EmptyResult,
  ResultPanel,
} from "../../../src/components/results/index.js";

describe("result panels", () => {
  it.each([
    80, 100, 120,
  ])("renders a titled rounded boundary at %i columns", (width) => {
    const frame =
      render(
        <ResultPanel title="/status" tone="info" width={width}>
          <Text>content</Text>
        </ResultPanel>,
      ).lastFrame() ?? "";
    expect(frame).toContain("╭");
    expect(frame).toContain("● /status");
    expect(frame).toContain("content");
    expect(frame).toContain("╯");
  });

  it("keeps an empty state explicit without relying on color", () => {
    const frame =
      render(
        <EmptyResult
          title="/mcp"
          message="No MCP servers configured"
          width={80}
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("○ No MCP servers configured");
  });
});
