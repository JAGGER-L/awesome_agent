import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { Picker } from "../../src/components/Picker.js";

const selection = {
  prompt: "Choose one",
  options: [
    { value: "a", label: "Alpha", selected: true },
    { value: "b", label: "Beta", selected: false },
  ],
};

describe("Picker", () => {
  it("moves with arrows and selects with Enter", async () => {
    const onSelect = vi.fn();
    const view = render(
      <Picker selection={selection} onSelect={onSelect} onClose={() => {}} />,
    );
    view.stdin.write("\u001b[B");
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    view.stdin.write("\r");
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("closes ordinary pickers with Esc", async () => {
    const onClose = vi.fn();
    const view = render(
      <Picker selection={selection} onSelect={() => {}} onClose={onClose} />,
    );
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    view.stdin.write("\u001b");
    await new Promise<void>((resolve) => setTimeout(resolve, 25));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not dismiss a blocking trust interaction with Esc", async () => {
    const onClose = vi.fn();
    const view = render(
      <Picker
        selection={selection}
        onSelect={() => {}}
        onClose={onClose}
        blocking
      />,
    );
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    view.stdin.write("\u001b");
    await new Promise<void>((resolve) => setTimeout(resolve, 25));
    expect(onClose).not.toHaveBeenCalled();
  });
});
