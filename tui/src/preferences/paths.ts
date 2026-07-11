import { homedir } from "node:os";
import path from "node:path";

export interface AwesomeHomeInput {
  readonly environ?: Readonly<Record<string, string | undefined>>;
  readonly home?: string;
  readonly platform?: NodeJS.Platform | string;
}

export function resolveAwesomeHome({
  environ = process.env,
  home = homedir(),
  platform = process.platform,
}: AwesomeHomeInput = {}): string {
  const windows = platform.startsWith("win");
  const paths = windows ? path.win32 : path.posix;
  const override = environ.AWESOME_HOME;
  if (override !== undefined && override.trim().length > 0) {
    return expandHome(override, home, paths.sep, paths.join);
  }
  if (windows) {
    const base = environ.LOCALAPPDATA || paths.join(home, "AppData", "Local");
    return paths.join(base, "awesome-agent");
  }
  return paths.join(home, ".awesome-agent");
}

function expandHome(
  value: string,
  home: string,
  separator: string,
  join: (...parts: string[]) => string,
): string {
  if (value === "~") return home;
  if (
    value.startsWith(`~${separator}`) ||
    value.startsWith("~/") ||
    value.startsWith("~\\")
  ) {
    return join(home, value.slice(2));
  }
  return value;
}
