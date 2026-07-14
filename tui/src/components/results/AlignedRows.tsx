import { Box, Text } from "ink";

import type { PresentationRow } from "../../commands/presenters.js";
import { terminalDisplayWidth } from "../../layout/width.js";
import { useTheme } from "../theme.js";

export function AlignedRows({
  rows,
  width,
}: {
  readonly rows: readonly PresentationRow[];
  readonly width: number;
}) {
  const theme = useTheme();
  const availableWidth = Math.max(1, width - 4);
  const labelWidth = Math.min(
    Math.max(0, ...rows.map((row) => terminalDisplayWidth(row.label))),
    Math.max(1, Math.floor(availableWidth * 0.45)),
  );
  const occurrences = new Map<string, number>();
  return (
    <Box flexDirection="column">
      {rows.map((row) => {
        const identity = `${row.label}\u0000${row.value}`;
        const occurrence = occurrences.get(identity) ?? 0;
        occurrences.set(identity, occurrence + 1);
        const key = `${identity}\u0000${occurrence}`;
        const gap = 3;
        const valueColumn = labelWidth + gap;
        const wraps =
          terminalDisplayWidth(row.label) +
            terminalDisplayWidth(row.value) +
            gap >
          availableWidth;
        const valueColor =
          row.status === "success"
            ? theme.success
            : row.status === "warning"
              ? theme.warning
              : row.status === "danger"
                ? theme.danger
                : undefined;
        return wraps ? (
          <Box flexDirection="column" key={key}>
            <Text>{row.label}</Text>
            <Box
              marginLeft={Math.min(valueColumn, availableWidth - 1)}
              width={Math.max(1, availableWidth - valueColumn)}
              justifyContent="flex-start"
            >
              {valueColor ? (
                <Text color={valueColor}>{row.value}</Text>
              ) : (
                <Text>{row.value}</Text>
              )}
            </Box>
          </Box>
        ) : (
          <Box key={key} width={availableWidth}>
            <Box width={valueColumn}>
              <Text>{row.label}</Text>
            </Box>
            <Box flexGrow={1} justifyContent="flex-start">
              {valueColor ? (
                <Text color={valueColor}>{row.value}</Text>
              ) : (
                <Text>{row.value}</Text>
              )}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}
