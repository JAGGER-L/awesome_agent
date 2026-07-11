import type { ProductError } from "../protocol/base.js";
import type {
  MethodName,
  MethodParams,
  MethodValue,
} from "../protocol/methods.js";
import type { LaunchIntent } from "../cli/args.js";

interface StartupSurface {
  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<
    | { readonly ok: true; readonly value: MethodValue[Method] }
    | { readonly ok: false; readonly error: ProductError }
  >;
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

export async function runStartup(
  surface: StartupSurface,
  intent: LaunchIntent,
): Promise<StartupThreadResult> {
  switch (intent.kind) {
    case "new":
      return await createThread(surface);
    case "continue": {
      const page = await surface.request("thread.list", { limit: 1 });
      if (!page.ok) throw productFailure(page.error);
      const recent = page.value.threads[0];
      return recent
        ? await selectStartupThread(surface, recent.id)
        : await createThread(surface);
    }
    case "resume":
      return await selectStartupThread(surface, intent.threadId);
    case "resume-picker": {
      const result = await executeCommand(surface, { name: "resume" });
      if (result.selection) {
        return { kind: "selection_required", selection: result.selection };
      }
      if (
        Array.isArray(result.data.threads) &&
        result.data.threads.length === 0
      ) {
        return await createThread(surface);
      }
      return await hydrateCommandThread(surface, result);
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
