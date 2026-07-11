import type { CoreExit } from "../core/process.js";

export type ExitReason =
  | "quit_command"
  | "ctrl_d"
  | "double_ctrl_c"
  | "trust_denied";

export interface ExitOutcome {
  readonly reason: ExitReason;
  readonly exitCode: 0;
  readonly forced: boolean;
  readonly warning?: string;
}

export interface ExitClock {
  schedule(callback: () => void, delay: number): { cancel(): void };
}

interface ExitSession {
  readonly exit: Promise<CoreExit>;
  requestShutdown(): Promise<void>;
  terminate(): Promise<void>;
}

interface ExitDependencies {
  readonly clock?: ExitClock;
  readonly disableInput: () => void;
  readonly cleanupTerminal: () => Promise<void> | void;
}

const systemClock: ExitClock = {
  schedule(callback, delay) {
    const timer = setTimeout(callback, delay);
    return { cancel: () => clearTimeout(timer) };
  },
};

export class ExitController {
  #exitPromise: Promise<ExitOutcome> | undefined;

  constructor(
    private readonly session: ExitSession,
    private readonly dependencies: ExitDependencies,
  ) {}

  requestExit(reason: ExitReason): Promise<ExitOutcome> {
    if (this.#exitPromise) return this.#exitPromise;
    this.#exitPromise = this.#run(reason);
    return this.#exitPromise;
  }

  async #run(reason: ExitReason): Promise<ExitOutcome> {
    this.dependencies.disableInput();
    let shutdownWarning: string | undefined;
    const clock = this.dependencies.clock ?? systemClock;
    let deadline!: () => void;
    const timedOut = new Promise<"timeout">((resolve) => {
      deadline = () => resolve("timeout");
    });
    const timer = clock.schedule(deadline, 5_000);
    void this.session.requestShutdown().catch((error: unknown) => {
      shutdownWarning =
        error instanceof Error ? error.message : "Core shutdown failed.";
    });

    try {
      const outcome = await Promise.race([
        this.session.exit.then(() => "exited" as const),
        timedOut,
      ]);
      if (outcome === "exited") {
        timer.cancel();
        return {
          reason,
          exitCode: 0,
          forced: false,
          ...(shutdownWarning ? { warning: shutdownWarning } : {}),
        };
      }
      await this.session.terminate();
      return {
        reason,
        exitCode: 0,
        forced: true,
        warning: "Core did not exit within 5 seconds and was terminated.",
      };
    } finally {
      timer.cancel();
      await this.dependencies.cleanupTerminal();
    }
  }
}
