import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const sourceRoot = resolve(import.meta.dirname, "../../src");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry);
    return statSync(path).isDirectory() ? sourceFiles(path) : [path];
  });
}

describe("terminal input ownership", () => {
  it("allows only TerminalInput to subscribe to Ink input", () => {
    const owners = sourceFiles(sourceRoot)
      .filter((path) => /\.(?:ts|tsx)$/u.test(path))
      .filter((path) => /\buseInput\b/u.test(readFileSync(path, "utf8")))
      .map((path) => relative(sourceRoot, path).replaceAll("\\", "/"));

    expect(owners).toEqual(["interaction/TerminalInput.tsx"]);
  });

  it("does not retain hidden input-blocking state", () => {
    const app = readFileSync(resolve(sourceRoot, "app/App.tsx"), "utf8");
    expect(app).not.toContain("commandInputBlocked");
    expect(app).not.toContain("CredentialFlow");
  });
});
