import { Writable } from "node:stream";

import { Box, Text, render, renderToString, type Instance } from "ink";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  composerReducer,
  initialComposerState,
} from "../../src/composer/reducer.js";
import { Composer } from "../../src/components/Composer.js";
import { TerminalSurfaceLayout } from "../../src/components/TerminalSurfaceLayout.js";

const SHOW_CURSOR = "\u001B[?25h";
const HIDE_CURSOR = "\u001B[?25l";
// biome-ignore lint/complexity/useRegexLiterals: a constructor keeps terminal control bytes out of the regex literal.
const CURSOR_SUFFIX = new RegExp(
  String.raw`(?:\x1B\[(\d+)A)?\x1B\[(\d+)G\x1B\[\?25h`,
  "gu",
);

class CaptureTty extends Writable {
  readonly isTTY = true;
  columns = 80;
  rows: number;
  readonly writes: string[] = [];

  constructor(rows: number) {
    super();
    this.rows = rows;
  }

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

  clearWrites(): void {
    this.writes.length = 0;
  }

  resize(rows: number): void {
    this.rows = rows;
    this.emit("resize");
  }
}

const mounted: Instance[] = [];

afterEach(() => {
  for (const instance of mounted.splice(0)) {
    instance.unmount();
    instance.cleanup();
  }
});

function composerState() {
  return composerReducer(initialComposerState(), { type: "resize", width: 72 });
}

function surface(
  transcriptRows: number,
  active = true,
  activeTurnRows = 0,
  composerVisible = true,
): ReactElement {
  return (
    <TerminalSurfaceLayout
      transcript={
        <Box flexDirection="column">
          {Array.from(
            { length: transcriptRows },
            (_, index) => `history ${index + 1}`,
          ).map((line) => (
            <Text key={line}>{line}</Text>
          ))}
        </Box>
      }
      activeTurn={
        activeTurnRows > 0 ? (
          <Text>
            {Array.from(
              { length: activeTurnRows },
              (_, index) => `response ${index + 1}`,
            ).join("\n")}
          </Text>
        ) : null
      }
      input={
        composerVisible ? (
          <Composer state={composerState()} active={active} />
        ) : (
          <Box height={4}>
            <Text>Exclusive interaction</Text>
          </Box>
        )
      }
      status={null}
    />
  );
}

async function eventually(assertion: () => void): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      lastError = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  throw lastError;
}

async function mountSurface(
  tty: CaptureTty,
  transcriptRows: number,
): Promise<Instance> {
  const instance = render(surface(transcriptRows), {
    stdout: tty as unknown as NodeJS.WriteStream,
    interactive: true,
    patchConsole: false,
    maxFps: 1_000,
  });
  mounted.push(instance);
  await eventually(() => expect(tty.writes.join("")).toContain(SHOW_CURSOR));
  await instance.waitUntilRenderFlush();
  return instance;
}

function cursorFrame(
  tty: CaptureTty,
  transcriptRows: number,
  activeTurnRows = 0,
): {
  readonly frameHeight: number;
  readonly promptLine: number;
  readonly cursorLine: number;
  readonly cursorLines: readonly number[];
} {
  const frame = renderToString(surface(transcriptRows, true, activeTurnRows), {
    columns: tty.columns,
  });
  const lines = frame.split("\n");
  const promptLine = lines.findIndex((line) => line.includes("❯"));
  expect(promptLine).toBeGreaterThanOrEqual(0);

  const matches = [...tty.writes.join("").matchAll(CURSOR_SUFFIX)];
  const physicalBase =
    lines.length >= tty.rows ? lines.length - 1 : lines.length;
  const cursorLines = matches.map(
    (suffix) => physicalBase - Number(suffix[1] ?? 0),
  );
  const cursorLine = cursorLines.at(-1);
  expect(cursorLine).toBeDefined();
  return {
    frameHeight: lines.length,
    promptLine,
    cursorLine: cursorLine ?? -1,
    cursorLines,
  };
}

function expectOnlyPromptCursors(
  tty: CaptureTty,
  transcriptRows: number,
  activeTurnRows = 0,
): void {
  const frame = cursorFrame(tty, transcriptRows, activeTurnRows);
  expect(frame.cursorLines.length).toBeGreaterThan(0);
  expect(frame.cursorLines.every((line) => line === frame.promptLine)).toBe(
    true,
  );
}

async function waitForPromptCursorFrames(
  instance: Instance,
  tty: CaptureTty,
  transcriptRows: number,
  activeTurnRows = 0,
): Promise<void> {
  await eventually(() =>
    expectOnlyPromptCursors(tty, transcriptRows, activeTurnRows),
  );
  await instance.waitUntilRenderFlush();
  expectOnlyPromptCursors(tty, transcriptRows, activeTurnRows);
}

describe("fullscreen Composer cursor", () => {
  it("never publishes a stale cursor while an active response gains lines", async () => {
    const tty = new CaptureTty(12);
    const instance = await mountSurface(tty, 2);

    for (const activeTurnRows of [1, 2, 6, 7]) {
      tty.clearWrites();
      instance.rerender(surface(2, true, activeTurnRows));
      await waitForPromptCursorFrames(instance, tty, 2, activeTurnRows);
    }
  });

  it("stays on the prompt when content crosses the viewport threshold", async () => {
    const tty = new CaptureTty(12);
    const instance = await mountSurface(tty, 2);
    expectOnlyPromptCursors(tty, 2);

    tty.clearWrites();
    instance.rerender(surface(8));
    await waitForPromptCursorFrames(instance, tty, 8);
    const equal = cursorFrame(tty, 8);
    expect(equal.frameHeight).toBe(12);
    expect(equal.cursorLine).toBe(equal.promptLine);

    tty.clearWrites();
    instance.rerender(surface(9));
    await waitForPromptCursorFrames(instance, tty, 9);
    const above = cursorFrame(tty, 9);
    expect(above.frameHeight).toBe(13);
    expect(above.cursorLine).toBe(above.promptLine);
  });

  it("recalculates the physical anchor across terminal resize", async () => {
    const tty = new CaptureTty(12);
    const instance = await mountSurface(tty, 8);
    expectOnlyPromptCursors(tty, 8);

    tty.clearWrites();
    tty.resize(20);
    await waitForPromptCursorFrames(instance, tty, 8);

    tty.clearWrites();
    tty.resize(10);
    await waitForPromptCursorFrames(instance, tty, 8);
  });

  it("hides the native cursor when Composer releases ownership", async () => {
    const tty = new CaptureTty(20);
    const instance = await mountSurface(tty, 2);

    tty.clearWrites();
    instance.rerender(surface(2, false));
    await eventually(() => expect(tty.writes.join("")).toContain(HIDE_CURSOR));
    await instance.waitUntilRenderFlush();
    expect(tty.writes.join("")).not.toContain(SHOW_CURSOR);
  });

  it("reanchors the cursor after the Composer remounts", async () => {
    const tty = new CaptureTty(20);
    const instance = await mountSurface(tty, 2);

    tty.clearWrites();
    instance.rerender(surface(2, true, 0, false));
    await eventually(() => expect(tty.writes.join("")).toContain(HIDE_CURSOR));
    await instance.waitUntilRenderFlush();
    expect(tty.writes.join("")).not.toContain(SHOW_CURSOR);

    tty.clearWrites();
    instance.rerender(surface(2));
    await waitForPromptCursorFrames(instance, tty, 2);
  });
});
