from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Literal

from zoomdoc.agents.seeker import Seeker
from zoomdoc.config import Settings, get_settings
from zoomdoc.graph.inspector_subgraph import run_inspector_round
from zoomdoc.graph.states import merge_usage, usage_from_sink
from zoomdoc.llm.vlm_client import OpenAICompatVLM, VLMClient
from zoomdoc.observability import TraceLogger
from zoomdoc.schemas import FinalResultM3, PageAsset, UsageTotals, VLMUsage
from zoomdoc.vision.render import get_pdf_page_count, render_pdf_pages


def run_sliding_grid_baseline(
    question: str,
    pdf_path: str,
    *,
    mode: Literal["B0_fullres_nozoom", "B1_thumb_zoom"],
    vlm: VLMClient | None = None,
    settings: Settings | None = None,
    trace_id: str | None = None,
    window_size: int = 20,
) -> tuple[FinalResultM3, list[int]]:
    """ViDoRAG-equivalent sliding-window thumbnail baseline for long documents.

    Evaluation modes:
    - B0_fullres_nozoom: High-res thumbnails (view_long_side=1024), max_zooms=0, no tree index.
    - B1_thumb_zoom: Low-res thumbnails (thumb_long_side=448), max_zooms=3, no tree index.

    Steps:
    1. Determine total pages.
    2. Slice document into chunks of size window_size (default 20).
    3. Run Seeker per window, aggregating selected pages.
    4. Truncate aggregated pages to max_inspector_images (default 6).
    5. Run Inspector round on aggregated pages.
    6. Return (FinalResultM3, all_selected_pages).
    """
    t_start = time.perf_counter()
    base_cfg = settings or get_settings()

    if mode == "B0_fullres_nozoom":
        cfg = base_cfg.model_copy(
            update={
                "thumb_long_side": base_cfg.view_long_side,
                "max_zooms": 0,
                "max_inspect_rounds": 1,
                "use_tree_index": False,
            }
        )
    elif mode == "B1_thumb_zoom":
        cfg = base_cfg.model_copy(
            update={
                "thumb_long_side": 448,
                "max_zooms": 3,
                "max_inspect_rounds": 1,
                "use_tree_index": False,
            }
        )
    else:
        raise ValueError(f"Unknown baseline mode: {mode}")

    t_id = trace_id or uuid.uuid4().hex[:12]
    run_dir = Path(cfg.runs_dir) / t_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = TraceLogger(cfg.runs_dir, t_id)
    client = vlm or OpenAICompatVLM(cfg)

    resolved_pdf = str(Path(pdf_path).resolve())
    total_pages = get_pdf_page_count(resolved_pdf)

    if total_pages == 0:
        wall_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        res = FinalResultM3(
            status="no_answer",
            answer=None,
            reason="PDF has 0 pages.",
            reference_pages=[],
            cited_regions=[],
            inspect_rounds=0,
            seek_rounds=0,
            nav_rounds=0,
            selected_node_ids=[],
            total_pages=0,
            render_latency_ms=0.0,
            tree_latency_ms=0.0,
            total_wall_ms=wall_ms,
            usage=UsageTotals(),
            trace_id=t_id,
        )
        return (res, [])

    # Partition 1..total_pages into windows of size window_size
    windows: list[list[int]] = []
    for start in range(1, total_pages + 1, window_size):
        end = min(start + window_size, total_pages + 1)
        windows.append(list(range(start, end)))

    render_latency_ms = 0.0
    total_usage = UsageTotals()
    all_rendered_pages: dict[int, PageAsset] = {}
    all_selected: list[int] = []

    seeker = Seeker(client, cfg, logger)

    for win_idx, win_pages in enumerate(windows):
        t0 = time.perf_counter()
        rendered = render_pdf_pages(resolved_pdf, win_pages, str(run_dir), cfg)
        render_latency_ms += (time.perf_counter() - t0) * 1000.0
        all_rendered_pages.update(rendered)

        candidate_assets = [all_rendered_pages[p] for p in win_pages]
        sink: list[VLMUsage] = []

        output, selected_pages, resp, attempts = seeker.run(
            question=question,
            candidate=candidate_assets,
            feedback=None,
            out_dir=str(run_dir),
            step=win_idx + 1,
            usage_sink=sink,
            namespace=f"seeker_win{win_idx + 1}",
        )
        total_usage = merge_usage(total_usage, usage_from_sink(sink))

        for p in selected_pages:
            if p not in all_selected:
                all_selected.append(p)

    if not all_selected:
        wall_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        res = FinalResultM3(
            status="no_answer",
            answer=None,
            reason="No pages selected across sliding grid windows",
            reference_pages=[],
            cited_regions=[],
            inspect_rounds=0,
            seek_rounds=len(windows),
            nav_rounds=0,
            selected_node_ids=[],
            total_pages=total_pages,
            render_latency_ms=round(render_latency_ms, 2),
            tree_latency_ms=0.0,
            total_wall_ms=wall_ms,
            usage=total_usage,
            trace_id=t_id,
        )
        res_path = run_dir / "result.json"
        with open(res_path, "w", encoding="utf-8") as f:
            f.write(res.model_dump_json(indent=2))
        return (res, [])

    final_selected_pages = all_selected[: cfg.max_inspector_images]
    sub_pages = {p: all_rendered_pages[p] for p in final_selected_pages}

    report = run_inspector_round(
        question=question,
        round_=1,
        pages=sub_pages,
        feedback=None,
        run_dir=str(run_dir),
        trace_id=t_id,
        vlm=client,
        settings=cfg,
        logger=logger,
    )
    total_usage = merge_usage(total_usage, report.usage)
    wall_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

    if report.status == "answered":
        final_status = "answered"
    elif report.status == "budget_exhausted":
        final_status = "budget_exhausted"
    else:
        final_status = "no_answer"

    res = FinalResultM3(
        status=final_status,
        answer=report.answer,
        reason=report.reason,
        reference_pages=report.reference_pages,
        cited_regions=report.cited_regions,
        inspect_rounds=1,
        seek_rounds=len(windows),
        nav_rounds=0,
        selected_node_ids=[],
        total_pages=total_pages,
        render_latency_ms=round(render_latency_ms, 2),
        tree_latency_ms=0.0,
        total_wall_ms=wall_ms,
        usage=total_usage,
        trace_id=t_id,
    )

    res_path = run_dir / "result.json"
    with open(res_path, "w", encoding="utf-8") as f:
        f.write(res.model_dump_json(indent=2))

    return (res, all_selected)
