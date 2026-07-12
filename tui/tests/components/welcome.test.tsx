import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { App } from "../../src/app/App.js";
import { Welcome } from "../../src/components/Welcome.js";
import {
  COMPACT_LOGO_ROWS,
  FULL_LOGO_ROWS,
} from "../../src/components/welcome-logo.js";
import { resolveTheme } from "../../src/preferences/theme.js";
import { createSurfaceStore } from "../../src/state/store.js";

const baseProps = {
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

  it("renders approved new-thread metadata without a tagline or version", () => {
    const view = render(<Welcome {...baseProps} width={80} />);
    expect(view.lastFrame()).toContain("E:\\projects\\awesome · New thread");
    expect(view.lastFrame()).toContain(
      "deepseek/deepseek-v4-flash · Thinking off · Memory off · Permissions request approval",
    );
    expect(view.lastFrame()).toContain("/ commands · @ files · ! shell");
    expect(view.lastFrame()).not.toContain("feature/auth");
    expect(view.lastFrame()).not.toContain("Local-first coding agent");
    expect(view.lastFrame()).not.toContain("0.1.0");
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
    expect(view.lastFrame()).toContain(
      "E:\\projects\\awesome · Resumed · Fix auth",
    );
    expect(view.lastFrame()).not.toContain("feature/auth");
    expect(view.lastFrame()).toContain(
      "kimi/kimi-k2.6 · Thinking on · Memory local + Mem0 · Permissions request approval",
    );
  });

  it("shows full access as an explicit welcome mode", () => {
    const view = render(
      <Welcome {...baseProps} width={80} permissionMode="full_access" />,
    );
    expect(view.lastFrame()).toContain("Permissions full access");
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

  it("integrates the Welcome into static scrollback above the composer", () => {
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
