import React from "react";

interface GraphTopologySvgProps {
  visitedNodes: Set<string>;
  activeNode: string | null;
  zoomCount: number;
  residualCorrections: number;
  isolationPassed: boolean | null;
}

interface NodeSpec {
  id: string;
  label: string;
  sublabel: string;
  x: number;
  y: number;
  w: number;
  h: number;
  group: "master" | "nav_sub" | "insp_sub";
}

const NODES: NodeSpec[] = [
  { id: "ingest", label: "Ingest", sublabel: "Lazy PDF Probe", x: 18, y: 84, w: 96, h: 46, group: "master" },
  { id: "tree_builder", label: "TreeBuilder", sublabel: "PageIndex + SHA256", x: 132, y: 84, w: 112, h: 46, group: "master" },
  { id: "supervisor", label: "Supervisor", sublabel: "R0–R8 Router", x: 264, y: 84, w: 108, h: 46, group: "master" },
  { id: "navigator", label: "Navigator Subgraph", sublabel: "Expand / Leaf Select", x: 404, y: 22, w: 142, h: 46, group: "nav_sub" },
  { id: "seeker", label: "Seeker", sublabel: "SoM [idx] Grid", x: 404, y: 84, w: 142, h: 46, group: "master" },
  { id: "inspector", label: "Inspector + S-BOED", sublabel: "Fovea + Residual", x: 404, y: 148, w: 142, h: 48, group: "insp_sub" },
  { id: "critic", label: "Critic", sublabel: "Deterministic Gate", x: 576, y: 84, w: 104, h: 46, group: "master" },
  { id: "synthesizer", label: "Synthesizer", sublabel: "Cross-Page Merge", x: 700, y: 46, w: 108, h: 42, group: "master" },
  { id: "finalize", label: "Finalize", sublabel: "Cited Result", x: 700, y: 114, w: 108, h: 42, group: "master" },
];

