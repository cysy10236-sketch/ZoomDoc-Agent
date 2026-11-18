export interface HealthStatus {
  status: string;
  version: string;
  vlm_model: string;
  vlm_base_url: string;
  has_api_key: boolean;
  cache_dir: string;
  benchmarks_available: {
    m4_m5: boolean;
    m6_matrix: boolean;
  };
}

export interface DocumentItem {
  doc_id: string;
  filename: string;
  total_pages: number;
  category: string;
}

export interface DocTreeNode {
  node_id: string;
  structure: string;
  title: string;
  level: number;
  start_page: number;
  end_page: number;
  summary: string;
  children: DocTreeNode[];
}

export interface DocumentTree {
  doc_id: string;
  total_pages: number;
  max_leaf_pages: number;
  roots: DocTreeNode[];
}

export interface DocumentTreeResponse {
  doc_id: string;
  filename: string;
  cache_hit: boolean;
  latency_ms?: number;
  tree: DocumentTree;
}

export interface PresetItem {
  preset_id: string;
  suite: string;
  case_id: string;
  doc_id: string;
  filename: string;
  total_pages: number;
  query_type: string;
  font_pt: number;
  gold_page: number;
  gold: string;
  question: string;
  replay_trace_id: string | null;
}

export interface NormBBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface CitedRegion {
  page: number;
  page_box: NormBBox | null;
  target: string | null;
}

export interface UsageTotals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  missing_usage_calls: number;
  latency_ms: number;
}

export interface FinalResultM3 {
  status: "answered" | "synthesized" | "no_answer" | "budget_exhausted";
  answer: string | null;
  reason: string | null;
  reference_pages: number[];
  cited_regions: CitedRegion[];
  inspect_rounds: number;
  seek_rounds: number;
  usage: UsageTotals;
  trace_id: string;
  nav_rounds?: number;
  selected_node_ids?: string[];
  total_pages?: number;
  zoom_count?: number;
  render_latency_ms?: number;
  tree_latency_ms?: number;
  total_wall_ms?: number;
  cache_hit?: boolean;
  residual_corrections?: number;
}

export interface TraceEvent {
  ts: string;
  trace_id: string;
  span_id: string;
  node: string;
  namespace?: string;
  status: "ok" | "retry" | "error";
  latency_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  attempt: number;
  payload: Record<string, unknown>;
}

export interface IsolationAudit {
  supervisor_state_bytes: number;
  base64_occurrences: number;
  sub_crop_path_occurrences: number;
  passed: boolean;
}

export interface WarpMeta {
  u_knots: number[];
  x_knots: number[];
  v_knots: number[];
  y_knots: number[];
}

export interface RunAssetCrop {
  name: string;
  is_fovea: boolean;
  url: string;
  warp_meta: WarpMeta | null;
}

export interface RunAssetGrid {
  name: string;
  url: string;
}

export interface RunDetailResponse {
  trace_id: string;
  doc_id: string | null;
  result: FinalResultM3 | null;
  supervisor_state: Record<string, unknown> | null;
  isolation_audit: IsolationAudit;
  trace_events: TraceEvent[];
  grids: RunAssetGrid[];
  crops: RunAssetCrop[];
}

export interface RunSummaryItem {
  trace_id: string;
  status: string;
  answer: string | null;
  reference_pages: number[];
  zoom_count: number;
  total_wall_ms: number;
  usage: UsageTotals | null;
  mtime: number;
}

export interface FoveaPreviewRequest {
  doc_id: string;
  page_num: number;
  bbox: [number, number, number, number];
  pad_ratio: number;
  focus_share: number;
  header_ratio: number;
  stub_ratio: number;
  anchor_share: number;
  auto_anchors: boolean;
  adaptive_aspect: boolean;
}

export interface FoveaPreviewResponse {
  latency_ms: number;
  source_size: [number, number];
  padded_box: [number, number, number, number];
  pixel_box: [number, number, number, number];
  detected_stub_x_norm: number;
  detected_table_header_y_norm: [number, number] | null;
  linear_crop: {
    size: [number, number];
    pixels: number;
    image_data_url: string;
  };
  fovea_warp: {
    size: [number, number];
    pixels: number;
    mag_x: number;
    mag_y: number;
    warp_meta: WarpMeta;
    image_data_url: string;
  };
}

export interface RunAgentRequest {
  doc_id: string;
  question: string;
  zoom_mode: "fovea" | "crop";
  enable_sboed_gate: boolean;
  enable_residual_verification: boolean;
  fovea_auto_anchors: boolean;
  fovea_adaptive_aspect: boolean;
  enable_content_cache: boolean;
}

export type StreamEvent =
  | {
      event_type: "start";
      trace_id: string;
      doc_id: string;
      filename: string;
      question: string;
    }
  | {
      event_type: "stage";
      ts: string;
      trace_id: string;
      stage: string;
      payload: Record<string, unknown>;
    }
  | ({
      event_type: "trace";
    } & TraceEvent)
  | {
      event_type: "complete";
      trace_id: string;
      result: FinalResultM3;
    }
  | {
      event_type: "error";
      trace_id: string;
      error: string;
    };

export interface BenchmarkGroupStats {
  n: number;
  correct: number;
  acc: number;
  ci: [number, number];
  nav_recall?: number;
  seeker_recall?: number;
  vector_acc?: number;
  raster_acc?: number;
  zoom_rate: number;
  avg_prompt_tokens: number;
  avg_completion_tokens: number;
  p50_wall_ms: number;
  p95_wall_ms: number;
  cache_hit_rate?: number;
  residual_corrections: number;
  errors: number;
}

export interface BenchmarksResponse {
  m4_m5: {
    groups: Record<string, BenchmarkGroupStats>;
    pairwise: {
      M5_vs_B0?: {
        wins: number;
        losses: number;
        mcnemar_p: number;
        bootstrap: {
          ratio: number;
          ratio_ci_low: number;
          ratio_ci_high: number;
          savings: number;
          savings_ci_low: number;
          savings_ci_high: number;
        };
      };
      M5_vs_M3?: {
        wins: number;
        losses: number;
        mcnemar_p: number;
      };
    };
    figures: {
      fovea_vs_crop: string;
      pareto_frontier: string;
    };
  };
  m6_matrix: {
    groups: Record<string, BenchmarkGroupStats>;
    pairwise: Record<
      string,
      {
        wins: number;
        losses: number;
        mcnemar_p: number;
      }
    >;
  } | null;
}

export interface LensJumpTarget {
  doc_id: string;
  page_num: number;
  bbox?: [number, number, number, number];
}
