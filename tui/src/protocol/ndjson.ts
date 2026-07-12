export const MAX_FRAME_BYTES = 1024 * 1024;

export class NdjsonError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "NdjsonError";
  }
}

export class NdjsonDecoder {
  readonly #decoder = new TextDecoder("utf-8", { fatal: true });
  #buffer = new Uint8Array();
  #discardingOversizedLine = false;
  #finished = false;

  push(chunk: Uint8Array): unknown[] {
    if (this.#finished) throw new NdjsonError("Decoder is already finished");
    const values: unknown[] = [];
    let offset = 0;
    while (offset < chunk.byteLength) {
      if (this.#discardingOversizedLine) {
        const terminator = chunk.indexOf(0x0a, offset);
        if (terminator === -1) return values;
        this.#discardingOversizedLine = false;
        offset = terminator + 1;
        continue;
      }

      const terminator = chunk.indexOf(0x0a, offset);
      const end = terminator === -1 ? chunk.byteLength : terminator;
      const segment = chunk.subarray(offset, end);
      if (this.#buffer.byteLength + segment.byteLength > MAX_FRAME_BYTES) {
        this.#buffer = new Uint8Array();
        this.#discardingOversizedLine = terminator === -1;
        throw new NdjsonError(`NDJSON frame exceeds ${MAX_FRAME_BYTES} bytes`);
      }
      this.#append(segment);
      if (terminator === -1) return values;

      const value = this.#consumeLine();
      if (value !== undefined) values.push(value);
      offset = terminator + 1;
    }
    return values;
  }

  finish(): unknown[] {
    if (this.#finished) throw new NdjsonError("Decoder is already finished");
    this.#finished = true;
    if (this.#discardingOversizedLine) {
      throw new NdjsonError("Oversized NDJSON frame was not terminated");
    }
    const value = this.#consumeLine();
    return value === undefined ? [] : [value];
  }

  #append(segment: Uint8Array): void {
    if (segment.byteLength === 0) return;
    const joined = new Uint8Array(this.#buffer.byteLength + segment.byteLength);
    joined.set(this.#buffer);
    joined.set(segment, this.#buffer.byteLength);
    this.#buffer = joined;
  }

  #consumeLine(): unknown | undefined {
    let bytes = this.#buffer;
    this.#buffer = new Uint8Array();
    if (bytes.at(-1) === 0x0d) bytes = bytes.subarray(0, bytes.byteLength - 1);
    if (bytes.byteLength === 0) return undefined;

    let text: string;
    try {
      text = this.#decoder.decode(bytes, { stream: true });
      text += this.#decoder.decode();
    } catch (error) {
      throw new NdjsonError("NDJSON frame is not valid UTF-8", {
        cause: error,
      });
    }
    try {
      return JSON.parse(text) as unknown;
    } catch (error) {
      throw new NdjsonError("NDJSON frame is not valid JSON", { cause: error });
    }
  }
}

const encoder = new TextEncoder();

export function encodeFrame(value: Record<string, unknown>): Uint8Array {
  let json: string;
  try {
    json = JSON.stringify(value);
  } catch (error) {
    throw new NdjsonError("NDJSON value cannot be serialized", {
      cause: error,
    });
  }
  const content = encoder.encode(json);
  if (content.byteLength > MAX_FRAME_BYTES) {
    throw new NdjsonError(`NDJSON frame exceeds ${MAX_FRAME_BYTES} bytes`);
  }
  const frame = new Uint8Array(content.byteLength + 1);
  frame.set(content);
  frame[content.byteLength] = 0x0a;
  return frame;
}
