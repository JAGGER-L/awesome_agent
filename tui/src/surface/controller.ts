import {
  type CoreLaunchOptions,
  type CoreSession,
  startCore,
} from "../core/index.js";
import {
  type ApplicationResult,
  type EventEnvelope,
  type MethodName,
  type MethodParams,
  type MethodValue,
  RpcClosedError,
  type ThreadRetryOperation,
} from "../protocol/index.js";
import {
  type BatchedEvent,
  DeltaBatcher,
  EventStreamGuard,
  ProtocolDesynchronized,
  type SurfaceStore,
  createSurfaceStore,
} from "../state/index.js";
import { projectLiveTurn } from "../transcript/live.js";
import { reconcileTerminalTurn } from "../transcript/reconcile.js";

export interface SurfaceConnectOptions extends CoreLaunchOptions {
  /** @internal Deterministic process seam for tests. */
  readonly startSession?: (options: CoreLaunchOptions) => Promise<CoreSession>;
}

export interface ConnectedSurface {
  readonly store: SurfaceStore;
  readonly session: CoreSession;
  request<Method extends MethodName>(
    method: Method,
    params: MethodParams[Method],
  ): Promise<ApplicationResult<MethodValue[Method]>>;
  activateThreadRetry?(
    operation: ThreadRetryOperation,
    generation: number,
  ): void;
  rejectThreadRetry?(message: string): never;
  close(): Promise<void>;
}

interface ThreadRetryGate {
  readonly events: EventEnvelope[];
  bytes: number;
  expected?: ThreadRetryOperation;
}

interface EventAcceptanceFailure {
  readonly code: "protocol_desynchronized" | "thread_retry_identity_mismatch";
  readonly fault: ProtocolDesynchronized;
}

const THREAD_RETRY_EVENT_LIMIT = 1_024;
const THREAD_RETRY_BYTE_LIMIT = 4 * 1_024 * 1_024;

function dispatchBatched(
  store: SurfaceStore,
  value: BatchedEvent,
  generation: number,
): void {
  if ("event_type" in value) {
    store.dispatch({ type: "event.received", event: value, generation });
  } else {
    store.dispatch({ type: "delta.received", delta: value, generation });
  }
}

