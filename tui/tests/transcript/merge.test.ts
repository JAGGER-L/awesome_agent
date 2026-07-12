import { describe, expect, it } from "vitest";

import { mergeTranscriptBlocks } from "../../src/transcript/merge.js";
import type { UserBlock } from "../../src/transcript/model.js";

function user(
  clientMessageId: string,
  status: UserBlock["status"],
  text = "same text",
): UserBlock {
  return {
    key: `user:${clientMessageId}`,
    kind: "user",
    client_message_id: clientMessageId,
    status,
    text,
  };
}

describe("mergeTranscriptBlocks", () => {
  it("replaces an optimistic user block only by client identity", () => {
    const merged = mergeTranscriptBlocks(
      [user("client_1", "pending")],
      [user("client_1", "persisted")],
    );

    expect(merged).toEqual([user("client_1", "persisted")]);
  });

  it("keeps equal text from different client messages", () => {
    expect(
      mergeTranscriptBlocks(
        [user("client_1", "pending")],
        [user("client_2", "persisted")],
      ),
    ).toHaveLength(2);
  });
});
