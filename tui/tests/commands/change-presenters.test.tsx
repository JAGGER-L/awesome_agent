import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { presentCommandPayload } from "../../src/commands/presenters.js";
import { CommandResultView } from "../../src/components/CommandResultView.js";

describe("change command presentation", () => {
  it("shows one folded summary and expands exact paths", () => {
    const presentation = presentCommandPayload("undo", {
      kind: "change",
      action: "undo",
      change_set_id: "change_123",
      lifecycle: "undone",
      restored_paths: ["a.py", "b.py", "c.py"],
    });
    const collapsed =
      render(
        <CommandResultView presentation={presentation} width={80} />,
      ).lastFrame() ?? "";
    expect(collapsed).toContain("✓ Undo · 3 files · Undone · Ctrl+O to expand");
    expect(collapsed).not.toContain("a.py");
    const expanded =
      render(
        <CommandResultView
          presentation={presentation}
          width={80}
          detailsExpanded
        />,
      ).lastFrame() ?? "";
    expect(expanded).toContain("change_123");
    expect(expanded).toContain("a.py");
    expect(expanded).toContain("b.py");
    expect(expanded).toContain("c.py");
  });

  it("renders zero paths without a phantom detail row", () => {
    const presentation = presentCommandPayload("redo", {
      kind: "change",
      action: "redo",
      change_set_id: "change_empty",
      lifecycle: "applied",
      restored_paths: [],
    });
    const frame =
      render(
        <CommandResultView presentation={presentation} width={80} />,
      ).lastFrame() ?? "";
    expect(frame).toContain("✓ Redo · 0 files · Applied");
    expect(frame).not.toContain("File 1");
  });
});
