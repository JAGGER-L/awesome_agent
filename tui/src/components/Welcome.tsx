import { Box, Text } from "ink";

import type { Theme } from "../preferences/theme.js";
import { COMPACT_LOGO_ROWS, FULL_LOGO_ROWS } from "./welcome-logo.js";

export interface WelcomeProps {
  readonly width: number;
  readonly workspacePath: string;
  readonly thread:
    | { readonly kind: "new" }
    | { readonly kind: "resumed"; readonly title: string };
  readonly model: string;
  readonly thinkingEnabled: boolean;
  readonly localMemoryEnabled: boolean;
  readonly mem0Enabled: boolean;
  readonly permissionMode: "request_approval" | "full_access";
  readonly theme: Theme;
}

export function Welcome(props: WelcomeProps) {
  if (props.width < 36) {
    return <Text>Terminal width 36 or greater required.</Text>;
  }
  const rows = props.width >= 44 ? FULL_LOGO_ROWS : COMPACT_LOGO_ROWS;
  const context = [
    props.workspacePath,
    props.thread.kind === "new"
      ? "New thread"
      : `Resumed · ${props.thread.title}`,
  ].join(" · ");
  const memory =
    props.localMemoryEnabled && props.mem0Enabled
      ? "local + Mem0"
      : props.localMemoryEnabled
        ? "local"
        : props.mem0Enabled
          ? "Mem0"
          : "off";
  const modes = [
    props.model,
    `Thinking ${props.thinkingEnabled ? "on" : "off"}`,
    `Memory ${memory}`,
    `Permissions ${props.permissionMode.replaceAll("_", " ")}`,
  ].join(" · ");

  return (
    <Box flexDirection="column">
      {rows.map((row, index) => (
        <Text
          key={row}
          {...(props.theme.logoRows[index]
            ? { color: props.theme.logoRows[index] }
            : {})}
        >
          {row}
        </Text>
      ))}
      <Text> </Text>
      <Text color={props.theme.primary}>{context}</Text>
      <Text color={props.theme.secondary}>{modes}</Text>
      <Text color={props.theme.muted}>/ commands · @ files · ! shell</Text>
      <Text> </Text>
    </Box>
  );
}
