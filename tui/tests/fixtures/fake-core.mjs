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
        configuration_valid: true,
        secret_status: {
          deepseek_api_key: true,
          moonshot_api_key: false,
          mem0_api_key: false,
        },
        memory_status: {},
        mcp_status: [],
        usage: {},
        configuration_diagnostics: [],
      }),
    });
  } else if (request.method === "thread.read") {
    const now = "2026-07-11T08:00:00Z";
    output({
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
          entries: [],
          turns: [],
          tool_activities: [],
        },
        change_sets: [],
        has_more: false,
      }),
    });
  } else if (request.method === "operation.cancel") {
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
