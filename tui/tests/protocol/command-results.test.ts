import { describe, expect, it } from "vitest";

import { commandOutcomeSchema } from "../../src/protocol/commands.js";
import { loadFixtureCorpus } from "../contracts/fixture-loader.js";

describe("Protocol v5 command outcomes", () => {
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
        "thread_transition_retry_requires_combined_payload",
        "thread_transition_fork_requires_lineage",
        "thread_transition_new_requires_null_lineage",
        "thread_retry_wrong_transition_reason",
        "thread_retry_wrong_lineage_kind",
        "thread_retry_operation_thread_mismatch",
        "thread_retry_operation_turn_missing",
        "thread_retry_operation_client_message_mismatch",
        "thread_retry_operation_user_entry_missing",
        "thread_retry_operation_turn_terminal",
        "thread_retry_operation_turn_not_last",
        "thread_retry_operation_multiple_in_progress",
        "thread_retry_unknown_field",
        "web_status_empty_diagnostic_code",
        "web_status_invalid_diagnostic_code",
      ]),
    );
    expect(valid.cases.map(({ name }) => name)).toContain(
      "result.thread_retry",
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

  it("requires an explicit nullable lineage field on every Thread", async () => {
    const corpus = await loadFixtureCorpus();
    const valid = corpus.files["command-results.valid.json"] as {
      cases: { name: string; outcome: unknown }[];
    };
    const fixture = valid.cases.find(
      ({ name }) => name === "result.thread_transition",
    );
    if (!fixture) throw new Error("Thread transition fixture is missing");
    const withoutLineage = structuredClone(fixture.outcome) as {
      payload: { transition: { thread: { view: { thread: object } } } };
    };
    const thread = withoutLineage.payload.transition.thread.view.thread as {
      lineage?: unknown;
    };
    delete thread.lineage;

    expect(commandOutcomeSchema.safeParse(withoutLineage).success).toBe(false);
  });
});
