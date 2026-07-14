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
import type { ProductError } from "../../src/protocol/base.js";
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

  it("classifies state created by a newer version without offering reset", () => {
    const fatal = toFatalState(
      new StartupProductError({
        code: "state_created_by_newer_version",
        message:
          "Local state was created by a newer Awesome version. Upgrade Awesome to continue.",
        retryable: false,
        data: {
          found_schema: 8,
          expected_schema: 7,
          state_directory: "E:\\awesome_agent\\.awesome-dev\\home\\state",
        },
      }),
      session(),
    );

    expect(fatal).toEqual({
      kind: "version_incompatible",
      message:
        "Local state was created by a newer Awesome version. Upgrade Awesome to continue.",
    });
    if (!fatal) throw new Error("expected fatal state");
    expect(fatalExitCode(fatal)).toBe(2);
  });

  it.each([
    {
      code: "state_unknown",
      message: "Safe local state failure.",
      retryable: false,
      data: { state_directory: "E:\\state" },
    },
    {
      code: "state_unavailable",
      message: "Safe local state failure.",
      retryable: true,
      data: { state_directory: "E:\\state" },
    },
    {
      code: "state_reset_busy",
      message: "Safe local state failure.",
      retryable: true,
      data: { state_directory: "E:\\state" },
    },
    {
      code: "state_reset_failed",
      message: "Safe local state failure.",
      retryable: true,
      data: {
        diagnostic_code: "fresh_state_initialization_failed",
        state_directory: "E:\\state",
      },
    },
  ] satisfies readonly ProductError[])("keeps $code as a bounded startup failure", (error) => {
    expect(toFatalState(new StartupProductError(error), session())).toEqual({
      kind: "startup_state",
      message: "Safe local state failure.",
      diagnosticCode: error.code,
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
