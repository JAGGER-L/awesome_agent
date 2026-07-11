import { z } from "zod";

import {
  type ApplicationResult,
  boundedText,
  type JsonValue,
  jsonValueSchema,
  requestIdSchema,
  safeIntegerSchema,
} from "./base.js";
import { type EventEnvelope, eventEnvelopeSchema } from "./events.js";
import {
  type MethodName,
  type MethodParams,
  type MethodValue,
  methodSchemas,
} from "./methods.js";
import { encodeFrame, NdjsonDecoder, NdjsonError } from "./ndjson.js";

export interface LineTransport {
  readonly readable: AsyncIterable<Uint8Array>;
  write(bytes: Uint8Array): Promise<void>;
  close(): Promise<void>;
}

export interface RpcClient {
  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<ApplicationResult<MethodValue[Method]>>;
  events(): AsyncIterable<EventEnvelope>;
  close(reason: Error): Promise<void>;
}

export class RpcProtocolError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly data?: JsonValue,
  ) {
    super(message);
    this.name = "RpcProtocolError";
  }
}

export class RpcClosedError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "RpcClosedError";
  }
}

export class RpcValidationError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "RpcValidationError";
  }
}

const protocolErrorSchema = z.strictObject({
  code: safeIntegerSchema,
  message: boundedText(1, 2_000),
  data: jsonValueSchema.optional(),
});

const responseSchema = z.union([
  z.strictObject({
    jsonrpc: z.literal("2.0"),
    id: requestIdSchema,
    result: z.unknown(),
  }),
  z.strictObject({
    jsonrpc: z.literal("2.0"),
    id: requestIdSchema,
    error: protocolErrorSchema,
  }),
]);

const eventNotificationSchema = z.strictObject({
  jsonrpc: z.literal("2.0"),
  method: z.literal("event"),
  params: z.unknown(),
});

type PendingRequest = {
  readonly method: MethodName;
  readonly resolve: (value: ApplicationResult<unknown>) => void;
  readonly reject: (reason: Error) => void;
};

type EventWaiter = (result: IteratorResult<EventEnvelope>) => void;

class EventQueue implements AsyncIterable<EventEnvelope> {
  readonly #values: EventEnvelope[] = [];
  readonly #waiters: EventWaiter[] = [];
  #closed = false;

