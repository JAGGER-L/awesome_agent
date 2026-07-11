import type { ProductError } from "../protocol/base.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../protocol/methods.js";
import type { LaunchIntent } from "../cli/args.js";
import type { SurfaceStore } from "../state/index.js";
import { PRODUCT_VERSION } from "../version.js";

interface StartupSurface {
  readonly store?: Pick<SurfaceStore, "dispatch">;
  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<
    | { readonly ok: true; readonly value: MethodValue[Method] }
    | { readonly ok: false; readonly error: ProductError }
  >;
}

export const SAFE_DIAGNOSTIC_COMMANDS = [
  "config",
  "doctor",
  "model",
  "workspace",
  "help",
  "quit",
] as const;

export type StartupResult =
  | {
      readonly kind: "trust_required";
      readonly interactionId: string;
      readonly workspacePath: string;
    }
  | { readonly kind: "denied" }
  | {
      readonly kind: "ready";
      readonly readiness: "agent_ready" | "diagnostics_ready";
      readonly application: MethodValue["application.getState"];
      readonly thread: StartupThreadResult;
      readonly diagnostic?: StartupDiagnostic;
      readonly safeCommands?: typeof SAFE_DIAGNOSTIC_COMMANDS;
    };

export interface StartupDiagnostic {
  readonly code: "configuration_invalid" | "provider_not_configured";
  readonly model: string;
  readonly environmentVariable?: "DEEPSEEK_API_KEY" | "MOONSHOT_API_KEY";
  readonly messages: readonly string[];
}

type CommandSelection = NonNullable<
  MethodValue["command.execute"]["selection"]
>;

export type StartupThreadResult =
  | { readonly kind: "ready"; readonly thread: MethodValue["thread.read"] }
  | {
      readonly kind: "selection_required";
      readonly selection: CommandSelection;
    };

export class StartupError extends Error {
  constructor(
    message: string,
    readonly code: string,
  ) {
    super(message);
    this.name = "StartupError";
  }
}

export async function beginStartup(
  surface: StartupSurface,
  intent: LaunchIntent,
): Promise<StartupResult> {
  surface.store?.dispatch({ type: "connection.handshaking" });
  const initialized = await surface.request("initialize", {
    protocol_version: 1,
    client_name: "awesome-tui",
    client_version: PRODUCT_VERSION,
  });
  if (!initialized.ok) throw productFailure(initialized.error);
  if (initialized.value.status === "trust_required") {
    surface.store?.dispatch({ type: "handshake.trust_required" });
    if (!initialized.value.interaction_id) {
      throw new StartupError(
        "Trust response omitted its interaction identity",
        "interaction_missing",
      );
    }
    return {
      kind: "trust_required",
      interactionId: initialized.value.interaction_id,
      workspacePath: initialized.value.workspace.display_path,
    };
  }

  surface.store?.dispatch({ type: "handshake.ready" });
  const application = await surface.request("application.getState", {});
  if (!application.ok) throw productFailure(application.error);
  surface.store?.dispatch({
    type: "hydrate.application",
    application: application.value,
  });
  const thread = await runStartup(surface, intent);
  const diagnostic = startupDiagnostic(application.value);
  return {
    kind: "ready",
    readiness: diagnostic ? "diagnostics_ready" : "agent_ready",
    application: application.value,
    thread,
    ...(diagnostic
      ? { diagnostic, safeCommands: SAFE_DIAGNOSTIC_COMMANDS }
      : {}),
  };
}

export async function respondStartupTrust(
  surface: StartupSurface,
  intent: LaunchIntent,
  interactionId: string,
  decision: "trust" | "deny",
): Promise<StartupResult> {
  const response = await surface.request("interaction.respond", {
    interaction_id: interactionId,
    decision,
  });
  if (!response.ok) throw productFailure(response.error);
  if (!response.value.accepted) {
    throw new StartupError(
      response.value.error?.message || "Trust response was not accepted",
      response.value.error?.code || "interaction_rejected",
    );
  }
  if (decision === "deny") {
    const shutdown = await surface.request("shutdown", {});
    if (!shutdown.ok) throw productFailure(shutdown.error);
    return { kind: "denied" };
  }
  return await beginStartup(surface, intent);
}

