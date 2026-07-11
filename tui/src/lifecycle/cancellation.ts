import type { ProductError } from "../protocol/base.js";
import type { MethodValue } from "../protocol/methods.js";
import type { SurfaceState } from "../state/model.js";

export type CancellationSnapshot =
  | { readonly status: "idle" }
  | {
      readonly status: "requested" | "confirmed";
      readonly operationId: string;
    }
  | {
      readonly status: "failed";
      readonly operationId: string;
      readonly message: string;
    };

interface CancellationSource {
  getState(): SurfaceState;
  subscribe(listener: () => void): () => void;
  request(params: {
    readonly operation_id: string;
  }): Promise<
    | { readonly ok: true; readonly value: MethodValue["operation.cancel"] }
    | { readonly ok: false; readonly error: ProductError }
  >;
}

export class CancellationController {
  #snapshot: CancellationSnapshot = { status: "idle" };
  #requestPromise: Promise<void> | undefined;
  #generation = 0;
  readonly #listeners = new Set<() => void>();
  readonly #unsubscribe: () => void;

  constructor(private readonly source: CancellationSource) {
    this.#unsubscribe = source.subscribe(() => this.#synchronize());
  }

  snapshot = (): CancellationSnapshot => this.#snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  cancelActiveOperation(): Promise<void> {
    if (this.#requestPromise) return this.#requestPromise;
    const operation = this.source.getState().active_operation;
    if (operation?.status !== "active") return Promise.resolve();

    const operationId = operation.id;
    const generation = this.#generation;
    this.#setSnapshot({ status: "requested", operationId });
    this.#requestPromise = this.source
      .request({ operation_id: operationId })
      .then((result) => {
        if (generation !== this.#generation) return;
        if (this.#snapshot.status === "confirmed") return;
        if (!result.ok) {
          this.#setSnapshot({
            status: "failed",
            operationId,
            message: result.error.message,
          });
          return;
        }
        if (
          !result.value.cancelled ||
          result.value.operation_id !== operationId
        ) {
          this.#setSnapshot({
            status: "failed",
            operationId,
            message: "Cancellation was not accepted.",
          });
        }
      })
      .catch((error: unknown) => {
        if (generation !== this.#generation) return;
        this.#setSnapshot({
          status: "failed",
          operationId,
          message:
            error instanceof Error ? error.message : "Cancellation failed.",
        });
      });
    return this.#requestPromise;
  }

  reset(): void {
    this.#generation += 1;
    this.#requestPromise = undefined;
    this.#setSnapshot({ status: "idle" });
  }

  dispose(): void {
    this.#unsubscribe();
    this.#listeners.clear();
  }

  #synchronize(): void {
    if (this.#snapshot.status !== "requested") return;
    const state = this.source.getState();
    if (state.core_exit) {
      this.#setSnapshot({
        status: "failed",
        operationId: this.#snapshot.operationId,
        message: "Core exited while cancellation was pending.",
      });
      return;
    }
    if (
      state.active_operation?.id !== this.#snapshot.operationId ||
      state.active_operation.status !== "active"
    ) {
      this.#setSnapshot({
        status: "confirmed",
        operationId: this.#snapshot.operationId,
      });
    }
  }

  #setSnapshot(snapshot: CancellationSnapshot): void {
    this.#snapshot = snapshot;
    for (const listener of this.#listeners) listener();
  }
}
