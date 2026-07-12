export const MAX_REASONING_UNITS = 32_000;
export const REASONING_OMITTED_MARKER =
  "… earlier reasoning omitted from live view\n";

export class ReasoningBuffer {
  #value = "";
  #sawReasoning = false;

  append(delta: string): void {
    if (!delta) return;
    this.#sawReasoning = true;
    this.#value = appendReasoningTail(this.#value, delta);
  }

  snapshot(): string {
    return this.#value;
  }

  complete(elapsedMs: number): string | undefined {
    if (!this.#sawReasoning) return undefined;
    const label = reasoningElapsedMarker(elapsedMs);
    this.#value = "";
    this.#sawReasoning = false;
    return label;
  }
}

export function appendReasoningTail(current: string, delta: string): string {
  const omitted = current.startsWith(REASONING_OMITTED_MARKER);
  const tail = omitted
    ? current.slice(REASONING_OMITTED_MARKER.length)
    : current;
  const combined = tail + delta;
  if (!omitted && combined.length <= MAX_REASONING_UNITS) return combined;
  const tailBudget = MAX_REASONING_UNITS - REASONING_OMITTED_MARKER.length;
  let start = Math.max(0, combined.length - tailBudget);
  if (
    start > 0 &&
    isLowSurrogate(combined.charCodeAt(start)) &&
    isHighSurrogate(combined.charCodeAt(start - 1))
  ) {
    start += 1;
  }
  return REASONING_OMITTED_MARKER + combined.slice(start);
}

export function reasoningElapsedMarker(elapsedMs: number): string {
  return `Thought for ${formatDuration(elapsedMs)}`;
}

function isHighSurrogate(value: number): boolean {
  return value >= 0xd800 && value <= 0xdbff;
}

function isLowSurrogate(value: number): boolean {
  return value >= 0xdc00 && value <= 0xdfff;
}

export function formatDuration(elapsedMs: number): string {
  const safe = Math.max(0, Math.round(elapsedMs));
  if (safe < 1_000) return `${safe} ms`;
  const seconds = (safe / 1_000).toFixed(1).replace(/\.0$/, "");
  return `${seconds} s`;
}
