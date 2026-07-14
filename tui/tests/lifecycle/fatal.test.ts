import { describe, expect, it } from "vitest";

import { CoreSpawnError } from "../../src/core/errors.js";
import type { CoreExit } from "../../src/core/process.js";
import {
  boundedStderrLines,
  fatalExitCode,
  RenderFailure,
  toFatalState,
} from "../../src/lifecycle/fatal.js";
import {
  RpcProtocolError,
  RpcValidationError,
} from "../../src/protocol/client.js";
import { ProtocolDesynchronized } from "../../src/state/event-stream.js";
import { StartupProductError } from "../../src/surface/startup.js";

const session = (stderr = new Uint8Array()) => ({ stderrTail: () => stderr });

describe("toFatalState", () => {
  it("classifies a missing runtime with exit code 2", () => {
    const fatal = toFatalState(
      new CoreSpawnError("Unable to spawn Core executable: awesome-core"),
      session(),
    );
    expect(fatal).toEqual({
      kind: "runtime_missing",
      executable: "awesome-core",
    });
    if (!fatal) throw new Error("expected fatal state");
    expect(fatalExitCode(fatal)).toBe(2);
  });

  it.each([
    new RpcValidationError("Invalid JSON-RPC message"),
    new RpcProtocolError(-32_000, "Unknown or duplicate response ID"),
    new ProtocolDesynchronized("Event sequence is not contiguous"),
  ])("classifies protocol/frame/response/Event faults", (error) => {
    expect(toFatalState(error, session())).toEqual({
      kind: "protocol",
      message: error.message,
    });
  });

  it("preserves only the stable diagnostic code for an internal response", () => {
    expect(
      toFatalState(
        new RpcProtocolError(-32603, "Internal error", {
          diagnostic_code: "core_request_failed",
        }),
        session(),
      ),
    ).toEqual({
      kind: "protocol",
      message: "Internal error",
      diagnosticCode: "core_request_failed",
    });
  });

  it("classifies version incompatibility separately", () => {
    expect(
      toFatalState(
        {
          code: "protocol_version_incompatible",
          message: "Protocol version is incompatible.",
          retryable: false,
          data: {},
        },
        session(),
      ),
    ).toEqual({
      kind: "version_incompatible",
      message: "Protocol version is incompatible.",
    });
  });

  it("classifies incompatible state with its exact recovery facts", () => {
    const fatal = toFatalState(
      new StartupProductError({
        code: "state_schema_incompatible",
        message: "Awesome state is incompatible with this version.",
        retryable: false,
        data: {
          found_schema: 1,
          expected_schema: 2,
          state_directory: "E:\\awesome_agent\\.awesome-dev\\home\\state",
        },
      }),
      session(),
    );

    expect(fatal).toEqual({
      kind: "state_schema_incompatible",
      foundSchema: 1,
      expectedSchema: 2,
      stateDirectory: "E:\\awesome_agent\\.awesome-dev\\home\\state",
    });
    if (!fatal) throw new Error("expected fatal state");
    expect(fatalExitCode(fatal)).toBe(1);
  });

  it("treats malformed incompatible-state data as a protocol fault", () => {
    expect(
      toFatalState(
        {
          code: "state_schema_incompatible",
          message: "Invalid state data.",
          retryable: false,
          data: { found_schema: 1 },
        },
        session(),
      ),
    ).toEqual({
      kind: "protocol",
      message: "Invalid incompatible-state diagnostic payload.",
      diagnosticCode: "invalid_state_schema_diagnostic",
    });
  });

  it("does not turn expected ProductError into fatal state", () => {
    expect(
      toFatalState(
        {
          code: "operation_busy",
          message: "Operation busy.",
          retryable: true,
          data: {},
        },
        session(),
      ),
    ).toBeUndefined();
  });

  it("classifies abnormal Core exit with only the final twenty stderr lines", () => {
    const stderr = new TextEncoder().encode(
      Array.from({ length: 25 }, (_, index) => `safe-${index}`).join("\r\n"),
    );
    const exit: CoreExit = {
      code: 23,
      signal: null,
      shutdown_requested: false,
    };
    const fatal = toFatalState(exit, session(stderr));
    expect(fatal).toMatchObject({ kind: "core_exit", exit });
    if (fatal?.kind !== "core_exit") throw new Error("expected core exit");
    expect(fatal.stderrLines).toHaveLength(20);
    expect(fatal.stderrLines[0]).toBe("safe-5");
    expect(fatal.stderrLines.at(-1)).toBe("safe-24");
    expect(fatalExitCode(fatal)).toBe(1);
  });

  it("ignores an intentional clean shutdown", () => {
    expect(
      toFatalState(
        { code: 0, signal: null, shutdown_requested: true } satisfies CoreExit,
        session(),
      ),
    ).toBeUndefined();
  });

  it("classifies render and unknown errors without echoing sensitive text", () => {
    const sensitive = "PROMPT secret tool reasoning";
    expect(toFatalState(new RenderFailure(sensitive), session())).toEqual({
      kind: "render",
      message: "The terminal interface failed to render.",
    });
    expect(toFatalState(new Error(sensitive), session())).toEqual({
      kind: "render",
      message: "The terminal interface failed unexpectedly.",
    });
  });
});

describe("boundedStderrLines", () => {
  it("decodes incomplete UTF-8 with replacement and removes empty lines", () => {
    const bytes = Uint8Array.from([
      ...new TextEncoder().encode("one\n\n two\rthree\r\n"),
      0xf0,
      0x9f,
    ]);
    expect(boundedStderrLines(bytes)).toEqual(["one", "two", "three", "�"]);
  });
});
