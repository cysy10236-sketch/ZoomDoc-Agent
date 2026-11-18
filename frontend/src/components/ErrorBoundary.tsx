import React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorBoundaryProps {
  children: React.ReactNode;
  onReset?: () => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  private handleRecover = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  override render() {
    if (this.state.error) {
      return (
        <div className="panel" style={{ maxWidth: 680, margin: "32px auto" }}>
          <div className="panel-header">
            <h3 className="panel-title">
              <AlertTriangle size={16} color="#b91c1c" />
              视图渲染异常隔离保护 (UI Fault Isolation Boundary)
            </h3>
          </div>
          <div className="panel-body">
            <p style={{ marginTop: 0, color: "#334155" }}>
              当前子视图捕获到未预期的数据结构或运行时异常，主应用壳层已隔离故障：
            </p>
            <pre className="json-pre" style={{ marginBottom: 12 }}>
              {this.state.error.stack ?? this.state.error.message}
            </pre>
            <button type="button" className="btn btn-primary" onClick={this.handleRecover}>
              <RotateCcw size={14} /> 重置当前视图状态
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
