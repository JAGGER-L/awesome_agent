import { describe, expect, it } from "vitest";

import {
  assertInteractiveTerminal,
  assertSupportedNode,
  RuntimeCheckError,
} from "../../src/cli/runtime-checks.js";

describe("runtime checks", () => {
  it.each(["22.0.0", "23.1.0", "v24.0.0"])("accepts Node %s", (version) => {
    expect(() => assertSupportedNode(version)).not.toThrow();
  });

  it.each(["20.19.0", "21.9.0", "unknown"])("rejects Node %s", (version) => {
    expect(() => assertSupportedNode(version)).toThrow(RuntimeCheckError);
  });

  it("requires both input and output TTY ownership", () => {
    expect(() => assertInteractiveTerminal(true, true)).not.toThrow();
    expect(() => assertInteractiveTerminal(false, true)).toThrow(
      RuntimeCheckError,
    );
    expect(() => assertInteractiveTerminal(true, false)).toThrow(
      RuntimeCheckError,
    );
  });
});
