import type { CommandName, CommandPayload } from "../protocol/commands.js";
import type { HelpResult } from "./help.js";

export interface PresentationRow {
  readonly label: string;
  readonly value: string;
  readonly status?: "normal" | "success" | "warning" | "danger";
}

export type CommandPresentation =
  | {
      readonly kind: "panel";
      readonly title: string;
      readonly rows: readonly PresentationRow[];
      readonly tone: "info" | "success" | "warning" | "danger";
    }
  | {
      readonly kind: "notice";
      readonly message: string;
      readonly tone: "info" | "success" | "warning";
    }
  | { readonly kind: "empty"; readonly title: string; readonly message: string }
  | {
      readonly kind: "progress";
      readonly message: string;
      readonly tone: "info" | "success" | "danger";
    }
  | {
      readonly kind: "markdown";
      readonly title: string;
      readonly source: string;
      readonly tone: "info" | "warning";
    }
  | {
      readonly kind: "diff";
      readonly title: "Diff";
      readonly changeSetId?: string;
      readonly source: string;
    }
  | {
      readonly kind: "error";
      readonly title: string;
      readonly message: string;
    }
  | {
      readonly kind: "change";
      readonly title: "Undo" | "Redo";
      readonly changeSetId: string;
      readonly lifecycle: string;
      readonly paths: readonly string[];
      readonly warning?: string;
    };

export function formatTokenCount(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1024 ** 2) return `${formatUnit(value / 1024 ** 2)}M`;
  if (absolute >= 1024) return `${formatUnit(value / 1024)}K`;
  return `${value}`;
}

export function presentHelpResult(result: HelpResult): CommandPresentation {
  return panel(
    "/help",
    result.rows.map((row) => ({
      label: row.usage,
      value: row.description,
    })),
  );
}

