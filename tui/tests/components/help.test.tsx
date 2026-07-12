import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { Help } from "../../src/components/Help.js";

describe("Help", () => {
  it("groups the complete command catalog by owner", () => {
    const view = render(<Help onClose={() => {}} />);
    expect(view.lastFrame()).toContain("Application");
    expect(view.lastFrame()).toContain("Skills");
    expect(view.lastFrame()).toContain("Ink local");
    for (const command of ["/new", "/init", "/help", "/quit"]) {
      expect(view.lastFrame()).toContain(command);
    }
  });

  it("shows usage, ownership, description, and examples for one command", () => {
    const view = render(<Help command="thinking" onClose={() => {}} />);
    expect(view.lastFrame()).toContain("/thinking [on|off]");
    expect(view.lastFrame()).toContain("Owner: application");
    expect(view.lastFrame()).toContain("Show or choose thinking mode");
    expect(view.lastFrame()).toContain("Examples");
  });

  it.each([
    "editor",
    "details",
    "unknown",
  ])("shows local not-found for %s", (command) => {
    const view = render(<Help command={command} onClose={() => {}} />);
    expect(view.lastFrame()).toContain(`No command named /${command}`);
  });

  it("closes with Esc", async () => {
    const onClose = vi.fn();
    const view = render(<Help onClose={onClose} />);
    view.stdin.write("\u001b");
    await new Promise<void>((resolve) => setTimeout(resolve, 25));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
