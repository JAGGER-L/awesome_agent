import { Text } from "ink";
import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { TerminalSurfaceLayout } from "../../src/components/TerminalSurfaceLayout.js";

describe("TerminalSurfaceLayout", () => {
  it("renders the terminal in one natural-flow order", () => {
    const frame =
      render(
        <TerminalSurfaceLayout
          welcome={<Text>Welcome</Text>}
          welcomeNotice={<Text>Welcome warning</Text>}
          transcript={<Text>Transcript</Text>}
          activeTurn={<Text>Active Turn</Text>}
          notices={<Text>Notices</Text>}
          commandMenu={<Text>Command Menu</Text>}
          input={<Text>Composer</Text>}
          status={<Text>Status</Text>}
        />,
      ).lastFrame() ?? "";
    const values = [
      "Welcome",
      "Welcome warning",
      "Transcript",
      "Active Turn",
      "Notices",
      "Command Menu",
      "Composer",
      "Status",
    ];

    expect(values.map((value) => frame.indexOf(value))).toEqual(
      [...values]
        .map((value) => frame.indexOf(value))
        .sort((left, right) => left - right),
    );
  });
});
