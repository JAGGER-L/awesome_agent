import { Box, Text } from "ink";

import {
  MAX_PENDING_INPUTS,
  type PendingInput,
} from "../pending-input/model.js";
import { useTheme } from "./theme.js";

export function PendingInputList({
  items,
}: {
  readonly items: readonly PendingInput[];
}) {
  const theme = useTheme();
  if (items.length === 0) return null;
  return (
    <Box flexDirection="column">
      <Text bold color={theme.secondary}>
        Pending inputs · {items.length} of {MAX_PENDING_INPUTS}
      </Text>
      {items.map((item, index) => (
        <Box key={item.id} flexDirection="column" marginLeft={2}>
          <Text>
            <Text bold color={theme.user}>
              ❯{" "}
            </Text>
            {oneLine(item.raw)}
          </Text>
          <Text color={theme.muted}>
            Queued · {index === 0 ? "Next · ↑ recalls latest" : index + 1}
          </Text>
        </Box>
      ))}
    </Box>
  );
}

function oneLine(value: string): string {
  return value.replace(/\s*\r?\n\s*/gu, " ↵ ");
}
