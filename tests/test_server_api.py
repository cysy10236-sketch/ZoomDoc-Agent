from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.make_synthetic_long_pdf import create_synthetic_long_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.cache import ContentAddressedCache
from zoomdoc.config import Settings
from zoomdoc.graph.supervisor import run_m5
from zoomdoc.index.tree import build_document_tree, get_leaf_nodes
from zoomdoc.server.app import create_app


def test_server_api_endpoints_and_sse_streaming(tmp_path: Path) -> None:
    """全面验证 FastAPI 后端：健康检查、PDF 上传、PageIndex 树、凹面镜实时对比预览、SSE 流式执行与路径穿越拦截。"""
    doc_dir = tmp_path / "docs"
    runs_dir = tmp_path / "runs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = doc_dir / "demo_30p.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=30,
        gold_page=15,
        gold_code="ZD-9090-WEB",
    )
    tree = build_document_tree(str(pdf_path), max_leaf_pages=20)
    leaf = next(l for l in get_leaf_nodes(tree) if l.start_page <= 15 <= l.end_page)
    p15_idx = 15 - leaf.start_page

    scripted = [
        json.dumps({"action": "select", "reason": "select target leaf", "node_ids": [leaf.node_id]}),
        json.dumps({"reason": "found table", "summary": "page 15", "choice": [p15_idx]}),
        json.dumps({"action": "answer", "reason": "draft", "answer": "ZD-9090-WEB", "confidence": 0.92, "reference": [0]}),
        json.dumps({"action": "answer", "reason": "verified", "answer": "ZD-9090-WEB", "confidence": 0.99, "reference": [0, 1]}),
    ]
    fake_vlm = FakeVLM(scripted)
    cfg = Settings(
        vlm_api_key="mock",
        vlm_model="mock-vlm",
        runs_dir=str(runs_dir),
        cache_dir=str(cache_dir),
    )

    app = create_app(
        vlm_override=fake_vlm,
        settings_override=cfg,
        extra_doc_dirs=[doc_dir],
        extra_runs_dirs=[runs_dir],
    )
    client = TestClient(app)

    # 1. Health check (must not leak API key)
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    h_data = r_health.json()
    assert h_data["status"] == "ok"
    assert "vlm_api_key" not in h_data

    # 2. Document catalog & tree
    r_docs = client.get("/api/documents")
    assert r_docs.status_code == 200
    demo_doc = next(d for d in r_docs.json()["documents"] if d["filename"] == "demo_30p.pdf")
    doc_id = demo_doc["doc_id"]

    r_tree = client.get(f"/api/documents/{doc_id}/tree")
    assert r_tree.status_code == 200
    assert r_tree.json()["tree"]["total_pages"] == 30

    # 3. Page image rendering endpoint
    r_img = client.get(f"/api/documents/{doc_id}/page/15/image?tier=view")
    assert r_img.status_code == 200
    assert r_img.headers["content-type"] == "image/png"
    assert r_img.content.startswith(b"\x89PNG")

    # 4. Interactive Fovea Lens Lab preview endpoint
    r_fov = client.post(
        "/api/vision/fovea-preview",
        json={
            "doc_id": doc_id,
            "page_num": 15,
            "bbox": [0.58, 0.78, 0.90, 0.92],
            "focus_share": 0.65,
            "auto_anchors": True,
            "adaptive_aspect": True,
        },
    )
    assert r_fov.status_code == 200
    fov_data = r_fov.json()
    assert fov_data["linear_crop"]["image_data_url"].startswith("data:image/png;base64,")
    assert fov_data["fovea_warp"]["image_data_url"].startswith("data:image/png;base64,")
    assert len(fov_data["fovea_warp"]["warp_meta"]["u_knots"]) >= 3

    # 5. Live SSE streaming run + trace inspection + path traversal guard
    sse_events: list[dict] = []
    with client.stream(
        "POST",
        "/api/run/stream",
        json={
            "doc_id": doc_id,
            "question": "What is the code on page 15?",
            "zoom_mode": "fovea",
            "enable_sboed_gate": True,
            "enable_residual_verification": True,
        },
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                sse_events.append(json.loads(line[6:]))

    event_types = [e["event_type"] for e in sse_events]
    assert "start" in event_types
    assert "stage" in event_types
    assert "complete" in event_types

    done_evt = next(e for e in sse_events if e["event_type"] == "complete")
    live_tid = done_evt["trace_id"]
    assert done_evt["result"]["answer"] == "ZD-9090-WEB"

    # Trace detail & context isolation audit
    r_run = client.get(f"/api/runs/{live_tid}")
    assert r_run.status_code == 200
    run_detail = r_run.json()
    assert run_detail["isolation_audit"]["passed"] is True
    assert run_detail["isolation_audit"]["base64_occurrences"] == 0
    assert run_detail["isolation_audit"]["sub_crop_path_occurrences"] == 0
    assert len(run_detail["grids"]) >= 1
    assert len(run_detail["crops"]) >= 1

    # Path traversal attempt must be rejected with 403
    r_trav = client.get(f"/api/runs/{live_tid}/asset?rel=../../demo_30p.pdf")
    assert r_trav.status_code == 403

    # Invalid upload must be rejected with 400
    r_bad_up = client.post(
        "/api/documents/upload",
        files={"file": ("not_a_pdf.txt", b"hello world", "text/plain")},
    )
    assert r_bad_up.status_code == 400

    # SPA static index & benchmarks endpoint
    r_spa = client.get("/")
    assert r_spa.status_code == 200
    assert "text/html" in r_spa.headers["content-type"]
    assert "FoveaDoc" in r_spa.text

    r_bench = client.get("/api/benchmarks")
    assert r_bench.status_code == 200
    assert "m4_m5" in r_bench.json()


def test_enterprise_cache_thread_safety_and_idempotent_rerun(tmp_path: Path) -> None:
    """企业级并发安全与幂等重放检验：8 线程并发读写同一 PDF 缓存零损坏，相同 trace_id 重跑零残留追加。"""
    pdf_path = tmp_path / "concurrent_25p.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=25,
        gold_page=12,
        gold_code="ZD-5151-ENT",
    )
    cache_dir = tmp_path / "atomic_cache"
    runs_dir = tmp_path / "idemp_runs"
    cfg = Settings(
        vlm_api_key="mock",
        vlm_model="mock-vlm",
        runs_dir=str(runs_dir),
        cache_dir=str(cache_dir),
    )
    cache = ContentAddressedCache(cache_dir=str(cache_dir))

    def _worker(idx: int) -> tuple[int, tuple[int, int]]:
        pages, _ = cache.get_or_render_pages(
            str(pdf_path), [12, 13], str(tmp_path / f"out_{idx}"), cfg
        )
        return len(pages), pages[12].source_size

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_worker, range(8)))

    assert len(results) == 8
    assert all(cnt == 2 and sz[0] > 400 for cnt, sz in results)
    # 临时文件必须 0 残留
    assert list(cache_dir.rglob("*.tmp.*")) == []

    # 确定性 trace_id 连续运行两次，trace.jsonl 行数必须严格幂等一致（无追加膨胀）
    tree = build_document_tree(str(pdf_path), max_leaf_pages=20)
    leaf = next(l for l in get_leaf_nodes(tree) if l.start_page <= 12 <= l.end_page)
    p12_idx = 12 - leaf.start_page
    scripted = [
        json.dumps({"action": "select", "reason": "pick", "node_ids": [leaf.node_id]}),
        json.dumps({"reason": "table", "summary": "p12", "choice": [p12_idx]}),
        json.dumps({"action": "answer", "reason": "draft", "answer": "ZD-5151-ENT", "confidence": 0.91, "reference": [0]}),
        json.dumps({"action": "answer", "reason": "verified", "answer": "ZD-5151-ENT", "confidence": 0.99, "reference": [0, 1]}),
    ]

    run_m5("What is the code?", str(pdf_path), vlm=FakeVLM(scripted), settings=cfg, trace_id="det-001")
    lines_run1 = (runs_dir / "det-001" / "trace.jsonl").read_text(encoding="utf-8").splitlines()

    run_m5("What is the code?", str(pdf_path), vlm=FakeVLM(scripted), settings=cfg, trace_id="det-001")
    lines_run2 = (runs_dir / "det-001" / "trace.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(lines_run1) == len(lines_run2) >= 4
