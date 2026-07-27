import {
  Box,
  useBoxMetrics,
  useCursor,
  useWindowSize,
  type DOMElement,
} from "ink";
import { useReducer, useRef, type ReactNode } from "react";

import { TerminalFrameMetricsProvider } from "./cursor/terminal-frame-metrics.js";

export interface TerminalSurfaceLayoutProps {
  readonly welcome?: ReactNode;
  readonly welcomeNotice?: ReactNode;
  readonly transcript: ReactNode;
  readonly activeTurn: ReactNode;
  readonly pendingInputs?: ReactNode;
  readonly notices?: ReactNode;
  readonly commandMenu?: ReactNode;
  readonly input: ReactNode;
  readonly status: ReactNode;
}

export function TerminalSurfaceLayout(props: TerminalSurfaceLayoutProps) {
  const frameRef = useRef<DOMElement>(null);
  const frame = useBoxMetrics(frameRef);
  const { rows } = useWindowSize();
  const [, requestCursorCommit] = useReducer(
    (revision: number) => revision + 1,
    0,
  );
  // The ancestor owns useCursor so the Composer's insertion effect can refresh
  // Yoga before this hook publishes the position for the same Ink commit.
  const { setCursorPosition } = useCursor();
  return (
    <TerminalFrameMetricsProvider
      value={{
        frameHeight: frame.height,
        terminalRows: rows,
        hasMeasured: frame.hasMeasured,
        frameRef,
        publishCursor: setCursorPosition,
        requestCursorCommit,
      }}
    >
      <Box ref={frameRef} flexDirection="column">
        {props.welcome}
        {props.welcomeNotice}
        {props.transcript}
        {props.activeTurn}
        {props.pendingInputs}
        {props.notices}
        {props.commandMenu}
        {props.input}
        {props.status}
      </Box>
    </TerminalFrameMetricsProvider>
  );
}
