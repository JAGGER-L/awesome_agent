import { Component, type ErrorInfo, type ReactNode } from "react";

import { RenderFailure } from "../lifecycle/fatal.js";

export class AppErrorBoundary extends Component<
  {
    readonly children: ReactNode;
    readonly fallback: ReactNode;
    readonly onError: (error: RenderFailure) => void;
  },
  { readonly failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: true } {
    return { failed: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    this.props.onError(new RenderFailure(error.message));
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
