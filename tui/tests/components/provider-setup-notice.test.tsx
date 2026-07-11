import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { ProviderSetupNotice } from "../../src/components/ProviderSetupNotice.js";

describe("ProviderSetupNotice", () => {
  it("explains the in-product setup path without naming a home file", () => {
    const view = render(<ProviderSetupNotice />);
    expect(view.lastFrame()).toContain("Choose a model Provider");
    expect(view.lastFrame()).toContain("Enter");
    expect(view.lastFrame()).toContain("/model");
    expect(view.lastFrame()).not.toContain(".env");
  });
});
