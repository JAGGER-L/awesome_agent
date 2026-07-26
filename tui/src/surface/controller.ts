import {
  type CoreLaunchOptions,
  type CoreSession,
  startCore,
} from "../core/index.js";
import {
  type ApplicationResult,
  type MethodName,
  type MethodParams,
  type MethodValue,
  RpcClosedError,
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
  close(): Promise<void>;
}

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

  const eventConsumer = (async () => {
    for await (const event of session.rpc.events()) {
      const fault = batcher.accept(event);
      if (fault) {
        store.dispatch({
          type: "protocol.fatal",
          code: "protocol_desynchronized",
          message: fault.message,
        });
        await session.rpc.close(fault);
        break;
      }
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
        }
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
    request(method, params) {
      if (closed)
        return Promise.reject(
          new RpcClosedError("Surface controller is closed"),
        );
      return session.rpc.request(method, params);
    },
    close,
  };
}
