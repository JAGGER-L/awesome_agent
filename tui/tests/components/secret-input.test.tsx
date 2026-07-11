import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { SecretInput } from "../../src/components/SecretInput.js";

async function eventually(assertion: () => void): Promise<void> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  assertion();
}

describe("SecretInput", () => {
  it("masks input and clears it before submitting", async () => {
    const submitted = vi.fn();
    const secret = "deepseek-secret-never-render";
    const view = render(
      <SecretInput
        label="DeepSeek API Key"
        onSubmit={submitted}
        onCancel={() => undefined}
      />,
    );

    view.stdin.write(secret);
    await eventually(() => expect(view.lastFrame()).toContain("•"));
    expect(view.lastFrame()).not.toContain(secret);
    view.stdin.write("\r");

    await eventually(() => expect(submitted).toHaveBeenCalledWith(secret));
    expect(view.frames.join("\n")).not.toContain(secret);
  });

  it("cancels with Escape", async () => {
    const cancel = vi.fn();
    const view = render(
      <SecretInput
        label="Kimi API Key"
        onSubmit={() => undefined}
        onCancel={cancel}
      />,
    );
    view.stdin.write("\u001b");
    await eventually(() => expect(cancel).toHaveBeenCalledOnce());
  });
});
