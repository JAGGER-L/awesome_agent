import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { SelectionPanel } from "../../../src/components/interactions/index.js";

const options = [
  {
    value: "environment",
    label: "Environment",
    description: "Not detected",
    selected: false,
    disabled: true,
  },
  {
    value: "awesome",
    label: "Awesome API key",
    description: "Configured",
    selected: true,
    disabled: false,
  },
];

describe("SelectionPanel", () => {
  it.each([
    "neutral",
    "brand",
    "warning",
    "danger",
  ] as const)("renders the shared %s interaction structure", (variant) => {
    const frame =
      render(
        <SelectionPanel
          title="Choose source"
          options={options}
          selected={1}
          variant={variant}
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("╭");
    expect(frame).toContain("Choose source");
    expect(frame).toContain("› Awesome API key · Configured");
    expect(frame).toContain("Environment · Not detected");
    expect(frame).toContain("↑↓ select · Enter confirm · Esc cancel");
  });
});
