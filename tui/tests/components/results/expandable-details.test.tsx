import { Text } from "ink";
import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { ExpandableDetails } from "../../../src/components/results/index.js";

describe("ExpandableDetails", () => {
  it("folds and expands one bounded detail body", () => {
    const collapsed =
      render(
        <ExpandableDetails expanded={false} summary={<Text>Summary</Text>}>
          <Text>detail</Text>
        </ExpandableDetails>,
      ).lastFrame() ?? "";
    expect(collapsed).toContain("Summary · Ctrl+O to expand");
    expect(collapsed).not.toContain("detail");
    const expanded =
      render(
        <ExpandableDetails expanded summary={<Text>Summary</Text>}>
          <Text>detail</Text>
        </ExpandableDetails>,
      ).lastFrame() ?? "";
    expect(expanded).toContain("Ctrl+O to collapse");
    expect(expanded).toContain("detail");
  });
});
