import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { SecretInput } from "../../src/components/SecretInput.js";

describe("SecretInput", () => {
  it("masks its controlled value", () => {
    const secret = "deepseek-secret-never-render";
    const frame =
      render(
        <SecretInput label="DeepSeek API Key" value={secret} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("•");
    expect(frame).not.toContain(secret);
  });

  it("renders controlled submitting and error feedback", () => {
    const frame =
      render(
        <SecretInput
          label="Kimi API Key"
          value=""
          submitting
          message="invalid"
        />,
      ).lastFrame() ?? "";
    expect(frame).toContain("Saving…");
    expect(frame).toContain("invalid");
  });
});
