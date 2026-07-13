import type { CoreLaunchOptions } from "../core/process.js";
import type { ConnectedSurface } from "../surface/controller.js";
import { connectSurface } from "../surface/controller.js";
import { beginStartup, type StartupResult } from "../surface/startup.js";
import { hydrateThreadPage } from "../transcript/hydrate.js";
import { mergeTranscriptBlocks } from "../transcript/merge.js";
import type { TranscriptBlock } from "../transcript/model.js";

export interface ReconnectContext {
  readonly cwd: string;
  readonly threadId: string;
}

interface ReconnectDependencies {
  readonly executable: string;
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly connect?: (options: CoreLaunchOptions) => Promise<ConnectedSurface>;
  readonly startup?: (
    surface: ConnectedSurface,
    intent: { readonly kind: "resume"; readonly threadId: string },
  ) => Promise<StartupResult>;
  readonly committedBlocks: () => readonly TranscriptBlock[];
}

export class ReconnectError extends Error {
  constructor(
    message: string,
    readonly code: string,
  ) {
    super(message);
    this.name = "ReconnectError";
  }
}

export class ReconnectController {
  #pending: Promise<ConnectedSurface> | undefined;

  constructor(private readonly dependencies: ReconnectDependencies) {}

  reconnect(context: ReconnectContext): Promise<ConnectedSurface> {
    if (this.#pending) return this.#pending;
    const pending = this.#run(context);
    this.#pending = pending;
    void pending.catch(() => {
      if (this.#pending === pending) this.#pending = undefined;
    });
    return pending;
  }

  reset(): void {
    this.#pending = undefined;
  }

  async #run(context: ReconnectContext): Promise<ConnectedSurface> {
    const connect = this.dependencies.connect ?? connectSurface;
    const startup = this.dependencies.startup ?? beginStartup;
    const surface = await connect({
      executable: this.dependencies.executable,
      cwd: context.cwd,
      env: this.dependencies.env,
    });
    try {
      const result = await startup(surface, {
        kind: "resume",
        threadId: context.threadId,
      });
      if (result.kind !== "ready") {
        throw new ReconnectError(
          "Reconnect did not reach a ready workspace.",
          "reconnect_not_ready",
        );
      }
      if (result.thread.kind !== "ready") {
        throw new ReconnectError(
          "Reconnect did not resolve the exact Thread.",
          "thread_ambiguous",
        );
      }
      const title = result.thread.thread.view.thread.title;
      const reconnected: TranscriptBlock = {
        key: `reconnected:${result.application.session_id}`,
        kind: "status",
        message: `Reconnected · ${title}`,
      };
      const durable = hydrateThreadPage(result.thread.thread).blocks;
      surface.store.dispatch({
        type: "transcript.reconciled",
        generation: surface.store.getState().thread_generation,
        blocks: mergeTranscriptBlocks(
          this.dependencies.committedBlocks(),
          [reconnected],
          durable,
        ),
      });
      return surface;
    } catch (error) {
      await surface.close();
      throw error;
    }
  }
}
