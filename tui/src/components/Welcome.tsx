import { Box, Text } from "ink";

import type { Theme } from "../preferences/theme.js";
import { COMPACT_LOGO_ROWS, FULL_LOGO_ROWS } from "./welcome-logo.js";

export interface WelcomeProps {
  readonly width: number;
  readonly workspacePath: string;
  readonly branch?: string | undefined;
  readonly thread:
    | { readonly kind: "new" }
    | { readonly kind: "resumed"; readonly title: string };
  readonly model: string;
  readonly thinkingEnabled: boolean;
  readonly localMemoryEnabled: boolean;
  readonly mem0Enabled: boolean;
  readonly credentialMissing: boolean;
  readonly theme: Theme;
}

export function Welcome(props: WelcomeProps) {
  if (props.width < 36) {
    return <Text>Terminal width 36 or greater required.</Text>;
  }
  const rows = props.width >= 44 ? FULL_LOGO_ROWS : COMPACT_LOGO_ROWS;
  const context = [
    props.workspacePath,
    ...(props.branch ? [props.branch] : []),
    props.thread.kind === "new"
      ? "new thread"
      : `resumed · ${props.thread.title}`,
  ].join(" · ");
  const modes = [
    props.model,
    ...(props.credentialMissing ? ["credential missing"] : []),
    `thinking ${props.thinkingEnabled ? "on" : "off"}`,
    `local memory ${props.localMemoryEnabled ? "on" : "off"}`,
    `mem0 ${props.mem0Enabled ? "on" : "off"}`,
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
      <Text>{context}</Text>
      <Text>{modes}</Text>
      <Text> </Text>
      <Text>/ commands · @path context · ! direct shell</Text>
    </Box>
  );
}
