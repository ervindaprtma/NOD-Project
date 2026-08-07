"use client";

import React, { Component, type ErrorInfo, type ReactNode } from "react";
import { logClient } from "@/lib/api";

type Variant = "page" | "panel";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  variant?: Variant;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
    // Ship the render exception to the System Logs sink (frontend source).
    logClient("ERROR", "frontend.exception", error.message, {
      stack: error.stack?.slice(0, 1500),
      componentStack: errorInfo.componentStack?.slice(0, 1500),
    });
  }

  handleTryAgain = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }
    if (this.props.fallback) {
      return this.props.fallback;
    }
    if (this.props.variant === "panel") {
      return (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
          <strong>Render error:</strong> {this.state.error?.message}
        </div>
      );
    }
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] p-6 text-center">
        <div className="max-w-md space-y-4">
          <div className="text-4xl">⚠️</div>
          <h2 className="text-lg font-semibold text-foreground">Something went wrong</h2>
          <p className="text-sm text-muted-foreground">
            {this.state.error?.message || "An unexpected error occurred."}
          </p>
          <button
            onClick={this.handleTryAgain}
            className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium text-primary-foreground bg-primary rounded-md hover:bg-primary/90 transition-colors"
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }
}
