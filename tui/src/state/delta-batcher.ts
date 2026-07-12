import type { EventEnvelope } from "../protocol/index.js";
import type {
  EventStreamGuard,
  ProtocolDesynchronized,
} from "./event-stream.js";

export interface CoalescedDelta {
  readonly kind: "coalesced_delta";
  readonly session_id: string;
  readonly thread_id?: string;
  readonly turn_id?: string;
  readonly operation_id?: string;
  readonly delta_kind: "text" | "reasoning";
  readonly text: string;
  readonly first_timestamp: string;
  readonly last_timestamp: string;
  readonly first_sequence: number;
  readonly last_sequence: number;
}

export type BatchedEvent = EventEnvelope | CoalescedDelta;

export interface ScheduledTask {
  cancel(): void;
}

export interface DeltaScheduler {
  schedule(callback: () => void, delay: number): ScheduledTask;
}

const defaultScheduler: DeltaScheduler = {
  schedule(callback, delay) {
    const timer = setTimeout(callback, delay);
    return { cancel: () => clearTimeout(timer) };
  },
};
const MAX_BATCH_CHARACTERS = 8_192;

export class DeltaBatcher {
  #pending: CoalescedDelta | undefined;
  #scheduled: ScheduledTask | undefined;
  #closed = false;

  constructor(
    readonly guard: EventStreamGuard,
    readonly emit: (value: BatchedEvent) => void,
    readonly scheduler: DeltaScheduler = defaultScheduler,
  ) {}

  accept(event: EventEnvelope): ProtocolDesynchronized | undefined {
    const fault = this.guard.accept(event);
    if (fault) return fault;
    const delta = this.#projectDelta(event);
    if (!delta) {
      this.flush();
      this.emit(event);
      return undefined;
    }
    if (this.#pending && this.#matches(this.#pending, delta)) {
      if (
        this.#pending.text.length + delta.text.length <=
        MAX_BATCH_CHARACTERS
      ) {
        this.#pending = {
          ...this.#pending,
          text: this.#pending.text + delta.text,
          last_sequence: delta.last_sequence,
          last_timestamp: delta.last_timestamp,
        };
        return undefined;
      }
      this.flush();
    }
    this.flush();
    this.#pending = delta;
    this.#scheduled = this.scheduler.schedule(() => this.flush(), 16);
    return undefined;
  }

  flush(): void {
    if (!this.#pending) return;
    this.#scheduled?.cancel();
    this.#scheduled = undefined;
    const pending = this.#pending;
    this.#pending = undefined;
    this.emit(pending);
  }

  close(): void {
    if (this.#closed) return;
    this.#closed = true;
    this.flush();
    this.guard.close();
  }

  #projectDelta(event: EventEnvelope): CoalescedDelta | undefined {
    if (
      event.payload.kind !== "assistant.text.delta" &&
      event.payload.kind !== "assistant.reasoning.delta"
    ) {
      return undefined;
    }
    return {
      kind: "coalesced_delta",
      session_id: event.session_id,
      ...(event.thread_id === undefined ? {} : { thread_id: event.thread_id }),
      ...(event.turn_id === undefined ? {} : { turn_id: event.turn_id }),
      ...(event.operation_id === undefined
        ? {}
        : { operation_id: event.operation_id }),
      delta_kind:
        event.payload.kind === "assistant.text.delta" ? "text" : "reasoning",
      text: event.payload.text,
      first_timestamp: event.timestamp,
      last_timestamp: event.timestamp,
      first_sequence: event.sequence,
      last_sequence: event.sequence,
    };
  }

  #matches(left: CoalescedDelta, right: CoalescedDelta): boolean {
    return (
      left.session_id === right.session_id &&
      left.thread_id === right.thread_id &&
      left.turn_id === right.turn_id &&
      left.operation_id === right.operation_id &&
      left.delta_kind === right.delta_kind
    );
  }
}
