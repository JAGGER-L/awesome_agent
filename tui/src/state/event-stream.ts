import type { EventEnvelope } from "../protocol/index.js";

export class ProtocolDesynchronized extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProtocolDesynchronized";
  }
}

export class EventStreamGuard {
  #sessionId: string | undefined;
  #lastSequence = 0;
  #fault: ProtocolDesynchronized | undefined;

  accept(event: EventEnvelope): ProtocolDesynchronized | undefined {
    if (this.#fault) return this.#fault;
    if (!Number.isSafeInteger(event.sequence))
      return this.#fail("Event sequence is unsafe");
    if (this.#sessionId === undefined) {
      if (event.sequence !== 1)
        return this.#fail("Event Session must begin at sequence 1");
      this.#sessionId = event.session_id;
      this.#lastSequence = 1;
      return undefined;
    }
    if (event.session_id !== this.#sessionId) {
      return this.#fail("Event Session changed without reset");
    }
    if (event.sequence !== this.#lastSequence + 1) {
      return this.#fail("Event sequence is not contiguous");
    }
    this.#lastSequence = event.sequence;
    return undefined;
  }

  reset(): void {
    this.#sessionId = undefined;
    this.#lastSequence = 0;
    this.#fault = undefined;
  }

  close(): void {
    if (!this.#fault)
      this.#fault = new ProtocolDesynchronized("Event stream is closed");
  }

  #fail(message: string): ProtocolDesynchronized {
    this.#fault = new ProtocolDesynchronized(message);
    return this.#fault;
  }
}
