import { describe, expect, it } from "vitest";

import {
  createRpcClient,
  RpcClosedError,
  RpcProtocolError,
  RpcValidationError,
} from "../../src/protocol/client.js";
import { MAX_FRAME_BYTES } from "../../src/protocol/ndjson.js";
import { FakeLineTransport } from "../helpers/fake-line-transport.js";

const success = (id: number, value: unknown) => ({
  jsonrpc: "2.0",
  id,
  result: { ok: true, value },
});

const warningEvent = {
  version: 1,
  event_id: "event_1",
  sequence: 1,
  session_id: "session_1",
  workspace_key: "workspace_1",
  event_type: "warning",
  timestamp: "2026-07-11T08:00:00Z",
  payload: { kind: "warning", code: "safe", message: "Safe warning." },
};

describe("RpcClient requests", () => {
  it("uses monotonic IDs and resolves concurrent calls out of order", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    const first = client.request("operation.cancel", {
      operation_id: "operation_1",
    });
    const second = client.request("operation.cancel", {
      operation_id: "operation_2",
    });
    const third = client.request("operation.cancel", {
      operation_id: "operation_3",
    });

    await expect(transport.nextClientMessage()).resolves.toMatchObject({
      id: 1,
    });
    await expect(transport.nextClientMessage()).resolves.toMatchObject({
      id: 2,
    });
    await expect(transport.nextClientMessage()).resolves.toMatchObject({
      id: 3,
    });
    transport.serverMessage(
      success(3, { operation_id: "operation_3", cancelled: true }),
    );
    transport.serverMessage(
      success(1, { operation_id: "operation_1", cancelled: true }),
    );
    transport.serverMessage(
      success(2, { operation_id: "operation_2", cancelled: false }),
    );

    await expect(first).resolves.toMatchObject({
      ok: true,
      value: { operation_id: "operation_1" },
    });
    await expect(second).resolves.toMatchObject({
      ok: true,
      value: { cancelled: false },
    });
    await expect(third).resolves.toMatchObject({
      ok: true,
      value: { operation_id: "operation_3" },
    });
    await client.close(new RpcClosedError("test complete"));
  });

  it("resolves product failures normally", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    const request = client.request("shutdown", {});
    await transport.nextClientMessage();
    transport.serverMessage({
      jsonrpc: "2.0",
      id: 1,
      result: {
        ok: false,
        error: {
          code: "internal_error",
          message: "Safe failure.",
          retryable: false,
          data: {},
        },
      },
    });
    await expect(request).resolves.toMatchObject({
      ok: false,
      error: { code: "internal_error" },
    });
    await client.close(new RpcClosedError("test complete"));
  });

  it("rejects one JSON-RPC error without closing other calls", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    const first = client.request("operation.cancel", {
      operation_id: "operation_1",
    });
    const second = client.request("operation.cancel", {
      operation_id: "operation_2",
    });
    await transport.nextClientMessage();
    await transport.nextClientMessage();
    transport.serverMessage({
      jsonrpc: "2.0",
      id: 1,
      error: { code: -32602, message: "Invalid params" },
    });
    transport.serverMessage(
      success(2, { operation_id: "operation_2", cancelled: true }),
    );

    await expect(first).rejects.toBeInstanceOf(RpcProtocolError);
    await expect(second).resolves.toMatchObject({ ok: true });
    await client.close(new RpcClosedError("test complete"));
  });

  it("closes on unknown and duplicate response IDs", async () => {
    const unknownTransport = new FakeLineTransport();
    const unknownClient = createRpcClient(unknownTransport);
    unknownTransport.serverMessage(success(99, { stopped: true }));
    await expect(unknownClient.request("shutdown", {})).rejects.toBeInstanceOf(
      RpcProtocolError,
    );

    const duplicateTransport = new FakeLineTransport();
    const duplicateClient = createRpcClient(duplicateTransport);
    const request = duplicateClient.request("shutdown", {});
    await duplicateTransport.nextClientMessage();
    duplicateTransport.serverMessage(success(1, { stopped: true }));
    await expect(request).resolves.toMatchObject({ ok: true });
    duplicateTransport.serverMessage(success(1, { stopped: true }));
    await expect(
      duplicateClient.request("shutdown", {}),
    ).rejects.toBeInstanceOf(RpcProtocolError);
  });

  it("closes on a method result schema mismatch", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    const request = client.request("shutdown", {});
    await transport.nextClientMessage();
    transport.serverMessage(success(1, { stopped: false }));
    await expect(request).rejects.toBeInstanceOf(RpcValidationError);
  });

  it("rejects pending calls on EOF and write failure", async () => {
    const eofTransport = new FakeLineTransport();
    const eofClient = createRpcClient(eofTransport);
    const pending = eofClient.request("shutdown", {});
    await eofTransport.nextClientMessage();
    eofTransport.eof();
    await expect(pending).rejects.toBeInstanceOf(RpcClosedError);

    const failedTransport = new FakeLineTransport();
    failedTransport.writeFailure = new Error("write failed");
    const failedClient = createRpcClient(failedTransport);
    await expect(failedClient.request("shutdown", {})).rejects.toBeInstanceOf(
      RpcClosedError,
    );
  });

  it("rejects an oversized request without leaking a pending call", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    await expect(
      client.request("command.execute", {
        name: "status",
        arguments: ["a".repeat(MAX_FRAME_BYTES)],
      }),
    ).rejects.toBeInstanceOf(RpcValidationError);

    const request = client.request("shutdown", {});
    await expect(transport.nextClientMessage()).resolves.toMatchObject({
      id: 2,
    });
    transport.serverMessage(success(2, { stopped: true }));
    await expect(request).resolves.toMatchObject({ ok: true });
    await client.close(new RpcClosedError("test complete"));
  });
});

describe("RpcClient events and close", () => {
  it("publishes validated Event notifications", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    const next = client.events()[Symbol.asyncIterator]().next();
    transport.serverMessage({
      jsonrpc: "2.0",
      method: "event",
      params: warningEvent,
    });
    await expect(next).resolves.toMatchObject({
      done: false,
      value: { event_type: "warning" },
    });
    await client.close(new RpcClosedError("test complete"));
  });

  it("closes on an invalid Event notification", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    transport.serverMessage({
      jsonrpc: "2.0",
      method: "event",
      params: { ...warningEvent, event_type: "assistant.text.delta" },
    });
    await expect(client.request("shutdown", {})).rejects.toBeInstanceOf(
      RpcValidationError,
    );
  });

  it("closes idempotently and completes the Event iterable once", async () => {
    const transport = new FakeLineTransport();
    const client = createRpcClient(transport);
    const iterator = client.events()[Symbol.asyncIterator]();
    await client.close(new RpcClosedError("closed"));
    await client.close(new RpcClosedError("closed again"));
    await expect(iterator.next()).resolves.toEqual({
      done: true,
      value: undefined,
    });
    expect(transport.closeCalls).toBe(1);
    await expect(client.request("shutdown", {})).rejects.toBeInstanceOf(
      RpcClosedError,
    );
  });
});
