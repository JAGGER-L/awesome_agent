import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { PendingInputList } from "../../src/components/PendingInputList.js";

describe("PendingInputList", () => {
  it("renders confirmed inline option A between turns and the Composer", () => {
    const frame = render(
      <PendingInputList
        items={[
          { id: "a", raw: "add unit tests", terminalBarrier: false },
          { id: "b", raw: "/status", terminalBarrier: false },
        ]}
      />,
    ).lastFrame();

    expect(frame).toContain("Pending inputs · 2 of 3");
    expect(frame).toContain("❯ add unit tests");
    expect(frame).toContain("Queued · Next · ↑ recalls latest");
    expect(frame).toContain("❯ /status");
    expect(frame).toContain("Queued · 2");
  });

  it("renders multiline input as one queue entry", () => {
    const frame = render(
      <PendingInputList
        items={[{ id: "a", raw: "first\nsecond", terminalBarrier: false }]}
      />,
    ).lastFrame();

    expect(frame).toContain("first ↵ second");
  });
});
