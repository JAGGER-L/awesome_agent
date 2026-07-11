import type { ConnectedSurface } from "../surface/controller.js";
import type { ExitOutcome, ExitReason } from "./exit.js";
import type { ReconnectContext } from "./reconnect.js";

export interface LifecycleCoordinator {
  cancelActiveOperation(): Promise<void>;
  requestExit(reason: ExitReason): Promise<ExitOutcome>;
  reconnect(): Promise<ConnectedSurface>;
}

export class DefaultLifecycleCoordinator implements LifecycleCoordinator {
  constructor(
    private readonly cancellation: {
      cancelActiveOperation(): Promise<void>;
    },
    private readonly exit: {
      requestExit(reason: ExitReason): Promise<ExitOutcome>;
    },
    private readonly recovery: {
      reconnect(context: ReconnectContext): Promise<ConnectedSurface>;
    },
    private readonly reconnectContext: ReconnectContext,
  ) {}

  cancelActiveOperation(): Promise<void> {
    return this.cancellation.cancelActiveOperation();
  }

  requestExit(reason: ExitReason): Promise<ExitOutcome> {
    return this.exit.requestExit(reason);
  }

  reconnect(): Promise<ConnectedSurface> {
    return this.recovery.reconnect(this.reconnectContext);
  }
}