export const GraphTopologySvg: React.FC<GraphTopologySvgProps> = ({
  visitedNodes,
  activeNode,
  zoomCount,
  residualCorrections,
  isolationPassed,
}) => {
  const isVisited = (id: string) => {
    if (visitedNodes.has(id)) return true;
    if (id === "inspector" && (visitedNodes.has("zoom") || visitedNodes.has("pack_report"))) {
      return true;
    }
    return false;
  };

  const isActive = (id: string) => {
    if (activeNode === id) return true;
    if (id === "inspector" && (activeNode === "zoom" || activeNode === "pack_report")) {
      return true;
    }
    return false;
  };

  return (
    <div style={{ width: "100%" }}>
      <svg
        viewBox="0 0 826 214"
        style={{ width: "100%", height: "auto", display: "block" }}
        role="img"
        aria-label="LangGraph Multi-Agent State Machine Topology"
      >
        <defs>
          <marker
            id="arrow-slate"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="5.5"
            markerHeight="5.5"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#94a3b8" />
          </marker>
          <marker
            id="arrow-active"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#2563eb" />
          </marker>
        </defs>

        {/* Subgraph Isolation Enclosures */}
        <rect
          x={394}
          y={12}
          width={162}
          height={66}
          rx={8}
          fill="#f5f3ff"
          stroke="#c4b5fd"
          strokeDasharray="4 3"
        />
        <rect
          x={394}
          y={138}
          width={162}
          height={68}
          rx={8}
          fill="#f0fdfa"
          stroke="#5eead4"
          strokeDasharray="4 3"
        />

        {/* Edges */}
        {[
          { d: "M 114 107 L 132 107", active: isVisited("tree_builder") },
          { d: "M 244 107 L 264 107", active: isVisited("supervisor") },
          { d: "M 372 95 L 404 48", active: isVisited("navigator") },
          { d: "M 372 107 L 404 107", active: isVisited("seeker") },
          { d: "M 372 119 L 404 170", active: isVisited("inspector") },
          { d: "M 546 45 L 576 96", active: isVisited("navigator") && isVisited("seeker") },
          { d: "M 546 107 L 576 107", active: isVisited("seeker") },
          { d: "M 546 172 L 576 118", active: isVisited("critic") },
          { d: "M 680 98 L 700 68", active: isVisited("synthesizer") },
          { d: "M 680 116 L 700 134", active: isVisited("finalize") },
          // Feedback loop from Critic back to Supervisor
          {
            d: "M 628 130 Q 628 202 318 202 Q 318 166 318 130",
            active: isVisited("critic"),
          },
        ].map((edge, idx) => (
          <path
            key={idx}
            d={edge.d}
            fill="none"
            stroke={edge.active ? "#2563eb" : "#cbd5e1"}
            strokeWidth={edge.active ? 1.8 : 1.3}
            markerEnd={edge.active ? "url(#arrow-active)" : "url(#arrow-slate)"}
          />
        ))}

        {/* Nodes */}
        {NODES.map((n) => {
          const visited = isVisited(n.id);
          const current = isActive(n.id);

          let fill = "#ffffff";
          let stroke = "#cbd5e1";
          let titleColor = "#0f172a";

          if (current) {
            fill = "#eff6ff";
            stroke = "#1d4ed8";
            titleColor = "#1e3a8a";
          } else if (visited) {
            if (n.group === "insp_sub") {
              fill = "#ccfbf1";
              stroke = "#0d9488";
              titleColor = "#115e59";
            } else if (n.group === "nav_sub") {
              fill = "#ede9fe";
              stroke = "#7c3aed";
              titleColor = "#4c1d95";
            } else {
              fill = "#f8fafc";
              stroke = "#3b82f6";
              titleColor = "#0f172a";
            }
          }

          return (
            <g key={n.id}>
              <rect
                x={n.x}
                y={n.y}
                width={n.w}
                height={n.h}
                rx={6}
                fill={fill}
                stroke={stroke}
                strokeWidth={current ? 2.4 : visited ? 1.7 : 1.2}
              />
              <text
                x={n.x + n.w / 2}
                y={n.y + 19}
                textAnchor="middle"
                fontSize="11.2"
                fontWeight="700"
                fill={titleColor}
              >
                {n.label}
              </text>
              <text
                x={n.x + n.w / 2}
                y={n.y + 34}
                textAnchor="middle"
                fontSize="9.6"
                fill="#475569"
              >
                {n.sublabel}
              </text>
            </g>
          );
        })}

        {/* Bottom Telemetry Badges */}
        <g transform="translate(18, 166)">
          <rect x={0} y={0} width={118} height={24} rx={4} fill="#f0fdfa" stroke="#99f6e4" />
          <text x={59} y={15} textAnchor="middle" fontSize="10" fontWeight="600" fill="#0f766e">
            Fovea Zooms: {zoomCount}
          </text>
        </g>
        <g transform="translate(144, 166)">
          <rect x={0} y={0} width={132} height={24} rx={4} fill="#fffbeb" stroke="#fde68a" />
          <text x={66} y={15} textAnchor="middle" fontSize="10" fontWeight="600" fill="#b45309">
            Residual Fixes: {residualCorrections}
          </text>
        </g>
        {isolationPassed !== null && (
          <g transform="translate(672, 168)">
            <rect
              x={0}
              y={0}
              width={138}
              height={24}
              rx={4}
              fill={isolationPassed ? "#ecfdf5" : "#fef2f2"}
              stroke={isolationPassed ? "#6ee7b7" : "#fecaca"}
            />
            <text
              x={69}
              y={15}
              textAnchor="middle"
              fontSize="10"
              fontWeight="700"
              fill={isolationPassed ? "#065f46" : "#991b1b"}
            >
              {isolationPassed ? "0-Leakage Verified" : "Isolation Alert"}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
};
