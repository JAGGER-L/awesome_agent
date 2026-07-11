import { graphemes } from "./graphemes.js";
import type { ComposerViewport } from "./model.js";

export const MAX_COMPOSER_ROWS = 8;

export function computeViewport(
  value: string,
  cursorGrapheme: number,
  requestedWidth: number,
): ComposerViewport {
  const width = Math.max(1, Math.floor(requestedWidth));
  const parts = graphemes(value);
  const cursor = Math.max(0, Math.min(parts.length, cursorGrapheme));
  const rows: string[] = [""];
  let column = 0;
  let cursorRow = 0;

  for (let index = 0; index <= parts.length; index += 1) {
    if (index === cursor) cursorRow = rows.length - 1;
    if (index === parts.length) break;

    const part = parts[index] ?? "";
    if (part === "\n") {
      rows.push("");
      column = 0;
      continue;
    }

    const partWidth = displayWidth(part);
    if (column > 0 && column + partWidth > width) {
      rows.push("");
      column = 0;
      if (index === cursor) cursorRow = rows.length - 1;
    }
    rows[rows.length - 1] += part;
    column += partWidth;
  }

  const maxStart = Math.max(0, rows.length - MAX_COMPOSER_ROWS);
  const startRow = Math.max(
    0,
    Math.min(maxStart, cursorRow - MAX_COMPOSER_ROWS + 1),
  );
  return {
    width,
    startRow,
    rows: rows.slice(startRow, startRow + MAX_COMPOSER_ROWS),
    hiddenAbove: startRow > 0,
    hiddenBelow: startRow + MAX_COMPOSER_ROWS < rows.length,
  };
}

export function displayWidth(grapheme: string): number {
  if (grapheme.length === 0) return 0;
  if (/^\p{Mark}+$/u.test(grapheme)) return 0;
  if (/\p{Extended_Pictographic}/u.test(grapheme)) return 2;
  const codePoint = grapheme.codePointAt(0) ?? 0;
  return isWideCodePoint(codePoint) ? 2 : 1;
}

function isWideCodePoint(codePoint: number): boolean {
  return (
    codePoint >= 0x1100 &&
    (codePoint <= 0x115f ||
      codePoint === 0x2329 ||
      codePoint === 0x232a ||
      (codePoint >= 0x2e80 && codePoint <= 0xa4cf && codePoint !== 0x303f) ||
      (codePoint >= 0xac00 && codePoint <= 0xd7a3) ||
      (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
      (codePoint >= 0xfe10 && codePoint <= 0xfe19) ||
      (codePoint >= 0xfe30 && codePoint <= 0xfe6f) ||
      (codePoint >= 0xff00 && codePoint <= 0xff60) ||
      (codePoint >= 0xffe0 && codePoint <= 0xffe6) ||
      (codePoint >= 0x20000 && codePoint <= 0x3fffd))
  );
}
