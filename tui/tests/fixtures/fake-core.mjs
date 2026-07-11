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
const handleLine = (line) => {
  const request = JSON.parse(line);
  if (request.method === "initialize") {
    output({
      jsonrpc: "2.0",
      id: request.id,
      result: application({
        product_version: "0.1.0",
        protocol_version: 1,
        status: "ready",
        session_id: "session_fake",
        workspace: {
          display_path: process.cwd(),
          branch: process.env.AWESOME_FAKE_CORE_MARKER ?? "fake",
        },
        capabilities: ["threads", "turns", "commands"],
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
