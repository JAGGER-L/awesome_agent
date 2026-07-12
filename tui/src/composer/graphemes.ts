const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

export function graphemes(value: string): string[] {
  return [...segmenter.segment(value)].map((part) => part.segment);
}

export function graphemeCount(value: string): number {
  return [...segmenter.segment(value)].length;
}

export function codePointCount(value: string): number {
  return Array.from(value).length;
}

export function codeUnitOffset(value: string, grapheme: number): number {
  const segments = [...segmenter.segment(value)];
  if (grapheme <= 0) return 0;
  if (grapheme >= segments.length) return value.length;
  return segments[grapheme]?.index ?? value.length;
}
