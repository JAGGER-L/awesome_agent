import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../src/app/App.js";
import type { CommandController } from "../../src/commands/controller.js";
import { initialSurfaceState } from "../../src/state/reducer.js";
import { createSurfaceStore } from "../../src/state/store.js";

async function eventually(assertion: () => void): Promise<void> {
  let last: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
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

describe("submitted slash command history", () => {
  it("applies authoritative rename metadata before presenting success", async () => {
    const thread = {
      id: "thread_1",
      workspace_key: "workspace_1",
      title: "New conversation",
      title_source: "automatic" as const,
      current_model: "deepseek/deepseek-v4-flash",
      thinking_enabled: true,
      skill_mode: "auto",
      lineage: null,
      created_at: "2026-07-14T00:00:00Z",
      updated_at: "2026-07-14T00:00:00Z",
    };
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: thread.id } as never,
      thread: {
        view: { thread, entries: [], turns: [], tool_activities: [] },
        change_sets: [],
        has_more: false,
      },
    });
    const controller = {
      submit: vi.fn(async () => ({
        kind: "result",
        payload: {
          kind: "thread_renamed",
          thread: { ...thread, title: "Cube helper", title_source: "manual" },
        },
      })),
    } as unknown as CommandController;
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/rename Cube helper");
    view.stdin.write("\r");

    await eventually(() =>
      expect(store.getState().thread?.view.thread.title).toBe("Cube helper"),
    );
    expect(view.lastFrame()).toContain("Conversation renamed · Cube helper");
  });

  it("keeps the Composer visible while the command menu is open", async () => {
    const store = createSurfaceStore();
    const view = render(
      <App store={store} reportFatal={() => undefined} width={80} />,
    );

    view.stdin.write("/");

    await eventually(() => expect(view.lastFrame()).toContain("/new"));
    expect(view.lastFrame()).toContain("❯ /");
    expect(view.lastFrame()).not.toContain("▌");
  });

  it("renders finalized Assistant and Worked blocks once after handoff", async () => {
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      active_operation: {
        id: "operation_1",
        status: "completed",
        turn: {
          id: "turn_1",
          status: "completed",
          started_at: "2026-07-13T00:00:00Z",
          thinking_sequence: 0,
          duration_ms: 2_000,
          timeline: [
            { kind: "assistant", id: "assistant:turn_1:1", text: "answer" },
          ],
        },
      },
    });
    const view = render(
      <App store={store} reportFatal={() => undefined} width={80} />,
    );

    expect(view.lastFrame()?.match(/answer/gu)).toHaveLength(1);
    expect(view.lastFrame()?.match(/Worked for/gu)).toHaveLength(1);

    store.dispatch({
      type: "transcript.reconciled",
      generation: 0,
      operation_id: "operation_1",
      turn_id: "turn_1",
      blocks: [
        { key: "assistant:1", kind: "assistant", text: "answer" },
        { key: "worked:1", kind: "worked", duration_ms: 2_000 },
      ],
    });

    await eventually(() =>
      expect(store.getState().active_operation).toBeUndefined(),
    );
    expect(view.lastFrame()?.match(/answer/gu)).toHaveLength(1);
    expect(view.lastFrame()?.match(/Worked for/gu)).toHaveLength(1);
  });

  it("Tab completes canonically without executing placeholders", async () => {
    const controller = { submit: vi.fn() } as unknown as CommandController;
    const store = createSurfaceStore();
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/res");
    view.stdin.write("\t");

    await eventually(() => expect(view.lastFrame()).toContain("/resume"));
    expect(view.lastFrame()).not.toContain("[thread_id]");
    expect(controller.submit).not.toHaveBeenCalled();
    expect(store.getState().committed_transcript).toBeUndefined();
  });

  it("Enter executes the selected canonical command once", async () => {
    const controller = {
      submit: vi.fn(async () => ({
        kind: "selection",
        intent: { name: "resume" },
        selection: {
          kind: "selection",
          prompt: "Resume a Thread",
          options: [{ value: "thread_1", label: "Thread 1", selected: true }],
        },
      })),
    } as unknown as CommandController;
    const store = createSurfaceStore();
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/res");
    view.stdin.write("\r");

    await eventually(() =>
      expect(view.lastFrame()).toContain("Resume a Thread"),
    );
    expect(controller.submit).toHaveBeenCalledOnce();
    expect(store.getState().committed_transcript).toEqual([
      expect.objectContaining({ kind: "command_input", text: "/resume" }),
    ]);
  });

  it("records the exact command before the Core request resolves", async () => {
    let resolveRequest: ((value: never) => void) | undefined;
    const pending = new Promise<never>((resolve) => {
      resolveRequest = resolve;
    });
    const controller = {
      submit: vi.fn(async () => await pending),
    } as unknown as CommandController;
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: "thread_1" } as never,
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/status");
    view.stdin.write("\r");

    await eventually(() => expect(controller.submit).toHaveBeenCalledOnce());
    expect(store.getState().committed_transcript).toEqual([
      expect.objectContaining({ kind: "command_input", text: "/status" }),
    ]);
    resolveRequest?.({
      kind: "result",
      payload: { kind: "notice", message: "ok" },
    } as never);
  });

  it("retains invalid command input before the visible error", async () => {
    const store = createSurfaceStore();
    const view = render(
      <App store={store} reportFatal={() => undefined} width={80} />,
    );

    view.stdin.write("/unknown argument");
    view.stdin.write("\r");

    await eventually(() =>
      expect(
        store.getState().committed_transcript?.map((block) => block.kind),
      ).toEqual(["command_input", "command_result"]),
    );
    expect(store.getState().committed_transcript?.[0]).toMatchObject({
      text: "/unknown argument",
    });
  });

  it("renders status through the normal command transcript path", async () => {
    const controller = {
      submit: vi.fn(async () => ({
        kind: "result",
        payload: {
          kind: "status",
          snapshot: {
            version: "1.3.0",
            workspace_path: "E:\\workspace",
            thread_title: "Current thread",
            thread_id: "thread_12345678",
            thread_display_id: "thread_12345678",
            model_identity: {
              provider: "deepseek",
              configured_model: "deepseek/deepseek-v4-flash",
              effective_model: "deepseek/deepseek-v4-flash",
              runtime_name: "Awesome Agent",
              fallback_active: false,
            },
            model_status: "configured",
            thinking_enabled: false,
            skill_mode: "auto",
            local_memory_enabled: false,
            mem0_enabled: false,
            mcp_ready: 0,
            mcp_degraded: 0,
            operation_status: "idle",
            operation_id: null,
            configuration_valid: true,
            configuration_diagnostic_count: 0,
            permission_mode: "request_approval",
            credential_source: "awesome",
            credential_source_available: true,
            context_used_tokens: 1024,
            context_budget_tokens: 262144,
            changed_file_count: 0,
          },
        },
      })),
    } as unknown as CommandController;
    const store = createSurfaceStore({
      ...initialSurfaceState(),
      application: { current_thread_id: "thread_1" } as never,
    });
    const view = render(
      <App
        store={store}
        controller={controller}
        reportFatal={() => undefined}
        width={80}
      />,
    );

    view.stdin.write("/status");
    view.stdin.write("\r");

    await eventually(() =>
      expect(
        store.getState().committed_transcript?.map((block) => block.kind),
      ).toEqual(["command_input", "command_result"]),
    );
    expect(view.lastFrame()).toContain("deepseek/deepseek-v4-flash");
  });
});
