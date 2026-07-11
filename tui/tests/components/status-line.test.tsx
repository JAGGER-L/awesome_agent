import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { App } from "../../src/app/App.js";
import { StatusLine } from "../../src/components/StatusLine.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

describe("StatusLine cancellation", () => {
  it("renders one cancelling state while a request is pending", () => {
    const view = render(
      <StatusLine
        state={initialSurfaceState()}
        cancellation={{ status: "requested", operationId: "operation_1" }}
      />,
    );
    expect(view.lastFrame()).toContain("Cancelling…");
    expect(view.lastFrame()).not.toContain("operation.cancel");
  });

  it("keeps the composer unavailable until terminal or recovery", () => {
    const cancellation = {
      status: "requested" as const,
      operationId: "operation_1",
    };
    const view = render(
      <App
        store={createSurfaceStore()}
        cancellation={cancellation}
        width={60}
      />,
    );
    expect(view.lastFrame()).toContain("Cancelling…");
    expect(view.lastFrame()).not.toContain("Message");
  });
});
