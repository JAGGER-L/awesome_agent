import { Writable } from "node:stream";

import { Box, Text, render, renderToString } from "ink";
import { describe, expect, it } from "vitest";

import {
  flushCurrentInkFrame,
  unmountInkApplication,
} from "../../src/cli/main.js";
import { TerminalSurfaceLayout } from "../../src/components/TerminalSurfaceLayout.js";

class CaptureTty extends Writable {
  readonly isTTY = true;
  columns = 100;
  rows = 30;
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
}

function surface(exiting: boolean) {
  return (
    <TerminalSurfaceLayout
      welcome={<Text>AWESOME</Text>}
      transcript={
        <Box flexDirection="column">
          <Text>● Previous response remains visible.</Text>
          <Text>❯ /quit</Text>
        </Box>
      }
      activeTurn={null}
      notices={exiting ? null : <Text>Choose a model Provider</Text>}
      commandMenu={exiting ? null : <Text>/new — Start a new thread</Text>}
      input={exiting ? null : <Text>│ Message</Text>}
      status={exiting ? null : <Text>◇ Request approval</Text>}
    />
  );
}

describe("terminal exit output boundary", () => {
  it("does not emit Ink output after the shell regains control", async () => {
    const tty = new CaptureTty();
    const instance = render(surface(false), {
      stdout: tty as unknown as NodeJS.WriteStream,
      interactive: false,
      patchConsole: false,
      maxFps: 1_000,
    });

    await flushCurrentInkFrame(instance);
    instance.rerender(surface(true));
    await flushCurrentInkFrame(instance);

    const finalFrame = renderToString(surface(true), { columns: tty.columns });
    expect(finalFrame).toContain("Previous response remains visible.");
    expect(finalFrame).toContain("❯ /quit");
    expect(finalFrame).not.toContain("Message");
    expect(finalFrame).not.toContain("Request approval");
    expect(finalFrame).not.toContain("Start a new thread");

    await unmountInkApplication(instance);
    const shellWriteIndex = tty.writes.length;
    tty.write("PS E:\\awesome_agent> ");
    await new Promise<void>((resolve) => setImmediate(resolve));

    expect(tty.writes.slice(shellWriteIndex).join("")).toBe(
      "PS E:\\awesome_agent> ",
    );
  });
});
