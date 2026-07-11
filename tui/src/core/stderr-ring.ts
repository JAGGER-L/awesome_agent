export const STDERR_TAIL_BYTES = 65_536;

export class StderrRing {
  #bytes = new Uint8Array();

  constructor(readonly capacity = STDERR_TAIL_BYTES) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) {
      throw new RangeError(
        "stderr ring capacity must be a positive safe integer",
      );
    }
  }

  append(chunk: Uint8Array): void {
    if (chunk.byteLength >= this.capacity) {
      this.#bytes = chunk.slice(chunk.byteLength - this.capacity);
      return;
    }
    const joined = new Uint8Array(this.#bytes.byteLength + chunk.byteLength);
    joined.set(this.#bytes);
    joined.set(chunk, this.#bytes.byteLength);
    this.#bytes =
      joined.byteLength > this.capacity
        ? joined.slice(joined.byteLength - this.capacity)
        : joined;
  }

  tail(): Uint8Array {
    return this.#bytes.slice();
  }
}
