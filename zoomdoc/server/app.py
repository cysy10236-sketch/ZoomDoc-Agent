from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pymupdf as fitz
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from zoomdoc.cache import get_cached_or_build_tree, get_cached_or_render_pages
from zoomdoc.config import Settings, get_settings
from zoomdoc.errors import ZoomDocError
from zoomdoc.eval.metrics import (
    compute_mcnemar,
    compute_recall_funnel,
    paired_bootstrap_ratio_ci,
    percentile,
    wilson_score_interval,
)
from zoomdoc.graph.supervisor import run_m5
from zoomdoc.llm.vlm_client import VLMClient
from zoomdoc.schemas import NormBBox
from zoomdoc.vision.fovea import (
    compute_adaptive_fovea_size,
    detect_table_anchor_bands,
    fovea_warp_image,
)
from zoomdoc.vision.geometry import norm_to_pixel, pad_and_clamp, upsample_size
from zoomdoc.vision.render import get_pdf_page_count, sha256_file

load_dotenv(override=False)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOADS_DIR = REPO_ROOT / ".cache" / "uploads"


class FoveaPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    page_num: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    pad_ratio: float = Field(0.08, ge=0.0, le=0.4)
    focus_share: float = Field(0.65, ge=0.40, le=0.85)
    header_ratio: float = Field(0.12, ge=0.0, le=0.35)
    stub_ratio: float = Field(0.15, ge=0.0, le=0.40)
    anchor_share: float = Field(0.15, ge=0.0, le=0.30)
    auto_anchors: bool = True
    adaptive_aspect: bool = False


class RunAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    question: str = Field(min_length=2, max_length=2000)
    zoom_mode: Literal["fovea", "crop"] = "fovea"
    enable_sboed_gate: bool = True
    enable_residual_verification: bool = True
    fovea_auto_anchors: bool = True
    fovea_adaptive_aspect: bool = False
    enable_content_cache: bool = True


