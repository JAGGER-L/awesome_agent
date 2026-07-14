# Fullscreen Composer Cursor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the native terminal cursor on the Composer input row before and after the Ink frame fills the terminal viewport.

**Architecture:** `Composer` continues to calculate one logical output-relative cursor position. `TerminalSurfaceLayout` publishes presentation-only frame height and terminal row metrics, and a narrow Ink 7.1 bridge translates the logical position to the physical coordinate expected by Ink's fullscreen renderer. No Turn, transcript, protocol, or persisted state participates.

**Tech Stack:** TypeScript 7, React 19, Ink 7.1, Vitest 4, Node.js 22.

## Global Constraints

- Preserve the current inline terminal and native scrollback behavior.
- Do not introduce alternate-screen rendering, a fixed-bottom input region, a fake cursor, direct ANSI writes from `Composer`, an Ink fork, a Git dependency, or `patch-package`.
- `TerminalInput` remains the only keyboard subscriber.
- Renderer measurements stay in React presentation context and never enter Surface state, protocol, Runtime, or persistence.
- The bridge may inspect only `frameHeight`, `terminalRows`, `hasMeasured`, and the logical cursor position; it must not inspect Thread count, transcript length, active Turn state, or command type.
- The accepted Ink 7.1 rule is exact: non-fullscreen uses logical `y`; `frameHeight >= terminalRows` uses logical `y + 1`.
- Actual Windows Pinyin preedit remains a manual host acceptance check; automated tests must not claim to operate the system IME.

---

### Task 1: Specify the frame contract and reproduce the fullscreen boundary

**Files:**
- Create: `tui/src/components/cursor/terminal-frame-metrics.tsx`
- Create: `tui/src/components/cursor/ink-cursor-bridge.ts`
- Create: `tui/tests/components/ink-cursor-bridge.test.ts`

**Interfaces:**
- Produces: `TerminalFrameMetrics`, `TerminalFrameMetricsProvider`, and `useTerminalFrameMetrics()`.
- Produces: `adaptInkCursorPosition(position: CursorPosition | undefined, frame: TerminalFrameMetrics): CursorPosition | undefined`.
- Consumes: Ink `CursorPosition` and React context only.

- [ ] **Step 1: Write the failing bridge boundary tests**

```ts
import { describe, expect, it } from "vitest";

import { adaptInkCursorPosition } from "../../src/components/cursor/ink-cursor-bridge.js";

describe("Ink cursor bridge", () => {
  const logical = { x: 12, y: 18 } as const;

  it("keeps the logical row below the fullscreen threshold", () => {
    expect(
      adaptInkCursorPosition(logical, {
        frameHeight: 23,
        terminalRows: 24,
        hasMeasured: true,
      }),
    ).toEqual(logical);
  });

  it.each([24, 25])(
    "adapts the physical row when frame height is %i",
    (frameHeight) => {
      expect(
        adaptInkCursorPosition(logical, {
          frameHeight,
          terminalRows: 24,
          hasMeasured: true,
        }),
      ).toEqual({ x: 12, y: 19 });
    },
  );

  it("hides the cursor until both frame and cursor are available", () => {
    expect(
      adaptInkCursorPosition(logical, {
        frameHeight: 0,
        terminalRows: 24,
        hasMeasured: false,
      }),
    ).toBeUndefined();
    expect(
      adaptInkCursorPosition(undefined, {
        frameHeight: 10,
        terminalRows: 24,
        hasMeasured: true,
      }),
    ).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run the bridge test and verify it fails for missing modules**

Run:

```bash
npm --prefix tui test -- tests/components/ink-cursor-bridge.test.ts
```

Expected: FAIL because the cursor bridge and frame metrics modules do not exist.

- [ ] **Step 3: Add the presentation-only frame contract and minimal adapter**

```tsx
// tui/src/components/cursor/terminal-frame-metrics.tsx
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
```

```ts
// tui/src/components/cursor/ink-cursor-bridge.ts
import type { CursorPosition } from "ink";

import type { TerminalFrameMetrics } from "./terminal-frame-metrics.js";

