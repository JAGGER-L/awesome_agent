import { Text } from "ink";
import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { AppErrorBoundary } from "../../src/components/AppErrorBoundary.js";

function Broken(): never {
  throw new Error("sensitive render value");
}

describe("AppErrorBoundary", () => {
  it("stops component updates and reports one render fault", () => {
    const onError = vi.fn();
    const view = render(
      <AppErrorBoundary
        onError={onError}
        fallback={<Text>Fatal fallback</Text>}
      >
        <Broken />
      </AppErrorBoundary>,
    );
    expect(view.lastFrame()).toContain("Fatal fallback");
    expect(view.lastFrame()).not.toContain("sensitive render value");
    expect(onError).toHaveBeenCalledOnce();
  });
});
