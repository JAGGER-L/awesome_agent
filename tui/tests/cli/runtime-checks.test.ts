import { describe, expect, it } from "vitest";

import {
  assertInteractiveTerminal,
  assertSupportedNode,
  resolveCoreExecutable,
  RuntimeCheckError,
} from "../../src/cli/runtime-checks.js";

describe("runtime checks", () => {
  it.each(["22.0.0", "23.1.0", "v24.0.0"])("accepts Node %s", (version) => {
    expect(() => assertSupportedNode(version)).not.toThrow();
  });

  it("resolves the first Windows PATH wrapper before a later executable", () => {
    const existing = new Set([
      "C:\\fixture\\awesome-core.cmd",
      "C:\\installed\\awesome-core.exe",
    ]);
    expect(
      resolveCoreExecutable(
        {
          Path: "C:\\fixture;C:\\installed",
          PATHEXT: ".EXE;.CMD",
        },
        "win32",
        (path) => existing.has(path),
      ),
    ).toBe("C:\\fixture\\awesome-core.cmd");
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
