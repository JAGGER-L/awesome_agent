export type ThemePreference = "system" | "dark" | "light";
export type ColorCapability = "none" | "ansi16" | "ansi256" | "truecolor";

export interface Theme {
  readonly preference: ThemePreference;
  readonly colorEnabled: boolean;
  readonly logoRows: readonly (string | undefined)[];
  readonly accent: string;
  readonly assistant: string;
  readonly error: string;
  readonly muted: string;
  readonly user: string;
  readonly warning: string;
}

const darkMint = [
  "#A7F3D0",
  "#6EE7B7",
  "#34D399",
  "#2DD4BF",
  "#22D3EE",
] as const;
const lightMint = [
  "#047857",
  "#059669",
  "#0F766E",
  "#0E7490",
  "#155E75",
] as const;
const systemRows = [
  "greenBright",
  "green",
  "green",
  "cyan",
  "cyanBright",
] as const;
const ansi16Rows = [
  "greenBright",
  "greenBright",
  "green",
  "cyan",
  "cyanBright",
] as const;

export function detectColorCapability(
  environ: Readonly<Record<string, string | undefined>>,
  isTty: boolean,
): ColorCapability {
  if (environ.NO_COLOR !== undefined) return "none";
  const forced = environ.FORCE_COLOR;
  if (forced === "0") return "none";
  if (forced !== undefined) {
    if (forced === "3") return "truecolor";
    if (forced === "2") return "ansi256";
    return "ansi16";
  }
  if (!isTty) return "none";
  if (/^(?:truecolor|24bit)$/iu.test(environ.COLORTERM ?? "")) {
    return "truecolor";
  }
  if (/256color/iu.test(environ.TERM ?? "")) return "ansi256";
  return "ansi16";
}

export function resolveTheme(
  preference: ThemePreference,
  capability: ColorCapability,
): Theme {
  const source = preference === "light" ? lightMint : darkMint;
  const logoRows =
    capability === "none"
      ? source.map(() => undefined)
      : preference === "system"
        ? systemRows
        : capability === "truecolor"
          ? source
          : capability === "ansi256"
            ? source.map(nearestXtermColor)
            : ansi16Rows;
  const light = preference === "light";
  return {
    preference,
    colorEnabled: capability !== "none",
    logoRows,
    accent: light ? "cyan" : "cyanBright",
    assistant: light ? "black" : "white",
    error: "red",
    muted: "gray",
    user: "green",
    warning: "yellow",
  };
}

function nearestXtermColor(value: string): string {
  const channels = [1, 3, 5].map((offset) =>
    Number.parseInt(value.slice(offset, offset + 2), 16),
  );
  const levels = [0, 95, 135, 175, 215, 255];
  const mapped = channels.map((channel) =>
    levels.reduce((best, candidate) =>
      Math.abs(candidate - channel) < Math.abs(best - channel)
        ? candidate
        : best,
    ),
  );
  return `#${mapped.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}