export function presentCommandPayload(
  command: CommandName,
  payload: CommandPayload,
): CommandPresentation {
  const title = `/${command}`;
  switch (payload.kind) {
    case "notice":
      return { kind: "notice", message: payload.message, tone: "info" };
    case "thread":
      return panel(title, [
        { label: "Thread", value: payload.thread_id },
        { label: "Title", value: payload.title },
      ]);
    case "context":
      return panel(title, [
        ...(["instructions", "conversation", "files", "memory"] as const).map(
          (name) => ({
            label: humanize(name),
            value: formatTokenCount(
              payload.categories.find((category) => category.name === name)
                ?.estimated_tokens ?? 0,
            ),
          }),
        ),
        { label: "Total", value: formatTokenCount(payload.total_tokens) },
        { label: "Budget", value: formatTokenCount(payload.budget_tokens) },
      ]);
    case "compact":
      return {
        kind: "progress",
        message: "Context compressed",
        tone: "success",
      };
    case "model":
      return {
        kind: "notice",
        message: `Model · ${payload.model}`,
        tone: "success",
      };
    case "thinking":
      return {
        kind: "notice",
        message: `Thinking · ${payload.enabled ? "On" : "Off"}`,
        tone: "info",
      };
    case "workspace":
      return { kind: "notice", message: payload.path, tone: "info" };
    case "diff":
      return payload.content
        ? {
            kind: "diff",
            title: "Diff",
            source: payload.content,
            ...(payload.change_set_id
              ? { changeSetId: payload.change_set_id }
              : {}),
          }
        : { kind: "empty", title, message: "No workspace changes" };
    case "change":
      return {
        kind: "change",
        title: payload.action === "undo" ? "Undo" : "Redo",
        changeSetId: payload.change_set_id,
        lifecycle: humanize(payload.lifecycle),
        paths: payload.restored_paths,
        ...(payload.warning ? { warning: payload.warning } : {}),
      };
    case "tools":
      return payload.tools.length === 0
        ? { kind: "empty", title, message: "No tools available" }
        : panel(
            title,
            payload.tools.map((tool) => ({
              label: tool.name,
              value: tool.approval_required ? "Approval required" : "Enabled",
              status: tool.approval_required ? "warning" : "success",
            })),
          );
    case "skills":
      return panel(title, [
        { label: "Active", value: payload.active_mode },
        ...payload.skills.map((skill) => ({
          label: skill.name,
          value: skill.description,
        })),
        ...payload.diagnostics.map((diagnostic) => ({
          label: diagnostic.name ?? diagnostic.code,
          value: diagnostic.message,
          status: "warning" as const,
        })),
      ]);
    case "mcp":
      return payload.servers.length === 0
        ? { kind: "empty", title, message: "No MCP servers configured" }
        : panel(
            title,
            payload.servers.map((server) => ({
              label: server.server_id,
              value: server.detail ?? humanize(server.state),
              status: server.state === "error" ? "danger" : "normal",
            })),
          );
    case "memory_status":
      return panel(title, [
        { label: "Local", value: payload.local_enabled ? "On" : "Off" },
        {
          label: "Cloud · Mem0",
          value: payload.cloud_enabled ? "On" : "Off",
        },
      ]);
    case "memory_document":
      return payload.entries.length === 0
        ? { kind: "empty", title, message: "No memories" }
        : panel(
            title,
            payload.entries.map((entry) => ({
              label: entry.id,
              value: entry.content,
            })),
          );
    case "memory_search":
      return payload.memories.length === 0
        ? { kind: "empty", title, message: "No memories found" }
        : panel(
            title,
            payload.memories.map((memory) => ({
              label: memory.scope,
              value: memory.content,
            })),
          );
    case "memory_mutation":
      return panel(title, [
        { label: "Provider", value: humanize(payload.provider) },
        { label: "Status", value: humanize(payload.status) },
      ]);
    case "status": {
      const snapshot = payload.snapshot;
      return panel(title, [
        { label: "Version", value: snapshot.version },
        { label: "Workspace", value: snapshot.workspace_path },
        {
          label: "Thread",
          value: `${snapshot.thread_title} · ${snapshot.thread_display_id}`,
        },
        { label: "Model", value: snapshot.model_identity.effective_model },
        { label: "Credentials", value: statusCredential(snapshot) },
        { label: "Permissions", value: humanize(snapshot.permission_mode) },
        {
          label: "Context",
          value: `${formatTokenCount(snapshot.context_used_tokens)} / ${formatTokenCount(snapshot.context_budget_tokens)}`,
        },
        { label: "Thinking", value: snapshot.thinking_enabled ? "On" : "Off" },
        { label: "Skill", value: humanize(snapshot.skill_mode) },
        {
          label: "Memory",
          value: `Local ${snapshot.local_memory_enabled ? "On" : "Off"} · Cloud Mem0 ${snapshot.mem0_enabled ? "On" : "Off"}`,
        },
        {
          label: "MCP",
          value: `${snapshot.mcp_ready} ready · ${snapshot.mcp_degraded} degraded`,
        },
        {
          label: "Operation",
          value:
            snapshot.operation_status === "idle"
              ? "Idle"
              : `Active · ${snapshot.operation_id ?? "Unknown"}`,
        },
        {
          label: "Changes",
          value:
            snapshot.changed_file_count === 0
              ? "None"
              : `${snapshot.changed_file_count} ${snapshot.changed_file_count === 1 ? "file" : "files"} modified`,
        },
      ]);
    }
    case "usage":
      return panel(title, [
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
        {
          label: "Cache read tokens",
          value: formatTokenCount(payload.usage.cache_read_tokens),
        },
        {
          label: "Cache write tokens",
          value: formatTokenCount(payload.usage.cache_write_tokens),
        },
        { label: "Model calls", value: `${payload.usage.model_calls}` },
        { label: "Tool calls", value: `${payload.usage.tool_calls}` },
        {
          label: "Provider retries",
          value: `${payload.usage.provider_retries}`,
        },
        { label: "Compressions", value: `${payload.usage.compressions}` },
        {
          label: "Active execution",
          value: formatDuration(payload.usage.active_execution_seconds),
        },
      ]);
    case "doctor":
      return panel(
        title,
        payload.checks.map((check) => ({
          label: check.name,
          value: doctorStatus(check.status),
          status:
            check.status === "error" || check.status === "invalid"
              ? "danger"
              : "normal",
        })),
      );
    case "config":
      return panel(title, [
        { label: "Sources", value: payload.sources.join(" → ") || "Defaults" },
        {
          label: "DeepSeek",
          value: credentialState(payload.credentials.deepseek),
        },
        { label: "Kimi", value: credentialState(payload.credentials.kimi) },
        { label: "Mem0", value: credentialState(payload.credentials.mem0) },
      ]);
    case "permissions":
      return {
        kind: "notice",
        message: `Permissions · ${humanize(payload.mode)}`,
        tone: "info",
      };
    default:
      return assertNever(payload);
  }
}

export function presentCommandError(
  command: CommandName,
  _code: string,
  message: string,
): CommandPresentation {
  return { kind: "error", title: `/${command}`, message };
}

function panel(
  title: string,
  rows: readonly PresentationRow[],
  tone: "info" | "success" | "warning" | "danger" = "info",
): CommandPresentation {
  return { kind: "panel", title, rows, tone };
}

function statusCredential(
  snapshot: Extract<CommandPayload, { kind: "status" }>["snapshot"],
): string {
  if (!snapshot.credential_source) return "Not configured";
  const source =
    snapshot.credential_source === "environment" ? "Environment" : "Awesome";
  return snapshot.credential_source_available
    ? source
    : `${source} · Unavailable`;
}

function credentialState(credential: {
  readonly selected_source?: "environment" | "awesome" | null | undefined;
  readonly environment_configured: boolean;
  readonly awesome_configured: boolean;
}): string {
  if (credential.selected_source === "environment") {
    return credential.environment_configured
      ? "Environment"
      : "Environment · Unavailable";
  }
  if (credential.selected_source === "awesome") {
    return credential.awesome_configured ? "Awesome" : "Awesome · Unavailable";
  }
  return "Not configured";
}

function doctorStatus(status: string): string {
  return status === "ok" ? "OK" : humanize(status);
}

function humanize(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatUnit(value: number): string {
  return Number.isInteger(value) ? `${value}` : value.toFixed(1);
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${formatUnit(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`;
}

function assertNever(value: never): never {
  throw new Error(`Unhandled command payload kind: ${String(value)}`);
}
