import { describe, expect, it, vi } from "vitest";

import type {
  CommandController,
  CommandDispatchOutcome,
} from "../../src/commands/controller.js";
import type { PendingInput } from "../../src/pending-input/model.js";
import type { SurfaceAction } from "../../src/state/actions.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import {
  createSurfaceStore,
  type SurfaceStore,
} from "../../src/state/store.js";
import {
  SubmissionCoordinator,
  type SubmissionEffects,
} from "../../src/app/submission-coordinator.js";

describe("SubmissionCoordinator", () => {
  it("correlates a fresh optimistic Turn with the accepted client identity", async () => {
    const { actions, store } = observedStore();
    const submit = vi.fn(
      async (_routed: unknown, threadId: string, clientMessageId?: string) =>
        accepted("operation_1", threadId, clientMessageId),
    );
    const applyOutcome = vi.fn(async () => ({
      accepted: true,
      operationId: "operation_1",
    }));
    const coordinator = coordinatorWith(store, submit, { applyOutcome });

    await expect(coordinator.submit("hello")).resolves.toMatchObject({
      accepted: true,
      operationId: "operation_1",
    });

    const clientMessageId = submit.mock.calls[0]?.[2];
    expect(clientMessageId).toMatch(/^client_/u);
    expect(
      actions
        .filter(
          (action) =>
            action.type === "transcript.user.pending" ||
            action.type === "transcript.user.accepted",
        )
        .map((action) => [action.type, action.client_message_id]),
    ).toEqual([
      ["transcript.user.pending", clientMessageId],
      ["transcript.user.accepted", clientMessageId],
    ]);
    expect(applyOutcome).toHaveBeenCalledOnce();
  });

  it("returns a typed busy race without projecting a queued Turn as failed", async () => {
    const { actions, store } = observedStore();
    const submit = vi
      .fn()
      .mockResolvedValueOnce({
        kind: "error",
        error: {
          code: "operation_busy",
          message: "Another operation is active.",
          retryable: true,
          data: {},
        },
      })
      .mockImplementationOnce(
        async (_routed, threadId: string, clientMessageId?: string) =>
          accepted("operation_1", threadId, clientMessageId),
      );
    const pending: PendingInput = {
      id: "pending_1",
      raw: "retry me",
      clientMessageId: "client_stable",
      terminalBarrier: false,
    };
    const coordinator = coordinatorWith(store, submit);

    await expect(coordinator.submit(pending.raw, pending)).resolves.toEqual({
      accepted: false,
      retryable: true,
      operationBusy: true,
    });
    expect(
      actions.some((action) => action.type === "transcript.user.failed"),
    ).toBe(false);

    await expect(
      coordinator.submit(pending.raw, pending),
    ).resolves.toMatchObject({ accepted: true });
    expect(submit.mock.calls.map((call) => call[2])).toEqual([
      "client_stable",
      "client_stable",
    ]);
  });

  it("does not commit a late acceptance after the Thread generation changes", async () => {
    const { actions, store } = observedStore();
    let complete: ((outcome: CommandDispatchOutcome) => void) | undefined;
    const submit = vi.fn<
      (
        routed: unknown,
        threadId: string | undefined,
        clientMessageId?: string,
      ) => Promise<CommandDispatchOutcome>
    >(
      async () =>
        await new Promise<CommandDispatchOutcome>((resolve) => {
          complete = resolve;
        }),
    );
    const coordinator = coordinatorWith(store, submit);

    const pending = coordinator.submit("old thread");
    const clientMessageId = submit.mock.calls[0]?.[2] as string;
    store.dispatch({
      type: "thread.replaced",
      application: applicationState("thread_new"),
      thread: threadPage("thread_new"),
      transcript: [],
    });
    complete?.(accepted("operation_old", "thread_old", clientMessageId));
    await pending;

    expect(
      actions.filter((action) => action.type === "transcript.user.accepted"),
    ).toEqual([]);
  });

  it("records a queued command only after it wins Core admission", async () => {
    const { actions, store } = observedStore();
    let complete: ((outcome: CommandDispatchOutcome) => void) | undefined;
    const submit = vi.fn(
      async () =>
        await new Promise<CommandDispatchOutcome>((resolve) => {
          complete = resolve;
        }),
    );
    const coordinator = coordinatorWith(store, submit);
    const queued: PendingInput = {
      id: "pending_status",
      raw: "/status",
      terminalBarrier: false,
    };

    const result = coordinator.submit(queued.raw, queued);
    expect(
      actions.some((action) => action.type === "transcript.command.submitted"),
    ).toBe(false);
    complete?.({
      kind: "result",
      payload: { kind: "notice", message: "ready" },
    });
    await result;

    expect(
      actions.filter(
        (action) => action.type === "transcript.command.submitted",
      ),
    ).toHaveLength(1);
  });

  it.each([
    {
      outcome: {
        kind: "result",
        payload: {
          kind: "compact",
          old_covered_entry_sequence: 0,
          new_covered_entry_sequence: 4,
          usage: {
            input_tokens: 10,
            output_tokens: 2,
            reasoning_tokens: 0,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            model_calls: 1,
            tool_calls: 0,
            provider_retries: 0,
            compressions: 1,
            active_execution_seconds: 0.2,
          },
        },
      } satisfies CommandDispatchOutcome,
      message: "Context compressed",
    },
    {
      outcome: {
        kind: "command_error",
        code: "nothing_to_compact",
        message: "Context is already compact.",
      } satisfies CommandDispatchOutcome,
      message: "Context compression failed · Context is already compact.",
    },
  ])("closes compact progress for $outcome.kind", async ({
    outcome,
    message,
  }) => {
    const { store } = observedStore();
    const finishProgress = vi.fn();
    const beginProgress = vi.fn(() => finishProgress);
    const coordinator = coordinatorWith(
      store,
      vi.fn(async () => outcome),
      { beginProgress },
    );

    await coordinator.submit("/compact");

    expect(beginProgress).toHaveBeenCalledWith(
      "compact",
      "Compressing context...",
      0,
    );
    expect(finishProgress).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "progress", message }),
    );
  });
});

