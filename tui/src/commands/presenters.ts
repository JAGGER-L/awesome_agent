import type { CommandName, CommandResult } from "../protocol/commands.js";

export interface PresentationRow {
  readonly label: string;
  readonly value: string;
}

export type CommandPresentation =
  | {
      readonly kind: "lines";
      readonly title: string;
      readonly rows: readonly PresentationRow[];
      readonly tone: "info" | "warning" | "error";
    }
  | {
      readonly kind: "progress";
      readonly title: string;
      readonly message: string;
      readonly tone: "info" | "error";
    }
  | { readonly kind: "picker"; readonly title: string }
  | { readonly kind: "secret"; readonly title: string }
  | {
      readonly kind: "error";
      readonly title: string;
      readonly message: string;
    };

export function formatTokenCount(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1024 ** 2) return `${formatUnit(value / 1024 ** 2)}M`;
  if (absolute >= 1024) return `${formatUnit(value / 1024)}K`;
  return `${value}`;
}

export function presentCommandResult(
  command: CommandName,
  result: CommandResult,
): CommandPresentation {
  if (result.status === "error") {
    return {
      kind: "error",
      title: `/${command}`,
      message: result.content || errorMessage(result.data),
    };
  }
  if (result.secret_prompt) return { kind: "secret", title: `/${command}` };
  if (result.selection) return { kind: "picker", title: `/${command}` };
  if (command === "compact") {
    return {
      kind: "progress",
      title: "/compact",
      message: result.content || "Context compressed",
      tone: "info",
    };
  }
  return {
    kind: "lines",
    title: `/${command}`,
    rows: rowsFor(command, result),
    tone: result.status === "interaction_required" ? "warning" : "info",
  };
}

function rowsFor(
  command: CommandName,
  result: CommandResult,
): readonly PresentationRow[] {
  if (result.content) return [{ label: "", value: result.content }];
  const data = result.data;
  if (command === "tools") return objectList(data.tools, "name", "description");
  if (command === "skills")
    return objectList(data.effective, "name", "description");
  if (command === "mcp") return objectList(data.servers, "server_id", "state");
  if (command === "memory") {
    return [
      { label: "Local memory", value: enabledValue(data.local) },
      { label: "Cloud memory · Mem0", value: enabledValue(data.mem0) },
    ];
  }
  const entries = Object.entries(data);
  if (entries.length === 0)
    return [{ label: "", value: emptyMessage(command) }];
  return entries.map(([label, value]) => ({
    label: humanize(label),
    value: numericValue(command, label, value),
  }));
}

function objectList(
  value: unknown,
  labelKey: string,
  valueKey: string,
): readonly PresentationRow[] {
  if (!Array.isArray(value) || value.length === 0)
    return [{ label: "", value: "None configured" }];
  return value.map((item) => {
    const record = isRecord(item) ? item : {};
    return {
      label: String(record[labelKey] ?? "Unknown"),
      value: String(record[valueKey] ?? ""),
    };
  });
}

function numericValue(
  command: CommandName,
  label: string,
  value: unknown,
): string {
  if (
    typeof value === "number" &&
    (command === "context" || command === "usage") &&
    label.includes("token")
  ) {
    return formatTokenCount(value);
  }
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value === null) return "—";
  if (Array.isArray(value)) return value.join(", ");
  if (isRecord(value)) return JSON.stringify(value);
  return String(value);
}

function enabledValue(value: unknown): string {
  return isRecord(value) && value.enabled === true ? "On" : "Off";
}

function emptyMessage(command: CommandName): string {
  if (command === "diff") return "No workspace changes";
  if (command === "usage") return "No usage recorded yet";
  return "No information available";
}

function errorMessage(data: Readonly<Record<string, unknown>>): string {
  return typeof data.error_code === "string"
    ? humanize(data.error_code)
    : "Command failed";
}

function humanize(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatUnit(value: number): string {
  return Number.isInteger(value) ? `${value}` : value.toFixed(1);
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
