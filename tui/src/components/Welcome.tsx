import { Box, Text } from "ink";

import { terminalDisplayWidth } from "../layout/width.js";
import type { Theme } from "../preferences/theme.js";
import type { PermissionMode } from "../protocol/base.js";
import type { WorkspaceInstructionDiagnostic } from "../protocol/product-projections.js";
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
  readonly permissionMode: PermissionMode;
  readonly workspaceInstructionDiagnostic?: WorkspaceInstructionDiagnostic | null;
  readonly theme: Theme;
}

export function Welcome(props: WelcomeProps) {
  if (props.width < 36)
    return <Text>Terminal width 36 or greater required.</Text>;
  const rows = props.width >= 54 ? FULL_LOGO_ROWS : COMPACT_LOGO_ROWS;
  const sideBySide = props.width >= 100;
  const logoPanelWidth = sideBySide
    ? Math.floor((props.width * 2) / 3)
    : props.width;
  const detailsPanelWidth = sideBySide
    ? props.width - logoPanelWidth
    : props.width;
  const logoWidth = Math.max(...rows.map(terminalDisplayWidth));
  const details = <WelcomeDetails {...props} />;
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box width={props.width} flexDirection={sideBySide ? "row" : "column"}>
        <Box
          borderStyle="round"
          borderColor={props.theme.border}
          paddingX={1}
          flexDirection="column"
          width={logoPanelWidth}
          alignItems="center"
          justifyContent="center"
        >
          <Box width={logoWidth} flexDirection="column">
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
        </Box>
        <Box
          borderStyle="round"
          borderColor={props.theme.border}
          paddingX={1}
          flexDirection="column"
          width={detailsPanelWidth}
        >
          {details}
        </Box>
      </Box>
      {props.workspaceInstructionDiagnostic ? (
        <Text color={props.theme.warning}>
          ⚠ {props.workspaceInstructionDiagnostic.message}
        </Text>
      ) : null}
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
    ["Permission", permissionModeLabel(props.permissionMode)],
  ] as const;
  return values.map(([label, value]) => (
    <Text key={label}>
      <Text color={props.theme.muted}>{label.padEnd(13)}</Text>
      {value}
    </Text>
  ));
}

function permissionModeLabel(mode: PermissionMode): string {
  switch (mode) {
    case "request_approval":
      return "Request approval";
    case "accept_edits":
      return "Accept edits";
    case "full_access":
      return "Full access";
  }
}
