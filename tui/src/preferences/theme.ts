export type ThemePreference = "system" | "dark" | "light";
export type ColorCapability = "none" | "ansi16" | "ansi256" | "truecolor";

export interface SemanticThemeRoles {
  readonly brand: string;
  readonly primary: string;
  readonly secondary: string;
  readonly muted: string;
  readonly success: string;
  readonly warning: string;
  readonly danger: string;
  readonly border: string;
  readonly user: string;
  readonly assistant: string;
  readonly tool: string;
  readonly statusBackground?: string;
}

export interface Theme extends SemanticThemeRoles {
  readonly preference: ThemePreference;
  readonly colorEnabled: boolean;
  readonly logoRows: readonly (string | undefined)[];
}

type BrandRole =
  | "brand"
  | "primary"
  | "secondary"
  | "border"
  | "user"
  | "assistant"
  | "tool"
  | "muted";

type BrandPalette = { readonly [Role in BrandRole]: string } & {
  readonly logoRows: readonly string[];
};

const darkAurora = {
  logoRows: ["#D0F5E7", "#B0EADF", "#95DCDA", "#8BC9E5", "#96B5DF"],
  brand: "#A9EADC",
  primary: "#9BE4D6",
  secondary: "#8FC8E8",
  border: "#5EA9AA",
  user: "#B8EADF",
  tool: "#88C4E2",
  assistant: "#E5ECEF",
  muted: "#74838B",
} as const satisfies BrandPalette;

const lightAurora = {
  logoRows: ["#2B7A70", "#337F7D", "#3D7E8C", "#3C7290", "#4B6290"],
  brand: "#2B7A70",
  primary: "#26766B",
  secondary: "#326B8A",
  border: "#4B7C7B",
  user: "#256F66",
  tool: "#316B86",
  assistant: "#1B242B",
  muted: "#5B6870",
} as const satisfies BrandPalette;

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
  colorDepth?: number,
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
  if (colorDepth !== undefined) {
    if (colorDepth >= 24) return "truecolor";
    if (colorDepth >= 8) return "ansi256";
    return "ansi16";
  }
  if (environ.WT_SESSION) return "truecolor";
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
  const source = preference === "light" ? lightAurora : darkAurora;
  const logoRows =
    capability === "none"
      ? source.logoRows.map(() => undefined)
      : capability === "truecolor"
        ? source.logoRows
        : capability === "ansi256"
          ? source.logoRows.map(nearestXtermColor)
          : ansi16Rows;
  const light = preference === "light";
  const roles = resolveBrandRoles(source, capability, light);
  return {
    preference,
    colorEnabled: capability !== "none",
    logoRows,
    ...roles,
    ...(capability === "none"
      ? {}
      : {
          statusBackground:
            capability === "ansi16"
              ? "blackBright"
              : capability === "ansi256"
                ? nearestXtermColor(light ? "#D8EFEA" : "#183C3A")
                : light
                  ? "#D8EFEA"
                  : "#183C3A",
        }),
    success: "green",
    warning: "yellow",
    danger: "red",
  };
}

function resolveBrandRoles(
  source: BrandPalette,
  capability: ColorCapability,
  light: boolean,
): Pick<
  SemanticThemeRoles,
  | "brand"
  | "primary"
  | "secondary"
  | "border"
  | "user"
  | "assistant"
  | "tool"
  | "muted"
> {
  if (capability === "none") {
    return {
      brand: light ? "green" : "greenBright",
      primary: light ? "green" : "greenBright",
      secondary: light ? "cyan" : "cyanBright",
      border: light ? "green" : "greenBright",
      user: light ? "green" : "greenBright",
      assistant: light ? "black" : "white",
      tool: light ? "cyan" : "cyanBright",
      muted: "gray",
    };
  }
  if (capability === "ansi16") {
    return {
      brand: "greenBright",
      primary: "greenBright",
      secondary: "cyanBright",
      border: "green",
      user: "greenBright",
      assistant: light ? "black" : "white",
      tool: "cyanBright",
      muted: "gray",
    };
  }
  const map = capability === "ansi256" ? nearestXtermColor : identity;
  return {
    brand: map(source.brand),
    primary: map(source.primary),
    secondary: map(source.secondary),
    border: map(source.border),
    user: map(source.user),
    assistant: map(source.assistant),
    tool: map(source.tool),
    muted: map(source.muted),
  };
}

function identity(value: string): string {
  return value;
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
