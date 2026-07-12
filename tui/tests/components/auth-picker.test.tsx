import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { AuthPicker } from "../../src/components/AuthPicker.js";

const services = {
  prompt: "Authentication",
  options: [
    {
      value: "deepseek",
      label: "DeepSeek",
      description: "Active · environment",
      selected: true,
    },
    {
      value: "kimi",
      label: "Kimi",
      description: "Not configured",
      selected: false,
    },
    {
      value: "mem0",
      label: "Mem0 Cloud",
      description: "Active · awesome",
      selected: false,
    },
  ],
};

describe("AuthPicker", () => {
  it.each([
    80, 100, 120,
  ])("renders the approved service hierarchy at %i columns", (width) => {
    const frame = render(
      <AuthPicker selection={services} selected={0} width={width} />,
    ).lastFrame();
    expect(frame).toContain("Model providers");
    expect(frame).toContain("DeepSeek · Active · environment");
    expect(frame).toContain("Memory providers");
    expect(frame).toContain("Mem0 Cloud · Active · awesome");
    expect(frame).toContain("Enter confirm · Esc cancel");
  });

  it("keeps an unavailable Environment source visible", () => {
    const frame = render(
      <AuthPicker
        selection={{
          prompt: "DeepSeek credential source",
          options: [
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
            },
          ],
        }}
        selected={0}
      />,
    ).lastFrame();
    expect(frame).toContain("Environment · Not detected");
    expect(frame).toContain("Awesome API key · Configured");
  });
});
