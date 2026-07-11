import { existsSync } from "node:fs";
import { posix, win32 } from "node:path";

export class RuntimeCheckError extends Error {
  constructor(
    message: string,
    readonly exitCode: 2 = 2,
  ) {
    super(message);
    this.name = "RuntimeCheckError";
  }
}

export function assertSupportedNode(version: string): void {
  const match = /^v?(\d+)(?:\.|$)/u.exec(version);
  if (!match || Number(match[1]) < 22) {
    throw new RuntimeCheckError("awesome-tui requires Node.js 22 or newer.");
  }
}

export function assertInteractiveTerminal(
  stdinIsTTY: boolean,
  stdoutIsTTY: boolean,
): void {
  if (!stdinIsTTY || !stdoutIsTTY) {
    throw new RuntimeCheckError(
      "awesome-tui requires an interactive terminal for input and output.",
    );
  }
}

export function resolveCoreExecutable(
  environ: Readonly<Record<string, string | undefined>>,
  platform: NodeJS.Platform | string = process.platform,
  exists: (path: string) => boolean = existsSync,
): string {
  const windows = platform.startsWith("win");
  const paths = windows ? win32 : posix;
  const pathEntry = Object.entries(environ).find(
    ([key]) => key.toLowerCase() === "path",
  )?.[1];
  if (!pathEntry) return "awesome-core";
  const extensions = windows
    ? (environ.PATHEXT ?? ".COM;.EXE;.BAT;.CMD")
        .split(";")
        .filter(Boolean)
        .map((value) => value.toLowerCase())
    : [""];
  for (const directory of pathEntry.split(paths.delimiter).filter(Boolean)) {
    for (const extension of extensions) {
      const candidate = paths.join(directory, `awesome-core${extension}`);
      if (exists(candidate)) return candidate;
    }
  }
  return "awesome-core";
}
