import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

import {
  createRpcClient,
  type LineTransport,
  type RpcClient,
  RpcClosedError,
} from "../protocol/index.js";
import {
  CoreShutdownError,
  CoreSpawnError,
  CoreTerminationError,
} from "./errors.js";
import { StderrRing } from "./stderr-ring.js";

export interface CoreLaunchOptions {
  executable: string;
  cwd: string;
  env: Readonly<Record<string, string | undefined>>;
}

export interface CoreExit {
  readonly code: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly shutdown_requested: boolean;
}

export interface CoreSession {
  readonly rpc: RpcClient;
  readonly exit: Promise<CoreExit>;
  stderrTail(): Uint8Array;
  requestShutdown(): Promise<void>;
  terminate(): Promise<void>;
}

class PipeTransport implements LineTransport {
  readonly readable: AsyncIterable<Uint8Array>;
  #closed = false;

  constructor(readonly child: ChildProcessWithoutNullStreams) {
    this.readable = child.stdout;
  }

  async write(bytes: Uint8Array): Promise<void> {
    if (this.#closed || this.child.stdin.destroyed) {
      throw new RpcClosedError("Core stdin is closed");
    }
    await new Promise<void>((resolve, reject) => {
      this.child.stdin.write(bytes, (error) => {
        if (error) reject(error);
        else resolve();
      });
    });
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    if (!this.child.stdin.destroyed) this.child.stdin.end();
  }
}

class ProcessSession implements CoreSession {
  readonly rpc: RpcClient;
  readonly exit: Promise<CoreExit>;
  readonly #stderr = new StderrRing();
  #shutdownRequested = false;
  #shutdownPromise: Promise<void> | undefined;
  #terminationPromise: Promise<void> | undefined;

  constructor(readonly child: ChildProcessWithoutNullStreams) {
    this.rpc = createRpcClient(new PipeTransport(child));
    child.stderr.on("data", (chunk: Buffer) => this.#stderr.append(chunk));
    this.exit = new Promise((resolve) => {
      const exited = (code: number | null, signal: NodeJS.Signals | null) => {
        resolve({ code, signal, shutdown_requested: this.#shutdownRequested });
      };
      child.once("exit", exited);
      if (child.exitCode !== null || child.signalCode !== null) {
        exited(child.exitCode, child.signalCode);
      }
    });
  }

  stderrTail(): Uint8Array {
    return this.#stderr.tail();
  }

  requestShutdown(): Promise<void> {
    if (this.#shutdownPromise) return this.#shutdownPromise;
    this.#shutdownRequested = true;
    this.#shutdownPromise = this.rpc.request("shutdown", {}).then((result) => {
      if (!result.ok) throw new CoreShutdownError(result.error.message);
    });
    return this.#shutdownPromise;
  }

  terminate(): Promise<void> {
    if (this.#terminationPromise) return this.#terminationPromise;
    this.#terminationPromise = (async () => {
      await this.rpc.close(new RpcClosedError("Core process terminated"));
      if (this.child.exitCode !== null || this.child.signalCode !== null)
        return;
      if (!this.child.kill())
        throw new CoreTerminationError("Unable to terminate Core process");
    })();
    return this.#terminationPromise;
  }
}

export async function startCore(
  options: CoreLaunchOptions,
): Promise<CoreSession> {
  return await startCoreProcess(options, []);
}

/** @internal Test seam for executable fixtures; not exported by core/index.ts. */
export async function startCoreProcess(
  options: CoreLaunchOptions,
  arguments_: readonly string[],
): Promise<CoreSession> {
  const child = spawn(options.executable, [...arguments_], {
    cwd: options.cwd,
    env: { ...process.env, ...options.env },
    shell: false,
    stdio: ["pipe", "pipe", "pipe"],
  });
  await new Promise<void>((resolve, reject) => {
    const spawned = () => {
      child.off("error", failed);
      resolve();
    };
    const failed = (error: Error) => {
      child.off("spawn", spawned);
      reject(
        new CoreSpawnError(
          `Unable to spawn Core executable: ${options.executable}`,
          { cause: error },
        ),
      );
    };
    child.once("spawn", spawned);
    child.once("error", failed);
  });
  return new ProcessSession(child);
}
