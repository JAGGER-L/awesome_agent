import { useEffect, useState } from "react";

export function useElapsedTime({
  active,
  startedAt,
  durationMs,
  refreshMs = 100,
}: {
  readonly active: boolean;
  readonly startedAt?: string;
  readonly durationMs?: number;
  readonly refreshMs?: number;
}): number {
  const startedMs = startedAt === undefined ? undefined : Date.parse(startedAt);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active || startedMs === undefined || !Number.isFinite(startedMs))
      return;
    const timer = setInterval(() => setNow(Date.now()), refreshMs);
    return () => clearInterval(timer);
  }, [active, refreshMs, startedMs]);

  if (!active) return Math.max(0, durationMs ?? 0);
  if (startedMs === undefined || !Number.isFinite(startedMs)) return 0;
  return Math.max(0, now - startedMs);
}
