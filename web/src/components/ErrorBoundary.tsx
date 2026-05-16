import { Component, type ReactNode } from "react";

/**
 * Per phase-8-design-memo §N + Q12 (Software Architect): in-house ~30
 * line boundary used at TargetDetails body level + Sparkline
 * specifically. Not a Dashboard-route-level boundary — a corrupted
 * cache should hard-fail visibly in dev.
 */
interface State {
  hasError: boolean;
}

interface Props {
  readonly fallback: ReactNode;
  readonly children: ReactNode;
  readonly onError?: (error: Error) => void;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  override componentDidCatch(error: Error): void {
     
    console.error("ui_error_boundary", error);
    this.props.onError?.(error);
  }

  override render(): ReactNode {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}
