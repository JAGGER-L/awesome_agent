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
