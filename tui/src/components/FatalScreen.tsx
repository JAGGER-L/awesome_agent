import { Box, Text } from "ink";

import { fatalExitCode, type FatalState } from "../lifecycle/fatal.js";
import { Picker } from "./Picker.js";
import { useTheme } from "./theme.js";

export function FatalScreen({
  fatal,
  selected = 0,
  disabled = false,
}: {
  readonly fatal: FatalState;
  readonly selected?: number;
  readonly disabled?: boolean;
}) {
  const theme = useTheme();
  const summary = fatalSummary(fatal);
  return (
    <Box flexDirection="column">
      <Text color={theme.danger}>{summary}</Text>
      {fatal.kind === "core_exit" ? (
        <>
          <Text>
            {fatal.exit.code === null
              ? `Signal ${fatal.exit.signal ?? "unknown"}`
              : `Exit code ${fatal.exit.code}`}
          </Text>
          {keyedLines(fatal.stderrLines).map(({ key, line }) => (
            <Text key={key}>{line}</Text>
          ))}
        </>
      ) : null}
      {fatal.kind === "runtime_missing" ||
      fatal.kind === "version_incompatible" ? (
        <Text>Exit code {fatalExitCode(fatal)}</Text>
      ) : null}
      <Picker
        selected={selected}
        selection={{
          prompt: disabled ? "Reconnecting…" : "Choose recovery action",
          options: [
            { value: "reconnect", label: "Reconnect", selected: true },
            { value: "quit", label: "Quit", selected: false },
          ],
        }}
      />
    </Box>
  );
}

function keyedLines(
  lines: readonly string[],
): readonly { readonly key: string; readonly line: string }[] {
  const occurrences = new Map<string, number>();
  return lines.map((line) => {
    const occurrence = occurrences.get(line) ?? 0;
    occurrences.set(line, occurrence + 1);
    return { key: `${line}\u0000${occurrence}`, line };
  });
}

function fatalSummary(fatal: FatalState): string {
  switch (fatal.kind) {
    case "protocol":
      return `Protocol failure · ${fatal.message}`;
    case "core_exit":
      return "Core exited unexpectedly";
    case "render":
      return fatal.message;
    case "runtime_missing":
      return `Core runtime not found · ${fatal.executable}`;
    case "version_incompatible":
      return `Version incompatible · ${fatal.message}`;
  }
}
