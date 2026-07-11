import { describe, expect, it, vi } from "vitest";

import { createClipboardAdapter } from "../../src/adapters/clipboard.js";

describe("createClipboardAdapter", () => {
  it("delegates exact content to clipboardy-compatible writer", async () => {
    const write = vi.fn(async () => undefined);
    const adapter = createClipboardAdapter({ write });
    await adapter.writeText("exact assistant answer");
    expect(write).toHaveBeenCalledWith("exact assistant answer");
  });

  it("propagates writer failure without fallback", async () => {
    const adapter = createClipboardAdapter({
      write: async () => {
        throw new Error("clipboard unavailable");
      },
    });
    await expect(adapter.writeText("answer")).rejects.toThrow(
      "clipboard unavailable",
    );
  });
});
