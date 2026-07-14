import { Writable } from "node:stream";

import {
  Box,
  Text,
  render,
  renderToString,
  type Instance,
} from "ink";
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
const CURSOR_SUFFIX =
  /(?:\u001B\[(\d+)A)?\u001B\[(\d+)G\u001B\[\?25h/gu;

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

function surface(transcriptRows: number, active = true): ReactElement {
  return (
    <TerminalSurfaceLayout
      transcript={
        <Box flexDirection="column">
          {Array.from({ length: transcriptRows }, (_, index) => (
            <Text key={index}>history {index + 1}</Text>
          ))}
        </Box>
      }
      activeTurn={null}
      input={<Composer state={composerState()} active={active} />}
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
  return instance;
}

function cursorFrame(
  tty: CaptureTty,
  transcriptRows: number,
): {
  readonly frameHeight: number;
  readonly promptLine: number;
  readonly cursorLine: number;
} {
  const frame = renderToString(surface(transcriptRows), {
    columns: tty.columns,
  });
  const lines = frame.split("\n");
  const promptLine = lines.findIndex((line) => line.includes("❯"));
  expect(promptLine).toBeGreaterThanOrEqual(0);

  const matches = [...tty.writes.join("").matchAll(CURSOR_SUFFIX)];
  const suffix = matches.at(-1);
  expect(suffix).toBeDefined();
  const moveUp = Number(suffix?.[1] ?? 0);
  const physicalBase =
    lines.length >= tty.rows ? lines.length - 1 : lines.length;
  return {
    frameHeight: lines.length,
    promptLine,
    cursorLine: physicalBase - moveUp,
  };
}

async function waitForMeasuredCursor(
  tty: CaptureTty,
  transcriptRows: number,
): Promise<void> {
  await eventually(() => {
    const frame = cursorFrame(tty, transcriptRows);
    expect(frame.cursorLine).toBe(frame.promptLine);
  });
}

describe("fullscreen Composer cursor", () => {
  it("stays on the prompt when content crosses the viewport threshold", async () => {
    const tty = new CaptureTty(12);
    const instance = await mountSurface(tty, 2);
    expect(cursorFrame(tty, 2).cursorLine).toBe(cursorFrame(tty, 2).promptLine);

    tty.clearWrites();
    instance.rerender(surface(8));
    await waitForMeasuredCursor(tty, 8);
    const equal = cursorFrame(tty, 8);
    expect(equal.frameHeight).toBe(12);
    expect(equal.cursorLine).toBe(equal.promptLine);

    tty.clearWrites();
    instance.rerender(surface(9));
    await waitForMeasuredCursor(tty, 9);
    const above = cursorFrame(tty, 9);
    expect(above.frameHeight).toBe(13);
    expect(above.cursorLine).toBe(above.promptLine);
  });

  it("recalculates the physical anchor across terminal resize", async () => {
    const tty = new CaptureTty(12);
    await mountSurface(tty, 8);
    expect(cursorFrame(tty, 8).cursorLine).toBe(cursorFrame(tty, 8).promptLine);

    tty.clearWrites();
    tty.resize(20);
    await waitForMeasuredCursor(tty, 8);
    expect(cursorFrame(tty, 8).cursorLine).toBe(cursorFrame(tty, 8).promptLine);

    tty.clearWrites();
    tty.resize(10);
    await waitForMeasuredCursor(tty, 8);
    expect(cursorFrame(tty, 8).cursorLine).toBe(cursorFrame(tty, 8).promptLine);
  });

  it("hides the native cursor when Composer releases ownership", async () => {
    const tty = new CaptureTty(20);
    const instance = await mountSurface(tty, 2);

    tty.clearWrites();
    instance.rerender(surface(2, false));
    await eventually(() => expect(tty.writes.join("")).toContain(HIDE_CURSOR));
    expect(tty.writes.join("")).not.toContain(SHOW_CURSOR);
  });
});
