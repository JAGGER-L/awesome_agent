import { describe, expect, it } from "vitest";

import {
  MAX_REASONING_UNITS,
  REASONING_OMITTED_MARKER,
  ReasoningBuffer,
} from "../../src/transcript/reasoning.js";

describe("ReasoningBuffer", () => {
  it("keeps ASCII and Chinese text below the exact bound", () => {
    const buffer = new ReasoningBuffer();
    buffer.append("思考".repeat(10));
    buffer.append("a".repeat(MAX_REASONING_UNITS - 20));
    expect(buffer.snapshot()).toHaveLength(MAX_REASONING_UNITS);
  });

  it("retains the newest tail with one omission marker", () => {
    const buffer = new ReasoningBuffer();
    buffer.append("a".repeat(MAX_REASONING_UNITS + 1));
    buffer.append("newest");
    const snapshot = buffer.snapshot();
    expect(snapshot).toHaveLength(MAX_REASONING_UNITS);
    expect(snapshot.startsWith(REASONING_OMITTED_MARKER)).toBe(true);
    expect(snapshot.endsWith("newest")).toBe(true);
    expect(snapshot.indexOf(REASONING_OMITTED_MARKER)).toBe(
      snapshot.lastIndexOf(REASONING_OMITTED_MARKER),
    );
  });

  it("never leaves a split emoji surrogate at the retained boundary", () => {
    const buffer = new ReasoningBuffer();
    buffer.append(`x${"😀".repeat(MAX_REASONING_UNITS)}`);
    const tail = buffer.snapshot().slice(REASONING_OMITTED_MARKER.length);
    expect(tail.charCodeAt(0)).not.toBeGreaterThanOrEqual(0xdc00);
    expect(buffer.snapshot().length).toBeLessThanOrEqual(MAX_REASONING_UNITS);
  });

  it("completes to one elapsed marker and discards live text", () => {
    const buffer = new ReasoningBuffer();
    expect(buffer.complete(100)).toBeUndefined();
    buffer.append("private");
    expect(buffer.complete(1_250)).toBe("Thought for 1.3 s");
    expect(buffer.snapshot()).toBe("");
    expect(buffer.complete(2_000)).toBeUndefined();
  });
});
