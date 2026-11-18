import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Eye,
  FileText,
  FolderTree,
  Layers,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Upload,
  Zap,
} from "lucide-react";
import {
  getDocumentTree,
  getPageImageUrl,
  getRunDetail,
  streamAgentRun,
  uploadDocument,
} from "../api";
import type {
  DocTreeNode,
  DocumentItem,
  DocumentTree,
  FinalResultM3,
  IsolationAudit,
  LensJumpTarget,
  PresetItem,
  RunAssetCrop,
  RunAssetGrid,
  TraceEvent,
} from "../types";
import { GraphTopologySvg } from "./GraphTopologySvg";

interface AgentStudioViewProps {
  documents: DocumentItem[];
  presets: PresetItem[];
  hasApiKey: boolean;
  onDocumentsRefresh: () => Promise<void>;
  onJumpToLensLab: (target: LensJumpTarget) => void;
  onJumpToTraceAudit: (traceId: string) => void;
}

const TreeItem: React.FC<{
  node: DocTreeNode;
  selectedIds: Set<string>;
  goldPage: number | null;
  depth: number;
}> = ({ node, selectedIds, goldPage, depth }) => {
  const isSelected = selectedIds.has(node.node_id);
  const hasGold =
    goldPage !== null && goldPage >= node.start_page && goldPage <= node.end_page;

  return (
    <div>
      <div
        className={`tree-node-row ${isSelected ? "selected-leaf" : ""} ${
          hasGold ? "gold-hit" : ""
        }`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <span className="mono" style={{ color: "#64748b", marginRight: 6, fontSize: 11 }}>
            {node.structure}
          </span>
          <span>{node.title}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
          {isSelected && <span className="badge badge-teal">Selected</span>}
          <span className="mono" style={{ fontSize: 11, color: "#475569" }}>
            p.{node.start_page}–{node.end_page}
          </span>
        </div>
      </div>
      {node.children.map((ch) => (
        <TreeItem
          key={ch.node_id}
          node={ch}
          selectedIds={selectedIds}
          goldPage={goldPage}
          depth={depth + 1}
        />
      ))}
    </div>
  );
};

export const AgentStudioView: React.FC<AgentStudioViewProps> = ({
  documents,
  presets,
  hasApiKey,
  onDocumentsRefresh,
  onJumpToLensLab,
  onJumpToTraceAudit,
}) => {
  const [selectedPresetId, setSelectedPresetId] = useState<string>("");
  const [docId, setDocId] = useState<string>("");
  const [question, setQuestion] = useState<string>("");
  const [goldAnswer, setGoldAnswer] = useState<string | null>(null);
  const [goldPage, setGoldPage] = useState<number | null>(null);

  // Algorithm toggles
  const [zoomMode, setZoomMode] = useState<"fovea" | "crop">("fovea");
  const [enableSboed, setEnableSboed] = useState<boolean>(true);
  const [autoAnchors, setAutoAnchors] = useState<boolean>(true);
  const [adaptiveAspect, setAdaptiveAspect] = useState<boolean>(true);
  const [enableResidual, setEnableResidual] = useState<boolean>(true);
  const [enableCache, setEnableCache] = useState<boolean>(true);

  // Tree state
  const [docTree, setDocTree] = useState<DocumentTree | null>(null);
  const [treeCacheHit, setTreeCacheHit] = useState<boolean>(false);

  // Run / Replay telemetry state
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [runModeLabel, setRunModeLabel] = useState<string>("Idle");
  const [stageMessage, setStageMessage] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activeTraceId, setActiveTraceId] = useState<string | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [finalResult, setFinalResult] = useState<FinalResultM3 | null>(null);
  const [isolationAudit, setIsolationAudit] = useState<IsolationAudit | null>(null);
  const [crops, setCrops] = useState<RunAssetCrop[]>([]);
  const [grids, setGrids] = useState<RunAssetGrid[]>([]);
  const [evidenceTab, setEvidenceTab] = useState<"crops" | "page" | "grids">("crops");
  const [uploading, setUploading] = useState<boolean>(false);

  const abortRef = useRef<AbortController | null>(null);
  const replayTimerRef = useRef<number | null>(null);

  // Initialize default preset when presets arrive
  useEffect(() => {
    if (presets.length > 0 && !selectedPresetId) {
      const m6First = presets.find((p) => p.case_id === "m6_case_007" && p.replay_trace_id);
      const m5First = presets.find((p) => p.replay_trace_id);
      const chosen = m6First ?? m5First ?? presets[0];
      applyPreset(chosen);
    } else if (presets.length === 0 && documents.length > 0 && !docId) {
      setDocId(documents[0].doc_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets, documents]);

  // Load PageIndex tree whenever docId changes
  useEffect(() => {
    if (!docId) {
      setDocTree(null);
      return;
    }
    let cancelled = false;
    getDocumentTree(docId)
      .then((res) => {
        if (!cancelled) {
          setDocTree(res.tree);
          setTreeCacheHit(res.cache_hit);
        }
      })
      .catch(() => {
        if (!cancelled) setDocTree(null);
      });
    return () => {
      cancelled = true;
    };
  }, [docId]);

  const applyPreset = (p: PresetItem) => {
    setSelectedPresetId(p.preset_id);
    setDocId(p.doc_id);
    setQuestion(p.question);
    setGoldAnswer(p.gold);
    setGoldPage(p.gold_page);
    setErrorMessage(null);
    if (p.replay_trace_id) {
      void handleReplay(p.replay_trace_id, false);
    }
  };

  const stopAnyActive = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (replayTimerRef.current !== null) {
      window.clearInterval(replayTimerRef.current);
      replayTimerRef.current = null;
    }
    setIsRunning(false);
    setActiveNode(null);
  };

  const handleReplay = async (traceId: string, animate = true) => {
    stopAnyActive();
    setErrorMessage(null);
    setActiveTraceId(traceId);
    setRunModeLabel(animate ? "Replaying Real Trace..." : "Loaded Real Trace");
    try {
      const detail = await getRunDetail(traceId);
      setIsolationAudit(detail.isolation_audit);
      setCrops(detail.crops);
      setGrids(detail.grids);

      if (!animate || detail.trace_events.length === 0) {
        setEvents(detail.trace_events);
        setFinalResult(detail.result);
        setActiveNode(null);
        setStageMessage(`已加载真实评测轨迹 ${traceId}（共 ${detail.trace_events.length} 个 Span）`);
        setEvidenceTab(detail.crops.length > 0 ? "crops" : "page");
        return;
      }

      setIsRunning(true);
      setEvents([]);
      setFinalResult(null);
      let idx = 0;
      replayTimerRef.current = window.setInterval(() => {
        if (idx < detail.trace_events.length) {
          const ev = detail.trace_events[idx];
          setEvents((prev) => [...prev, ev]);
          setActiveNode(ev.node);
          setStageMessage(`Replay Span #${idx + 1}/${detail.trace_events.length}: ${ev.namespace} :: ${ev.node}`);
          idx += 1;
        } else {
          if (replayTimerRef.current !== null) {
            window.clearInterval(replayTimerRef.current);
            replayTimerRef.current = null;
          }
          setIsRunning(false);
          setActiveNode("finalize");
          setFinalResult(detail.result);
          setRunModeLabel("Replay Complete");
          setStageMessage(`回放完成：${traceId}`);
          setEvidenceTab(detail.crops.length > 0 ? "crops" : "page");
        }
      }, 220);
    } catch (err) {
      setIsRunning(false);
      setErrorMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const handleLiveRun = async () => {
    if (!docId || question.trim().length < 2) return;
    stopAnyActive();
    setIsRunning(true);
    setRunModeLabel("Live SSE Streaming");
    setErrorMessage(null);
    setEvents([]);
    setFinalResult(null);
    setIsolationAudit(null);
    setCrops([]);
    setGrids([]);
    setStageMessage("正在建立 SSE 事件流并初始化 LangGraph 状态机...");

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let currentTid: string | null = null;

    try {
      await streamAgentRun(
        {
          doc_id: docId,
          question: question.trim(),
          zoom_mode: zoomMode,
          enable_sboed_gate: enableSboed,
          enable_residual_verification: enableResidual,
          fovea_auto_anchors: autoAnchors,
          fovea_adaptive_aspect: adaptiveAspect,
          enable_content_cache: enableCache,
        },
        (evt) => {
          if (evt.event_type === "start") {
            currentTid = evt.trace_id;
            setActiveTraceId(evt.trace_id);
            setStageMessage(`已分配 Trace ID: ${evt.trace_id}`);
          } else if (evt.event_type === "stage") {
            setActiveNode(evt.stage);
            setStageMessage(`正在执行节点: ${evt.stage}`);
          } else if (evt.event_type === "trace") {
            setActiveNode(evt.node);
            setEvents((prev) => [...prev, evt]);
          } else if (evt.event_type === "complete") {
            setFinalResult(evt.result);
            setActiveNode("finalize");
            setRunModeLabel("Live Run Complete");
            setStageMessage(`推理完成 (${(evt.result.total_wall_ms ?? 0).toFixed(0)} ms)`);
          } else if (evt.event_type === "error") {
            setErrorMessage(evt.error);
            setRunModeLabel("Run Error");
          }
        },
        ctrl.signal
      );

      if (currentTid) {
        const detail = await getRunDetail(currentTid);
        setIsolationAudit(detail.isolation_audit);
        setCrops(detail.crops);
        setGrids(detail.grids);
        setEvidenceTab(detail.crops.length > 0 ? "crops" : "page");
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setErrorMessage(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setIsRunning(false);
    }
  };

  const handleUploadPdf = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setErrorMessage(null);
    try {
      const uploaded = await uploadDocument(file);
      await onDocumentsRefresh();
      setSelectedPresetId("");
      setDocId(uploaded.doc_id);
      setGoldAnswer(null);
      setGoldPage(null);
      setQuestion("Summarize the key quantitative metrics or identifiers reported in this document.");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const visitedNodes = useMemo(() => {
    const s = new Set<string>();
    for (const ev of events) s.add(ev.node);
    if (finalResult) {
      s.add("ingest");
      s.add("supervisor");
      s.add("critic");
      s.add("finalize");
    }
    return s;
  }, [events, finalResult]);

  const selectedNodeIds = useMemo(
    () => new Set<string>(finalResult?.selected_node_ids ?? []),
    [finalResult]
  );

  // Extract maximum S-BOED entropy observed across Inspector spans
  const maxObservedEntropy = useMemo(() => {
    let maxH: number | null = null;
    for (const ev of events) {
      const h = ev.payload?.answer_max_entropy ?? ev.payload?.max_token_entropy;
      if (typeof h === "number") {
        maxH = maxH === null ? h : Math.max(maxH, h);
      }
    }
    return maxH;
  }, [events]);

  const currentPreset = useMemo(
    () => presets.find((p) => p.preset_id === selectedPresetId) ?? null,
    [presets, selectedPresetId]
  );

  const normalizeAns = (s: string | null | undefined) =>
    (s ?? "").trim().toUpperCase().replace(/\s+/g, " ");

  const isExactMatch =
    goldAnswer !== null &&
    finalResult?.answer != null &&
    normalizeAns(finalResult.answer) === normalizeAns(goldAnswer);

  const primaryCitedPage =
    finalResult?.cited_regions?.[0]?.page ??
    finalResult?.reference_pages?.[0] ??
    goldPage ??
    1;

  return (
    <div className="studio-grid">
      {/* LEFT RAIL: Task & Algorithm Configuration */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <Sparkles size={15} color="#2563eb" />
              评测样本与自定义文档输入
            </h3>
            <label className="btn btn-sm" style={{ cursor: "pointer" }}>
              <Upload size={13} />
              {uploading ? "上传中..." : "上传 PDF"}
              <input
                type="file"
                accept="application/pdf"
                style={{ display: "none" }}
                onChange={handleUploadPdf}
                disabled={uploading}
              />
            </label>
          </div>
          <div className="panel-body">
            <div className="field-group">
              <label className="field-label">
                <span>基准预设用例 (M5 50-100p & M6 18x5 矩阵/扫描)</span>
                <span className="mono" style={{ fontSize: 11, color: "#64748b" }}>
                  {presets.length} cases
                </span>
              </label>
              <select
                className="select-input"
                value={selectedPresetId}
                onChange={(e) => {
                  const p = presets.find((x) => x.preset_id === e.target.value);
                  if (p) applyPreset(p);
                  else {
                    setSelectedPresetId("");
                    setGoldAnswer(null);
                    setGoldPage(null);
                  }
                }}
              >
                <option value="">-- 自定义文档与提问 --</option>
                {presets.map((p) => (
                  <option key={p.preset_id} value={p.preset_id}>
                    [{p.case_id}] {p.query_type} ({p.total_pages}p, {p.font_pt}pt)
                  </option>
                ))}
              </select>
            </div>

            <div className="field-group">
              <label className="field-label">目标 PDF 文档</label>
              <select
                className="select-input"
                value={docId}
                onChange={(e) => {
                  setDocId(e.target.value);
                  setSelectedPresetId("");
                  setGoldAnswer(null);
                  setGoldPage(null);
                }}
              >
                {documents.map((d) => (
                  <option key={d.doc_id} value={d.doc_id}>
                    {d.filename} ({d.total_pages} pages · {d.category})
                  </option>
                ))}
              </select>
            </div>

            <div className="field-group" style={{ marginBottom: 8 }}>
              <label className="field-label">
                <span>查询问题 (Question)</span>
                {goldAnswer && (
                  <span className="badge badge-blue mono">
                    Gold: {goldAnswer} (p.{goldPage})
                  </span>
                )}
              </label>
              <textarea
                className="textarea-input"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="输入针对长文档细粒度表格/附注的查询问题..."
              />
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              {currentPreset?.replay_trace_id && (
                <button
                  type="button"
                  className="btn btn-teal"
                  style={{ flex: 1 }}
                  onClick={() => void handleReplay(currentPreset.replay_trace_id!, true)}
                  disabled={isRunning}
                >
                  <RotateCcw size={14} />
                  动态回放真实轨迹
                </button>
              )}
              <button
                type="button"
                className="btn btn-primary"
                style={{ flex: 1 }}
                onClick={() => void handleLiveRun()}
                disabled={isRunning || !docId || !hasApiKey}
                title={!hasApiKey ? "未检测到有效 VLM_API_KEY，请使用真实轨迹回放或在 .env 配置密钥" : ""}
              >
                <Play size={14} />
                {isRunning ? "运行中..." : "实时 SSE 推理"}
              </button>
              {isRunning && (
                <button type="button" className="btn btn-danger btn-sm" onClick={stopAnyActive}>
                  中止
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Algorithm Switches */}
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <Zap size={15} color="#0f766e" />
              FoveaDoc v2.0 核心算法开关 (Live Run)
            </h3>
          </div>
          <div className="panel-body">
            <div className="field-group">
              <label className="field-label">视觉放大算子 (Zoom Operator)</label>
              <select
                className="select-input"
                value={zoomMode}
                onChange={(e) => setZoomMode(e.target.value as "fovea" | "crop")}
              >
                <option value="fovea">Anisotropic Topological Foveation v2.0 (双锚定凹面镜)</option>
                <option value="crop">Standard Linear Crop (局部矩形裁剪消融)</option>
              </select>
            </div>

            <div className="toggle-grid">
              <label className={`toggle-chip ${enableSboed ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={enableSboed}
                  onChange={(e) => setEnableSboed(e.target.checked)}
                />
                <span>S-BOED 信息熵门控</span>
              </label>
              <label className={`toggle-chip ${autoAnchors ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={autoAnchors}
                  onChange={(e) => setAutoAnchors(e.target.checked)}
                />
                <span>页中表头/行名自适应检测</span>
              </label>
              <label className={`toggle-chip ${adaptiveAspect ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={adaptiveAspect}
                  onChange={(e) => setAdaptiveAspect(e.target.checked)}
                />
                <span>自适应画布长宽比</span>
              </label>
              <label className={`toggle-chip ${enableResidual ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={enableResidual}
                  onChange={(e) => setEnableResidual(e.target.checked)}
                />
                <span>跨模态残差纠偏校验</span>
              </label>
              <label className={`toggle-chip ${enableCache ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={enableCache}
                  onChange={(e) => setEnableCache(e.target.checked)}
                />
                <span>SHA-256 持久化缓存</span>
              </label>
            </div>
          </div>
        </div>
      </div>

      {/* CENTER COLUMN: Topology + PageIndex Tree + Live Span Stream */}
      <div>
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <Layers size={15} color="#2563eb" />
              LangGraph 主从状态机与双隔离子图拓扑
            </h3>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="badge badge-slate mono">{runModeLabel}</span>
              {activeTraceId && (
                <button
                  type="button"
                  className="btn btn-sm mono"
                  onClick={() => onJumpToTraceAudit(activeTraceId)}
                >
                  审计 {activeTraceId}
                </button>
              )}
            </div>
          </div>
          <div className="panel-body" style={{ padding: "10px 12px" }}>
            <GraphTopologySvg
              visitedNodes={visitedNodes}
              activeNode={activeNode}
              zoomCount={finalResult?.zoom_count ?? crops.length}
              residualCorrections={finalResult?.residual_corrections ?? 0}
              isolationPassed={isolationAudit ? isolationAudit.passed : null}
            />
            {stageMessage && (
              <div
                style={{
                  marginTop: 6,
                  padding: "6px 10px",
                  borderRadius: 5,
                  background: "#f8fafc",
                  border: "1px solid #e2e8f0",
                  fontSize: 12,
                  color: "#334155",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span>{stageMessage}</span>
                {maxObservedEntropy !== null && (
                  <span className="badge badge-amber mono">
                    S-BOED H_max = {maxObservedEntropy.toFixed(3)} bits (τ = 0.150)
                  </span>
                )}
              </div>
            )}
            {errorMessage && (
              <div
                style={{
                  marginTop: 8,
                  padding: "8px 10px",
                  borderRadius: 6,
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  color: "#991b1b",
                  fontSize: 12,
                }}
              >
                {errorMessage}
              </div>
            )}
          </div>
        </div>

        <div className="studio-mid-split">
          {/* PageIndex Hierarchical Tree */}
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">
                <FolderTree size={15} color="#6d28d9" />
                PageIndex 层次化目录树
              </h3>
              {docTree && (
                <span className="badge badge-purple mono">
                  {docTree.total_pages}p {treeCacheHit ? "· Cache Hit" : ""}
                </span>
              )}
            </div>
            <div className="tree-scroll">
              {docTree ? (
                docTree.roots.map((r) => (
                  <TreeItem
                    key={r.node_id}
                    node={r}
                    selectedIds={selectedNodeIds}
                    goldPage={goldPage}
                    depth={0}
                  />
                ))
              ) : (
                <div style={{ padding: 16, color: "#64748b", fontSize: 12 }}>
                  选择文档后自动加载结构化章节索引树...
                </div>
              )}
            </div>
          </div>

          {/* Live Span Telemetry Feed */}
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">
                <Activity size={15} color="#0f766e" />
                结构化执行轨迹流 (Span Stream)
              </h3>
              <span className="mono" style={{ fontSize: 11.5, color: "#64748b" }}>
                {events.length} spans
              </span>
            </div>
            <div className="span-feed">
              {events.length === 0 ? (
                <div style={{ padding: 16, color: "#64748b", fontSize: 12 }}>
                  点击左侧「动态回放真实轨迹」或「实时 SSE 推理」观测各智能体节点输出。
                </div>
              ) : (
                events.map((ev, i) => {
                  const ns = ev.namespace ?? "main";
                  return (
                  <div key={`${ev.span_id}-${i}`} className="span-card">
                    <div className="span-card-top">
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span
                          className={`badge ${
                            ns.startsWith("inspector_sub")
                              ? "badge-teal"
                              : ns.startsWith("navigator_sub")
                              ? "badge-purple"
                              : "badge-blue"
                          } mono`}
                        >
                          {ns}
                        </span>
                        <strong className="mono">{ev.node}</strong>
                      </div>
                      <span className="mono" style={{ fontSize: 11, color: "#475569", whiteSpace: "nowrap" }}>
                        {ev.latency_ms.toFixed(0)} ms
                        {ev.prompt_tokens != null ? ` · ${ev.prompt_tokens} tok` : ""}
                      </span>
                    </div>
                    <div
                      className="mono"
                      style={{
                        fontSize: 11,
                        color: "#475569",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {JSON.stringify(ev.payload)}
                    </div>
                  </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>

      {/* RIGHT COLUMN: Answer Card + Isolation Audit + Visual Evidence */}
      <div className="studio-right-col" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <CheckCircle2 size={15} color="#0f766e" />
              推理结论与物理上下文隔离断言
            </h3>
            {goldAnswer !== null && finalResult && (
              <span className={`badge ${isExactMatch ? "badge-teal" : "badge-red"}`}>
                {isExactMatch ? "EXACT MATCH (Pass)" : "MISMATCH"}
              </span>
            )}
          </div>
          <div className="panel-body">
            {finalResult ? (
              <>
                <div
                  style={{
                    padding: "10px 12px",
                    borderRadius: 6,
                    background: "#f8fafc",
                    border: "1px solid #cbd5e1",
                    marginBottom: 10,
                  }}
                >
                  <div style={{ fontSize: 11, color: "#64748b", marginBottom: 3 }}>
                    最终输出答案 (Predicted Answer) · Status: <strong className="mono">{finalResult.status}</strong>
                  </div>
                  <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: "#0f172a" }}>
                    {finalResult.answer ?? "(null)"}
                  </div>
                  {finalResult.reason && (
                    <div style={{ fontSize: 12, color: "#475569", marginTop: 6 }}>
                      {finalResult.reason}
                    </div>
                  )}
                </div>

                <div className="kpi-grid" style={{ marginBottom: 10 }}>
                  <div className="kpi-box">
                    <div className="kpi-label">引用页码</div>
                    <div className="kpi-val">
                      {finalResult.reference_pages.length > 0
                        ? `p.${finalResult.reference_pages.join(",")}`
                        : "—"}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">Prompt Tokens</div>
                    <div className="kpi-val">
                      {finalResult.usage.prompt_tokens.toLocaleString()}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">VLM 调用 / Zoom</div>
                    <div className="kpi-val">
                      {finalResult.usage.calls} / {finalResult.zoom_count ?? 0}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">端到端延迟</div>
                    <div className="kpi-val">
                      {((finalResult.total_wall_ms ?? finalResult.usage.latency_ms) / 1000).toFixed(1)}s
                    </div>
                  </div>
                </div>

                {isolationAudit && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "7px 10px",
                      borderRadius: 6,
                      background: isolationAudit.passed ? "#ecfdf5" : "#fef2f2",
                      border: `1px solid ${isolationAudit.passed ? "#6ee7b7" : "#fecaca"}`,
                      fontSize: 11.5,
                    }}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 600 }}>
                      <ShieldCheck size={14} color="#059669" />
                      SupervisorState 零图像泄漏契约
                    </span>
                    <span className="mono" style={{ color: "#065f46" }}>
                      base64={isolationAudit.base64_occurrences} · /sub/={isolationAudit.sub_crop_path_occurrences}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <div style={{ color: "#64748b", fontSize: 12.5 }}>
                等待运行完成以展示结构化答案、Token 统计与物理隔离校验结果。
              </div>
            )}
          </div>
        </div>

        {/* Visual Evidence Inspector */}
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <Eye size={15} color="#2563eb" />
              多分辨率视觉证据检视器
            </h3>
            <div style={{ display: "flex", gap: 4 }}>
              <button
                type="button"
                className={`btn btn-sm ${evidenceTab === "crops" ? "btn-primary" : ""}`}
                onClick={() => setEvidenceTab("crops")}
              >
                凹面镜切片 ({crops.length})
              </button>
              <button
                type="button"
                className={`btn btn-sm ${evidenceTab === "page" ? "btn-primary" : ""}`}
                onClick={() => setEvidenceTab("page")}
              >
                引用原页
              </button>
              <button
                type="button"
                className={`btn btn-sm ${evidenceTab === "grids" ? "btn-primary" : ""}`}
                onClick={() => setEvidenceTab("grids")}
              >
                SoM 网格 ({grids.length})
              </button>
            </div>
          </div>
          <div className="panel-body">
            {evidenceTab === "crops" && (
              <div>
                {crops.length === 0 ? (
                  <div style={{ color: "#64748b", fontSize: 12 }}>
                    当前轨迹未生成局部放大图（可切换至「引用原页」或「SoM 网格」查看）。
                  </div>
                ) : (
                  crops.map((c) => (
                    <div key={c.name} style={{ marginBottom: 12 }}>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          marginBottom: 6,
                        }}
                      >
                        <span className="mono" style={{ fontSize: 11.5, fontWeight: 600 }}>
                          {c.name}
                        </span>
                        {docId && (
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => {
                              const reg = finalResult?.cited_regions?.[0];
                              onJumpToLensLab({
                                doc_id: docId,
                                page_num: reg?.page ?? primaryCitedPage,
                                bbox: reg?.page_box
                                  ? [
                                      reg.page_box.x1,
                                      reg.page_box.y1,
                                      reg.page_box.x2,
                                      reg.page_box.y2,
                                    ]
                                  : undefined,
                              });
                            }}
                          >
                            送入凹面镜实验室对比
                          </button>
                        )}
                      </div>
                      <div className="image-frame">
                        <img src={c.url} alt={c.name} />
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}

            {evidenceTab === "page" && docId && (
              <div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: 6,
                    fontSize: 12,
                  }}
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <FileText size={13} />
                    证据页码: <strong className="mono">Page {primaryCitedPage}</strong>
                  </span>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() =>
                      onJumpToLensLab({
                        doc_id: docId,
                        page_num: primaryCitedPage,
                      })
                    }
                  >
                    在凹面镜实验室打开此页
                  </button>
                </div>
                <div style={{ position: "relative", border: "1px solid #cbd5e1", borderRadius: 6, overflow: "hidden" }}>
                  <img
                    src={getPageImageUrl(docId, primaryCitedPage, "view")}
                    alt={`Page ${primaryCitedPage}`}
                    style={{ width: "100%", height: "auto", display: "block" }}
                  />
                  {finalResult?.cited_regions
                    .filter((r) => r.page === primaryCitedPage && r.page_box !== null)
                    .map((r, idx) => {
                      const b = r.page_box!;
                      return (
                        <div
                          key={idx}
                          style={{
                            position: "absolute",
                            left: `${b.x1 * 100}%`,
                            top: `${b.y1 * 100}%`,
                            width: `${(b.x2 - b.x1) * 100}%`,
                            height: `${(b.y2 - b.y1) * 100}%`,
                            border: "2px solid #dc2626",
                            background: "rgba(220, 38, 38, 0.14)",
                            pointerEvents: "none",
                          }}
                        />
                      );
                    })}
                </div>
              </div>
            )}

            {evidenceTab === "grids" && (
              <div>
                {grids.length === 0 ? (
                  <div style={{ color: "#64748b", fontSize: 12 }}>当前轨迹无 SoM 缩略图拼接网格。</div>
                ) : (
                  grids.map((g) => (
                    <div key={g.name} style={{ marginBottom: 10 }}>
                      <div className="mono" style={{ fontSize: 11.5, marginBottom: 4 }}>
                        {g.name}
                      </div>
                      <div className="image-frame">
                        <img src={g.url} alt={g.name} />
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
