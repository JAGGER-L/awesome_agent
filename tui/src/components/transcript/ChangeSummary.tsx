import { Box, Text } from "ink";

import type { ChangeDelta } from "../../transcript/model.js";
import { terminalDisplayWidth } from "../../layout/width.js";
import { ExpandableDetails } from "../results/index.js";
import { useTheme } from "../theme.js";

export function ChangeSummary({
  changes,
  expanded,
  width,
}: {
  readonly changes: readonly ChangeDelta[];
  readonly expanded: boolean;
  readonly width: number;
}) {
  const theme = useTheme();
  const pathWidth = Math.min(
    Math.max(0, ...changes.map((change) => terminalDisplayWidth(change.path))),
    Math.max(1, width - 28),
  );
  return (
    <ExpandableDetails
      expanded={expanded}
      summary={
        <Text color={theme.secondary}>
          ◇ {formatChangeCount(changes)} changed
        </Text>
      }
    >
      <Box flexDirection="column">
        {changes.map((change) => (
          <Box key={`${change.kind}:${change.path}`}>
            <Text color={theme.muted}>
              {"  "}
              {padPath(change.path, pathWidth)} {"  "}
            </Text>
            <ChangeDetail change={change} />
          </Box>
        ))}
      </Box>
    </ExpandableDetails>
  );
}

export function formatChangeCount(changes: readonly ChangeDelta[]): string {
  const files = changes.filter(
    (change) => change.kind === "text_file" || change.kind === "binary_file",
  ).length;
  const directories = changes.filter(
    (change) => change.kind === "directory",
  ).length;
  const symlinks = changes.filter((change) => change.kind === "symlink").length;
  return [
    countLabel(files, "file"),
    countLabel(directories, "directory"),
    countLabel(symlinks, "symlink"),
  ]
    .filter((value): value is string => value !== undefined)
    .join(" · ");
}

function ChangeDetail({ change }: { readonly change: ChangeDelta }) {
  const theme = useTheme();
  switch (change.kind) {
    case "text_file":
      return (
        <Text>
          <Text color={theme.success}>+{change.additions}</Text>{" "}
          <Text color={theme.danger}>-{change.deletions}</Text>
        </Text>
      );
    case "binary_file":
      return (
        <Text color={theme.muted}>
          Binary {change.before_bytes} → {change.after_bytes} bytes
        </Text>
      );
    case "directory":
      return <Text color={theme.muted}>Directory {change.change_kind}</Text>;
    case "symlink":
      return <Text color={theme.muted}>Symlink {change.change_kind}</Text>;
  }
}

function countLabel(count: number, singular: string): string | undefined {
  if (count === 0) return undefined;
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function padPath(path: string, width: number): string {
  return path + " ".repeat(Math.max(0, width - terminalDisplayWidth(path)));
}
