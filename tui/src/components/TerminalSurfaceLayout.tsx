import { Box, useBoxMetrics, useWindowSize, type DOMElement } from "ink";
import { useRef, type ReactNode } from "react";

import { TerminalFrameMetricsProvider } from "./cursor/terminal-frame-metrics.js";

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
  const frameRef = useRef<DOMElement>(null);
  const frame = useBoxMetrics(frameRef);
  const { rows } = useWindowSize();
  return (
    <TerminalFrameMetricsProvider
      value={{
        frameHeight: frame.height,
        terminalRows: rows,
        hasMeasured: frame.hasMeasured,
      }}
    >
      <Box ref={frameRef} flexDirection="column">
        {props.welcome}
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
