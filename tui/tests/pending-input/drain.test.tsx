import { Text } from "ink";
import { render } from "ink-testing-library";
import { useEffect, useRef } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  usePendingInputDrain,
  usePendingInputQueue,
} from "../../src/pending-input/use-pending-input-queue.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
    }
  }
  throw last;
}

function Harness({
  blocked,
  generation,
  promote,
}: {
  readonly blocked: boolean;
  readonly generation: number;
  readonly promote: (raw: string) => Promise<void>;
}) {
  const queue = usePendingInputQueue();
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    queue.enqueue("A");
    queue.enqueue("/status");
    queue.enqueue("!pwd");
  }, [queue]);
  usePendingInputDrain({
    queue,
    blocked,
    promote: async (item) => {
      await promote(item.raw);
      return { kind: "consumed" };
    },
    onError: (error) => {
      throw error;
    },
  });
  return (
    <Text>
      generation {generation} · {queue.items.map((item) => item.raw).join(",")}
    </Text>
  );
}

describe("pending input drain", () => {
  it("drains one head at a time in FIFO order", async () => {
    const order: string[] = [];
    const view = render(
      <Harness
        blocked={false}
        generation={0}
        promote={async (raw) => {
          order.push(raw);
        }}
      />,
    );

    await eventually(() => expect(order).toEqual(["A", "/status", "!pwd"]));
    await eventually(() =>
      expect(view.lastFrame()).toContain("generation 0 ·"),
    );
  });

  it("survives a Thread generation replacement while blocked", async () => {
    const promote = vi.fn(async () => undefined);
    const view = render(<Harness blocked generation={0} promote={promote} />);
    await eventually(() =>
      expect(view.lastFrame()).toContain("A,/status,!pwd"),
    );

    view.rerender(<Harness blocked generation={1} promote={promote} />);
    expect(view.lastFrame()).toContain("generation 1 · A,/status,!pwd");
    expect(promote).not.toHaveBeenCalled();

    view.rerender(<Harness blocked={false} generation={1} promote={promote} />);
    await eventually(() => expect(promote).toHaveBeenCalledTimes(3));
  });
});
