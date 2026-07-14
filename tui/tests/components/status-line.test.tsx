import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { App } from "../../src/app/App.js";
import { StatusLine } from "../../src/components/StatusLine.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

describe("StatusLine cancellation", () => {
  it("renders user-facing permission labels instead of internal enums", () => {
    const request = render(
      <StatusLine state={initialSurfaceState()} />,
    ).lastFrame();
    expect(request).toContain("◇ Request approval");
    expect(request).not.toContain("request_approval");
    expect(request).not.toMatch(/ready|idle|active/u);
    const full = render(
      <StatusLine
        state={{
          ...initialSurfaceState(),
          application: { permission_mode: "full_access" } as never,
        }}
      />,
    ).lastFrame();
    expect(full).toContain("◆ Full access");
    expect(full).not.toContain("full_access");
    expect(full).not.toMatch(/ready|idle|active/u);
  });
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
        reportFatal={() => undefined}
        width={60}
      />,
    );
    expect(view.lastFrame()).toContain("Cancelling…");
    expect(view.lastFrame()).not.toContain("Message");
  });

  it("shows cancellation failure while restoring the composer", () => {
    const cancellation = {
      status: "failed" as const,
      operationId: "operation_1",
      message: "Core did not accept cancellation.",
    };
    const view = render(
      <App
        store={createSurfaceStore()}
        cancellation={cancellation}
        reportFatal={() => undefined}
        width={60}
      />,
    );
    expect(view.lastFrame()).toContain("Cancellation failed");
    expect(view.lastFrame()).toContain("Core did not accept cancellation.");
    expect(view.lastFrame()).toContain("Message");
  });
});
