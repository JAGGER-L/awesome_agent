import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { App } from "../../src/app/App.js";
import { Welcome } from "../../src/components/Welcome.js";
import {
  COMPACT_LOGO_ROWS,
  FULL_LOGO_ROWS,
} from "../../src/components/welcome-logo.js";
import { resolveTheme } from "../../src/preferences/theme.js";
import { terminalDisplayWidth } from "../../src/layout/width.js";
import { createSurfaceStore } from "../../src/state/store.js";

const baseProps = {
  version: "1.1.1",
  workspacePath: "E:\\projects\\awesome",
  thread: { kind: "new" as const },
  model: "deepseek/deepseek-v4-flash",
  thinkingEnabled: false,
  localMemoryEnabled: false,
  mem0Enabled: false,
  permissionMode: "request_approval" as const,
  credentialMissing: false,
  theme: resolveTheme("dark", "truecolor"),
};

describe("Welcome", () => {
  it("exports the exact frameless README and compact glyph fixtures", () => {
    expect(FULL_LOGO_ROWS).toEqual([
      "  ███  █   █ █████ █████  ███  █   █ █████",
      " █   █ █   █ █     █     █   █ ██ ██ █",
      " █████ █ █ █ ████  █████ █   █ █ █ █ ████",
      " █   █ ██ ██ █         █ █   █ █   █ █",
      " █   █ █   █ █████ █████  ███  █   █ █████",
    ]);
    expect(COMPACT_LOGO_ROWS).toEqual([
      " ██  █  █ ████  ███  ██  █  █ ████",
      "█  █ █  █ █    █    █  █ ████ █",
      "████ ████ ███   ██  █  █ ████ ███",
      "█  █ ████ █       █ █  █ █  █ █",
      "█  █  ██  ████ ███   ██  █  █ ████",
    ]);
    expect([...FULL_LOGO_ROWS, ...COMPACT_LOGO_ROWS].join("\n")).not.toMatch(
      /[┃┏┓┗┛]/u,
    );
  });

  it("renders full, compact, and width-diagnostic modes", () => {
    const full = render(<Welcome {...baseProps} width={60} />);
    for (const row of FULL_LOGO_ROWS) expect(full.lastFrame()).toContain(row);

    const compact = render(<Welcome {...baseProps} width={40} />);
    for (const row of COMPACT_LOGO_ROWS) {
      expect(compact.lastFrame()).toContain(row);
    }

    const narrow = render(<Welcome {...baseProps} width={35} />);
    expect(narrow.lastFrame()).toContain(
      "Terminal width 36 or greater required",
    );
    expect(narrow.lastFrame()).not.toContain("████");
  });

  it("renders approved Welcome C metadata without a tagline", () => {
    const view = render(<Welcome {...baseProps} width={80} />);
    expect(view.lastFrame()).toContain("Version      1.1.1");
    expect(view.lastFrame()).toContain("Workspace    E:\\projects\\awesome");
    expect(view.lastFrame()).toContain("Thread       New thread");
    expect(view.lastFrame()).toContain(
      "Model        deepseek/deepseek-v4-flash",
    );
    expect(view.lastFrame()).toContain("Local memory Off");
    expect(view.lastFrame()).toContain("Cloud memory Off");
    expect(view.lastFrame()).toContain("Provider     Mem0 Cloud");
    expect(view.lastFrame()).toContain("Permission   Request approval");
    expect(view.lastFrame()).toContain("/ commands · @ files · ! shell");
    expect(view.lastFrame()).not.toContain("feature/auth");
    expect(view.lastFrame()).not.toContain("Local-first coding agent");
    expect(view.lastFrame()).not.toContain("Local coding session ready");
  });

  it.each([
    80, 100, 120,
  ])("keeps the approved field order at %i columns", (width) => {
    const frame =
      render(<Welcome {...baseProps} width={width} />).lastFrame() ?? "";
    const labels = [
      "Version",
      "Workspace",
      "Thread",
      "Model",
      "Thinking",
      "Local memory",
      "Cloud memory",
      "Provider",
      "Permission",
    ];
    for (let index = 1; index < labels.length; index += 1) {
      expect(frame.indexOf(labels[index - 1] ?? "")).toBeLessThan(
        frame.indexOf(labels[index] ?? ""),
      );
    }
    for (const row of FULL_LOGO_ROWS) expect(frame).toContain(row);
  });

  it("gives the Logo two thirds of a wide Welcome", () => {
    const frame100 =
      render(<Welcome {...baseProps} width={100} />).lastFrame() ?? "";
    const frame120 =
      render(<Welcome {...baseProps} width={120} />).lastFrame() ?? "";
    const frame99 =
      render(<Welcome {...baseProps} width={99} />).lastFrame() ?? "";
    const top100 = frame100.split("\n")[0] ?? "";
    const top120 = frame120.split("\n")[0] ?? "";
    const top99 = frame99.split("\n")[0] ?? "";
    expect(top100.indexOf("╮") + 1).toBe(66);
    expect(top120.indexOf("╮") + 1).toBe(80);
    expect(terminalDisplayWidth(top100)).toBe(100);
    expect(terminalDisplayWidth(top99)).toBe(99);
    expect(top99.match(/╭/gu)).toHaveLength(1);
  });

  it("centers the Logo inside its wide panel", () => {
    const frame =
      render(<Welcome {...baseProps} width={100} />).lastFrame() ?? "";
    for (const row of FULL_LOGO_ROWS) {
      const line = frame
        .split("\n")
        .find((candidate) => candidate.includes(row));
      expect(line).toBeDefined();
      const start = line?.indexOf(row) ?? 0;
      const leftPadding = terminalDisplayWidth((line ?? "").slice(1, start));
      const rightPadding = terminalDisplayWidth(
        (line ?? "").slice(start + row.length, 65),
      );
      expect(Math.abs(leftPadding - rightPadding)).toBeLessThanOrEqual(1);
    }
  });

  it("renders resumed, non-Git, and Kimi metadata", () => {
    const view = render(
      <Welcome
        {...baseProps}
        width={80}
        thread={{ kind: "resumed", title: "Fix auth" }}
        model="kimi/kimi-k2.6"
        thinkingEnabled
        localMemoryEnabled
        mem0Enabled
      />,
    );
    expect(view.lastFrame()).toContain("Thread       Resumed · Fix auth");
    expect(view.lastFrame()).not.toContain("feature/auth");
    expect(view.lastFrame()).toContain("Thinking     On");
    expect(view.lastFrame()).toContain("Local memory On");
    expect(view.lastFrame()).toContain("Cloud memory On");
  });

  it("shows full access as an explicit welcome mode", () => {
    const view = render(
      <Welcome {...baseProps} width={80} permissionMode="full_access" />,
    );
    expect(view.lastFrame()).toContain("Permission   Full access");
  });

  it("renders the same glyph without color support", () => {
    const view = render(
      <Welcome
        {...baseProps}
        width={60}
        theme={resolveTheme("dark", "none")}
      />,
    );
    for (const row of FULL_LOGO_ROWS) expect(view.lastFrame()).toContain(row);
  });

  it("integrates the Welcome into the natural flow above the composer", () => {
    const { width: _width, ...welcome } = { ...baseProps, width: 60 };
    const view = render(
      <App
        store={createSurfaceStore()}
        reportFatal={() => undefined}
        width={60}
        welcome={welcome}
      />,
    );
    expect(view.frames.join("\n")).toContain(FULL_LOGO_ROWS[0]);
    expect(view.lastFrame()).toContain("Message");
  });
});
