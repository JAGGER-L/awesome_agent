export const MAX_HISTORY_ENTRIES = 50;
export const MAX_HISTORY_BYTES = 1024 * 1024;

export function appendHistory(
  history: readonly string[],
  value: string,
): readonly string[] {
  if (value.length === 0 || history.at(-1) === value) return history;

  const next = [...history, value];
  while (
    next.length > MAX_HISTORY_ENTRIES ||
    historyBytes(next) > MAX_HISTORY_BYTES
  ) {
    next.shift();
  }
  return next;
}

function historyBytes(history: readonly string[]): number {
  return history.reduce(
    (total, entry) => total + Buffer.byteLength(entry, "utf8"),
    0,
  );
}
