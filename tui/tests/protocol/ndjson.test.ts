import { describe, expect, it } from "vitest";

import {
  MAX_FRAME_BYTES,
  NdjsonError,
  NdjsonDecoder,
  encodeFrame,
} from "../../src/protocol/ndjson.js";

const encoder = new TextEncoder();

describe("NdjsonDecoder", () => {
  it("decodes split UTF-8 code points", () => {
    const decoder = new NdjsonDecoder();
    const frame = encodeFrame({ text: "😀" });
    const emojiStart = frame.indexOf(0xf0);

    expect(decoder.push(frame.slice(0, emojiStart + 2))).toEqual([]);
    expect(decoder.push(frame.slice(emojiStart + 2))).toEqual([{ text: "😀" }]);
  });

  it("decodes multiple LF/CRLF lines and ignores blank lines", () => {
    const decoder = new NdjsonDecoder();
    const bytes = encoder.encode('\n{"a":1}\r\n\r\n{"b":2}\n');

    expect(decoder.push(bytes)).toEqual([{ a: 1 }, { b: 2 }]);
    expect(decoder.finish()).toEqual([]);
  });

  it("decodes a final line at EOF", () => {
    const decoder = new NdjsonDecoder();
    expect(decoder.push(encoder.encode('{"done":true}'))).toEqual([]);
    expect(decoder.finish()).toEqual([{ done: true }]);
  });

  it("rejects malformed UTF-8 and malformed JSON", () => {
    expect(() =>
      new NdjsonDecoder().push(Uint8Array.from([0xff, 0x0a])),
    ).toThrow(NdjsonError);
    expect(() => new NdjsonDecoder().push(encoder.encode("{bad}\n"))).toThrow(
      NdjsonError,
    );
  });

  it("accepts exactly 1 MiB and rejects one byte over", () => {
    const overhead = encoder.encode('{"x":""}').byteLength;
    const exact = { x: "a".repeat(MAX_FRAME_BYTES - overhead) };
    expect(encodeFrame(exact).byteLength).toBe(MAX_FRAME_BYTES + 1);
    expect(new NdjsonDecoder().push(encodeFrame(exact))).toEqual([exact]);

    const tooLarge = { x: `${exact.x}a` };
    expect(() => encodeFrame(tooLarge)).toThrow(NdjsonError);
  });

  it("recovers its line boundary after an oversized line terminator", () => {
    const decoder = new NdjsonDecoder();
    expect(() =>
      decoder.push(encoder.encode("a".repeat(MAX_FRAME_BYTES + 1))),
    ).toThrow(NdjsonError);
    expect(decoder.push(encoder.encode('\n{"recovered":true}\n'))).toEqual([
      { recovered: true },
    ]);
  });
});

describe("encodeFrame", () => {
  it("uses compact UTF-8 JSON with one LF", () => {
    expect(new TextDecoder().decode(encodeFrame({ value: "你好" }))).toBe(
      '{"value":"你好"}\n',
    );
  });
});
