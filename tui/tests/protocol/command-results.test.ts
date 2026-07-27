import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { loadFixtureCorpus } from "../contracts/fixture-loader.js";

describe("Protocol v4 command outcomes", () => {
  it("accepts every Python valid outcome and rejects every invalid outcome", async () => {
    const corpus = await loadFixtureCorpus();
    const valid = corpus.files["command-results.valid.json"] as {
      cases: { name: string; outcome: unknown }[];
    };
    const invalid = corpus.files["command-results.invalid.json"] as {
      cases: { name: string; outcome: unknown }[];
    };
    expect(invalid.cases.map(({ name }) => name)).toEqual(
      expect.arrayContaining([
        "web_status_empty_diagnostic_code",
        "web_status_invalid_diagnostic_code",
      ]),
    );
    for (const fixture of valid.cases) {
      expect(
        commandOutcomeSchema.safeParse(fixture.outcome).success,
        fixture.name,
      ).toBe(true);
    }
    for (const fixture of invalid.cases) {
      expect(
        commandOutcomeSchema.safeParse(fixture.outcome).success,
        fixture.name,
      ).toBe(false);
    }
  });
});
