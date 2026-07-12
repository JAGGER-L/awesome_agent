import { describe, expect, it } from "vitest";

import { classifyTerminalActionError } from "../../src/interaction/action-errors.js";
import {
  RpcClosedError,
  RpcProtocolError,
  RpcValidationError,
} from "../../src/protocol/client.js";

describe("classifyTerminalActionError", () => {
  it("keeps a server internal response request-scoped", () => {
    expect(
      classifyTerminalActionError(
        new RpcProtocolError(-32603, "Internal error", {
          diagnostic_code: "core_request_failed",
        }),
      ),
    ).toEqual({
      kind: "request",
      code: "core_request_failed",
      message: "Awesome could not complete this request. You can retry.",
    });
  });

  it.each([
    new RpcClosedError("closed"),
    new RpcValidationError("invalid frame"),
    new RpcProtocolError(-32000, "Unknown response ID"),
  ])("escalates connection and protocol integrity failures", (error) => {
    expect(classifyTerminalActionError(error)).toEqual({
      kind: "fatal",
      error,
    });
  });

  it("escalates unknown rejected values without exposing their message", () => {
    const error = new Error("private details");
    expect(classifyTerminalActionError(error)).toEqual({
      kind: "fatal",
      error,
    });
  });
});
