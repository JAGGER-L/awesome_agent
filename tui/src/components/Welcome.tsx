import { Box, Text } from "ink";

import type { Theme } from "../preferences/theme.js";
import { COMPACT_LOGO_ROWS, FULL_LOGO_ROWS } from "./welcome-logo.js";

export interface WelcomeProps {
  readonly width: number;
  readonly version: string;
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
  if (props.width < 36)
    return <Text>Terminal width 36 or greater required.</Text>;
  const rows = props.width >= 54 ? FULL_LOGO_ROWS : COMPACT_LOGO_ROWS;
  const details = <WelcomeDetails {...props} />;
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box flexDirection={props.width >= 100 ? "row" : "column"}>
        <Box
          borderStyle="round"
          borderColor={props.theme.border}
          paddingX={1}
          flexDirection="column"
          flexGrow={1}
        >
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
        </Box>
        <Box
          borderStyle="round"
          borderColor={props.theme.border}
          paddingX={1}
          flexDirection="column"
          minWidth={props.width >= 100 ? 38 : undefined}
        >
          {details}
        </Box>
      </Box>
      <Text color={props.theme.muted}>/ commands · @ files · ! shell</Text>
    </Box>
  );
}

function WelcomeDetails(props: WelcomeProps) {
  const values = [
    ["Version", props.version],
    ["Workspace", props.workspacePath],
    [
      "Thread",
      props.thread.kind === "new"
        ? "New thread"
        : `Resumed · ${props.thread.title}`,
    ],
    ["Model", props.model],
    ["Thinking", props.thinkingEnabled ? "On" : "Off"],
    ["Local memory", props.localMemoryEnabled ? "On" : "Off"],
    ["Cloud memory", props.mem0Enabled ? "On" : "Off"],
    ["Provider", "Mem0 Cloud"],
    [
      "Permission",
      props.permissionMode === "full_access"
        ? "Full access"
        : "Request approval",
    ],
  ] as const;
  return values.map(([label, value]) => (
    <Text key={label}>
      <Text color={props.theme.muted}>{label.padEnd(13)}</Text>
      {value}
    </Text>
  ));
}
