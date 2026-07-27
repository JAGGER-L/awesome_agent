import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { COMMAND_CATALOG } from "../../src/commands/catalog.js";
import { CommandMenu } from "../../src/components/CommandMenu.js";

describe("CommandMenu", () => {
  it("renders only the selected ten-row window and its complete range", () => {
    const selected = COMMAND_CATALOG[12];
    if (!selected) throw new Error("Catalog fixture is incomplete.");
    const frame = render(
      <CommandMenu
        commands={COMMAND_CATALOG}
        selectedCommand={selected.name}
        viewportStart={3}
      />,
    ).lastFrame();

    expect(frame).toContain("4–13 of 30");
    expect(frame?.match(/^\s*[› ]\s*\//gmu)).toHaveLength(10);
    expect(frame).not.toContain("[thread_id]");
  });

  it("renders an explicit empty state without an invalid range", () => {
    const frame = render(
      <CommandMenu commands={[]} viewportStart={0} />,
    ).lastFrame();
    expect(frame).toContain("No matching commands");
    expect(frame).not.toContain("1–0");
  });
});
