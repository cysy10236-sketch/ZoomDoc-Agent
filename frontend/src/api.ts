import type {
  BenchmarksResponse,
  DocumentItem,
  DocumentTreeResponse,
  FoveaPreviewRequest,
  FoveaPreviewResponse,
  HealthStatus,
  PresetItem,
  RunAgentRequest,
  RunDetailResponse,
  RunSummaryItem,
  StreamEvent,
} from "./types";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // keep statusText
    }
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export async function getHealth(): Promise<HealthStatus> {
  return fetchJson<HealthStatus>("/api/health");
}

export async function listDocuments(): Promise<DocumentItem[]> {
  const data = await fetchJson<{ documents: DocumentItem[] }>("/api/documents");
  return data.documents;
}

export async function uploadDocument(file: File): Promise<{
  doc_id: string;
  filename: string;
  total_pages: number;
  cache_hit: boolean;
}> {
  const form = new FormData();
  form.append("file", file);
  return fetchJson("/api/documents/upload", {
    method: "POST",
    body: form,
  });
}

export async function getDocumentTree(docId: string): Promise<DocumentTreeResponse> {
  return fetchJson<DocumentTreeResponse>(`/api/documents/${encodeURIComponent(docId)}/tree`);
}

export function getPageImageUrl(
  docId: string,
  pageNum: number,
  tier: "view" | "thumb" | "source" = "view"
): string {
  return `/api/documents/${encodeURIComponent(docId)}/page/${pageNum}/image?tier=${tier}`;
}

export async function listPresets(): Promise<PresetItem[]> {
  const data = await fetchJson<{ presets: PresetItem[] }>("/api/presets");
  return data.presets;
}

export async function listRuns(limit = 50): Promise<RunSummaryItem[]> {
  const data = await fetchJson<{ runs: RunSummaryItem[] }>(`/api/runs?limit=${limit}`);
  return data.runs;
}

export async function getRunDetail(traceId: string): Promise<RunDetailResponse> {
  return fetchJson<RunDetailResponse>(`/api/runs/${encodeURIComponent(traceId)}`);
}

export async function getFoveaPreview(
  req: FoveaPreviewRequest,
  signal?: AbortSignal
): Promise<FoveaPreviewResponse> {
  return fetchJson<FoveaPreviewResponse>("/api/vision/fovea-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
}

export async function getBenchmarks(): Promise<BenchmarksResponse> {
  return fetchJson<BenchmarksResponse>("/api/benchmarks");
}

export async function streamAgentRun(
  req: RunAgentRequest,
  onEvent: (evt: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch("/api/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });

  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const chunk of parts) {
      const line = chunk
        .split("\n")
        .find((ln) => ln.startsWith("data: "));
      if (!line) continue;
      const jsonStr = line.slice(6).trim();
      if (!jsonStr) continue;
      const parsed = JSON.parse(jsonStr) as StreamEvent;
      onEvent(parsed);
    }
  }
}
