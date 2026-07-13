import { describe, expect, it } from "vitest";

import {
  initialSurfaceState,
  surfaceReducer,
} from "../../src/state/reducer.js";
import { createCommandSubmissionId } from "../../src/transcript/identity.js";

describe("slash command transcript input", () => {
  it("records the exact submitted command for the active generation", () => {
    const state = surfaceReducer(initialSurfaceState(), {
      type: "transcript.command.submitted",
      submission_id: "command_11111111111111111111111111111111",
      text: "/resume thread_abcd",
      generation: 0,
    });

    expect(state.committed_transcript).toEqual([
      {
        key: "command:command_11111111111111111111111111111111",
        kind: "command_input",
        submission_id: "command_11111111111111111111111111111111",
        text: "/resume thread_abcd",
      },
    ]);
    expect(state.transcript_persisted).toBe(false);
  });

  it("ignores submissions from an older thread generation", () => {
    const state = surfaceReducer(
      { ...initialSurfaceState(), thread_generation: 2 },
      {
        type: "transcript.command.submitted",
        submission_id: "command_11111111111111111111111111111111",
        text: "/status",
        generation: 1,
      },
    );

    expect(state.committed_transcript).toBeUndefined();
  });

  it("creates stable UUID-backed command identities once per submission", () => {
    const first = createCommandSubmissionId();
    const second = createCommandSubmissionId();

    expect(first).toMatch(/^command_[a-f0-9]{32}$/u);
    expect(second).toMatch(/^command_[a-f0-9]{32}$/u);
    expect(second).not.toBe(first);
  });
});
