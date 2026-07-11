import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import { CommandMenu } from "../../src/components/CommandMenu.js";
import { Composer } from "../../src/components/Composer.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  throw last;
}

describe("Composer", () => {
  it.each([
    40, 60, 120,
  ])("renders multiline input and a cursor at %i columns", (width) => {
    const view = render(
      <Composer
        width={width}
        initialValue={"first\nsecond"}
        onSubmit={async () => ({ accepted: true })}
      />,
    );
    expect(view.lastFrame()).toContain("first");
    expect(view.lastFrame()).toContain("second");
    expect(view.lastFrame()).toContain("▌");
  });

  it("accepts pasted text and clears only after accepted dispatch", async () => {
    const onSubmit = vi.fn(async () => ({ accepted: true as const }));
    const view = render(<Composer width={40} onSubmit={onSubmit} />);
    view.stdin.write("hello\nworld");
    await eventually(() => expect(view.lastFrame()).toContain("hello"));
    expect(view.lastFrame()).toContain("world");
    view.stdin.write("\r");
    await eventually(() =>
      expect(onSubmit).toHaveBeenCalledWith("hello\nworld"),
    );
    await eventually(() => expect(view.lastFrame()).not.toContain("hello"));
  });

  it("retains a retryable draft after an immediate product error", async () => {
    const view = render(
      <Composer
        width={40}
        onSubmit={async () => ({
          accepted: false,
          retryable: true,
          message: "busy",
        })}
      />,
    );
    view.stdin.write("retry me");
    view.stdin.write("\r");
    await eventually(() => expect(view.lastFrame()).toContain("busy"));
    expect(view.lastFrame()).toContain("retry me");
  });

  it("renders the cursor at the grapheme editing position", async () => {
    const view = render(
      <Composer
        width={40}
        initialValue="a😀c"
        onSubmit={async () => ({ accepted: true })}
      />,
    );
    view.stdin.write("\u001b[D");
    await eventually(() => expect(view.lastFrame()).toContain("a😀▌c"));
  });
});

describe("CommandMenu", () => {
  it("opens for slash input, filters, and disappears for ordinary text", () => {
    const view = render(<CommandMenu query="/th" />);
    expect(view.lastFrame()).toContain("/thinking");
    expect(view.lastFrame()).toContain("/theme");
    view.rerender(<CommandMenu query="hello" />);
    expect(view.lastFrame()).toBe("");
  });
});

describe("App composer integration", () => {
  it("places the composer below the transcript at narrow width", () => {
    const view = render(<App store={createSurfaceStore()} width={40} />);
    expect(view.lastFrame()).toContain("Message");
    expect(view.lastFrame()).toContain("▌");
  });
});
