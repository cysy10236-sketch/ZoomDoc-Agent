import React, { useEffect, useState } from "react";
import { CheckCircle2, Clock, Database, ShieldCheck, Terminal } from "lucide-react";
import { getRunDetail, listRuns } from "../api";
import type { RunDetailResponse, RunSummaryItem, TraceEvent } from "../types";

interface TraceAuditViewProps {
  initialTraceId: string | null;
}

export const TraceAuditView: React.FC<TraceAuditViewProps> = ({ initialTraceId }) => {
  const [runs, setRuns] = useState<RunSummaryItem[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string>("");
  const [detail, setDetail] = useState<RunDetailResponse | null>(null);
  const [selectedSpanIdx, setSelectedSpanIdx] = useState<number>(0);
  const [jsonTab, setJsonTab] = useState<"span" | "supervisor" | "result">("span");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listRuns(60)
      .then((items) => {
        setRuns(items);
        if (initialTraceId) {
          setSelectedTraceId(initialTraceId);
        } else if (items.length > 0 && !selectedTraceId) {
          setSelectedTraceId(items[0].trace_id);
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTraceId]);

  useEffect(() => {
    if (!selectedTraceId) return;
    setError(null);
    getRunDetail(selectedTraceId)
      .then((d) => {
        setDetail(d);
        setSelectedSpanIdx(0);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [selectedTraceId]);

  const events: TraceEvent[] = detail?.trace_events ?? [];
  const totalSpanMs = Math.max(
    1,
    events.reduce((acc, ev) => acc + Math.max(1, ev.latency_ms), 0)
  );

  let cumulativeMs = 0;
  const waterfallItems = events.map((ev) => {
    const startPct = (cumulativeMs / totalSpanMs) * 100;
    const widthPct = Math.max(1.5, (Math.max(1, ev.latency_ms) / totalSpanMs) * 100);
    cumulativeMs += Math.max(1, ev.latency_ms);
    return { ev, startPct, widthPct };
  });

  const activeSpan = events[selectedSpanIdx] ?? null;

  return (
    <div className="audit-grid">
      {/* LEFT: Run History Selector */}
      <div className="panel">
        <div className="panel-header">
          <h3 className="panel-title">
            <Database size={15} color="#2563eb" />
            运行归档目录 (Trace Archive)
          </h3>
          <span className="mono" style={{ fontSize: 11, color: "#64748b" }}>
            {runs.length} runs
          </span>
        </div>
        <div style={{ maxHeight: 680, overflowY: "auto", padding: 8 }}>
          {runs.map((r) => {
            const active = r.trace_id === selectedTraceId;
            return (
              <div
                key={r.trace_id}
                onClick={() => setSelectedTraceId(r.trace_id)}
                style={{
                  padding: "8px 10px",
                  borderRadius: 6,
                  marginBottom: 5,
                  cursor: "pointer",
                  background: active ? "#eff6ff" : "#ffffff",
                  border: `1px solid ${active ? "#93c5fd" : "#e2e8f0"}`,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: 3,
                  }}
                >
                  <span className="mono" style={{ fontWeight: 700, fontSize: 12 }}>
                    {r.trace_id}
                  </span>
                  <span
                    className={`badge ${
                      r.status === "answered" || r.status === "synthesized"
                        ? "badge-teal"
                        : "badge-amber"
                    } mono`}
                  >
                    {r.status}
                  </span>
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: "#475569",
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>Ans: {r.answer ?? "—"}</span>
                  <span>
                    z={r.zoom_count} · {(r.total_wall_ms / 1000).toFixed(1)}s
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* RIGHT: Isolation Contract + Span Waterfall + JSON Inspector */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {error && (
          <div className="panel panel-body" style={{ color: "#b91c1c" }}>
            {error}
          </div>
        )}

        {detail && (
          <>
            {/* Physical Context Isolation Audit Banner */}
            <div className="panel">
              <div className="panel-header">
                <h3 className="panel-title">
                  <ShieldCheck size={15} color="#059669" />
                  主从子图物理上下文隔离契约审计 (`supervisor_state.json`)
                </h3>
                <span
                  className={`badge ${
                    detail.isolation_audit.passed
                      ? "badge-teal"
                      : detail.isolation_audit.supervisor_state_bytes === 0
                      ? "badge-slate"
                      : "badge-red"
                  } mono`}
                >
                  {detail.isolation_audit.passed
                    ? "ISOLATION CONTRACT PASSED"
                    : detail.isolation_audit.supervisor_state_bytes === 0
                    ? "M1 FLAT GRAPH (N/A)"
                    : "CONTRACT VIOLATION"}
                </span>
              </div>
              <div className="panel-body">
                <div className="kpi-grid">
                  <div className="kpi-box">
                    <div className="kpi-label">SupervisorState 字节体积</div>
                    <div className="kpi-val">
                      {(detail.isolation_audit.supervisor_state_bytes / 1024).toFixed(1)} KB
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">Base64 / Data-URL 泄漏数</div>
                    <div className="kpi-val" style={{ color: "#059669" }}>
                      {detail.isolation_audit.base64_occurrences}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">子图 `/sub/` 局部图像路径泄漏数</div>
                    <div className="kpi-val" style={{ color: "#059669" }}>
                      {detail.isolation_audit.sub_crop_path_occurrences}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">结构化 JSONL Span 总数</div>
                    <div className="kpi-val">{events.length}</div>
                  </div>
                </div>
              </div>
            </div>

            {/* Span Waterfall */}
            <div className="panel">
              <div className="panel-header">
                <h3 className="panel-title">
                  <Clock size={15} color="#2563eb" />
                  多命名空间 Span 瀑布流 (`trace.jsonl` · 点击任意行审阅原始载荷)
                </h3>
              </div>
              <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {waterfallItems.map(({ ev, startPct, widthPct }, idx) => {
                  const ns = ev.namespace ?? "main";
                  const isSubInsp = ns.startsWith("inspector_sub");
                  const isSubNav = ns.startsWith("navigator_sub");
                  const barColor = isSubInsp
                    ? "#0d9488"
                    : isSubNav
                    ? "#7c3aed"
                    : "#2563eb";
                  return (
                    <div
                      key={`${ev.span_id}-${idx}`}
                      className={`waterfall-row ${idx === selectedSpanIdx ? "active" : ""}`}
                      onClick={() => {
                        setSelectedSpanIdx(idx);
                        setJsonTab("span");
                      }}
                    >
                      <div className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <span style={{ color: barColor, fontWeight: 700 }}>{ns}</span>
                        <span style={{ color: "#334155" }}> :: {ev.node}</span>
                      </div>
                      <div className="waterfall-track">
                        <div
                          className="waterfall-bar"
                          style={{
                            left: `${Math.min(96, startPct)}%`,
                            width: `${Math.min(100 - Math.min(96, startPct), widthPct)}%`,
                            background: barColor,
                          }}
                        />
                      </div>
                      <div className="mono" style={{ textAlign: "right", color: "#475569" }}>
                        {ev.latency_ms.toFixed(0)} ms
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Raw Structured JSON Inspector */}
            <div className="panel">
              <div className="panel-header">
                <h3 className="panel-title">
                  <Terminal size={15} color="#0f172a" />
                  原始结构化协议载荷检视器
                </h3>
                <div style={{ display: "flex", gap: 5 }}>
                  <button
                    type="button"
                    className={`btn btn-sm ${jsonTab === "span" ? "btn-primary" : ""}`}
                    onClick={() => setJsonTab("span")}
                  >
                    Selected Span #{selectedSpanIdx + 1}
                  </button>
                  <button
                    type="button"
                    className={`btn btn-sm ${jsonTab === "result" ? "btn-primary" : ""}`}
                    onClick={() => setJsonTab("result")}
                  >
                    <CheckCircle2 size={12} /> result.json
                  </button>
                  <button
                    type="button"
                    className={`btn btn-sm ${jsonTab === "supervisor" ? "btn-primary" : ""}`}
                    onClick={() => setJsonTab("supervisor")}
                  >
                    supervisor_state.json
                  </button>
                </div>
              </div>
              <div className="panel-body">
                <pre className="json-pre">
                  {jsonTab === "span"
                    ? JSON.stringify(activeSpan, null, 2)
                    : jsonTab === "result"
                    ? JSON.stringify(detail.result, null, 2)
                    : JSON.stringify(detail.supervisor_state, null, 2)}
                </pre>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
