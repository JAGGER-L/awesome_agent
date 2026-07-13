import { Box, Text } from "ink";
import type { ReactNode } from "react";

import { useTheme } from "../theme.js";

export type ResultTone = "info" | "success" | "warning" | "danger";

const symbols: Readonly<Record<ResultTone, string>> = {
  info: "●",
  success: "✓",
  warning: "!",
  danger: "×",
};

export function ResultPanel({
  title,
  tone,
  width,
  children,
}: {
  readonly title: string;
  readonly tone: ResultTone;
  readonly width: number;
  readonly children: ReactNode;
}) {
  const theme = useTheme();
  const color =
    tone === "success"
      ? theme.success
      : tone === "warning"
        ? theme.warning
        : tone === "danger"
          ? theme.danger
          : theme.border;
  return (
    <Box
      borderColor={color}
      borderStyle="round"
      flexDirection="column"
      paddingX={1}
      width={Math.max(20, width)}
    >
      <Text bold color={color}>
        {symbols[tone]} {title}
      </Text>
      {children}
    </Box>
  );
}
