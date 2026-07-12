import { createInterface } from "node:readline";

const mode = process.env.AWESOME_FAKE_CORE_MODE ?? "normal";
const stderr = process.env.AWESOME_FAKE_CORE_STDERR_BASE64;
if (stderr) process.stderr.write(Buffer.from(stderr, "base64"));
if (mode === "exit-before-handshake") process.exit(23);

const output = (value) => {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`);
  if (process.env.AWESOME_FAKE_CORE_FRAGMENT === "1" && bytes.length > 2) {
    const midpoint = Math.floor(bytes.length / 2);
    process.stdout.write(bytes.subarray(0, midpoint));
    queueMicrotask(() => process.stdout.write(bytes.subarray(midpoint)));
  } else process.stdout.write(bytes);
};

const application = (value) => ({ ok: true, value });
const failure = (code) => ({
  ok: false,
  error: { code, message: `Safe ${code}.`, retryable: false, data: {} },
});
const handleLine = (line) => {
  const request = JSON.parse(line);
  if (request.method === "initialize") {
    if (process.env.AWESOME_FAKE_CORE_EVENT_BEFORE_INIT === "1") {
      output({
        jsonrpc: "2.0",
        method: "event",
        params: {
          version: 1,
          event_id: "event_1",
          sequence: 1,
          session_id: "session_fake",
          workspace_key: "workspace_fake",
          event_type: "warning",
          timestamp: "2026-07-11T08:00:00Z",
          payload: {
            kind: "warning",
            code: "early",
            message: "Early warning.",
          },
        },
      });
    }
    if (mode === "handshake-failure") {
      output({
        jsonrpc: "2.0",
        id: request.id,
        result: failure("client_version_incompatible"),
      });
      return;
    }
    output({
      jsonrpc: "2.0",
      id: request.id,
      result: application({
        product_version: "0.1.0",
        protocol_version: 1,
        status: mode === "trust-required" ? "trust_required" : "ready",
        session_id: "session_fake",
        ...(mode === "trust-required"
          ? { interaction_id: "interaction_fake" }
          : {}),
        workspace: {
          display_path: process.cwd(),
          branch: process.env.AWESOME_FAKE_CORE_MARKER ?? "fake",
        },
        capabilities: ["threads", "turns", "commands"],
      }),
    });
  } else if (request.method === "application.getState") {
    const thread =
      process.env.AWESOME_FAKE_CORE_THREAD === "1" ? "thread_fake" : undefined;
    output({
      jsonrpc: "2.0",
      id: request.id,
      result: application({
        initialized: true,
        session_id: "session_fake",
        workspace_key: "workspace_fake",
        workspace: { display_path: process.cwd(), branch: "fake" },
        workspace_trusted: mode !== "trust-required",
        ...(thread ? { current_thread_id: thread } : {}),
        current_model: "deepseek/deepseek-v4-flash",
        thinking_enabled: false,
        skill_mode: "auto",
        permission_mode: "request_approval",
        configuration_valid: true,
        secret_status: {
          deepseek_api_key: true,
          moonshot_api_key: false,
          mem0_api_key: false,
        },
        provider_credentials: {
          deepseek: {
            provider: "deepseek",
            environment_variable: "DEEPSEEK_API_KEY",
            source: "process_environment",
            mutable: false,
          },
          kimi: {
            provider: "kimi",
            environment_variable: "MOONSHOT_API_KEY",
            source: "missing",
            mutable: true,
          },
        },
        memory_status: {},
        mcp_status: [],
        usage: {},
        configuration_diagnostics: [],
      }),
    });
  } else if (request.method === "thread.read") {
    const now = "2026-07-11T08:00:00Z";
    const terminal = process.env.AWESOME_FAKE_CORE_TERMINAL === "1";
    if (terminal) process.stderr.write("thread-read\n");
    const response = {
      jsonrpc: "2.0",
      id: request.id,
      result: application({
        view: {
          thread: {
            id: request.params.thread_id,
            workspace_key: "workspace_fake",
            title: "Fake Thread",
            current_model: "deepseek/deepseek-v4-flash",
            thinking_enabled: false,
            skill_mode: "auto",
            created_at: now,
            updated_at: now,
          },
          entries: terminal
            ? [
                {
                  id: "entry_user",
                  thread_id: request.params.thread_id,
                  sequence: 1,
                  kind: "user_message",
                  content: "question",
                  metadata: {},
                  created_at: now,
                },
                {
                  id: "entry_assistant",
                  thread_id: request.params.thread_id,
                  sequence: 2,
                  kind: "assistant_message",
                  content: "durable answer",
                  metadata: {},
                  created_at: now,
                },
              ]
            : [],
          turns: terminal
            ? [
                {
                  id: "turn_terminal",
                  thread_id: request.params.thread_id,
                  checkpoint_key: "turn_terminal",
                  status: "completed",
                  provider: "deepseek",
                  model: "deepseek-v4-flash",
                  thinking_enabled: false,
                  skill_mode: "auto",
                  budgets: {
                    model_calls: 32,
                    tool_calls: 64,
                    provider_retries: 2,
                    compressions: 2,
                    active_execution_seconds: 1800,
                    total_context_tokens: 262144,
                  },
                  user_entry_id: "entry_user",
                  assistant_entry_id: "entry_assistant",
                  usage: {
                    input_tokens: 0,
                    output_tokens: 0,
                    reasoning_tokens: 0,
                    cache_read_tokens: 0,
                    cache_write_tokens: 0,
                    model_calls: 0,
                    tool_calls: 0,
                    provider_retries: 0,
                    compressions: 0,
                    active_execution_seconds: 0,
                  },
                  termination_reason: "completed",
                  context_manifest: [],
                  created_at: now,
                  updated_at: now,
                  completed_at: now,
                },
              ]
            : [],
          tool_activities: [],
        },
        change_sets: [],
        has_more: false,
      }),
    };
    const delay = Number(
      process.env.AWESOME_FAKE_CORE_THREAD_READ_DELAY_MS ?? 0,
    );
    if (delay > 0) setTimeout(() => output(response), delay);
    else output(response);
  } else if (request.method === "command.execute") {
    const threadId = request.params.arguments?.[0] ?? "thread_fake";
    output({
      jsonrpc: "2.0",
      id: request.id,
      result: application({
        status: "success",
        content: "",
        data: { thread_id: threadId, title: "Fake Thread" },
      }),
    });
  } else if (request.method === "operation.cancel") {
    if (process.env.AWESOME_FAKE_CORE_TERMINAL === "1") {
      const common = {
        jsonrpc: "2.0",
        method: "event",
      };
      const envelope = (sequence, event_type, payload, identities = {}) => ({
        ...common,
        params: {
          version: 1,
          event_id: `event_${sequence}`,
          sequence,
          session_id: "session_fake",
          workspace_key: "workspace_fake",
          thread_id: "thread_terminal",
          operation_id: request.params.operation_id,
          event_type,
          timestamp: "2026-07-11T08:00:00Z",
          payload,
          ...identities,
        },
      });
      output(
        envelope(1, "operation.started", {
          kind: "operation.started",
          message: "",
        }),
      );
      output(
        envelope(
          2,
          "turn.started",
          { kind: "turn.started" },
          { turn_id: "turn_terminal" },
        ),
      );
      output(
        envelope(
          3,
          "assistant.text.delta",
          { kind: "assistant.text.delta", text: "transient" },
          { turn_id: "turn_terminal" },
        ),
      );
      output(
        envelope(
          4,
          "turn.completed",
          { kind: "turn.completed" },
          { turn_id: "turn_terminal" },
        ),
      );
      output(
        envelope(5, "operation.completed", {
          kind: "operation.completed",
          message: "",
        }),
      );
    }
    output({
      jsonrpc: "2.0",
      id: request.id,
      result: application({
        operation_id: request.params.operation_id,
        cancelled: true,
      }),
    });
  } else if (request.method === "shutdown") {
    output({
      jsonrpc: "2.0",
      id: request.id,
      result: application({ stopped: true }),
    });
    if (mode !== "hang-after-shutdown") setImmediate(() => process.exit(0));
  }
};

const lines = createInterface({
  input: process.stdin,
  crlfDelay: Number.POSITIVE_INFINITY,
});
lines.on("line", handleLine);

await new Promise(() => {});