export async function connectSurface(
  options: SurfaceConnectOptions,
): Promise<ConnectedSurface> {
  const store = createSurfaceStore();
  store.dispatch({ type: "connection.start" });
  const session = await (options.startSession ?? startCore)(options);
  const guard = new EventStreamGuard();
  const operationGenerations = new Map<string, number>();
  const eventGeneration = (value: BatchedEvent): number => {
    const current = store.getState().thread_generation;
    if ("event_type" in value && value.event_type === "operation.started") {
      if (value.operation_id)
        operationGenerations.set(value.operation_id, current);
      return current;
    }
    return value.operation_id
      ? (operationGenerations.get(value.operation_id) ?? current)
      : current;
  };
  const batcher = new DeltaBatcher(guard, (value) =>
    dispatchBatched(store, value, eventGeneration(value)),
  );
  let closed = false;
  let closePromise: Promise<void> | undefined;
  const reconciledTerminals = new Set<string>();
  const reconciliationTasks = new Set<Promise<void>>();
  let retryGate: ThreadRetryGate | undefined;
  let activeRetryBinding: ThreadRetryOperation | undefined;
  const retryEventEncoder = new TextEncoder();

  const reconcileTerminal = async (
    threadId: string,
    key: string,
    generation: number,
    operationId: string,
    turnId: string,
  ): Promise<void> => {
    if (reconciledTerminals.has(key)) return;
    reconciledTerminals.add(key);
    const live = projectLiveTurn(store.getState());
    const page = await session.rpc.request("thread.read", {
      thread_id: threadId,
      limit: 50,
    });
    const result = page.ok
      ? reconcileTerminalTurn(live, page.value)
      : {
          operation_id: operationId,
          turn_id: turnId,
          blocks: [
            ...live.blocks,
            {
              key: `reconcile:error:${key}`,
              kind: "error" as const,
              code: "thread_read_failed",
              message: page.error.message,
            },
          ],
        };
    store.dispatch({
      type: "transcript.reconciled",
      generation,
      operation_id: result.operation_id,
      turn_id: result.turn_id,
      blocks: result.blocks,
      ...(page.ok ? { thread: page.value } : {}),
    });
  };

  const ownReconciliation = (reconciliation: Promise<void>): void => {
    const task = reconciliation.catch(async (error: unknown) => {
      if (closed && error instanceof RpcClosedError) return;
      const fault =
        error instanceof Error
          ? error
          : new ProtocolDesynchronized(
              "Unknown terminal reconciliation failure",
            );
      store.dispatch({
        type: "protocol.fatal",
        code: "terminal_reconciliation_failed",
        message: fault.message,
      });
      await session.rpc.close(fault);
    });
    reconciliationTasks.add(task);
    void task.then(
      () => reconciliationTasks.delete(task),
      () => reconciliationTasks.delete(task),
    );
  };

  const failProtocol = (code: string, fault: ProtocolDesynchronized): void => {
    store.dispatch({ type: "protocol.fatal", code, message: fault.message });
    void session.rpc.close(fault);
  };

  const discardRetryGate = (gate: ThreadRetryGate): void => {
    gate.events.length = 0;
    gate.bytes = 0;
    if (retryGate === gate) retryGate = undefined;
  };

  const bufferRetryEvent = (
    gate: ThreadRetryGate,
    event: EventEnvelope,
  ): ProtocolDesynchronized | undefined => {
    const bytes = retryEventEncoder.encode(JSON.stringify(event)).byteLength;
    if (
      gate.events.length >= THREAD_RETRY_EVENT_LIMIT ||
      gate.bytes + bytes > THREAD_RETRY_BYTE_LIMIT
    ) {
      discardRetryGate(gate);
      return new ProtocolDesynchronized(
        "Thread retry Event buffer exceeded its bounded capacity",
      );
    }
    gate.events.push(event);
    gate.bytes += bytes;
    return undefined;
  };

  const acceptEvent = (
    event: EventEnvelope,
  ): EventAcceptanceFailure | undefined => {
    if (
      activeRetryBinding &&
      ((event.operation_id !== undefined &&
        event.operation_id !== activeRetryBinding.operation_id) ||
        (event.thread_id !== undefined &&
          event.thread_id !== activeRetryBinding.thread_id) ||
        (event.turn_id !== undefined &&
          event.turn_id !== activeRetryBinding.turn_id) ||
        (event.client_message_id !== undefined &&
          event.client_message_id !== activeRetryBinding.client_message_id))
    ) {
      return {
        code: "thread_retry_identity_mismatch",
        fault: new ProtocolDesynchronized(
          "Retry Event identity does not match its accepted Operation",
        ),
      };
    }
    const fault = batcher.accept(event);
    if (fault) return { code: "protocol_desynchronized", fault };
    if (
      event.event_type === "operation.completed" ||
      event.event_type === "operation.failed" ||
      event.event_type === "operation.cancelled"
    ) {
      const threadId =
        event.thread_id ?? store.getState().application?.current_thread_id;
      const turnId =
        event.turn_id ?? store.getState().active_operation?.turn?.id;
      if (threadId && event.operation_id && turnId) {
        const generation =
          operationGenerations.get(event.operation_id) ??
          store.getState().thread_generation;
        ownReconciliation(
          reconcileTerminal(
            threadId,
            `operation:${event.operation_id}`,
            generation,
            event.operation_id,
            turnId,
          ),
        );
        if (activeRetryBinding?.operation_id === event.operation_id) {
          activeRetryBinding = undefined;
        }
      }
    }
    return undefined;
  };

  const validateBufferedRetryEvents = (
    gate: ThreadRetryGate,
    expected: ThreadRetryOperation | undefined,
  ): ProtocolDesynchronized | undefined => {
    const currentThreadId = store.getState().application?.current_thread_id;
    for (const event of gate.events) {
      if (expected) {
        if (
          (event.operation_id !== undefined &&
            event.operation_id !== expected.operation_id) ||
          (event.thread_id !== undefined &&
            event.thread_id !== expected.thread_id) ||
          (event.turn_id !== undefined && event.turn_id !== expected.turn_id) ||
          (event.client_message_id !== undefined &&
            event.client_message_id !== expected.client_message_id)
        ) {
          return new ProtocolDesynchronized(
            "Buffered retry Event identity does not match its accepted Operation",
          );
        }
        continue;
      }
      if (
        event.thread_id !== undefined &&
        event.thread_id !== currentThreadId
      ) {
        return new ProtocolDesynchronized(
          "A rejected retry emitted an Event for another Thread",
        );
      }
    }
    return undefined;
  };

  const releaseRetryGate = (
    gate: ThreadRetryGate,
    generation: number,
    expected: ThreadRetryOperation | undefined,
  ): void => {
    if (retryGate !== gate) {
      throw new ProtocolDesynchronized(
        "Thread retry Event gate changed unexpectedly",
      );
    }
    const identityFault = validateBufferedRetryEvents(gate, expected);
    retryGate = undefined;
    const buffered = gate.events.splice(0, gate.events.length);
    gate.bytes = 0;
    if (identityFault) {
      failProtocol("thread_retry_identity_mismatch", identityFault);
      throw identityFault;
    }
    if (expected) {
      operationGenerations.set(expected.operation_id, generation);
      activeRetryBinding = expected;
    }
    for (const event of buffered) {
      const failure = acceptEvent(event);
      if (!failure) continue;
      activeRetryBinding = undefined;
      failProtocol(failure.code, failure.fault);
      throw failure.fault;
    }
  };

  const eventConsumer = (async () => {
    for await (const event of session.rpc.events()) {
      if (retryGate) {
        const fault = bufferRetryEvent(retryGate, event);
        if (fault) {
          failProtocol("thread_retry_buffer_limit", fault);
          await session.rpc.close(fault);
          break;
        }
        continue;
      }
      const failure = acceptEvent(event);
      if (failure) {
        failProtocol(failure.code, failure.fault);
        await session.rpc.close(failure.fault);
        break;
      }
    }
  })()
    .catch((error: unknown) => {
      const fault =
        error instanceof Error
          ? error
          : new ProtocolDesynchronized("Unknown Event consumer failure");
      store.dispatch({
        type: "protocol.fatal",
        code: "event_consumer_failed",
        message: fault.message,
      });
    })
    .finally(() => batcher.close());

  void session.exit.then((exit) =>
    store.dispatch({ type: "core.exited", exit }),
  );

  const close = (): Promise<void> => {
    if (closePromise) return closePromise;
    closed = true;
    if (retryGate) discardRetryGate(retryGate);
    activeRetryBinding = undefined;
    closePromise = (async () => {
      try {
        await session.requestShutdown();
      } catch {
        // The controller still closes its local transport; PR7 owns recovery UX.
      }
      await session.rpc.close(new RpcClosedError("Surface controller closed"));
      await eventConsumer;
      await Promise.all(reconciliationTasks);
      store.dispatch({ type: "surface.closed" });
    })();
    return closePromise;
  };

  return {
    store,
    session,
    async request(method, params) {
      if (closed)
        return Promise.reject(
          new RpcClosedError("Surface controller is closed"),
        );
      const retryRequested =
        method === "command.execute" &&
        typeof params === "object" &&
        params !== null &&
        "name" in params &&
        params.name === "retry";
      let ownedGate: ThreadRetryGate | undefined;
      if (retryRequested) {
        if (retryGate) {
          throw new ProtocolDesynchronized(
            "Only one Thread retry request may be in flight",
          );
        }
        ownedGate = { events: [], bytes: 0 };
        retryGate = ownedGate;
      }
      try {
        const response = await session.rpc.request(method, params);
        if (!ownedGate) return response;
        const value = response.ok ? response.value : undefined;
        const expected =
          value &&
          typeof value === "object" &&
          "kind" in value &&
          value.kind === "result" &&
          value.payload.kind === "thread_retry"
            ? value.payload.operation
            : undefined;
        if (expected) {
          if (retryGate !== ownedGate) {
            throw new ProtocolDesynchronized(
              "Thread retry Event gate changed before its response",
            );
          }
          ownedGate.expected = expected;
        } else {
          releaseRetryGate(
            ownedGate,
            store.getState().thread_generation,
            undefined,
          );
        }
        return response;
      } catch (error) {
        if (ownedGate && retryGate === ownedGate) {
          releaseRetryGate(
            ownedGate,
            store.getState().thread_generation,
            undefined,
          );
        }
        throw error;
      }
    },
    activateThreadRetry(operation, generation) {
      const gate = retryGate;
      if (!gate?.expected) {
        const fault = new ProtocolDesynchronized(
          "Thread retry response has no pending Event gate",
        );
        failProtocol("thread_retry_gate_missing", fault);
        throw fault;
      }
      if (
        gate.expected.operation_id !== operation.operation_id ||
        gate.expected.thread_id !== operation.thread_id ||
        gate.expected.turn_id !== operation.turn_id ||
        gate.expected.client_message_id !== operation.client_message_id ||
        store.getState().thread_generation !== generation ||
        store.getState().application?.current_thread_id !== operation.thread_id
      ) {
        const fault = new ProtocolDesynchronized(
          "Thread retry activation identity does not match the installed transition",
        );
        discardRetryGate(gate);
        failProtocol("thread_retry_identity_mismatch", fault);
        throw fault;
      }
      releaseRetryGate(gate, generation, operation);
    },
    rejectThreadRetry(message) {
      if (retryGate) discardRetryGate(retryGate);
      const fault = new ProtocolDesynchronized(message);
      failProtocol("thread_retry_transition_rejected", fault);
      throw fault;
    },
    close,
  };
}
