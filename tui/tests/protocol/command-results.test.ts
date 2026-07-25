import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { loadFixtureCorpus } from "../contracts/fixture-loader.js";

describe("Protocol v3 command outcomes", () => {
  it("accepts every Python valid outcome and rejects every invalid outcome", async () => {
    const corpus = await loadFixtureCorpus();
    const valid = corpus.files["command-results.valid.json"] as {
      cases: { name: string; outcome: unknown }[];
    };
    const invalid = corpus.files["command-results.invalid.json"] as {
      cases: { name: string; outcome: unknown }[];
    };
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