export async function runStartup(
  surface: StartupSurface,
  intent: LaunchIntent,
): Promise<StartupThreadResult> {
  switch (intent.kind) {
    case "new": {
      const selected = await createThread(surface);
      hydrateSurface(surface, selected);
      return selected;
    }
    case "continue": {
      const page = await surface.request("thread.list", { limit: 1 });
      if (!page.ok) throw productFailure(page.error);
      const recent = page.value.threads[0];
      const selected = recent
        ? await selectStartupThread(surface, recent.id)
        : await createThread(surface);
      hydrateSurface(surface, selected);
      return selected;
    }
    case "resume": {
      const selected = await selectStartupThread(surface, intent.threadId);
      hydrateSurface(surface, selected);
      return selected;
    }
    case "resume-picker": {
      const result = await executeCommand(surface, { name: "resume" });
      if (result.selection) {
        return { kind: "selection_required", selection: result.selection };
      }
      if (
        Array.isArray(result.data.threads) &&
        result.data.threads.length === 0
      ) {
        const selected = await createThread(surface);
        hydrateSurface(surface, selected);
        return selected;
      }
      const selected = await hydrateCommandThread(surface, result);
      hydrateSurface(surface, selected);
      return selected;
    }
  }
}

export async function selectStartupThread(
  surface: StartupSurface,
  threadId: string,
): Promise<StartupThreadResult> {
  const result = await executeCommand(surface, {
    name: "resume",
    arguments: [threadId],
  });
  if (result.selection) {
    return { kind: "selection_required", selection: result.selection };
  }
  return await hydrateCommandThread(surface, result);
}

async function createThread(
  surface: StartupSurface,
): Promise<StartupThreadResult> {
  return await hydrateCommandThread(
    surface,
    await executeCommand(surface, { name: "new" }),
  );
}

async function executeCommand(
  surface: StartupSurface,
  params: MethodParams["command.execute"],
): Promise<MethodValue["command.execute"]> {
  const response = await surface.request("command.execute", params);
  if (!response.ok) throw productFailure(response.error);
  if (response.value.status === "error") {
    const code = response.value.data.error_code;
    throw new StartupError(
      response.value.content || "Startup command failed",
      typeof code === "string" ? code : "command_failed",
    );
  }
  return response.value;
}

async function hydrateCommandThread(
  surface: StartupSurface,
  result: MethodValue["command.execute"],
): Promise<StartupThreadResult> {
  const threadId = result.data.thread_id;
  if (typeof threadId !== "string" || threadId.length === 0) {
    throw new StartupError(
      "Startup command did not select a Thread",
      "thread_not_selected",
    );
  }
  const page = await surface.request("thread.read", {
    thread_id: threadId,
    limit: 50,
  });
  if (!page.ok) throw productFailure(page.error);
  return { kind: "ready", thread: page.value };
}

function productFailure(error: ProductError): StartupError {
  return new StartupError(error.message, error.code);
}

function hydrateSurface(
  surface: StartupSurface,
  result: StartupThreadResult,
): void {
  if (result.kind === "ready") {
    surface.store?.dispatch({ type: "hydrate.thread", thread: result.thread });
  }
}

function startupDiagnostic(
  application: MethodValue["application.getState"],
): StartupDiagnostic | undefined {
  const model = application.current_model ?? "";
  if (!application.configuration_valid) {
    return {
      code: "configuration_invalid",
      model,
      messages: application.configuration_diagnostics,
    };
  }
  if (
    model.startsWith("deepseek/") &&
    !application.secret_status.deepseek_api_key
  ) {
    return {
      code: "provider_not_configured",
      model,
      environmentVariable: "DEEPSEEK_API_KEY",
      messages: [],
    };
  }
  if (
    model.startsWith("kimi/") &&
    !application.secret_status.moonshot_api_key
  ) {
    return {
      code: "provider_not_configured",
      model,
      environmentVariable: "MOONSHOT_API_KEY",
      messages: [],
    };
  }
  return undefined;
}
