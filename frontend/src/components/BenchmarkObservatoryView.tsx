import React, { useEffect, useState } from "react";
import { AlertTriangle, Award, BarChart3, RefreshCw, TrendingUp } from "lucide-react";
import { getBenchmarks } from "../api";
import type { BenchmarkGroupStats, BenchmarksResponse } from "../types";

const GROUP_LABELS: Record<string, string> = {
  B0: "B0 · Full-Doc Thumbnail Dump",
  B1: "B1 · PyMuPDF BM25 Top-6 RAG",
  B2: "B2 · Flat Agent (No Tree)",
  M3: "M3 · PageIndex + Linear Crop (Voluntary)",
  M5_no_sboed: "M5 w/o S-BOED Gate (Ablation)",
  M5_crop: "M5 w/o Fovea (S-BOED + Linear Crop)",
  M5: "M5 · Full FoveaDoc",
};

const GROUP_COLORS: Record<string, string> = {
  B0: "#94a3b8",
  B1: "#64748b",
  B2: "#d97706",
  M3: "#7c3aed",
  M5_no_sboed: "#ea580c",
  M5_crop: "#2563eb",
  M5: "#0f766e",
};

const ParetoChartSvg: React.FC<{ groups: Record<string, BenchmarkGroupStats> }> = ({
  groups,
}) => {
  const entries = Object.entries(groups);
  if (entries.length === 0) return null;

  const w = 660;
  const h = 290;
  const padL = 54;
  const padR = 30;
  const padT = 24;
  const padB = 42;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const maxTok = Math.max(25000, ...entries.map(([, g]) => g.avg_prompt_tokens * 1.12));

  const toX = (tok: number) => padL + (tok / maxTok) * plotW;
  const toY = (acc: number) => padT + plotH - acc * plotH;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {/* Horizontal grid lines */}
      {[0, 0.25, 0.5, 0.75, 1.0].map((tick) => {
        const y = toY(tick);
        return (
          <g key={tick}>
            <line
              x1={padL}
              y1={y}
              x2={padL + plotW}
              y2={y}
              stroke="#e2e8f0"
              strokeDasharray={tick === 1.0 ? undefined : "4 3"}
            />
            <text x={padL - 8} y={y + 4} textAnchor="end" fontSize="10.5" fill="#64748b">
              {(tick * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}

      {/* X axis ticks */}
      {[0, 5000, 10000, 15000, 20000, 25000]
        .filter((v) => v <= maxTok)
        .map((v) => {
          const x = toX(v);
          return (
            <g key={v}>
              <line x1={x} y1={padT + plotH} x2={x} y2={padT + plotH + 5} stroke="#94a3b8" />
              <text
                x={x}
                y={padT + plotH + 18}
                textAnchor="middle"
                fontSize="10.5"
                fill="#64748b"
              >
                {(v / 1000).toFixed(0)}k
              </text>
            </g>
          );
        })}

      <line x1={padL} y1={padT} x2={padL} y2={padT + plotH} stroke="#64748b" strokeWidth={1.3} />
      <line
        x1={padL}
        y1={padT + plotH}
        x2={padL + plotW}
        y2={padT + plotH}
        stroke="#64748b"
        strokeWidth={1.3}
      />

      <text
        x={padL + plotW / 2}
        y={h - 6}
        textAnchor="middle"
        fontSize="11"
        fontWeight="600"
        fill="#334155"
      >
        Average Prompt Tokens per Query (Lower = More Efficient)
      </text>

      {entries.map(([key, g]) => {
        const cx = toX(g.avg_prompt_tokens);
        const cy = toY(g.acc);
        const yHigh = toY(g.ci[1]);
        const yLow = toY(g.ci[0]);
        const col = GROUP_COLORS[key] ?? "#2563eb";
        const isM5 = key === "M5";
        const alignLeft = key === "M5_no_sboed" || key === "B2";
        const tx = alignLeft ? cx - 9 : cx + 9;
        const ty =
          key === "M5"
            ? cy - 8
            : key === "M5_crop"
            ? cy + 15
            : key === "M5_no_sboed"
            ? cy + 5
            : key === "B2"
            ? cy + 5
            : key === "B0"
            ? cy + 12
            : cy - 4;

        return (
          <g key={key}>
            {/* 95% Wilson CI Whisker */}
            <line x1={cx} y1={yHigh} x2={cx} y2={yLow} stroke={col} strokeWidth={1.5} opacity={0.65} />
            <line x1={cx - 4} y1={yHigh} x2={cx + 4} y2={yHigh} stroke={col} strokeWidth={1.5} />
            <line x1={cx - 4} y1={yLow} x2={cx + 4} y2={yLow} stroke={col} strokeWidth={1.5} />
            <circle
              cx={cx}
              cy={cy}
              r={isM5 ? 7 : 5.5}
              fill={col}
              stroke="#ffffff"
              strokeWidth={2}
            />
            <text
              x={tx}
              y={ty}
              textAnchor={alignLeft ? "end" : "start"}
              fontSize="10.5"
              fontWeight={isM5 ? "700" : "600"}
              fill={col}
            >
              {key} ({(g.acc * 100).toFixed(1)}%)
            </text>
          </g>
        );
      })}
    </svg>
  );
};

const M6MatrixBarsSvg: React.FC<{ groups: Record<string, BenchmarkGroupStats> }> = ({
  groups,
}) => {
  const order = ["NoZoom", "LinearCrop", "FoveaDoc_v2"].filter((k) => k in groups);
  if (order.length === 0) return null;

  const w = 640;
  const h = 270;
  const padL = 52;
  const padR = 24;
  const padT = 26;
  const padB = 42;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const metrics: Array<{ label: string; getter: (g: BenchmarkGroupStats) => number }> = [
    { label: "Overall (N=12)", getter: (g) => g.acc },
    { label: "Vector Matrix (N=6)", getter: (g) => g.vector_acc ?? 0 },
    { label: "Pure-Raster Scan (N=6)", getter: (g) => g.raster_acc ?? 0 },
  ];

  const colors: Record<string, string> = {
    NoZoom: "#94a3b8",
    LinearCrop: "#2563eb",
    FoveaDoc_v2: "#0f766e",
  };

  const clusterW = plotW / metrics.length;
  const barW = 36;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {[0, 0.25, 0.5, 0.75, 1.0].map((t) => {
        const y = padT + plotH - t * plotH;
        return (
          <g key={t}>
            <line x1={padL} y1={y} x2={padL + plotW} y2={y} stroke="#e2e8f0" />
            <text x={padL - 8} y={y + 4} textAnchor="end" fontSize="10.5" fill="#64748b">
              {(t * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}

      {metrics.map((m, mIdx) => {
        const centerX = padL + clusterW * (mIdx + 0.5);
        const startX = centerX - (order.length * (barW + 6)) / 2;
        return (
          <g key={m.label}>
            {order.map((grpKey, gIdx) => {
              const val = m.getter(groups[grpKey]);
              const bh = Math.max(2, val * plotH);
              const bx = startX + gIdx * (barW + 6);
              const by = padT + plotH - bh;
              return (
                <g key={grpKey}>
                  <rect
                    x={bx}
                    y={by}
                    width={barW}
                    height={bh}
                    rx={3}
                    fill={colors[grpKey] ?? "#334155"}
                  />
                  <text
                    x={bx + barW / 2}
                    y={by - 5}
                    textAnchor="middle"
                    fontSize="10"
                    fontWeight="700"
                    fill="#0f172a"
                  >
                    {(val * 100).toFixed(0)}%
                  </text>
                </g>
              );
            })}
            <text
              x={centerX}
              y={padT + plotH + 20}
              textAnchor="middle"
              fontSize="11"
              fontWeight="600"
              fill="#334155"
            >
              {m.label}
            </text>
          </g>
        );
      })}

      {/* Legend */}
      <g transform={`translate(${padL + 8}, 6)`}>
        {order.map((k, idx) => (
          <g key={k} transform={`translate(${idx * 155}, 0)`}>
            <rect x={0} y={0} width={11} height={11} rx={2} fill={colors[k]} />
            <text x={16} y={10} fontSize="10.5" fontWeight="600" fill="#334155">
              {k}
            </text>
          </g>
        ))}
      </g>
    </svg>
  );
};

export const BenchmarkObservatoryView: React.FC = () => {
  const [data, setData] = useState<BenchmarksResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getBenchmarks();
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (loading && !data) {
    return <div className="panel panel-body">正在加载实测评测数据...</div>;
  }

  if (error) {
    return <div className="panel panel-body" style={{ color: "#b91c1c" }}>{error}</div>;
  }

  const m4Groups = data?.m4_m5.groups ?? {};
  const m5Stat = m4Groups["M5"];
  const pwB0 = data?.m4_m5.pairwise.M5_vs_B0;
  const pwM3 = data?.m4_m5.pairwise.M5_vs_M3;
  const m6Groups = data?.m6_matrix?.groups ?? {};
  const m6Fovea = m6Groups["FoveaDoc_v2"];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Top Statistical Summary Strip */}
      <div className="kpi-grid">
        <div className="panel panel-body">
          <div className="kpi-label">M5 长文档基准准确率 (N=16, 50-100p)</div>
          <div className="kpi-val" style={{ fontSize: 22, color: "#0f766e" }}>
            {m5Stat ? `${(m5Stat.acc * 100).toFixed(1)}% (${m5Stat.correct}/${m5Stat.n})` : "—"}
          </div>
          {m5Stat && (
            <div className="mono" style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>
              95% Wilson CI [{(m5Stat.ci[0] * 100).toFixed(1)}%, {(m5Stat.ci[1] * 100).toFixed(1)}%]
            </div>
          )}
        </div>

        <div className="panel panel-body">
          <div className="kpi-label">配对精确检验显著性 (McNemar Exact Test)</div>
          <div className="kpi-val" style={{ fontSize: 22, color: "#1d4ed8" }}>
            {pwB0 ? `p = ${pwB0.mcnemar_p.toFixed(4)}` : "—"}
          </div>
          <div className="mono" style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>
            vs B0 (+{pwB0?.wins ?? 0}/-{pwB0?.losses ?? 0}) · vs M3 p={pwM3?.mcnemar_p.toFixed(4) ?? "—"}
          </div>
        </div>

        <div className="panel panel-body">
          <div className="kpi-label">Prompt Token 节省比 (Paired Bootstrap 95% CI)</div>
          <div className="kpi-val" style={{ fontSize: 22, color: "#0f172a" }}>
            {pwB0?.bootstrap
              ? `${(pwB0.bootstrap.savings * 100).toFixed(1)}% Saved`
              : "—"}
          </div>
          {pwB0?.bootstrap && (
            <div className="mono" style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>
              Ratio {pwB0.bootstrap.ratio.toFixed(3)} [{pwB0.bootstrap.ratio_ci_low.toFixed(3)}, {pwB0.bootstrap.ratio_ci_high.toFixed(3)}]
            </div>
          )}
        </div>

        <div className="panel panel-body">
          <div className="kpi-label">M6 18x5 宽矩阵与零文本纯扫描页 (N=12)</div>
          <div className="kpi-val" style={{ fontSize: 22, color: "#6d28d9" }}>
            {m6Fovea
              ? `${(m6Fovea.acc * 100).toFixed(1)}% (${m6Fovea.correct}/${m6Fovea.n})`
              : "Running..."}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 3 }}>
            <span className="mono" style={{ fontSize: 11, color: "#64748b" }}>
              {m6Fovea
                ? `Vec ${(m6Fovea.vector_acc! * 100).toFixed(0)}% · Scan ${(m6Fovea.raster_acc! * 100).toFixed(0)}%`
                : "qwen3.8-flash temp=0"}
            </span>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              <RefreshCw size={11} /> 刷新
            </button>
          </div>
        </div>
      </div>

      {/* Interactive SVG Charts */}
      <div className="compare-duo">
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <TrendingUp size={15} color="#0f766e" />
              M4/M5 准确率–Token 成本帕累托前沿 (N=16, qwen3.8-flash)
            </h3>
            <span className="badge badge-teal mono">temp=0 · seed=42</span>
          </div>
          <div className="panel-body">
            <ParetoChartSvg groups={m4Groups} />
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <BarChart3 size={15} color="#2563eb" />
              M6 18x5 宽矩阵与零文本纯位图扫描页压力测试 (N=12, 3.8pt)
            </h3>
            <span className="badge badge-blue mono">6 Vector + 6 Raster</span>
          </div>
          <div className="panel-body">
            {Object.keys(m6Groups).length > 0 ? (
              <M6MatrixBarsSvg groups={m6Groups} />
            ) : (
              <div style={{ padding: 24, color: "#64748b" }}>
                M6 评测汇总生成后将自动渲染对比柱状图（点击右上角刷新）。
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Suite A Table: M4/M5 Full Ablation */}
      <div className="panel">
        <div className="panel-header">
          <h3 className="panel-title">
            <Award size={15} color="#1d4ed8" />
            表 1：长文档细粒度视觉证据基准与严格消融实验 (`manifest_full16.jsonl`, N=16)
          </h3>
          <span className="mono" style={{ fontSize: 11.5, color: "#64748b" }}>
            4-gram Prompt Contamination = 0.0%
          </span>
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>实验组 (Group)</th>
                <th>准确率 (Acc)</th>
                <th>95% Wilson CI</th>
                <th>Nav Recall</th>
                <th>Seeker Recall</th>
                <th>Zoom 触发率</th>
                <th>残差纠偏</th>
                <th>Avg Prompt Tokens</th>
                <th>P50 / P95 耗时 (s)</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(m4Groups).map(([key, g]) => (
                <tr key={key} className={key === "M5" ? "highlight-row" : ""}>
                  <td>
                    <span className="mono" style={{ fontWeight: 700 }}>{key}</span>
                    <span style={{ color: "#64748b", marginLeft: 6, fontSize: 11.5 }}>
                      {GROUP_LABELS[key] ?? ""}
                    </span>
                  </td>
                  <td className="mono">
                    {(g.acc * 100).toFixed(1)}% ({g.correct}/{g.n})
                  </td>
                  <td className="mono">
                    [{(g.ci[0] * 100).toFixed(1)}%, {(g.ci[1] * 100).toFixed(1)}%]
                  </td>
                  <td className="mono">
                    {g.nav_recall != null ? `${(g.nav_recall * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="mono">
                    {g.seeker_recall != null ? `${(g.seeker_recall * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="mono">{(g.zoom_rate * 100).toFixed(1)}%</td>
                  <td className="mono">{g.residual_corrections}</td>
                  <td className="mono">{Math.round(g.avg_prompt_tokens).toLocaleString()}</td>
                  <td className="mono">
                    {(g.p50_wall_ms / 1000).toFixed(1)} / {(g.p95_wall_ms / 1000).toFixed(1)}
                  </td>
                  <td className="mono">{g.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Suite B Table: M6 Matrix & Pure-Raster Scan */}
      {Object.keys(m6Groups).length > 0 && (
        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              <BarChart3 size={15} color="#0f766e" />
              表 2：M6 18x5 多列密集矩阵与零矢量纯扫描栅格页评测 (`manifest_m6.jsonl`, N=12)
            </h3>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>实验组 (Group)</th>
                  <th>总体准确率 (N=12)</th>
                  <th>95% Wilson CI</th>
                  <th>矢量矩阵子集 (N=6)</th>
                  <th>纯栅格扫描子集 (N=6)</th>
                  <th>Zoom 触发率</th>
                  <th>Avg Prompt Tokens</th>
                  <th>P50 / P95 耗时 (s)</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(m6Groups).map(([key, g]) => (
                  <tr key={key} className={key === "FoveaDoc_v2" ? "highlight-row" : ""}>
                    <td className="mono" style={{ fontWeight: 700 }}>{key}</td>
                    <td className="mono">
                      {(g.acc * 100).toFixed(1)}% ({g.correct}/{g.n})
                    </td>
                    <td className="mono">
                      [{(g.ci[0] * 100).toFixed(1)}%, {(g.ci[1] * 100).toFixed(1)}%]
                    </td>
                    <td className="mono">{((g.vector_acc ?? 0) * 100).toFixed(1)}%</td>
                    <td className="mono">{((g.raster_acc ?? 0) * 100).toFixed(1)}%</td>
                    <td className="mono">{(g.zoom_rate * 100).toFixed(1)}%</td>
                    <td className="mono">{Math.round(g.avg_prompt_tokens).toLocaleString()}</td>
                    <td className="mono">
                      {(g.p50_wall_ms / 1000).toFixed(1)} / {(g.p95_wall_ms / 1000).toFixed(1)}
                    </td>
                    <td className="mono">{g.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Publication Figures & Threats to Validity */}
      {data && (
        <div className="compare-duo">
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">300-DPI 论文配图：凹面镜正交共线与精确逆映射验证</h3>
            </div>
            <div className="panel-body">
              <img
                src={data.m4_m5.figures.fovea_vs_crop}
                alt="Fovea vs Crop Comparison"
                style={{ width: "100%", height: "auto", borderRadius: 6, border: "1px solid #e2e8f0" }}
              />
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">
                <AlertTriangle size={15} color="#b45309" />
                有效性威胁与失效边界披露 (Threats to Validity)
              </h3>
            </div>
            <div className="panel-body" style={{ fontSize: 12.5, color: "#334155", lineHeight: 1.6 }}>
              <p style={{ marginTop: 0 }}>
                <strong>1. 内部有效性 (Internal Validity)：</strong>
                所有对比组严格固定 <code className="mono">qwen3.8-flash</code>、
                <code className="mono">temperature = 0</code>、<code className="mono">seed = 42</code>，
                但云端 MoE/FP8 推理仍存在约 ±1 题的算子级非确定性抖动；已报告配对精确二项 McNemar{" "}
                <span className="mono">p</span> 值与 95% Wilson 区间。
              </p>
              <p>
                <strong>2. 外部有效性 (External Validity)：</strong>
                纯位图扫描页在无目录书签（<code className="mono">toc == []</code>）且无矢量文本层时，
                <code className="mono">build_document_tree</code> 退化为固定 20 页分块树；对旋转倾斜（Skew &gt; 5°）或非正交手写批注，一维可分离正交网格变形不再保持行列严格共线，需前置透视校正或 OCR 布局检测。
              </p>
              <p style={{ marginBottom: 0 }}>
                <strong>3. 结构有效性 (Construct Validity)：</strong>
                在简单 3×2 单表页（<code className="mono">manifest_full16</code>）中，由于 Step 2 同时保留全页视图，
                <code className="mono">M5_crop</code> 与 <code className="mono">M5</code> 同达 100.0%；而在 18×5 密集多列矩阵（
                <code className="mono">manifest_m6</code>）当首步定位框发生 1 行偏移时（如{" "}
                <code className="mono">m6_case_007</code>），局部矩形裁剪因截断左侧行名直接误读相邻行，而双锚定凹面镜保留同行标实现自纠偏。
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
