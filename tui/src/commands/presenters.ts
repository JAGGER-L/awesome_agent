import type { CommandPayload } from "../protocol/commands.js";

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

export function presentCommandPayload(
  payload: CommandPayload,
): CommandPresentation {
  const title = `/${commandFor(payload.kind)}`;
  switch (payload.kind) {
    case "notice":
      return lines(title, [{ label: "", value: payload.message }]);
    case "thread":
      return lines(title, [
        { label: "Thread", value: payload.thread_id },
        { label: "Title", value: payload.title },
      ]);
    case "context":
      return lines(title, [
        ...payload.categories.map((category) => ({
          label: humanize(category.name),
          value: formatTokenCount(category.estimated_tokens),
        })),
        { label: "Total", value: formatTokenCount(payload.total_tokens) },
        { label: "Budget", value: formatTokenCount(payload.budget_tokens) },
      ]);
    case "compact":
      return {
        kind: "progress",
        title,
        message: "Context compressed",
        tone: "info",
      };
    case "model":
      return lines(title, [{ label: "Model", value: payload.model }]);
    case "thinking":
      return lines(title, [
        { label: "Thinking", value: payload.enabled ? "On" : "Off" },
      ]);
    case "workspace":
      return lines(title, [{ label: "Workspace", value: payload.path }]);
    case "diff":
      return lines(title, [
        {
          label: payload.change_set_id ? "Change set" : "",
          value: payload.content || "No workspace changes",
        },
      ]);
    case "change":
      return lines(title, [
        { label: "Change set", value: payload.change_set_id },
        { label: "State", value: payload.lifecycle },
        { label: "Files", value: `${payload.restored_paths.length}` },
        ...(payload.warning
          ? [{ label: "Warning", value: payload.warning }]
          : []),
      ]);
    case "tools":
      return lines(
        title,
        payload.tools.length
          ? payload.tools.map((tool) => ({
              label: tool.name,
              value: tool.approval_required ? "Approval required" : "Enabled",
            }))
          : [{ label: "", value: "No tools available" }],
      );
    case "skills":
      return lines(title, [
        { label: "Active", value: payload.active_mode },
        ...payload.skills.map((skill) => ({
          label: skill.name,
          value: skill.description,
        })),
      ]);
    case "mcp":
      return lines(
        title,
        payload.servers.length
          ? payload.servers.map((server) => ({
              label: server.server_id,
              value: server.detail ?? humanize(server.state),
            }))
          : [{ label: "", value: "No MCP servers configured" }],
      );
    case "memory_status":
      return lines(title, [
        {
          label: "Local memory",
          value: payload.local_enabled ? "On" : "Off",
        },
        {
          label: "Cloud memory · Mem0",
          value: payload.cloud_enabled ? "On" : "Off",
        },
      ]);
    case "memory_document":
      return lines(
        title,
        payload.entries.length
          ? payload.entries.map((entry) => ({
              label: entry.id,
              value: entry.content,
            }))
          : [{ label: "", value: "No memories" }],
      );
    case "memory_search":
      return lines(
        title,
        payload.memories.length
          ? payload.memories.map((memory) => ({
              label: memory.scope,
              value: memory.content,
            }))
          : [{ label: "", value: "No memories found" }],
      );
    case "memory_mutation":
      return lines(title, [
        { label: "Provider", value: payload.provider },
        { label: "Status", value: humanize(payload.status) },
      ]);
    case "status": {
      const snapshot = payload.snapshot;
      return lines(title, [
        { label: "Version", value: snapshot.version },
        { label: "Workspace", value: snapshot.workspace_path },
        { label: "Thread", value: snapshot.thread_display_id },
        { label: "Model", value: snapshot.model_identity.effective_model },
        { label: "Permissions", value: humanize(snapshot.permission_mode) },
        {
          label: "Context",
          value: `${formatTokenCount(snapshot.context_used_tokens)} / ${formatTokenCount(snapshot.context_budget_tokens)}`,
        },
      ]);
    }
    case "usage":
      return lines(title, [
        {
          label: "Input tokens",
          value: formatTokenCount(payload.usage.input_tokens),
        },
        {
          label: "Output tokens",
          value: formatTokenCount(payload.usage.output_tokens),
        },
        {
          label: "Reasoning tokens",
          value: formatTokenCount(payload.usage.reasoning_tokens),
        },
        { label: "Model calls", value: `${payload.usage.model_calls}` },
        { label: "Tool calls", value: `${payload.usage.tool_calls}` },
        {
          label: "Active time",
          value: `${payload.usage.active_execution_seconds}s`,
        },
      ]);
    case "doctor":
      return lines(
        title,
        payload.checks.map((check) => ({
          label: check.name,
          value: check.detail ?? humanize(check.status),
        })),
      );
    case "config":
      return lines(title, [
        {
          label: "DeepSeek",
          value: credentialState(payload.credentials.deepseek),
        },
        { label: "Kimi", value: credentialState(payload.credentials.kimi) },
        { label: "Mem0", value: credentialState(payload.credentials.mem0) },
      ]);
    case "permissions":
      return lines(title, [
        { label: "Permissions", value: humanize(payload.mode) },
      ]);
    default:
      return assertNever(payload);
  }
}

function commandFor(kind: CommandPayload["kind"]): string {
  if (kind === "thread") return "new";
  if (kind.startsWith("memory_")) return "memory";
  if (kind === "change") return "undo";
  if (kind === "notice") return "command";
  return kind;
}

function lines(
  title: string,
  rows: readonly PresentationRow[],
): CommandPresentation {
  return { kind: "lines", title, rows, tone: "info" };
}

function credentialState(credential: {
  readonly selected_source?: "environment" | "awesome" | null | undefined;
  readonly environment_configured: boolean;
  readonly awesome_configured: boolean;
}): string {
  if (credential.selected_source === "environment") return "Environment";
  if (credential.selected_source === "awesome") return "Awesome API key";
  return "Not configured";
}

function humanize(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatUnit(value: number): string {
  return Number.isInteger(value) ? `${value}` : value.toFixed(1);
}

function assertNever(value: never): never {
  throw new Error(`Unhandled command payload: ${String(value)}`);
}