export function adaptInkCursorPosition(
  position: CursorPosition | undefined,
  frame: TerminalFrameMetrics,
): CursorPosition | undefined {
  if (!position || !frame.hasMeasured || frame.terminalRows <= 0) {
    return undefined;
  }
  return frame.frameHeight >= frame.terminalRows
    ? { x: position.x, y: position.y + 1 }
    : position;
}
```

Keep one focused comment beside the fullscreen branch naming Ink 7.1's missing trailing newline behavior. Do not mention Turn numbers.

- [ ] **Step 4: Run the bridge tests**

Run:

```bash
npm --prefix tui test -- tests/components/ink-cursor-bridge.test.ts
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit the renderer contract**

```bash
git add tui/src/components/cursor tui/tests/components/ink-cursor-bridge.test.ts
git commit -m "fix: define ink fullscreen cursor bridge"
```

---

### Task 2: Measure the real frame and route Composer cursor ownership through the bridge

**Files:**
- Modify: `tui/src/components/TerminalSurfaceLayout.tsx`
- Modify: `tui/src/components/use-composer-cursor.ts`
- Modify: `tui/tests/components/composer-cursor.test.tsx`
- Create: `tui/tests/components/fullscreen-composer-cursor.test.tsx`

**Interfaces:**
- Consumes: `TerminalFrameMetricsProvider` and `adaptInkCursorPosition()` from Task 1.
- Produces: one measured root frame for every descendant Composer.
- Preserves: `resolveComposerCursorPosition(options)` as the logical coordinate function.

- [ ] **Step 1: Add an interactive fake TTY regression that crosses the boundary**

Create a focused `CaptureTty` in
`fullscreen-composer-cursor.test.tsx`. It is a Node `Writable` with mutable
`columns`, `rows`, `isTTY = true`, a list of UTF-8 writes, and a `resize(rows)`
method that updates `rows` and emits `resize`:

```ts
class CaptureTty extends Writable {
  readonly isTTY = true;
  columns = 80;
  rows = 12;
  readonly writes: string[] = [];

  override _write(
    chunk: Uint8Array | string,
    _encoding: BufferEncoding,
    done: (error?: Error | null) => void,
  ): void {
    this.writes.push(
      typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8"),
    );
    done();
  }

  resize(rows: number): void {
    this.rows = rows;
    this.emit("resize");
  }
}
```

Render a real `TerminalSurfaceLayout` with Ink `render(..., {
interactive: true, patchConsole: false, stdout })`, a controlled transcript,
and a real `Composer`. Use `renderToString()` on the same React tree to obtain
the complete visible frame and the zero-based line containing `❯`. Decode the
last emitted Ink cursor suffix with:

```ts
const cursorSuffix =
  /(?:\u001B\[(\d+)A)?\u001B\[(\d+)G\u001B\[\?25h/gu;
```

For a frame with `N` visible lines, compute the terminal's physical base row as
`N` below the fullscreen threshold and `N - 1` at or above it. Subtract the
decoded cursor-up count and compare that physical row with the `❯` row. This
assertion inspects Ink's emitted ANSI contract rather than calling the pure
adapter.

Exercise a short transcript, rerender with enough transcript rows to cross the
threshold, and assert:

```ts
const before = await renderCursorFrame({ terminalRows: 12, transcriptRows: 2 });
expect(before.cursorLine).toBe(before.promptLine);

const after = await renderCursorFrame({ terminalRows: 12, transcriptRows: 10 });
expect(after.frameHeight).toBeGreaterThanOrEqual(12);
expect(after.cursorLine).toBe(after.promptLine);
```

Repeat after changing the fake TTY from 12 to 20 rows and emitting `resize`.
Cover the reverse resize direction in a second case, plus frames exactly equal
to and one row above the viewport. When `Composer.active` becomes false, assert
the last cursor state is hidden and no visible cursor suffix remains active.

- [ ] **Step 2: Run the cursor suites and verify fullscreen currently fails**

Run:

```bash
npm --prefix tui test -- tests/components/composer-cursor.test.tsx tests/components/fullscreen-composer-cursor.test.tsx
```

Expected: logical tests pass, but the fullscreen frame assertion places the
cursor one row above `promptLine` until the layout and hook consume the bridge.