function coordinatorWith(
  store: SurfaceStore,
  submit: ReturnType<typeof vi.fn>,
  overrides: Partial<SubmissionEffects> = {},
): SubmissionCoordinator {
  const effects: SubmissionEffects = {
    clearNotice: vi.fn(),
    appendInputError: vi.fn(),
    beginProgress: vi.fn(() => vi.fn()),
    applyOutcome: vi.fn(async (outcome) => ({
      accepted: true,
      ...(outcome.kind === "accepted"
        ? { operationId: outcome.operation.operation_id }
        : {}),
    })),
    ...overrides,
  };
  return new SubmissionCoordinator(
    store,
    { submit } as unknown as CommandController,
    effects,
  );
}

function observedStore(): {
  readonly actions: SurfaceAction[];
  readonly store: SurfaceStore;
} {
  const base = createSurfaceStore({
    ...initialSurfaceState(),
    connection: "ready",
    application: applicationState("thread_old"),
  });
  const actions: SurfaceAction[] = [];
  return {
    actions,
    store: {
      getState: base.getState,
      subscribe: base.subscribe,
      dispatch(action) {
        actions.push(action);
        base.dispatch(action);
      },
    },
  };
}

function accepted(
  operationId: string,
  threadId: string,
  clientMessageId?: string,
): CommandDispatchOutcome {
  return {
    kind: "accepted",
    operation: {
      operation_id: operationId,
      thread_id: threadId,
      ...(clientMessageId ? { client_message_id: clientMessageId } : {}),
    },
  };
}

function applicationState(threadId: string) {
  return {
    current_thread_id: threadId,
    permission_mode: "request_approval",
    provider_credentials: {},
  } as never;
}

function threadPage(threadId: string) {
  return {
    has_more: false,
    view: {
      thread: {
        id: threadId,
        workspace_key: "workspace_1",
        title: "Conversation",
        thinking_enabled: false,
        skill_mode: "auto",
        created_at: "2026-07-14T00:00:00Z",
        updated_at: "2026-07-14T00:00:00Z",
      },
      entries: [],
      turns: [],
      tool_activities: [],
    },
    change_sets: [],
  } as never;
}
