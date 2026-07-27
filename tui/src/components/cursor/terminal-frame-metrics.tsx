import type { CursorPosition, DOMElement } from "ink";
import {
  createContext,
  useContext,
  type ReactNode,
  type RefObject,
} from "react";

export interface TerminalFrameMetrics {
  readonly frameHeight: number;
  readonly terminalRows: number;
  readonly hasMeasured: boolean;
}

export interface TerminalCursorFrame extends TerminalFrameMetrics {
  readonly frameRef: RefObject<DOMElement | null>;
  readonly publishCursor: (position: CursorPosition | undefined) => void;
  readonly requestCursorCommit: () => void;
}

const detachedFrameRef: RefObject<DOMElement | null> = { current: null };
const TerminalFrameMetricsContext = createContext<TerminalCursorFrame>({
  frameHeight: 0,
  terminalRows: 0,
  hasMeasured: false,
  frameRef: detachedFrameRef,
  publishCursor: () => undefined,
  requestCursorCommit: () => undefined,
});

export function TerminalFrameMetricsProvider({
  value,
  children,
}: {
  readonly value: TerminalCursorFrame;
  readonly children: ReactNode;
}) {
  return (
    <TerminalFrameMetricsContext.Provider value={value}>
      {children}
    </TerminalFrameMetricsContext.Provider>
  );
}

export function useTerminalFrameMetrics(): TerminalCursorFrame {
  return useContext(TerminalFrameMetricsContext);
}
