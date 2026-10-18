import { Component, type ErrorInfo, type ReactNode } from "react";

/** Shows a readable message instead of a blank screen when a page throws while rendering. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI error", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="m-6 rounded border-2 border-red-800 bg-red-50 p-4 text-red-900">
        <p className="font-bold">Something went wrong on this page.</p>
        <p className="mt-1 text-sm">{this.state.error.message}</p>
        <button onClick={() => location.reload()} className="mt-3 rounded bg-red-800 px-3 py-1 text-white">
          Reload
        </button>
      </div>
    );
  }
}