- [ ] **Step 3: Publish real frame metrics from `TerminalSurfaceLayout`**

Use one root `Box` ref, `useBoxMetrics()`, and `useWindowSize()`:

```tsx
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
      {/* keep the existing child order exactly */}
    </Box>
  </TerminalFrameMetricsProvider>
);
```

Do not copy these metrics into `App.tsx` or Surface state.

- [ ] **Step 4: Adapt only the final physical position in `useComposerCursor`**

```ts
export function useComposerCursor(options: ComposerCursorOptions): void {
  const frame = useTerminalFrameMetrics();
  const { setCursorPosition } = useCursor();
  const logical = resolveComposerCursorPosition(options);
  setCursorPosition(adaptInkCursorPosition(logical, frame));
}
```

Do not change `resolveComposerCursorPosition()` or add history-sensitive
parameters.

- [ ] **Step 5: Run the focused and surrounding TUI tests**

Run:

```bash
npm --prefix tui test -- tests/components/ink-cursor-bridge.test.ts tests/components/composer-cursor.test.tsx tests/components/fullscreen-composer-cursor.test.tsx tests/components/composer.test.tsx tests/components/command-menu.test.tsx tests/components/secret-input.test.tsx tests/components/app-command-flow.test.tsx tests/structural/input-ownership.test.ts
```

Expected: all tests pass and no rendered frame contains `▌`.

- [ ] **Step 6: Commit the measured integration**

```bash
git add tui/src/components/TerminalSurfaceLayout.tsx tui/src/components/use-composer-cursor.ts tui/tests/components
git commit -m "fix: keep composer cursor aligned in fullscreen frames"
```

---

### Task 3: Lock architecture documentation and complete PR validation

**Files:**
- Modify: `docs/architecture/protocol-and-ink.md`
- Test: `tui/tests/components/ink-cursor-bridge.test.ts`
- Test: `tui/tests/components/fullscreen-composer-cursor.test.tsx`

**Interfaces:**
- Documents: logical Composer position → frame metrics → Ink physical position.
- Removes: any documentation claim that `useBoxMetrics()` alone is sufficient for every Ink frame mode.

- [ ] **Step 1: Update the Ink architecture guide**

Document the following exact boundary:

```text
Composer logical cursor
  -> TerminalFrameMetrics (React-only)
  -> InkCursorBridge (Ink 7.1 fullscreen convention)
  -> useCursor physical position
```

State that the bridge is removable only after a dependency upgrade passes the
below/equal/above-viewport ANSI regression. Do not present it as a Turn-specific
workaround.

- [ ] **Step 2: Run the complete PR validation ladder**

```bash
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- tests/components/ink-cursor-bridge.test.ts tests/components/composer-cursor.test.tsx tests/components/fullscreen-composer-cursor.test.tsx tests/components/composer.test.tsx tests/components/command-menu.test.tsx tests/components/secret-input.test.tsx tests/components/app-command-flow.test.tsx tests/pending-input/app-flow.test.tsx tests/structural/input-ownership.test.ts
npm --prefix tui run build
git diff --check
rg -n "hasHistory|turnCount|transcriptLength|▌" tui/src/components tui/src/app
```

Expected: all commands exit 0; the final search returns no cursor workaround
based on product history and no fake cursor glyph.

- [ ] **Step 3: Perform the Windows host acceptance check**

Run from PowerShell with current development state:

```powershell
uv run awesome-dev
```

Verify a short first Turn and a second Turn that fills the terminal, resize the
window across the threshold, enter Pinyin after CJK and emoji, open and close
the Slash Command Menu, and confirm the preedit anchor remains on the Composer
input row. Record the terminal host and result; do not claim this gate from a
PTY-only test.

- [ ] **Step 4: Commit documentation and open the PR**

```bash
git add docs/architecture/protocol-and-ink.md
git commit -m "docs: define fullscreen cursor boundary"
git status --short
```

Expected: clean worktree after the commit. Push the scoped branch, open a PR
against the accepted integration branch, include automated results and the
manual host result, and merge only after the PR is conflict-free.
