import { encodeFrame, type LineTransport } from "../../src/protocol/index.js";

type Resolver<T> = (result: IteratorResult<T>) => void;

class ByteQueue implements AsyncIterable<Uint8Array> {
  readonly #values: Uint8Array[] = [];
  readonly #waiters: Resolver<Uint8Array>[] = [];
  #ended = false;

  push(value: Uint8Array): void {
    if (this.#ended) throw new Error("Fake server stream is closed");
    const waiter = this.#waiters.shift();
    if (waiter) waiter({ done: false, value });
    else this.#values.push(value);
  }

  end(): void {
    if (this.#ended) return;
    this.#ended = true;
    for (const waiter of this.#waiters.splice(0))
      waiter({ done: true, value: undefined });
  }

  [Symbol.asyncIterator](): AsyncIterator<Uint8Array> {
    return {
      next: async () => {
        const value = this.#values.shift();
        if (value) return { done: false, value };
        if (this.#ended) return { done: true, value: undefined };
        return await new Promise<IteratorResult<Uint8Array>>((resolve) =>
          this.#waiters.push(resolve),
        );
      },
    };
  }
}

export class FakeLineTransport implements LineTransport {
  readonly #source = new ByteQueue();
  readonly #writeWaiters: ((value: unknown) => void)[] = [];
  readonly #writtenMessages: unknown[] = [];
  readonly readable: AsyncIterable<Uint8Array> = this.#source;
  closeCalls = 0;
  writeFailure: Error | undefined;

  async write(bytes: Uint8Array): Promise<void> {
    if (this.writeFailure) {
      const error = this.writeFailure;
      this.writeFailure = undefined;
      throw error;
    }
    const message = JSON.parse(
      new TextDecoder().decode(bytes).trim(),
    ) as unknown;
    const waiter = this.#writeWaiters.shift();
    if (waiter) waiter(message);
    else this.#writtenMessages.push(message);
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
    this.#source.end();
  }

  serverMessage(value: Record<string, unknown>): void {
    this.#source.push(encodeFrame(value));
  }

  serverBytes(bytes: Uint8Array): void {
    this.#source.push(bytes);
  }

  eof(): void {
    this.#source.end();
  }

  async nextClientMessage(): Promise<unknown> {
    const message = this.#writtenMessages.shift();
    if (message) return message;
    return await new Promise<unknown>((resolve) =>
      this.#writeWaiters.push(resolve),
    );
  }
}