  push(value: EventEnvelope): void {
    if (this.#closed) return;
    const waiter = this.#waiters.shift();
    if (waiter) waiter({ done: false, value });
    else this.#values.push(value);
  }

  close(): void {
    if (this.#closed) return;
    this.#closed = true;
    for (const waiter of this.#waiters.splice(0)) {
      waiter({ done: true, value: undefined });
    }
  }

  [Symbol.asyncIterator](): AsyncIterator<EventEnvelope> {
    return {
      next: async () => {
        const value = this.#values.shift();
        if (value) return { done: false, value };
        if (this.#closed) return { done: true, value: undefined };
        return await new Promise<IteratorResult<EventEnvelope>>((resolve) =>
          this.#waiters.push(resolve),
        );
      },
    };
  }
}

class DefaultRpcClient implements RpcClient {
  readonly #pending = new Map<string | number, PendingRequest>();
  readonly #eventQueue = new EventQueue();
  readonly #decoder = new NdjsonDecoder();
  #nextRequestId = 1;
  #writeChain: Promise<void> = Promise.resolve();
  #closed = false;
  #closeReason: Error = new RpcClosedError("RPC client is closed");
  #closePromise: Promise<void> | undefined;

  constructor(readonly transport: LineTransport) {
    void this.#readLoop();
  }

  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<ApplicationResult<MethodValue[Method]>> {
    if (this.#closed) return Promise.reject(this.#closeReason);
    const parameterSchema = methodSchemas[method].params as z.ZodType;
    const parsedParams = parameterSchema.safeParse(params);
    if (!parsedParams.success) {
      return Promise.reject(
        new RpcValidationError(`Invalid parameters for ${method}`, {
          cause: parsedParams.error,
        }),
      );
    }
    if (!Number.isSafeInteger(this.#nextRequestId)) {
      return Promise.reject(
        new RpcClosedError("RPC request ID space is exhausted"),
      );
    }

    const id = this.#nextRequestId;
    this.#nextRequestId += 1;
    let frame: Uint8Array;
    try {
      frame = encodeFrame({
        jsonrpc: "2.0",
        id,
        method,
        params: parsedParams.data,
      });
    } catch (error) {
      return Promise.reject(
        new RpcValidationError(`Request frame for ${method} is invalid`, {
          cause: error,
        }),
      );
    }
    const result = new Promise<ApplicationResult<MethodValue[Method]>>(
      (resolve, reject) => {
        this.#pending.set(id, {
          method,
          resolve: (value) =>
            resolve(value as ApplicationResult<MethodValue[Method]>),
          reject,
        });
      },
    );
    this.#writeChain = this.#writeChain
      .then(async () => {
        if (!this.#closed) await this.transport.write(frame);
      })
      .catch(async (error: unknown) => {
        await this.#closeInternal(
          new RpcClosedError("RPC transport write failed", { cause: error }),
        );
      });
    return result;
  }

  events(): AsyncIterable<EventEnvelope> {
    return this.#eventQueue;
  }

  async close(reason: Error): Promise<void> {
    await this.#closeInternal(reason);
  }

  async #readLoop(): Promise<void> {
    try {
      for await (const chunk of this.transport.readable) {
        for (const value of this.#decoder.push(chunk))
          this.#handleMessage(value);
      }
      for (const value of this.#decoder.finish()) this.#handleMessage(value);
      if (!this.#closed)
        await this.#closeInternal(
          new RpcClosedError("RPC transport reached EOF"),
        );
    } catch (error) {
      if (this.#closed) return;
      const reason =
        error instanceof RpcProtocolError ||
        error instanceof RpcValidationError ||
        error instanceof RpcClosedError
          ? error
          : new RpcValidationError("Invalid RPC transport frame", {
              cause: error instanceof NdjsonError ? error : undefined,
            });
      await this.#closeInternal(reason);
    }
  }

  #handleMessage(value: unknown): void {
    const notification = eventNotificationSchema.safeParse(value);
    if (notification.success) {
      const event = eventEnvelopeSchema.safeParse(notification.data.params);
      if (!event.success) {
        throw new RpcValidationError("Invalid Event notification", {
          cause: event.error,
        });
      }
      this.#eventQueue.push(event.data);
      return;
    }

    const response = responseSchema.safeParse(value);
    if (!response.success) {
      throw new RpcValidationError("Invalid JSON-RPC message", {
        cause: response.error,
      });
    }
    const pending = this.#pending.get(response.data.id);
    if (!pending) {
      throw new RpcProtocolError(
        -32_000,
        `Unknown or duplicate response ID: ${response.data.id}`,
      );
    }
    if ("error" in response.data) {
      this.#pending.delete(response.data.id);
      pending.reject(
        new RpcProtocolError(
          response.data.error.code,
          response.data.error.message,
          response.data.error.data,
        ),
      );
      return;
    }

    const resultSchema = methodSchemas[pending.method].result as z.ZodType<
      ApplicationResult<unknown>
    >;
    const result = resultSchema.safeParse(response.data.result);
    if (!result.success) {
      throw new RpcValidationError(`Invalid result for ${pending.method}`, {
        cause: result.error,
      });
    }
    this.#pending.delete(response.data.id);
    pending.resolve(result.data);
  }

  #closeInternal(reason: Error): Promise<void> {
    if (this.#closePromise) return this.#closePromise;
    this.#closed = true;
    this.#closeReason = reason;
    for (const pending of this.#pending.values()) pending.reject(reason);
    this.#pending.clear();
    this.#eventQueue.close();
    this.#closePromise = this.transport.close().catch(() => undefined);
    return this.#closePromise;
  }
}

export function createRpcClient(transport: LineTransport): RpcClient {
  return new DefaultRpcClient(transport);
}
