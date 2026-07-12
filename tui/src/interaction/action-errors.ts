import {
  RpcClosedError,
  RpcProtocolError,
  RpcValidationError,
} from "../protocol/client.js";

export type TerminalActionFailure =
  | {
      readonly kind: "request";
      readonly code: string;
      readonly message: string;
    }
  | { readonly kind: "fatal"; readonly error: Error };

const retryMessage = "Awesome could not complete this request. You can retry.";

export function classifyTerminalActionError(
  error: unknown,
): TerminalActionFailure {
  if (error instanceof RpcClosedError || error instanceof RpcValidationError) {
    return { kind: "fatal", error };
  }
  if (error instanceof RpcProtocolError) {
    const diagnostic = diagnosticCode(error.data);
    if (error.code === -32603 && diagnostic === "core_request_failed") {
      return { kind: "request", code: diagnostic, message: retryMessage };
    }
    return { kind: "fatal", error };
  }
  return {
    kind: "fatal",
    error:
      error instanceof Error
        ? error
        : new Error("Unknown terminal action failure"),
  };
}

function diagnosticCode(value: unknown): string | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  if (!("diagnostic_code" in value)) return undefined;
  return typeof value.diagnostic_code === "string"
    ? value.diagnostic_code
    : undefined;
}
