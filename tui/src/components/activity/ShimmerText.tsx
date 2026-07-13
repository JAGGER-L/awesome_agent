import { Text } from "ink";
import { useEffect, useState } from "react";

import { useTheme } from "../theme.js";

export const SHIMMER_INTERVAL_MS = 140;

export function ShimmerText({
  text,
  active,
}: {
  readonly text: string;
  readonly active: boolean;
}) {
  const theme = useTheme();
  const enabled = active && theme.colorEnabled;
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(
      () => setPhase((value) => value + 1),
      SHIMMER_INTERVAL_MS,
    );
    return () => clearInterval(timer);
  }, [enabled]);

  if (!enabled) return <Text color={theme.muted}>{text}</Text>;
  const characters = [...text];
  const highlight = phase % Math.max(1, characters.length + 2);
  const before = characters.slice(0, Math.max(0, highlight - 1)).join("");
  const leading = characters[highlight - 1] ?? "";
  const current = characters[highlight] ?? "";
  const trailing = characters[highlight + 1] ?? "";
  const after = characters.slice(highlight + 2).join("");
  return (
    <Text>
      <Text color={theme.muted}>{before}</Text>
      <Text color={theme.secondary}>{leading}</Text>
      <Text color={theme.primary}>{current}</Text>
      <Text color={theme.secondary}>{trailing}</Text>
      <Text color={theme.muted}>{after}</Text>
    </Text>
  );
}
