import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import type { CommandController } from "../../src/commands/controller.js";
import type { EventEnvelope } from "../../src/protocol/index.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      last = error;
      await new Promise<void>((resolve) => setTimeout(resolve, 5));
    }
  }
  throw last;
}

function activeStore() {
  return createSurfaceStore({
    ...initialSurfaceState(),
    connection: "ready",
    application: applicationState("thread_old"),
    active_operation: { id: "operation_0", status: "active" },
  });
}

describe("App pending input queue", () => {
  it("queues mixed input while active and promotes it FIFO", async () => {
    const submit = vi.fn(
      async (
        routed: { kind: string; intent?: { name: string } },
        threadId: string,
        clientMessageId?: string,
      ) => {
        if (routed.kind === "turn") {
          return accepted("operation_1", threadId, clientMessageId);
        }
        if (routed.kind === "command") {
          return { kind: "result", payload: { kind: "notice", message: "ok" } };
        }
        return accepted("operation_2", threadId);
      },
    );
    const controller = { submit } as unknown as CommandController;
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );

    submitInput(view, "first");
    submitInput(view, "/status");
    submitInput(view, "!pwd");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Pending inputs · 3 of 3"),
    );
    expect(submit).not.toHaveBeenCalled();

    store.dispatch(terminalEvent("operation.completed", "operation_0", 1));
    await eventually(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({
      kind: "turn",
      content: "first",
    });

    store.dispatch(terminalEvent("operation.started", "operation_1", 2));
    store.dispatch(terminalEvent("operation.completed", "operation_1", 3));
    await eventually(() => expect(submit).toHaveBeenCalledTimes(3));
    expect(submit.mock.calls.map((call) => call[0].kind)).toEqual([
      "turn",
      "command",
      "direct",
    ]);
  });

  it("recalls C, then B, then A from the tail into an empty Composer", async () => {
    const store = activeStore();
    const view = render(
      <App store={store} reportFatal={() => undefined} width={80} />,
    );
    for (const value of ["A", "B", "C"]) submitInput(view, value);
    await eventually(() =>
      expect(view.lastFrame()).toContain("Pending inputs · 3 of 3"),
    );

    view.stdin.write("\u001b[A");
    await eventually(() => expect(view.lastFrame()).toContain("❯ C"));
    view.stdin.write("\u0015");
    view.stdin.write("\u001b[A");
    await eventually(() => expect(view.lastFrame()).toContain("❯ B"));
    view.stdin.write("\u0015");
    view.stdin.write("\u001b[A");
    await eventually(() => expect(view.lastFrame()).toContain("❯ A"));
    expect(view.lastFrame()).not.toContain("Pending inputs");
  });

  it("keeps a fourth draft and shows one full notice", async () => {
    const view = render(
      <App store={activeStore()} reportFatal={() => undefined} width={80} />,
    );
    for (const value of ["A", "B", "C", "D"]) submitInput(view, value);

    await eventually(() =>
      expect(view.lastFrame()).toContain(
        "Pending input queue is full (3 of 3).",
      ),
    );
    expect(view.lastFrame()).toContain("❯ D");
    expect(view.lastFrame()?.match(/queue is full/gu)).toHaveLength(1);
  });

  it("keeps later input in the Composer after a queued quit barrier", async () => {
    const view = render(
      <App store={activeStore()} reportFatal={() => undefined} width={80} />,
    );
    submitInput(view, "/quit");
    submitInput(view, "after quit");

    await eventually(() =>
      expect(view.lastFrame()).toContain("Quit is already queued"),
    );
    expect(view.lastFrame()).toContain("❯ after quit");
    expect(view.lastFrame()).toContain("❯ /quit");
  });

  it("requeues an operation-busy race without a failed transcript", async () => {
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
      .mockImplementationOnce(async (_routed, threadId, clientMessageId) =>
        accepted("operation_1", threadId, clientMessageId),
      );
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit } as unknown as CommandController}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );
    submitInput(view, "retry me");
    store.dispatch(terminalEvent("operation.completed", "operation_0", 1));

    await eventually(() => expect(submit).toHaveBeenCalledTimes(2));
    const ids = submit.mock.calls.map((call) => call[2]);
    expect(ids[0]).toBe(ids[1]);
    expect(view.lastFrame()).not.toContain("Another operation is active");
    expect(
      store
        .getState()
        .committed_transcript?.filter(
          (block) => block.kind === "user" && block.status === "failed",
        ),
    ).toEqual([]);
  });

  it("keeps queue order while an accepted Operation awaits its start event", async () => {
    const submit = vi.fn(async (_routed, threadId, clientMessageId) =>
      accepted("operation_1", threadId, clientMessageId),
    );
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit } as unknown as CommandController}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );
    submitInput(view, "first");
    store.dispatch(terminalEvent("operation.completed", "operation_0", 1));
    await eventually(() => expect(submit).toHaveBeenCalledOnce());

    submitInput(view, "second");
    await eventually(() =>
      expect(view.lastFrame()).toContain("Pending inputs · 1 of 3"),
    );
    expect(submit).toHaveBeenCalledOnce();

    store.dispatch(terminalEvent("operation.started", "operation_1", 2));
    store.dispatch(terminalEvent("operation.completed", "operation_1", 3));
    await eventually(() => expect(submit).toHaveBeenCalledTimes(2));
  });

  it("keeps the queue across /new and binds the next message to the new Thread", async () => {
    const submit = vi.fn(async (routed, threadId, clientMessageId) => {
      if (routed.kind === "command" && routed.intent.name === "new") {
        return {
          kind: "result",
          payload: {
            kind: "thread_transition",
            transition: {
              reason: "new",
              application: applicationState("thread_new"),
              thread: threadPage("thread_new"),
            },
          },
        };
      }
      return accepted("operation_1", threadId, clientMessageId);
    });
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit } as unknown as CommandController}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );
    submitInput(view, "/new");
    submitInput(view, "new thread message");
    store.dispatch(terminalEvent("operation.cancelled", "operation_0", 1));

    await eventually(() => expect(submit).toHaveBeenCalledTimes(2));
    expect(submit.mock.calls[1]?.[1]).toBe("thread_new");
    expect(store.getState().application?.current_thread_id).toBe("thread_new");
  });

  it("queues rename behind the active Turn with its exact title", async () => {
    const submit = vi.fn(async (_routed: unknown, _threadId?: string) => ({
      kind: "result",
      payload: { kind: "notice", message: "renamed" },
    }));
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit } as unknown as CommandController}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );

    submitInput(view, "/rename Cube helper");
    expect(submit).not.toHaveBeenCalled();
    store.dispatch(terminalEvent("operation.completed", "operation_0", 1));

    await eventually(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit.mock.calls[0]?.[0]).toMatchObject({
      kind: "command",
      intent: { name: "rename", arguments: ["Cube", "helper"] },
    });
    expect(view.lastFrame()).toContain("❯ /rename Cube helper");
  });

  it("pauses at a /resume picker before using the resumed Thread", async () => {
    const submit = vi.fn(async (routed, threadId, clientMessageId) => {
      if (routed.kind === "command" && routed.intent.name === "resume") {
        return {
          kind: "selection",
          intent: { name: "resume" },
          selection: {
            kind: "selection",
            prompt: "Resume a Thread",
            options: [
              { value: "thread_resumed", label: "Resumed", selected: true },
            ],
          },
        };
      }
      return accepted("operation_1", threadId, clientMessageId);
    });
    const select = vi.fn(async () => ({
      kind: "result",
      payload: {
        kind: "thread_transition",
        transition: {
          reason: "resume",
          application: applicationState("thread_resumed"),
          thread: threadPage("thread_resumed"),
        },
      },
    }));
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit, select } as unknown as CommandController}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );
    submitInput(view, "/resume");
    submitInput(view, "resumed message");
    store.dispatch(terminalEvent("operation.completed", "operation_0", 1));

    await eventually(() =>
      expect(view.lastFrame()).toContain("Resume a Thread"),
    );
    expect(submit).toHaveBeenCalledTimes(1);
    view.stdin.write("\r");
    await eventually(() => expect(submit).toHaveBeenCalledTimes(2));
    expect(select).toHaveBeenCalledOnce();
    expect(submit.mock.calls[1]?.[1]).toBe("thread_resumed");
  });

  it("does not promote pending input while Approval owns interaction", async () => {
    const submit = vi.fn(async (_routed, threadId, clientMessageId) =>
      accepted("operation_1", threadId, clientMessageId),
    );
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit } as unknown as CommandController}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );
    submitInput(view, "after approval");
    store.dispatch({
      type: "event.received",
      generation: 0,
      event: {
        version: 1,
        event_id: "event_interaction_required",
        sequence: 1,
        session_id: "session_1",
        workspace_key: "workspace_1",
        timestamp: "2026-07-14T00:00:00Z",
        event_type: "interaction.required",
        thread_id: "thread_old",
        operation_id: "operation_0",
        payload: {
          kind: "interaction.required",
          interaction_id: "interaction_approval",
          interaction_kind: "tool_approval",
          prompt: "Run tests?",
          operation: "execute",
          target: "pytest",
          choices: [
            { decision: "allow_once", label: "Yes" },
            { decision: "deny", label: "No" },
          ],
        },
      } as EventEnvelope,
    });
    store.dispatch(terminalEvent("operation.completed", "operation_0", 2));

    await eventually(() => expect(view.lastFrame()).toContain("Run tests?"));
    expect(submit).not.toHaveBeenCalled();

    store.dispatch({
      type: "event.received",
      generation: 0,
      event: {
        version: 1,
        event_id: "event_interaction_resolved",
        sequence: 3,
        session_id: "session_1",
        workspace_key: "workspace_1",
        timestamp: "2026-07-14T00:00:01Z",
        event_type: "interaction.resolved",
        thread_id: "thread_old",
        operation_id: "operation_0",
        payload: {
          kind: "interaction.resolved",
          interaction_id: "interaction_approval",
          decision: "allow_once",
        },
      } as EventEnvelope,
    });
    await eventually(() => expect(submit).toHaveBeenCalledOnce());
  });

  it("executes a queued quit only at its ordered position", async () => {
    const order: string[] = [];
    const submit = vi.fn(async (routed) => {
      if (routed.kind === "local") {
        order.push("quit-dispatched");
        return { kind: "local", intent: routed.intent };
      }
      order.push("status");
      return { kind: "result", payload: { kind: "notice", message: "ok" } };
    });
    const requestExit = vi.fn(async () => {
      order.push("exit");
    });
    const store = activeStore();
    const view = render(
      <App
        store={store}
        controller={{ submit } as unknown as CommandController}
        localCommands={
          {
            execute: vi.fn(async () => ({ kind: "shutdown" })),
          } as never
        }
        lifecycle={{
          cancelActiveOperation: async () => undefined,
          requestExit,
        }}
        reportFatal={(error) => {
          throw error;
        }}
        width={80}
      />,
    );
    submitInput(view, "/status");
    submitInput(view, "/quit");
    store.dispatch(terminalEvent("operation.completed", "operation_0", 1));

    await eventually(() => expect(requestExit).toHaveBeenCalledOnce());
    expect(order).toEqual(["status", "quit-dispatched", "exit"]);
  });
});

function submitInput(view: ReturnType<typeof render>, value: string): void {
  view.stdin.write(value);
  view.stdin.write("\r");
}

function accepted(
  operationId: string,
  threadId: string,
  clientMessageId?: string,
) {
  return {
    kind: "accepted",
    operation: {
      operation_id: operationId,
      thread_id: threadId,
      ...(clientMessageId ? { client_message_id: clientMessageId } : {}),
    },
  };
}

function terminalEvent(
  kind: "operation.started" | "operation.completed" | "operation.cancelled",
  operationId: string,
  sequence: number,
): {
  readonly type: "event.received";
  readonly generation: 0;
  readonly event: EventEnvelope;
} {
  return {
    type: "event.received",
    generation: 0,
    event: {
      version: 1,
      event_id: `event_${sequence}`,
      sequence,
      session_id: "session_1",
      workspace_key: "workspace_1",
      thread_id: "thread_old",
      operation_id: operationId,
      event_type: kind,
      timestamp: "2026-07-14T00:00:00Z",
      payload: { kind, message: "" },
    } as EventEnvelope,
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
        title: "New conversation",
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
