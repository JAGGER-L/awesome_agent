import { describe, expect, it } from "vitest";

import {
  createPendingInput,
  MAX_PENDING_INPUTS,
  type PendingInput,
} from "../../src/pending-input/model.js";
import {
  initialPendingInputState,
  pendingInputReducer,
} from "../../src/pending-input/reducer.js";

function item(id: string, raw: string, terminalBarrier = false): PendingInput {
  return { id, raw, terminalBarrier };
}

describe("pendingInputReducer", () => {
  it("caps the queue at three without replacing stable identities", () => {
    const inputs = [item("a", "A"), item("b", "B"), item("c", "C")];
    const state = inputs.reduce(
      (current, value) =>
        pendingInputReducer(current, { type: "enqueue", item: value }),
      initialPendingInputState(),
    );
    const full = pendingInputReducer(state, {
      type: "enqueue",
      item: item("d", "D"),
    });

    expect(MAX_PENDING_INPUTS).toBe(3);
    expect(full).toBe(state);
    expect(full.items).toEqual(inputs);
  });

  it("promotes FIFO and recalls LIFO", () => {
    const queued = {
      items: [item("a", "A"), item("b", "B"), item("c", "C")],
    };
    const promoted = pendingInputReducer(queued, {
      type: "accept_head",
      id: "a",
    });
    const recalled = pendingInputReducer(promoted, { type: "recall_tail" });

    expect(promoted.items.map(({ id }) => id)).toEqual(["b", "c"]);
    expect(recalled.items.map(({ id }) => id)).toEqual(["b"]);
  });

  it("requeues the same identity at the head after a busy race", () => {
    const pending = item("a", "A");
    const state = pendingInputReducer(
      { items: [item("b", "B")] },
      { type: "requeue_head", item: pending },
    );

    expect(state.items).toEqual([pending, item("b", "B")]);
  });

  it("blocks later entries after the quit terminal barrier", () => {
    const quit = item("quit", "/quit", true);
    const state = pendingInputReducer(
      { items: [item("a", "A"), quit] },
      { type: "enqueue", item: item("b", "B") },
    );

    expect(state.items).toEqual([item("a", "A"), quit]);
  });

  it("classifies only the quit command as a terminal barrier", () => {
    expect(createPendingInput(" /quit").terminalBarrier).toBe(true);
    expect(createPendingInput("/quit now").terminalBarrier).toBe(true);
    expect(createPendingInput("/quite").terminalBarrier).toBe(false);
    expect(createPendingInput("explain /quit").terminalBarrier).toBe(false);
  });
});
