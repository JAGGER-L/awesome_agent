import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { Picker } from "../../src/components/Picker.js";

const selection = {
  prompt: "Choose one",
  options: [
    { value: "a", label: "Alpha", selected: true },
    { value: "b", label: "Beta", selected: false },
  ],
};

describe("Picker", () => {
  it("renders the controlled selection", () => {
    expect(
      render(<Picker selection={selection} selected={1} />).lastFrame(),
    ).toContain("› Beta");
  });

  it("does not own terminal input", () => {
    const view = render(<Picker selection={selection} selected={0} />);
    view.stdin.write("\u001b[B\r");
    expect(view.lastFrame()).toContain("› Alpha");
  });
});
