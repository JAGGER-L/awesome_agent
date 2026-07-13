export function formatActivityDuration(durationMs: number): string {
  const safe = Math.max(0, durationMs);
  if (safe > 0 && safe < 100) return "<0.1 s";
  return `${(safe / 1_000).toFixed(1)} s`;
}
