import { Box } from "ink";
import type { ReactNode } from "react";

export interface TerminalSurfaceLayoutProps {
  readonly welcome?: ReactNode;
  readonly transcript: ReactNode;
  readonly activeTurn: ReactNode;
  readonly pendingInputs?: ReactNode;
  readonly notices?: ReactNode;
  readonly commandMenu?: ReactNode;
  readonly input: ReactNode;
  readonly status: ReactNode;
}

export function TerminalSurfaceLayout(props: TerminalSurfaceLayoutProps) {
  return (
    <Box flexDirection="column">
      {props.welcome}
      {props.transcript}
      {props.activeTurn}
      {props.pendingInputs}
      {props.notices}
      {props.commandMenu}
      {props.input}
      {props.status}
    </Box>
  );
}
