import { Text } from "ink";

import { useTheme } from "../theme.js";

const symbols = { info: "●", success: "✓", warning: "!", danger: "×" } as const;

export function ResultNotice({
  message,
  tone,
}: {
  readonly message: string;
  readonly tone: keyof typeof symbols;
}) {
  const theme = useTheme();
  const color =
    tone === "success"
      ? theme.success
      : tone === "warning"
        ? theme.warning
        : tone === "danger"
          ? theme.danger
          : theme.secondary;
  return (
    <Text color={color}>
      {symbols[tone]} {message}
    </Text>
  );
}
