import { describe, expect, it } from "vitest";

import { createClientMessageId } from "../../src/transcript/identity.js";

describe("createClientMessageId", () => {
  it("creates unique UUID-backed protocol identities without separators", () => {
    const first = createClientMessageId();
    const second = createClientMessageId();

    expect(first).toMatch(/^client_[a-f0-9]{32}$/u);
    expect(second).toMatch(/^client_[a-f0-9]{32}$/u);
    expect(second).not.toBe(first);
  });
});
