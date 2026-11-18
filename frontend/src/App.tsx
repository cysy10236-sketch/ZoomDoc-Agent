import React, { useCallback, useEffect, useState } from "react";
import { BarChart3, Cpu, ScanSearch, ShieldAlert } from "lucide-react";
import { getHealth, listDocuments, listPresets } from "./api";
import type {
  DocumentItem,
  HealthStatus,
  LensJumpTarget,
  PresetItem,
} from "./types";
import { AgentStudioView } from "./components/AgentStudioView";
import { FoveaLensLabView } from "./components/FoveaLensLabView";
import { BenchmarkObservatoryView } from "./components/BenchmarkObservatoryView";
import { TraceAuditView } from "./components/TraceAuditView";
import { ErrorBoundary } from "./components/ErrorBoundary";

type TabKey = "studio" | "lens" | "benchmarks" | "audit";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>("studio");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [presets, setPresets] = useState<PresetItem[]>([]);
  const [lensJump, setLensJump] = useState<LensJumpTarget | null>(null);
  const [auditTraceId, setAuditTraceId] = useState<string | null>(null);

  const refreshDocuments = useCallback(async () => {
    const [docs, pres] = await Promise.all([listDocuments(), listPresets()]);
    setDocuments(docs);
    setPresets(pres);
  }, []);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    void refreshDocuments();
  }, [refreshDocuments]);

  return (
    <div className="app-shell">
      <header className="top-nav">
        <div className="brand-group">
          <div className="brand-mark">F</div>
          <div>
            <div className="brand-title">
              <span>FoveaDoc Observatory</span>
              <span className="brand-version">v{health?.version ?? "0.2.0"}</span>
            </div>
            <div className="brand-subtitle">
              Multi-Agent Long-Document Visual Reasoning &amp; Anisotropic Topological Foveation
            </div>
          </div>
        </div>

        <nav className="nav-tabs" aria-label="Primary Views">
          <button
            type="button"
            className={`nav-tab-btn ${activeTab === "studio" ? "active" : ""}`}
            onClick={() => setActiveTab("studio")}
          >
            <Cpu size={14} />
            <span>Agent Studio · 工作台</span>
          </button>
          <button
            type="button"
            className={`nav-tab-btn ${activeTab === "lens" ? "active" : ""}`}
            onClick={() => setActiveTab("lens")}
          >
            <ScanSearch size={14} />
            <span>Fovea Lens · 凹面镜</span>
          </button>
          <button
            type="button"
            className={`nav-tab-btn ${activeTab === "benchmarks" ? "active" : ""}`}
            onClick={() => setActiveTab("benchmarks")}
          >
            <BarChart3 size={14} />
            <span>Benchmarks · 评测消融</span>
          </button>
          <button
            type="button"
            className={`nav-tab-btn ${activeTab === "audit" ? "active" : ""}`}
            onClick={() => setActiveTab("audit")}
          >
            <ShieldAlert size={14} />
            <span>Trace Audit · 轨迹审计</span>
          </button>
        </nav>

        <div className="status-cluster">
          <div className="status-pill">
            <span className={health?.has_api_key ? "dot-ok" : "dot-warn"} />
            <span>{health?.vlm_model ?? "offline"}</span>
          </div>
        </div>
      </header>

      <main className="workspace-container">
        <ErrorBoundary key={activeTab} onReset={() => setActiveTab("studio")}>
          {activeTab === "studio" && (
            <AgentStudioView
              documents={documents}
              presets={presets}
              hasApiKey={Boolean(health?.has_api_key)}
              onDocumentsRefresh={refreshDocuments}
              onJumpToLensLab={(target) => {
                setLensJump(target);
                setActiveTab("lens");
              }}
              onJumpToTraceAudit={(tid) => {
                setAuditTraceId(tid);
                setActiveTab("audit");
              }}
            />
          )}

          {activeTab === "lens" && (
            <FoveaLensLabView documents={documents} jumpTarget={lensJump} />
          )}

          {activeTab === "benchmarks" && <BenchmarkObservatoryView />}

          {activeTab === "audit" && <TraceAuditView initialTraceId={auditTraceId} />}
        </ErrorBoundary>
      </main>
    </div>
  );
};
