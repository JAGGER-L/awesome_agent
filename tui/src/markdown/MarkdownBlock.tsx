import { Box, Text } from "ink";
import type { ReactNode } from "react";

import { useTheme } from "../components/theme.js";
import type { MarkdownInline, MarkdownNode } from "./model.js";
import { parseTerminalMarkdown } from "./parse.js";

export function MarkdownBlock({
  source,
  width,
}: {
  source: string;
  width: number;
}) {
  const theme = useTheme();
  const nodes = parseTerminalMarkdown(source);
  return (
    <Box flexDirection="column" width={Math.max(1, width)}>
      {withStableKeys(nodes, (node) => JSON.stringify(node)).map((entry) => (
        <MarkdownNodeView
          key={entry.key}
          node={entry.value}
          width={width}
          accent={theme.primary}
          muted={theme.muted}
        />
      ))}
    </Box>
  );
}

function MarkdownNodeView({
  node,
  width,
  accent,
  muted,
}: {
  node: MarkdownNode;
  width: number;
  accent: string;
  muted: string;
}) {
  switch (node.kind) {
    case "heading":
      return (
        <Text bold color={accent} wrap="wrap">
          {renderInline(node.children, muted)}
        </Text>
      );
    case "paragraph":
      return <Text wrap="wrap">{renderInline(node.children, muted)}</Text>;
    case "list":
      return (
        <Box flexDirection="column">
          {withStableKeys(node.items, (item) => JSON.stringify(item)).map(
            (entry) => (
              <Box key={entry.key} width={Math.max(1, width)}>
                <Text>
                  {node.ordered ? `${node.start + entry.position}. ` : "• "}
                </Text>
                <Text wrap="wrap">{renderInline(entry.value, muted)}</Text>
              </Box>
            ),
          )}
        </Box>
      );
    case "quote":
      return (
        <Box>
          <Text color={muted}>│ </Text>
          <Box flexDirection="column" width={Math.max(1, width - 2)}>
            {withStableKeys(node.children, (child) =>
              JSON.stringify(child),
            ).map((entry) => (
              <MarkdownNodeView
                key={entry.key}
                node={entry.value}
                width={Math.max(1, width - 2)}
                accent={accent}
                muted={muted}
              />
            ))}
          </Box>
        </Box>
      );
    case "code":
      return (
        <Box flexDirection="column">
          {node.language ? <Text color={muted}>{node.language}</Text> : null}
          <Text color={muted} wrap="wrap">
            {node.text}
          </Text>
        </Box>
      );
    case "rule":
      return <Text color={muted}>{"-".repeat(Math.max(3, width))}</Text>;
    case "math":
      return <Text color={accent}>{node.text}</Text>;
    case "table": {
      const lines = renderTable(node, width);
      return (
        <Box flexDirection="column">
          {lines.map((line) => (
            <Text key={line}>{line}</Text>
          ))}
        </Box>
      );
    }
  }
}

function renderInline(
  nodes: readonly MarkdownInline[],
  muted: string,
): ReactNode[] {
  return nodes.map((node, index) => {
    const key = `${node.kind}:${index}`;
    switch (node.kind) {
      case "text":
        return node.text;
      case "strong":
        return (
          <Text key={key} bold>
            {renderInline(node.children, muted)}
          </Text>
        );
      case "emphasis":
        return (
          <Text key={key} italic>
            {renderInline(node.children, muted)}
          </Text>
        );
      case "deleted":
        return (
          <Text key={key} strikethrough>
            {renderInline(node.children, muted)}
          </Text>
        );
      case "inline_code":
        return (
          <Text key={key} color={muted}>
            {node.text}
          </Text>
        );
      case "math":
        return (
          <Text key={key} color={muted}>
            {node.text}
          </Text>
        );
      case "link":
        return (
          <Text key={key} underline>
            {renderInline(node.children, muted)} ({node.href})
          </Text>
        );
      case "break":
        return "\n";
    }
    node satisfies never;
    return null;
  });
}

function renderTable(
  node: Extract<MarkdownNode, { kind: "table" }>,
  width: number,
): readonly string[] {
  const rows = [node.header, ...node.rows].map((row) => row.map(inlineText));
  const columns = Math.max(1, ...rows.map((row) => row.length));
  const naturalSizes = Array.from({ length: columns }, (_, column) =>
    Math.max(1, ...rows.map((row) => displayWidth(row[column] ?? ""))),
  );
  const naturalWidth =
    naturalSizes.reduce((total, size) => total + size, 0) + (columns + 1) * 3;
  if (naturalWidth + 2 >= width) {
    const labels = rows[0] ?? [];
    return rows
      .slice(1)
      .flatMap((row, rowIndex) => [
        ...(rowIndex === 0 ? [] : [""]),
        ...row.map(
          (value, column) =>
            `${labels[column] ?? `Column ${column + 1}`}: ${value}`,
        ),
      ]);
  }
  const available = Math.max(columns, width - (columns + 1) * 3);
  const maximum = Math.max(3, Math.floor(available / columns));
  const sizes = Array.from({ length: columns }, (_, column) =>
    Math.min(maximum, naturalSizes[column] ?? 1),
  );
  const line = (row: readonly string[]) =>
    `| ${sizes.map((size, column) => padDisplay(truncateDisplay(row[column] ?? "", size), size)).join(" | ")} |`;
  const divider = `|-${sizes.map((size) => "-".repeat(size)).join("-|-")}-|`;
  return [line(rows[0] ?? []), divider, ...rows.slice(1).map(line)];
}

function inlineText(nodes: readonly MarkdownInline[]): string {
  return nodes
    .map((node): string => {
      if (
        node.kind === "text" ||
        node.kind === "inline_code" ||
        node.kind === "math"
      )
        return node.text;
      if (node.kind === "break") return " ";
      return inlineText(node.children);
    })
    .join("");
}

function displayWidth(value: string): number {
  return Array.from(value).reduce(
    (total, character) =>
      total + ((character.codePointAt(0) ?? 0) > 0xff ? 2 : 1),
    0,
  );
}

function truncateDisplay(value: string, width: number): string {
  let result = "";
  for (const character of value) {
    if (displayWidth(result + character) > width) break;
    result += character;
  }
  return result;
}

function padDisplay(value: string, width: number): string {
  return value + " ".repeat(Math.max(0, width - displayWidth(value)));
}

function withStableKeys<T>(
  values: readonly T[],
  signature: (value: T) => string,
): readonly {
  readonly key: string;
  readonly value: T;
  readonly position: number;
}[] {
  const occurrences = new Map<string, number>();
  return values.map((value, position) => {
    const base = signature(value);
    const occurrence = occurrences.get(base) ?? 0;
    occurrences.set(base, occurrence + 1);
    return { key: `${base}:${occurrence}`, value, position };
  });
}
