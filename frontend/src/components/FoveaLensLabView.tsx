import React, { useCallback, useEffect, useRef, useState } from "react";
import { Crosshair, Maximize2, Sliders, Sparkles } from "lucide-react";
import { getFoveaPreview, getPageImageUrl } from "../api";
import type {
  DocumentItem,
  FoveaPreviewResponse,
  LensJumpTarget,
  WarpMeta,
} from "../types";

interface FoveaLensLabViewProps {
  documents: DocumentItem[];
  jumpTarget: LensJumpTarget | null;
}

const KnotCurveSvg: React.FC<{
  title: string;
  outKnots: number[];
  srcKnots: number[];
  color: string;
}> = ({ title, outKnots, srcKnots, color }) => {
  if (outKnots.length < 2 || srcKnots.length < 2) return null;
  const maxOut = outKnots[outKnots.length - 1] || 1;
  const maxSrc = srcKnots[srcKnots.length - 1] || 1;

  const w = 320;
  const h = 156;
  const padL = 38;
  const padR = 14;
  const padT = 18;
  const padB = 26;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const pts = outKnots.map((u, i) => {
    const px = padL + (u / maxOut) * plotW;
    const py = padT + plotH - (srcKnots[i] / maxSrc) * plotH;
    return { px, py, u: Math.round(u), x: Math.round(srcKnots[i]) };
  });

  const pathD = pts
    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${p.px.toFixed(1)} ${p.py.toFixed(1)}`)
    .join(" ");

  return (
    <div
      style={{
        border: "1px solid #e2e8f0",
        borderRadius: 6,
        padding: "8px 10px",
        background: "#ffffff",
      }}
    >
      <div
        style={{
          fontSize: 11.5,
          fontWeight: 600,
          color: "#334155",
          marginBottom: 4,
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>{title}</span>
        <span className="mono" style={{ color: "#64748b", fontSize: 10.5 }}>
          {outKnots.length} knots · Bijective
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto", display: "block" }}>
        {/* Identity diagonal reference */}
        <line
          x1={padL}
          y1={padT + plotH}
          x2={padL + plotW}
          y2={padT}
          stroke="#cbd5e1"
          strokeDasharray="4 3"
          strokeWidth={1.1}
        />
        {/* Axes */}
        <line x1={padL} y1={padT} x2={padL} y2={padT + plotH} stroke="#94a3b8" strokeWidth={1.2} />
        <line
          x1={padL}
          y1={padT + plotH}
          x2={padL + plotW}
          y2={padT + plotH}
          stroke="#94a3b8"
          strokeWidth={1.2}
        />
        {/* Piecewise monotonic curve */}
        <path d={pathD} fill="none" stroke={color} strokeWidth={2.3} />
        {pts.map((p, idx) => (
          <circle key={idx} cx={p.px} cy={p.py} r={3.4} fill="#ffffff" stroke={color} strokeWidth={2} />
        ))}
        <text x={padL} y={12} fontSize="9.5" fill="#64748b">
          src [0..{Math.round(maxSrc)}px]
        </text>
        <text x={padL + plotW} y={h - 6} textAnchor="end" fontSize="9.5" fill="#64748b">
          warped canvas [0..{Math.round(maxOut)}px]
        </text>
      </svg>
    </div>
  );
};

export const FoveaLensLabView: React.FC<FoveaLensLabViewProps> = ({
  documents,
  jumpTarget,
}) => {
  const [docId, setDocId] = useState<string>("");
  const [pageNum, setPageNum] = useState<number>(25);
  const [bbox, setBbox] = useState<[number, number, number, number]>([0.53, 0.642, 0.675, 0.706]);
  const [focusShare, setFocusShare] = useState<number>(0.65);
  const [padRatio, setPadRatio] = useState<number>(0.06);
  const [headerRatio, setHeaderRatio] = useState<number>(0.12);
  const [stubRatio, setStubRatio] = useState<number>(0.15);
  const [autoAnchors, setAutoAnchors] = useState<boolean>(true);
  const [adaptiveAspect, setAdaptiveAspect] = useState<boolean>(true);

  const [preview, setPreview] = useState<FoveaPreviewResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Mouse drag state on source page canvas
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [dragStart, setDragStart] = useState<[number, number] | null>(null);
  const [dragCurrent, setDragCurrent] = useState<[number, number] | null>(null);

  // Initialize default document
  useEffect(() => {
    if (jumpTarget) {
      setDocId(jumpTarget.doc_id);
      setPageNum(jumpTarget.page_num);
      if (jumpTarget.bbox) {
        setBbox(jumpTarget.bbox);
      }
      return;
    }
    if (!docId && documents.length > 0) {
      const matVec = documents.find((d) => d.filename.includes("matrix_vector"));
      const m5Doc = documents.find((d) => d.filename.includes("100p_a"));
      const chosen = matVec ?? m5Doc ?? documents[0];
      setDocId(chosen.doc_id);
      setPageNum(matVec ? 25 : Math.min(chosen.total_pages, 62));
      setBbox(matVec ? [0.53, 0.642, 0.675, 0.706] : [0.55, 0.42, 0.88, 0.56]);
    }
  }, [documents, jumpTarget, docId]);

  const currentDoc = documents.find((d) => d.doc_id === docId) ?? null;

  const runPreview = useCallback(
    async (signal?: AbortSignal) => {
      if (!docId) return;
      setLoading(true);
      setError(null);
      try {
        const res = await getFoveaPreview(
          {
            doc_id: docId,
            page_num: pageNum,
            bbox,
            pad_ratio: padRatio,
            focus_share: focusShare,
            header_ratio: headerRatio,
            stub_ratio: stubRatio,
            anchor_share: 0.15,
            auto_anchors: autoAnchors,
            adaptive_aspect: adaptiveAspect,
          },
          signal
        );
        if (!signal?.aborted) {
          setPreview(res);
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [docId, pageNum, bbox, padRatio, focusShare, headerRatio, stubRatio, autoAnchors, adaptiveAspect]
  );

  useEffect(() => {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => {
      void runPreview(ctrl.signal);
    }, 100);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [runPreview]);

  const relPos = (e: React.MouseEvent<HTMLDivElement>): [number, number] | null => {
    const el = canvasRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    return [x, y];
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    const p = relPos(e);
    if (!p) return;
    setDragStart(p);
    setDragCurrent(p);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!dragStart) return;
    const p = relPos(e);
    if (p) setDragCurrent(p);
  };

  const handleMouseUp = () => {
    if (dragStart && dragCurrent) {
      const x1 = Math.min(dragStart[0], dragCurrent[0]);
      const y1 = Math.min(dragStart[1], dragCurrent[1]);
      const x2 = Math.max(dragStart[0], dragCurrent[0]);
      const y2 = Math.max(dragStart[1], dragCurrent[1]);
      if (x2 - x1 >= 0.03 && y2 - y1 >= 0.02) {
        setBbox([
          Number(x1.toFixed(4)),
          Number(y1.toFixed(4)),
          Number(x2.toFixed(4)),
          Number(y2.toFixed(4)),
        ]);
      }
    }
    setDragStart(null);
    setDragCurrent(null);
  };

  const activeBox: [number, number, number, number] =
    dragStart && dragCurrent
      ? [
          Math.min(dragStart[0], dragCurrent[0]),
          Math.min(dragStart[1], dragCurrent[1]),
          Math.max(dragStart[0], dragCurrent[0]),
          Math.max(dragStart[1], dragCurrent[1]),
        ]
      : bbox;

  const applyQuickScene = (kind: "vec_matrix" | "ras_matrix" | "m5_footnote") => {
    if (kind === "vec_matrix") {
      const d = documents.find((x) => x.filename.includes("matrix_vector"));
      if (d) {
        setDocId(d.doc_id);
        setPageNum(25);
        setBbox([0.53, 0.642, 0.675, 0.706]);
      }
    } else if (kind === "ras_matrix") {
      const d = documents.find((x) => x.filename.includes("matrix_raster"));
      if (d) {
        setDocId(d.doc_id);
        setPageNum(25);
        setBbox([0.768, 0.773, 0.922, 0.838]);
      }
    } else {
      const d = documents.find((x) => x.filename.includes("100p_a")) ?? documents[0];
      if (d) {
        setDocId(d.doc_id);
        setPageNum(Math.min(d.total_pages, 62));
        setBbox([0.55, 0.42, 0.88, 0.56]);
      }
    }
  };

  const warpMeta: WarpMeta | null = preview?.fovea_warp.warp_meta ?? null;

  return (
    <div className="lens-grid">
      {/* LEFT: Interactive Source Page ROI Selector & Parameters */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <Crosshair size={15} color="#2563eb" />
              源页面交互框选 (拖拽鼠标框选任意目标单元)
            </h3>
            {preview && (
              <span className="badge badge-teal mono">{preview.latency_ms} ms</span>
            )}
          </div>
          <div className="panel-body">
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => applyQuickScene("vec_matrix")}
              >
                <Sparkles size={12} /> 18x5 宽矩阵单格
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => applyQuickScene("ras_matrix")}
              >
                <Sparkles size={12} /> 纯扫描栅格矩阵
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => applyQuickScene("m5_footnote")}
              >
                <Sparkles size={12} /> M5 100p 财务附注
              </button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 92px", gap: 8, marginBottom: 10 }}>
              <select
                className="select-input"
                value={docId}
                onChange={(e) => {
                  setDocId(e.target.value);
                  setPageNum(1);
                }}
              >
                {documents.map((d) => (
                  <option key={d.doc_id} value={d.doc_id}>
                    {d.filename} ({d.total_pages}p)
                  </option>
                ))}
              </select>
              <input
                type="number"
                className="text-input mono"
                min={1}
                max={currentDoc?.total_pages ?? 100}
                value={pageNum}
                onChange={(e) =>
                  setPageNum(
                    Math.max(
                      1,
                      Math.min(currentDoc?.total_pages ?? 100, Number(e.target.value) || 1)
                    )
                  )
                }
              />
            </div>

            {docId && (
              <div
                ref={canvasRef}
                className="page-canvas-wrap"
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
              >
                <img
                  src={getPageImageUrl(docId, pageNum, "view")}
                  alt={`Page ${pageNum}`}
                />
                {/* Detected Mid-Page Table Header Band */}
                {preview?.detected_table_header_y_norm && (
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      right: 0,
                      top: `${preview.detected_table_header_y_norm[0] * 100}%`,
                      height: `${
                        (preview.detected_table_header_y_norm[1] -
                          preview.detected_table_header_y_norm[0]) *
                        100
                      }%`,
                      background: "rgba(245, 158, 11, 0.20)",
                      borderTop: "1.5px dashed #d97706",
                      borderBottom: "1.5px dashed #d97706",
                      pointerEvents: "none",
                    }}
                  />
                )}
                {/* Detected Left Row-Stub Divider */}
                {preview && preview.detected_stub_x_norm > 0.01 && (
                  <div
                    style={{
                      position: "absolute",
                      top: 0,
                      bottom: 0,
                      left: 0,
                      width: `${preview.detected_stub_x_norm * 100}%`,
                      background: "rgba(16, 185, 129, 0.12)",
                      borderRight: "1.5px dashed #059669",
                      pointerEvents: "none",
                    }}
                  />
                )}
                {/* User Focus ROI */}
                <div
                  style={{
                    position: "absolute",
                    left: `${activeBox[0] * 100}%`,
                    top: `${activeBox[1] * 100}%`,
                    width: `${(activeBox[2] - activeBox[0]) * 100}%`,
                    height: `${(activeBox[3] - activeBox[1]) * 100}%`,
                    border: "2px solid #0284c7",
                    background: "rgba(14, 165, 233, 0.18)",
                    pointerEvents: "none",
                  }}
                />
              </div>
            )}

            <div
              className="mono"
              style={{
                marginTop: 8,
                fontSize: 11,
                color: "#475569",
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span>
                ROI: [{bbox.map((v) => v.toFixed(3)).join(", ")}]
              </span>
              {preview && (
                <span>
                  Stub x={preview.detected_stub_x_norm.toFixed(3)}
                  {preview.detected_table_header_y_norm
                    ? ` · Hdr y=[${preview.detected_table_header_y_norm[0].toFixed(2)}, ${preview.detected_table_header_y_norm[1].toFixed(2)}]`
                    : ""}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <Sliders size={15} color="#0f766e" />
              变形场核参数 (Anisotropic Warp Parameters)
            </h3>
          </div>
          <div className="panel-body">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div className="field-group">
                <label className="field-label">
                  <span>焦点分配 (focus_share)</span>
                  <span className="mono">{focusShare.toFixed(2)}</span>
                </label>
                <input
                  type="range"
                  min={0.45}
                  max={0.82}
                  step={0.01}
                  value={focusShare}
                  onChange={(e) => setFocusShare(Number(e.target.value))}
                />
              </div>

              <div className="field-group">
                <label className="field-label">
                  <span>安全外扩 (pad_ratio)</span>
                  <span className="mono">{padRatio.toFixed(2)}</span>
                </label>
                <input
                  type="range"
                  min={0.0}
                  max={0.25}
                  step={0.01}
                  value={padRatio}
                  onChange={(e) => setPadRatio(Number(e.target.value))}
                />
              </div>

              <div className="field-group">
                <label className="field-label">
                  <span>顶栏比例 (header_ratio)</span>
                  <span className="mono">{headerRatio.toFixed(2)}</span>
                </label>
                <input
                  type="range"
                  min={0.05}
                  max={0.25}
                  step={0.01}
                  value={headerRatio}
                  onChange={(e) => setHeaderRatio(Number(e.target.value))}
                />
              </div>

              <div className="field-group">
                <label className="field-label">
                  <span>左侧行名 (stub_ratio)</span>
                  <span className="mono">{stubRatio.toFixed(2)}</span>
                </label>
                <input
                  type="range"
                  min={0.05}
                  max={0.30}
                  step={0.01}
                  value={stubRatio}
                  onChange={(e) => setStubRatio(Number(e.target.value))}
                />
              </div>
            </div>

            <div className="toggle-grid">
              <label className={`toggle-chip ${autoAnchors ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={autoAnchors}
                  onChange={(e) => setAutoAnchors(e.target.checked)}
                />
                <span>页中表头/分隔线检测</span>
              </label>
              <label className={`toggle-chip ${adaptiveAspect ? "active" : ""}`}>
                <input
                  type="checkbox"
                  checked={adaptiveAspect}
                  onChange={(e) => setAdaptiveAspect(e.target.checked)}
                />
                <span>自适应长宽比画布</span>
              </label>
            </div>
          </div>
        </div>
      </div>

      {/* RIGHT: Side-by-Side Linear Crop vs FoveaDoc v2.0 + Piecewise Bijective Curves */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {error && (
          <div
            style={{
              padding: 12,
              borderRadius: 6,
              background: "#fef2f2",
              border: "1px solid #fecaca",
              color: "#991b1b",
            }}
          >
            {error}
          </div>
        )}

        <div className="compare-duo">
          {/* Standard Linear Crop */}
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">
                <Maximize2 size={14} color="#dc2626" />
                传统局部矩形裁剪 (Standard Linear Crop)
              </h3>
              {preview && (
                <span className="badge badge-red mono">
                  {preview.linear_crop.size[0]}×{preview.linear_crop.size[1]}
                </span>
              )}
            </div>
            <div className="panel-body">
              <div style={{ fontSize: 12, color: "#64748b", marginBottom: 8 }}>
                仅裁剪局部矩形区域：表头列标与左侧行名被物理截断，当焦点跨多列或存在 1 行偏移时无法自校验。
              </div>
              <div className="image-frame" style={{ minHeight: 340 }}>
                {preview ? (
                  <img src={preview.linear_crop.image_data_url} alt="Linear Crop" />
                ) : (
                  <span style={{ color: "#94a3b8" }}>{loading ? "渲染中..." : "等待输入"}</span>
                )}
              </div>
            </div>
          </div>

          {/* Anisotropic Topological Foveation v2.0 */}
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">
                <Sparkles size={14} color="#0f766e" />
                各向异性拓扑凹面镜 (FoveaDoc v2.0)
              </h3>
              {preview && (
                <span className="badge badge-teal mono">
                  {preview.fovea_warp.size[0]}×{preview.fovea_warp.size[1]} · {preview.fovea_warp.mag_x}x/{preview.fovea_warp.mag_y}x
                </span>
              )}
            </div>
            <div className="panel-body">
              <div style={{ fontSize: 12, color: "#0f766e", marginBottom: 8 }}>
                可分离正交非均匀重采样：焦点高倍放大（青色指示框），同时保持页中表头列与左侧行名严格正交共线。
              </div>
              <div className="image-frame" style={{ minHeight: 340 }}>
                {preview ? (
                  <img src={preview.fovea_warp.image_data_url} alt="Anisotropic Fovea Warp" />
                ) : (
                  <span style={{ color: "#94a3b8" }}>{loading ? "渲染中..." : "等待输入"}</span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Piecewise Monotonic Knot Mapping Curves */}
        {warpMeta && (
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">
                一维可分离分段严格单调映射曲线 T(x,y) = (g_x⁻¹(x), g_y⁻¹(y)) · det J_T &gt; 0
              </h3>
              <span className="badge badge-blue mono">Exact Inverse Error ≤ 2.5e-4</span>
            </div>
            <div className="panel-body">
              <div className="compare-duo">
                <KnotCurveSvg
                  title="水平映射 x = g_x(u) [左侧行名锚定带 + 焦点放大带]"
                  outKnots={warpMeta.u_knots}
                  srcKnots={warpMeta.x_knots}
                  color="#2563eb"
                />
                <KnotCurveSvg
                  title="垂直映射 y = g_y(v) [页顶 + 页中表头锚定带 + 焦点放大带]"
                  outKnots={warpMeta.v_knots}
                  srcKnots={warpMeta.y_knots}
                  color="#0f766e"
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
