import { createContext, useContext, type ReactNode } from "react";

export interface TerminalFrameMetrics {
  readonly frameHeight: number;
  readonly terminalRows: number;
  readonly hasMeasured: boolean;
}

const TerminalFrameMetricsContext = createContext<TerminalFrameMetrics>({
  frameHeight: 0,
  terminalRows: 0,
  hasMeasured: false,
});

export function TerminalFrameMetricsProvider({
  value,
  children,
}: {
  readonly value: TerminalFrameMetrics;
  readonly children: ReactNode;
}) {
  return (
    <TerminalFrameMetricsContext.Provider value={value}>
      {children}
    </TerminalFrameMetricsContext.Provider>
  );
}

export function useTerminalFrameMetrics(): TerminalFrameMetrics {
  return useContext(TerminalFrameMetricsContext);
}
