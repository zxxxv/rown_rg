import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div
        role="alert"
        className="m-6 flex flex-col items-start gap-3 rounded border border-fg-danger/30 bg-bg-danger p-6"
      >
        <h2 className="text-lg font-semibold text-fg-danger">문제가 발생했습니다</h2>
        <p className="text-sm text-fg-secondary">
          페이지를 표시하는 중 오류가 발생했습니다. 다시 시도해 주세요.
        </p>
        <pre className="max-w-full overflow-x-auto rounded border border-border bg-bg p-3 font-mono text-xs text-fg-secondary">
          {error.message}
        </pre>
        <Button type="button" onClick={this.reset}>
          다시 시도
        </Button>
      </div>
    );
  }
}
