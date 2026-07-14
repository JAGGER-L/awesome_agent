import { CoreSpawnError } from "../core/errors.js";
import type { CoreExit, CoreSession } from "../core/process.js";
import { stateSchemaIncompatibleDataSchema } from "../protocol/base.js";
import { RpcProtocolError, RpcValidationError } from "../protocol/client.js";
import { ProtocolDesynchronized } from "../state/event-stream.js";

export type FatalState =
  | {
      readonly kind: "protocol";
      readonly message: string;
      readonly diagnosticCode?: string;
    }
  | {
      readonly kind: "core_exit";
      readonly exit: CoreExit;
      readonly stderrLines: readonly string[];
    }
  | { readonly kind: "render"; readonly message: string }
  | { readonly kind: "runtime_missing"; readonly executable: string }
  | { readonly kind: "version_incompatible"; readonly message: string }
  | {
      readonly kind: "state_schema_incompatible";
      readonly foundSchema: number;
      readonly expectedSchema: number;
      readonly stateDirectory: string;
    };

export class RenderFailure extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RenderFailure";
  }
}

export function toFatalState(
  error: unknown,
  session: Pick<CoreSession, "stderrTail">,
): FatalState | undefined {
  if (error instanceof CoreSpawnError) {
    return {
      kind: "runtime_missing",
      executable: executableFromSpawnError(error),
    };
  }
  if (isProductError(error)) {
    if (error.code === "state_schema_incompatible") {
      const data = stateSchemaIncompatibleDataSchema.safeParse(error.data);
      if (!data.success) {
        return {
          kind: "protocol",
          message: "Invalid incompatible-state diagnostic payload.",
          diagnosticCode: "invalid_state_schema_diagnostic",
        };
      }
      return {
        kind: "state_schema_incompatible",
        foundSchema: data.data.found_schema,
        expectedSchema: data.data.expected_schema,
        stateDirectory: data.data.state_directory,
      };
    }
    return error.code === "protocol_version_incompatible" ||
      error.code === "client_version_incompatible"
      ? { kind: "version_incompatible", message: error.message }
      : undefined;
  }
  if (
    error instanceof RpcValidationError ||
    error instanceof RpcProtocolError ||
    error instanceof ProtocolDesynchronized
  ) {
    const diagnosticCode =
      error instanceof RpcProtocolError
        ? protocolDiagnosticCode(error.data)
        : undefined;
    return {
      kind: "protocol",
      message: error.message,
      ...(diagnosticCode === undefined ? {} : { diagnosticCode }),
    };
  }
  if (isCoreExit(error)) {
    if (error.shutdown_requested && error.code === 0) return undefined;
    return {
      kind: "core_exit",
      exit: error,
      stderrLines: boundedStderrLines(session.stderrTail()),
    };
  }
  return {
    kind: "render",
    message:
      error instanceof RenderFailure
        ? "The terminal interface failed to render."
        : "The terminal interface failed unexpectedly.",
  };
}

function protocolDiagnosticCode(value: unknown): string | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return undefined;
  }
  if (!("diagnostic_code" in value)) return undefined;
  return typeof value.diagnostic_code === "string"
    ? value.diagnostic_code
    : undefined;
}

export function boundedStderrLines(bytes: Uint8Array): readonly string[] {
  return new TextDecoder("utf-8", { fatal: false })
    .decode(bytes)
    .split(/\r\n|\n|\r/u)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .slice(-20);
}

export function fatalExitCode(fatal: FatalState): 1 | 2 {
  return fatal.kind === "runtime_missing" ||
    fatal.kind === "version_incompatible"
    ? 2
    : 1;
}

function executableFromSpawnError(error: CoreSpawnError): string {
  const prefix = "Unable to spawn Core executable:";
  return error.message.startsWith(prefix)
    ? error.message.slice(prefix.length).trim()
    : "awesome-core";
}

function isProductError(value: unknown): value is {
  code: string;
  message: string;
  retryable: boolean;
  data?: unknown;
} {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    typeof value.code === "string" &&
    "message" in value &&
    typeof value.message === "string" &&
    "retryable" in value &&
    typeof value.retryable === "boolean"
  );
}

function isCoreExit(value: unknown): value is CoreExit {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    (typeof value.code === "number" || value.code === null) &&
    "signal" in value &&
    "shutdown_requested" in value &&
    typeof value.shutdown_requested === "boolean"
  );
}
