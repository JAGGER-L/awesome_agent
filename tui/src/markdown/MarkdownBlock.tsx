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
          accent={theme.accent}
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