def _discover_documents(extra_dirs: list[Path] | None = None) -> dict[str, dict[str, Any]]:
    """Scan known benchmark & upload directories and index PDFs by deterministic doc_id."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    scan_roots: list[tuple[str, Path]] = [
        ("M4/M5 Long-Doc Benchmark", REPO_ROOT / "eval" / "m4" / "pdfs"),
        ("M6 Matrix & Scan Benchmark", REPO_ROOT / "eval" / "m6_matrix" / "pdfs"),
        ("Uploaded Documents", UPLOADS_DIR),
    ]
    if extra_dirs:
        for d in extra_dirs:
            scan_roots.append(("Test Documents", d))

    catalog: dict[str, dict[str, Any]] = {}
    for category, folder in scan_roots:
        if not folder.is_dir():
            continue
        for pdf_file in sorted(folder.glob("*.pdf")):
            try:
                doc_hash = sha256_file(str(pdf_file))[:16]
                pages = get_pdf_page_count(str(pdf_file))
            except (OSError, RuntimeError):
                continue
            catalog[doc_hash] = {
                "doc_id": doc_hash,
                "filename": pdf_file.name,
                "path": str(pdf_file.resolve()),
                "total_pages": pages,
                "category": category,
            }
    return catalog


def _encode_pil_png_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _find_trace_dir(trace_id: str, extra_runs_dirs: list[Path] | None = None) -> Path | None:
    if not re.match(r"^[A-Za-z0-9_\-\.]+$", trace_id):
        return None
    roots = [
        REPO_ROOT / "runs",
        REPO_ROOT / "eval" / "m4" / "runs",
        REPO_ROOT / "eval" / "m6_matrix" / "runs",
    ]
    if extra_runs_dirs:
        roots = list(extra_runs_dirs) + roots
    for r in roots:
        cand = r / trace_id
        if cand.is_dir():
            return cand
    return None


def _analyze_eval_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {}
    corr = sum(1 for r in records if r.get("is_correct", False))
    ci = wilson_score_interval(corr, n)
    funnel = compute_recall_funnel(records)
    zoom_cnt = sum(1 for r in records if r.get("is_zoomed") or r.get("zoom_count", 0) > 0)
    ptoks = [
        r["usage"]["prompt_tokens"]
        for r in records
        if r.get("usage") and r["usage"].get("prompt_tokens") is not None
    ]
    ctoks = [
        r["usage"]["completion_tokens"]
        for r in records
        if r.get("usage") and r["usage"].get("completion_tokens") is not None
    ]
    walls = [r.get("total_wall_ms", 0.0) for r in records if r.get("total_wall_ms") is not None]
    cache_hits = sum(1 for r in records if r.get("cache_hit", False))
    return {
        "n": n,
        "correct": corr,
        "acc": round(corr / n, 4),
        "ci": list(ci),
        "nav_recall": funnel["nav_recall"],
        "seeker_recall": funnel["seeker_recall"],
        "zoom_rate": round(zoom_cnt / n, 4),
        "avg_prompt_tokens": round(sum(ptoks) / len(ptoks), 1) if ptoks else 0.0,
        "avg_completion_tokens": round(sum(ctoks) / len(ctoks), 1) if ctoks else 0.0,
        "p50_wall_ms": round(percentile(walls, 50), 1) if walls else 0.0,
        "p95_wall_ms": round(percentile(walls, 95), 1) if walls else 0.0,
        "cache_hit_rate": round(cache_hits / n, 4),
        "residual_corrections": sum(r.get("residual_corrections", 0) for r in records),
        "errors": sum(1 for r in records if r.get("status") == "error"),
    }


def create_app(
    *,
    vlm_override: VLMClient | None = None,
    settings_override: Settings | None = None,
    extra_doc_dirs: list[Path] | None = None,
    extra_runs_dirs: list[Path] | None = None,
    max_concurrent_runs: int = 4,
) -> FastAPI:
    app = FastAPI(
        title="FoveaDoc (ZoomDoc-Agent) Observatory API",
        version="0.2.0",
        description="Streaming multi-agent document reasoning runtime & topological foveation workbench",
    )
    run_semaphore = threading.BoundedSemaphore(max(1, max_concurrent_runs))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _cfg() -> Settings:
        return settings_override or get_settings()

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        s = _cfg()
        has_key = bool(
            s.vlm_api_key
            and not s.vlm_api_key.startswith("mock")
            and not s.vlm_api_key.startswith("sk-your")
        )
        return {
            "status": "ok",
            "version": "0.2.0",
            "vlm_model": s.vlm_model,
            "vlm_base_url": s.vlm_base_url,
            "has_api_key": has_key or (vlm_override is not None),
            "cache_dir": s.cache_dir,
            "benchmarks_available": {
                "m4_m5": (REPO_ROOT / "eval" / "m4" / "results" / "M5").is_dir(),
                "m6_matrix": (REPO_ROOT / "eval" / "m6_matrix" / "summary.json").is_file(),
            },
        }

    @app.get("/api/documents")
    def list_documents() -> dict[str, Any]:
        docs = _discover_documents(extra_doc_dirs)
        items = [
            {
                "doc_id": d["doc_id"],
                "filename": d["filename"],
                "total_pages": d["total_pages"],
                "category": d["category"],
            }
            for d in docs.values()
        ]
        return {"documents": items}

    @app.post("/api/documents/upload")
    async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
        max_bytes = 50 * 1024 * 1024
        chunks: list[bytes] = []
        total_read = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > max_bytes:
                raise HTTPException(status_code=413, detail="PDF exceeds 50 MB upload limit.")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if len(raw) < 8 or not raw.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF header.")

        doc_hash = hashlib.sha256(raw).hexdigest()[:16]
        safe_stem = re.sub(r"[^A-Za-z0-9_\-.]", "_", Path(file.filename or "document.pdf").name)
        if not safe_stem.lower().endswith(".pdf"):
            safe_stem += ".pdf"
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS_DIR / f"{doc_hash}_{safe_stem}"
        if not dest.is_file():
            dest.write_bytes(raw)

        try:
            total_pages = get_pdf_page_count(str(dest))
        except (OSError, RuntimeError) as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Corrupted PDF: {exc}") from exc

        if total_pages < 1:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="PDF has 0 pages.")

        s = _cfg()
        tree, cache_hit = get_cached_or_build_tree(str(dest), s)
        return {
            "doc_id": doc_hash,
            "filename": dest.name,
            "total_pages": total_pages,
            "cache_hit": cache_hit,
            "tree": tree.model_dump(),
        }

    @app.get("/api/documents/{doc_id}/tree")
    def get_document_tree(doc_id: str) -> dict[str, Any]:
        docs = _discover_documents(extra_doc_dirs)
        if doc_id not in docs:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        t0 = time.perf_counter()
        tree, cache_hit = get_cached_or_build_tree(docs[doc_id]["path"], _cfg())
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "doc_id": doc_id,
            "filename": docs[doc_id]["filename"],
            "cache_hit": cache_hit,
            "latency_ms": ms,
            "tree": tree.model_dump(),
        }

    @app.get("/api/documents/{doc_id}/page/{page_num}/image")
    def get_document_page_image(
        doc_id: str,
        page_num: int,
        tier: Literal["view", "thumb", "source"] = Query("view"),
    ) -> FileResponse:
        docs = _discover_documents(extra_doc_dirs)
        if doc_id not in docs:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        doc_meta = docs[doc_id]
        if not (1 <= page_num <= doc_meta["total_pages"]):
            raise HTTPException(status_code=400, detail=f"Page {page_num} out of bounds")

        s = _cfg()
        pages_dict, _ = get_cached_or_render_pages(
            doc_meta["path"], [page_num], str(REPO_ROOT / s.cache_dir), s
        )
        asset = pages_dict[page_num]
        target_path = (
            asset.source_path
            if tier == "source"
            else (asset.thumb_path if tier == "thumb" else asset.view_path)
        )
        return FileResponse(target_path, media_type="image/png")

    @app.post("/api/vision/fovea-preview")
    def post_fovea_preview(req: FoveaPreviewRequest) -> dict[str, Any]:
        docs = _discover_documents(extra_doc_dirs)
        if req.doc_id not in docs:
            raise HTTPException(status_code=404, detail=f"Document {req.doc_id} not found")
        doc_meta = docs[req.doc_id]
        if not (1 <= req.page_num <= doc_meta["total_pages"]):
            raise HTTPException(status_code=400, detail=f"Page {req.page_num} out of bounds")

        try:
            raw_box = NormBBox(
                x1=req.bbox[0], y1=req.bbox[1], x2=req.bbox[2], y2=req.bbox[3]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid bbox: {exc}") from exc

        t0 = time.perf_counter()
        s = _cfg()
        pages_dict, _ = get_cached_or_render_pages(
            doc_meta["path"], [req.page_num], str(REPO_ROOT / s.cache_dir), s
        )
        page_asset = pages_dict[req.page_num]
        padded = pad_and_clamp(raw_box, pad_ratio=req.pad_ratio, min_side=s.zoom_min_norm_side)
        pixel_box = norm_to_pixel(padded, page_asset.source_size)

        with Image.open(page_asset.source_path) as src_img:
            src_rgb = src_img.convert("RGB")
            # 1. Standard Linear Crop
            crop_reg = src_rgb.crop(pixel_box)
            crop_target_sz = upsample_size(
                pixel_box, target_long_side=768, max_upscale=s.zoom_max_upscale
            )
            linear_img = crop_reg.resize(crop_target_sz, Image.Resampling.LANCZOS)

            # 2. Anisotropic Topological Foveation
            if req.adaptive_aspect:
                fovea_out_sz = compute_adaptive_fovea_size(
                    page_asset.source_size,
                    pixel_box,
                    target_long_side=768,
                    header_ratio=req.header_ratio,
                    stub_ratio=req.stub_ratio,
                )
            else:
                fovea_out_sz = (768, 768)

            stub_end_x, sec_band_y = (
                detect_table_anchor_bands(
                    src_rgb,
                    pixel_box=pixel_box,
                    header_ratio=req.header_ratio,
                    stub_ratio=req.stub_ratio,
                )
                if req.auto_anchors
                else (min(pixel_box[0], int(round(req.stub_ratio * src_rgb.width))), None)
            )
            fovea_img, warp_meta = fovea_warp_image(
                src_img=src_rgb,
                pixel_box=pixel_box,
                out_size=fovea_out_sz,
                header_ratio=req.header_ratio,
                stub_ratio=req.stub_ratio,
                focus_share=req.focus_share,
                anchor_share=req.anchor_share,
                draw_seam=True,
                auto_detect_anchors=req.auto_anchors,
            )

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        bw = max(1, pixel_box[2] - pixel_box[0])
        bh = max(1, pixel_box[3] - pixel_box[1])
        u_span = float(
            np.interp(pixel_box[2], warp_meta["x_knots"], warp_meta["u_knots"])
            - np.interp(pixel_box[0], warp_meta["x_knots"], warp_meta["u_knots"])
        )
        v_span = float(
            np.interp(pixel_box[3], warp_meta["y_knots"], warp_meta["v_knots"])
            - np.interp(pixel_box[1], warp_meta["y_knots"], warp_meta["v_knots"])
        )

        return {
            "latency_ms": elapsed_ms,
            "source_size": list(page_asset.source_size),
            "padded_box": [padded.x1, padded.y1, padded.x2, padded.y2],
            "pixel_box": list(pixel_box),
            "detected_stub_x_norm": round(stub_end_x / max(1, page_asset.source_size[0]), 4),
            "detected_table_header_y_norm": (
                [
                    round(sec_band_y[0] / max(1, page_asset.source_size[1]), 4),
                    round(sec_band_y[1] / max(1, page_asset.source_size[1]), 4),
                ]
                if sec_band_y is not None
                else None
            ),
            "linear_crop": {
                "size": list(linear_img.size),
                "pixels": linear_img.size[0] * linear_img.size[1],
                "image_data_url": _encode_pil_png_data_url(linear_img),
            },
            "fovea_warp": {
                "size": list(fovea_img.size),
                "pixels": fovea_img.size[0] * fovea_img.size[1],
                "mag_x": round(u_span / bw, 2),
                "mag_y": round(v_span / bh, 2),
                "warp_meta": warp_meta,
                "image_data_url": _encode_pil_png_data_url(fovea_img),
            },
        }

    @app.get("/api/presets")
    def get_presets() -> dict[str, Any]:
        docs = _discover_documents(extra_doc_dirs)
        by_path = {Path(d["path"]).resolve(): d["doc_id"] for d in docs.values()}
        presets: list[dict[str, Any]] = []

        def _resolve_manifest_pdf(raw_p: str) -> Path:
            cand = Path(raw_p)
            if cand.is_absolute():
                return cand.resolve()
            return (REPO_ROOT / cand).resolve()

        m4_manifest = REPO_ROOT / "eval" / "m4" / "manifest_full16.jsonl"
        if m4_manifest.is_file():
            for ln in m4_manifest.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                item = json.loads(ln)
                p = _resolve_manifest_pdf(item["pdf_path"])
                did = by_path.get(p)
                if not did:
                    continue
                cid = item["case_id"]
                replay_id = f"m4-M5-{cid}"
                has_replay = _find_trace_dir(replay_id, extra_runs_dirs) is not None
                presets.append({
                    "preset_id": f"m5_{cid}",
                    "suite": "M5 Long-Doc Benchmark (50-100p)",
                    "case_id": cid,
                    "doc_id": did,
                    "filename": p.name,
                    "total_pages": item["total_pages"],
                    "query_type": item.get("query_type", "direct_section"),
                    "font_pt": item.get("font_pt", 5.0),
                    "gold_page": item["gold_page"],
                    "gold": item["gold"],
                    "question": item["question"],
                    "replay_trace_id": replay_id if has_replay else None,
                })

        m6_manifest = REPO_ROOT / "eval" / "m6_matrix" / "manifest_m6.jsonl"
        if m6_manifest.is_file():
            for ln in m6_manifest.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                item = json.loads(ln)
                p = _resolve_manifest_pdf(item["pdf_path"])
                did = by_path.get(p)
                if not did:
                    continue
                cid = item["case_id"]
                replay_id = f"m6-FoveaDoc_v2-{cid}"
                has_replay = _find_trace_dir(replay_id, extra_runs_dirs) is not None
                presets.append({
                    "preset_id": cid,
                    "suite": f"M6 Matrix ({item['modality']})",
                    "case_id": cid,
                    "doc_id": did,
                    "filename": p.name,
                    "total_pages": item["total_pages"],
                    "query_type": item["modality"],
                    "font_pt": item["font_pt"],
                    "gold_page": item["gold_page"],
                    "gold": item["gold"],
                    "question": item["question"],
                    "replay_trace_id": replay_id if has_replay else None,
                })

        return {"presets": presets}

    @app.get("/api/runs")
    def list_runs(limit: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
        roots = [
            REPO_ROOT / "runs",
            REPO_ROOT / "eval" / "m6_matrix" / "runs",
            REPO_ROOT / "eval" / "m4" / "runs",
        ]
        if extra_runs_dirs:
            roots = list(extra_runs_dirs) + roots

        all_rdirs: list[Path] = []
        for root in roots:
            if root.is_dir():
                all_rdirs.extend(p for p in root.iterdir() if p.is_dir())
        all_rdirs.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        items: list[dict[str, Any]] = []
        for rdir in all_rdirs:
            res_file = rdir / "result.json"
            if not res_file.is_file():
                continue
            try:
                res_data = json.loads(res_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({
                "trace_id": rdir.name,
                "status": res_data.get("status"),
                "answer": res_data.get("answer"),
                "reference_pages": res_data.get("reference_pages", []),
                "zoom_count": res_data.get("zoom_count", 0),
                "total_wall_ms": res_data.get("total_wall_ms", 0.0),
                "usage": res_data.get("usage"),
                "mtime": rdir.stat().st_mtime,
            })
            if len(items) >= limit:
                break
        return {"runs": items}

    @app.get("/api/runs/{trace_id}")
    def get_run_detail(trace_id: str) -> dict[str, Any]:
        tdir = _find_trace_dir(trace_id, extra_runs_dirs)
        if tdir is None:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

        res_file = tdir / "result.json"
        sup_file = tdir / "supervisor_state.json"
        trace_file = tdir / "trace.jsonl"

        result_obj = json.loads(res_file.read_text(encoding="utf-8")) if res_file.is_file() else None
        sup_raw = sup_file.read_text(encoding="utf-8") if sup_file.is_file() else ""
        sup_obj = json.loads(sup_raw) if sup_raw else None

        isolation_audit = {
            "supervisor_state_bytes": len(sup_raw.encode("utf-8")) if sup_raw else 0,
            "base64_occurrences": sup_raw.lower().count("base64,") + sup_raw.lower().count("data:image"),
            "sub_crop_path_occurrences": sup_raw.replace("\\", "/").count("/sub/"),
            "passed": (
                bool(sup_raw)
                and "base64," not in sup_raw.lower()
                and "data:image" not in sup_raw.lower()
                and "/sub/" not in sup_raw.replace("\\", "/")
            ),
        }

        trace_events: list[dict[str, Any]] = []
        if trace_file.is_file():
            for ln in trace_file.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    try:
                        ev_obj = json.loads(ln)
                        if isinstance(ev_obj, dict):
                            ev_obj.setdefault("namespace", "main")
                            trace_events.append(ev_obj)
                    except ValueError:
                        continue

        grids = [
            {
                "name": g.name,
                "url": f"/api/runs/{trace_id}/asset?rel={g.relative_to(tdir).as_posix()}",
            }
            for g in sorted((tdir / "grids").glob("*.png"))
        ] if (tdir / "grids").is_dir() else []

        crops: list[dict[str, Any]] = []
        sub_root = tdir / "sub"
        if sub_root.is_dir():
            for c_png in sorted(sub_root.glob("r*/crops/*.png")):
                rel = c_png.relative_to(tdir).as_posix()
                meta_file = c_png.with_suffix(".meta.json")
                wmeta = None
                if meta_file.is_file():
                    try:
                        wmeta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        wmeta = None
                crops.append({
                    "name": c_png.name,
                    "is_fovea": "_fovea_" in c_png.name,
                    "url": f"/api/runs/{trace_id}/asset?rel={rel}",
                    "warp_meta": wmeta,
                })

        # Match doc_id if pdf_path exists in catalog
        doc_id_match: str | None = None
        if sup_obj and sup_obj.get("pdf_path"):
            docs = _discover_documents(extra_doc_dirs)
            for did, dmeta in docs.items():
                if Path(dmeta["path"]).resolve() == Path(sup_obj["pdf_path"]).resolve():
                    doc_id_match = did
                    break

        return {
            "trace_id": trace_id,
            "doc_id": doc_id_match,
            "result": result_obj,
            "supervisor_state": sup_obj,
            "isolation_audit": isolation_audit,
            "trace_events": trace_events,
            "grids": grids,
            "crops": crops,
        }

    @app.get("/api/runs/{trace_id}/asset")
    def get_run_asset(trace_id: str, rel: str = Query(...)) -> FileResponse:
        tdir = _find_trace_dir(trace_id, extra_runs_dirs)
        if tdir is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        target = (tdir / rel).resolve()
        if not target.is_relative_to(tdir.resolve()) or not target.is_file():
            raise HTTPException(status_code=403, detail="Forbidden or missing asset path")
        if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise HTTPException(status_code=400, detail="Unsupported asset type")
        return FileResponse(str(target), media_type="image/png")

    @app.post("/api/run/stream")
    async def stream_agent_run(req: RunAgentRequest) -> StreamingResponse:
        docs = _discover_documents(extra_doc_dirs)
        if req.doc_id not in docs:
            raise HTTPException(status_code=404, detail=f"Document {req.doc_id} not found")
        if not run_semaphore.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail="Maximum concurrent live reasoning runs reached; retry after active runs finish.",
            )
        pdf_path = docs[req.doc_id]["path"]

        base_s = _cfg()
        run_trace_id = f"live-{uuid.uuid4().hex[:10]}"
        runs_root = extra_runs_dirs[0] if extra_runs_dirs else (REPO_ROOT / "runs")
        run_cfg = base_s.model_copy(
            update={
                "runs_dir": str(runs_root.resolve()),
                "use_tree_index": True,
                "zoom_mode": req.zoom_mode,
                "fovea_auto_anchors": req.fovea_auto_anchors,
                "fovea_adaptive_aspect": req.fovea_adaptive_aspect,
                "enable_logprobs_entropy": req.enable_sboed_gate,
                "enable_sboed_gate": req.enable_sboed_gate,
                "force_zoom_on_alnum_code": req.enable_sboed_gate,
                "enable_residual_verification": req.enable_residual_verification,
                "enable_tree_flattening": True,
                "annotate_seeker_grid": True,
                "enable_content_cache": req.enable_content_cache,
            }
        )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        disconnected = threading.Event()

        def _push(evt: dict[str, Any]) -> None:
            if disconnected.is_set():
                return
            loop.call_soon_threadsafe(queue.put_nowait, evt)

        def _worker() -> None:
            try:
                _push({
                    "event_type": "start",
                    "trace_id": run_trace_id,
                    "doc_id": req.doc_id,
                    "filename": docs[req.doc_id]["filename"],
                    "question": req.question,
                })
                res, _ = run_m5(
                    question=req.question,
                    pdf_path=pdf_path,
                    vlm=vlm_override,
                    settings=run_cfg,
                    trace_id=run_trace_id,
                    on_event=_push,
                )
                _push({
                    "event_type": "complete",
                    "trace_id": run_trace_id,
                    "result": res.model_dump(),
                })
            except (ZoomDocError, APIError, OSError, ValueError) as exc:
                _push({
                    "event_type": "error",
                    "trace_id": run_trace_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            finally:
                run_semaphore.release()
                if not disconnected.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_worker, daemon=True).start()

        async def _event_gen():
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if item is None:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            finally:
                disconnected.set()

        return StreamingResponse(
            _event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/benchmarks")
    def get_benchmarks() -> dict[str, Any]:
        m4_res_dir = REPO_ROOT / "eval" / "m4" / "results"
        m4_groups = ["B0", "B1", "B2", "M3", "M5_no_sboed", "M5_crop", "M5"]
        m4_stats: dict[str, Any] = {}
        recs_map: dict[str, list[dict[str, Any]]] = {}

        for grp in m4_groups:
            gdir = m4_res_dir / grp
            if not gdir.is_dir():
                continue
            recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(gdir.glob("case_*.json"))]
            if recs:
                recs_map[grp] = recs
                m4_stats[grp] = _analyze_eval_records(recs)

        m4_pairwise: dict[str, Any] = {}
        if "M5" in recs_map and "B0" in recs_map:
            b, c, pv = compute_mcnemar(recs_map["M5"], recs_map["B0"])
            d_m5 = {r["case_id"]: r for r in recs_map["M5"]}
            d_b0 = {r["case_id"]: r for r in recs_map["B0"]}
            cids = [k for k in d_m5 if k in d_b0]
            boot = paired_bootstrap_ratio_ci(
                [float((d_m5[k].get("usage") or {}).get("prompt_tokens") or 0.0) for k in cids],
                [float((d_b0[k].get("usage") or {}).get("prompt_tokens") or 0.0) for k in cids],
                n_boot=2000,
                seed=42,
            )
            m4_pairwise["M5_vs_B0"] = {"wins": b, "losses": c, "mcnemar_p": pv, "bootstrap": boot}
        if "M5" in recs_map and "M3" in recs_map:
            b, c, pv = compute_mcnemar(recs_map["M5"], recs_map["M3"])
            m4_pairwise["M5_vs_M3"] = {"wins": b, "losses": c, "mcnemar_p": pv}

        m6_summary_file = REPO_ROOT / "eval" / "m6_matrix" / "summary.json"
        m6_data = None
        if m6_summary_file.is_file():
            try:
                m6_data = json.loads(m6_summary_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                m6_data = None

        return {
            "m4_m5": {
                "groups": m4_stats,
                "pairwise": m4_pairwise,
                "figures": {
                    "fovea_vs_crop": "/api/benchmarks/figure/fovea_vs_crop_comparison.png",
                    "pareto_frontier": "/api/benchmarks/figure/pareto_accuracy_tokens.png",
                },
            },
            "m6_matrix": m6_data,
        }

    @app.get("/api/benchmarks/figure/{fig_name}")
    def get_benchmark_figure(fig_name: str) -> FileResponse:
        if fig_name not in ("fovea_vs_crop_comparison.png", "pareto_accuracy_tokens.png"):
            raise HTTPException(status_code=404, detail="Unknown figure")
        fpath = REPO_ROOT / "eval" / "m4" / "figures" / fig_name
        if not fpath.is_file():
            raise HTTPException(status_code=404, detail="Figure file missing")
        return FileResponse(str(fpath), media_type="image/png")

    assets_dir = STATIC_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def serve_spa_index() -> FileResponse:
        idx = STATIC_DIR / "index.html"
        if not idx.is_file():
            raise HTTPException(status_code=404, detail="Frontend bundle not built yet.")
        return FileResponse(str(idx), media_type="text/html")

    return app


app = create_app()
