import type { ProductError } from "../protocol/base.js";
import type { MethodValue } from "../protocol/methods.js";
import type { SurfaceState } from "../state/model.js";

export type InteractionSnapshot =
  | { readonly status: "idle" }
  | { readonly status: "responding"; readonly interactionId: string }
  | { readonly status: "resolved"; readonly interactionId: string }
  | {
      readonly status: "failed";
      readonly interactionId: string;
      readonly message: string;
    };

interface InteractionSource {
  getState(): SurfaceState;
  subscribe(listener: () => void): () => void;
  request(params: {
    readonly interaction_id: string;
    readonly decision: string;
  }): Promise<
    | { readonly ok: true; readonly value: MethodValue["interaction.respond"] }
    | { readonly ok: false; readonly error: ProductError }
  >;
}

export class InteractionController {
  #snapshot: InteractionSnapshot = { status: "idle" };
  #responsePromise: Promise<void> | undefined;
  readonly #unsubscribe: () => void;

  constructor(private readonly source: InteractionSource) {
    this.#unsubscribe = source.subscribe(() => this.#synchronize());
  }

  snapshot = (): InteractionSnapshot => this.#snapshot;

  respond(decision: string): Promise<void> {
    if (this.#responsePromise) return this.#responsePromise;
    const pending = this.source.getState().pending_interaction;
    if (!pending) return Promise.resolve();
    if (!pending.choices.some((choice) => choice.decision === decision)) {
      return Promise.reject(
        new Error(`Interaction choice ${decision} is not available.`),
      );
    }
    const interactionId = pending.interaction_id;
    this.#snapshot = { status: "responding", interactionId };
    this.#responsePromise = this.source
      .request({ interaction_id: interactionId, decision })
      .then((result) => {
        if (this.#snapshot.status !== "responding") return;
        if (!result.ok) {
          this.#snapshot = {
            status: "failed",
            interactionId,
            message: result.error.message,
          };
        } else if (!result.value.accepted) {
          this.#snapshot = {
            status: "failed",
            interactionId,
            message: result.value.error?.message ?? "Interaction was rejected.",
          };
        }
      })
      .catch((error: unknown) => {
        if (this.#snapshot.status !== "responding") return;
        this.#snapshot = {
          status: "failed",
          interactionId,
          message:
            error instanceof Error ? error.message : "Interaction failed.",
        };
      });
    return this.#responsePromise;
  }

  reset(): void {
    this.#responsePromise = undefined;
    this.#snapshot = { status: "idle" };
  }

  dispose(): void {
    this.#unsubscribe();
  }

  #synchronize(): void {
    if (this.#snapshot.status !== "responding") return;
    const state = this.source.getState();
    if (state.core_exit) {
      this.#snapshot = {
        status: "failed",
        interactionId: this.#snapshot.interactionId,
        message: "Core exited while interaction was pending.",
      };
    } else if (
      state.pending_interaction?.interaction_id !== this.#snapshot.interactionId
    ) {
      this.#snapshot = {
        status: "resolved",
        interactionId: this.#snapshot.interactionId,
      };
    }
  }
}
