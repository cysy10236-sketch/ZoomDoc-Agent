from __future__ import annotations

import functools
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from zoomdoc.agents.inspector import Inspector
from zoomdoc.errors import ZoomDocError
from zoomdoc.graph.states import (
    EvidenceReport,
    InspectorSubState,
    merge_usage,
    usage_from_sink,
)
from zoomdoc.prompts import INSPECTOR_PROMPT_M2
from zoomdoc.schemas import (
    CitedRegion,
    InspectorAnswerM2,
    InspectorOutputM2,
    InspectorSeek,
    InspectorZoom,
    VLMUsage,
)
from zoomdoc.vision.geometry import crop_box_to_page_box, vlm_bbox_to_norm
from zoomdoc.vision.zoom import zoom

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from zoomdoc.config import Settings
    from zoomdoc.llm.vlm_client import VLMClient
    from zoomdoc.observability import TraceLogger


def prepare_node(state: InspectorSubState, settings: Settings) -> dict[str, Any]:
    """Construct multi-image view for Inspector from pages and current crops."""
    view = Inspector.build_view_items(
        list(state.pages.values()),
        list(state.crops.values()),
        settings.max_inspector_images,
    )
    return {"view": view}


def inspect_node(
    state: InspectorSubState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Invoke Inspector VLM with M2 prompt and schema, aggregating usage."""
    if state.view is None:
        raise ZoomDocError("inspect_node called without view prepared")

    sink: list[VLMUsage] = []
    inspector = Inspector(vlm, settings, logger)
    zoom_left = max(0, state.max_zooms - state.zoom_count)

    output, resp, attempts = inspector.run(
        question=state.question,
        view=state.view,
        pages=state.pages,
        crops=state.crops,
        zoom_left=zoom_left,
        usage_sink=sink,
        prompt_template=INSPECTOR_PROMPT_M2,
        adapter=TypeAdapter(InspectorOutputM2),
        namespace=f"inspector_sub/r{state.round}",
        feedback=state.feedback,
    )

    new_usage = merge_usage(state.usage, usage_from_sink(sink))
    ans_entropy = (
        getattr(resp.usage, "max_token_entropy", None) if resp and resp.usage else None
    )

    # In-subgraph S-BOED gate
    if (
        isinstance(output, InspectorAnswerM2)
        and state.zoom_count == 0
        and state.zoom_count < state.max_zooms
        and (state.sub_step + 1) < state.max_sub_steps
        and getattr(settings, "enable_sboed_gate", False)
    ):
        high_ent = ans_entropy is not None and ans_entropy > settings.entropy_zoom_threshold
        has_code = (
            getattr(settings, "force_zoom_on_alnum_code", False)
            and bool(re.search(r"[A-Za-z0-9]{2,}-[A-Za-z0-9\-]+|\d+\.\d+", output.answer))
        )
        if high_ent or has_code:
            ref_idx = output.reference[0] if output.reference else 0
            if not (state.view and 0 <= ref_idx < len(state.view.items)):
                ref_idx = 0
            target_page_num = next(iter(state.pages.keys()), 1)
            if state.view and 0 <= ref_idx < len(state.view.items):
                it = state.view.items[ref_idx]
                if it[0] == "page":
                    target_page_num = it[1]
                elif it[0] == "crop" and it[1] in state.crops:
                    target_page_num = state.crops[it[1]].page
                    for idx_cand, item_cand in enumerate(state.view.items):
                        if item_cand == ("page", target_page_num):
                            ref_idx = idx_cand
                            break

            from zoomdoc.vision.residual import locate_target_zoom_box

            norm_box = locate_target_zoom_box(
                pdf_path=state.pdf_path,
                page_num=target_page_num,
                question=state.question,
                draft_answer=output.answer,
            )
            if settings.bbox_format == "yxyx_1000":
                vlm_bbox = (
                    round(norm_box.y1 * 1000.0, 1),
                    round(norm_box.x1 * 1000.0, 1),
                    round(norm_box.y2 * 1000.0, 1),
                    round(norm_box.x2 * 1000.0, 1),
                )
            elif settings.bbox_format == "xyxy_abs" and target_page_num in state.pages:
                w_v, h_v = state.pages[target_page_num].view_size
                vlm_bbox = (
                    round(norm_box.x1 * w_v, 1),
                    round(norm_box.y1 * h_v, 1),
                    round(norm_box.x2 * w_v, 1),
                    round(norm_box.y2 * h_v, 1),
                )
            else:
                vlm_bbox = (
                    round(norm_box.x1 * 1000.0, 1),
                    round(norm_box.y1 * 1000.0, 1),
                    round(norm_box.x2 * 1000.0, 1),
                    round(norm_box.y2 * 1000.0, 1),
                )
            synth_zoom = InspectorZoom(
                action="zoom",
                reason=(
                    f"S-BOED gate forced foveation (entropy={ans_entropy}, code_guard={has_code})"
                ),
                image=ref_idx,
                bbox_2d=vlm_bbox,
                target="exact character-by-character verification of the target value matching all question conditions",
            )
            return {
                "last_output": synth_zoom,
                "pending_zoom": synth_zoom,
                "sub_step": state.sub_step + 1,
                "usage": new_usage,
                "latest_answer_entropy": ans_entropy,
            }

    return {
        "last_output": output,
        "pending_zoom": output if isinstance(output, InspectorZoom) else None,
        "sub_step": state.sub_step + 1,
        "usage": new_usage,
        "latest_answer_entropy": ans_entropy,
    }


def route_inspect(state: InspectorSubState) -> Literal["zoom", "pack_report"]:
    """Determine next action in subgraph: zoom or pack report."""
    if state.sub_step >= state.max_sub_steps:
        return "pack_report"

    if isinstance(state.last_output, InspectorZoom) and state.zoom_count < state.max_zooms:
        return "zoom"

    return "pack_report"


def zoom_node(
    state: InspectorSubState,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Execute high-resolution crop on source image and record timing in trace."""
    pending = state.pending_zoom
    if pending is None:
        raise ZoomDocError("zoom_node called without pending_zoom")
    if state.view is None:
        raise ZoomDocError("zoom_node called without view in state")

    target_item = state.view.items[pending.image]
    if target_item[0] == "page":
        target_page = state.pages[target_item[1]]
        page_box = vlm_bbox_to_norm(pending.bbox_2d, settings.bbox_format, target_page.view_size)
    else:
        parent_crop = state.crops[target_item[1]]
        target_page = state.pages[parent_crop.page]
        local_box = vlm_bbox_to_norm(pending.bbox_2d, settings.bbox_format, parent_crop.size)
        if getattr(settings, "zoom_mode", "crop") == "fovea" and parent_crop.warp_meta is not None:
            from zoomdoc.vision.fovea import fovea_box_to_page_box

            page_box = fovea_box_to_page_box(
                local_box=local_box,
                warp_meta=parent_crop.warp_meta,
                src_size=target_page.source_size,
                out_size=parent_crop.size,
            )
        else:
            page_box = crop_box_to_page_box(local_box, parent_crop.page_box)

    crop_id = f"r{state.round}c{state.zoom_count}"
    with logger.span("zoom") as span:
        crop_asset = zoom(
            page=target_page,
            page_box_requested=page_box,
            settings=settings,
            out_dir=state.sub_dir,
            crop_id=crop_id,
            target=pending.target,
        )
        logger.event(
            span_id=span.span_id,
            node="zoom",
            status="ok",
            latency_ms=span.elapsed_ms(),
            prompt_tokens=None,
            completion_tokens=None,
            attempt=1,
            payload={
                "crop_id": crop_id,
                "page": target_page.page,
                "pixel_box": crop_asset.pixel_box,
                "target": pending.target,
                "reason": pending.reason,
            },
            namespace=f"inspector_sub/r{state.round}",
        )

    new_crops = dict(state.crops)
    new_crops[crop_id] = crop_asset

    return {
        "crops": new_crops,
        "zoom_count": state.zoom_count + 1,
        "sub_step": state.sub_step + 1,
        "pending_zoom": None,
    }


def pack_report_node(
    state: InspectorSubState, settings: Settings | None = None
) -> dict[str, Any]:
    """Format final EvidenceReport from subgraph execution, discarding internal images."""
    last = state.last_output
    ref_pages: list[int] = []
    cited_regions: list[CitedRegion] = []

    if state.sub_step >= state.max_sub_steps and not isinstance(last, InspectorAnswerM2):
        report = EvidenceReport(
            round=state.round,
            status="budget_exhausted",
            answer=None,
            confidence=None,
            reason=f"Sub-step budget exhausted ({state.sub_step} >= {state.max_sub_steps})",
            seek_hint=None,
            reference_pages=[],
            cited_regions=[],
            zoom_count=state.zoom_count,
            sub_steps=state.sub_step,
            usage=state.usage,
        )
        return {"report": report}

    if isinstance(last, InspectorAnswerM2):
        if state.view is not None:
            for r in last.reference:
                if 0 <= r < len(state.view.items):
                    it = state.view.items[r]
                    if it[0] == "page":
                        p_num = it[1]
                        ref_pages.append(p_num)
                        cited_regions.append(CitedRegion(page=p_num, page_box=None, target=None))
                    elif it[0] == "crop":
                        c_asset = state.crops[it[1]]
                        ref_pages.append(c_asset.page)
                        cited_regions.append(
                            CitedRegion(
                                page=c_asset.page,
                                page_box=c_asset.page_box,
                                target=c_asset.target,
                            )
                        )

            # Ensure that if any high-res crop exists for a referenced page, its page_box is included in cited_regions
            for p_num in list(ref_pages):
                for c_asset in state.crops.values():
                    if c_asset.page == p_num:
                        if not any(cr.page == p_num and cr.page_box == c_asset.page_box for cr in cited_regions):
                            cited_regions.append(
                                CitedRegion(
                                    page=c_asset.page,
                                    page_box=c_asset.page_box,
                                    target=c_asset.target,
                                )
                            )

        final_ans = last.answer
        is_corr = False
        if (
            settings is not None
            and getattr(settings, "enable_residual_verification", False)
            and state.pdf_path
        ):
            from zoomdoc.vision.residual import verify_and_correct_answer

            final_ans, is_corr = verify_and_correct_answer(
                answer=last.answer,
                pdf_path=state.pdf_path,
                reference_pages=sorted(list(set(ref_pages))),
                cited_regions=cited_regions,
                max_optical_distance=getattr(settings, "residual_max_optical_distance", 1.5),
            )

        report = EvidenceReport(
            round=state.round,
            status="answered",
            answer=final_ans,
            confidence=last.confidence,
            reason=last.reason,
            seek_hint=None,
            reference_pages=sorted(list(set(ref_pages))),
            cited_regions=cited_regions,
            zoom_count=state.zoom_count,
            sub_steps=state.sub_step,
            usage=state.usage,
            answer_entropy=state.latest_answer_entropy,
            residual_corrected=is_corr,
        )
    elif isinstance(last, InspectorSeek):
        if state.view is not None:
            for ch in last.choice:
                if 0 <= ch < len(state.view.items):
                    it = state.view.items[ch]
                    if it[0] == "page":
                        ref_pages.append(it[1])
                    elif it[0] == "crop":
                        ref_pages.append(state.crops[it[1]].page)

        report = EvidenceReport(
            round=state.round,
            status="need_more_pages",
            answer=None,
            confidence=None,
            reason=last.reason,
            seek_hint=last.information,
            reference_pages=sorted(list(set(ref_pages))),
            cited_regions=[],
            zoom_count=state.zoom_count,
            sub_steps=state.sub_step,
            usage=state.usage,
        )
    else:
        report = EvidenceReport(
            round=state.round,
            status="budget_exhausted",
            answer=None,
            confidence=None,
            reason=f"Sub-step budget exhausted ({state.sub_step} >= {state.max_sub_steps})",
            seek_hint=None,
            reference_pages=[],
            cited_regions=[],
            zoom_count=state.zoom_count,
            sub_steps=state.sub_step,
            usage=state.usage,
        )

    return {"report": report}


def build_inspector_subgraph(
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> CompiledStateGraph:
    """Build and compile the Inspector Sub-Graph state machine."""
    g = StateGraph(InspectorSubState)

    g.add_node("prepare", functools.partial(prepare_node, settings=settings))
    g.add_node("inspect", functools.partial(inspect_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("zoom", functools.partial(zoom_node, settings=settings, logger=logger))
    g.add_node("pack_report", functools.partial(pack_report_node, settings=settings))

    g.add_edge(START, "prepare")
    g.add_edge("prepare", "inspect")
    g.add_conditional_edges(
        "inspect",
        route_inspect,
        {
            "zoom": "zoom",
            "pack_report": "pack_report",
        },
    )
    g.add_edge("zoom", "prepare")
    g.add_edge("pack_report", END)

    return g.compile()


def run_inspector_round(
    *,
    question: str,
    round_: int,
    pages: dict[int, Any],
    feedback: str | None,
    run_dir: str,
    trace_id: str,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
    pdf_path: str = "",
) -> EvidenceReport:
    """Execute a single round of visual inspection in an isolated sub-state scope.

    All high-resolution crops and intermediate VLM views remain strictly inside
    the sub-state scope and are discarded upon return. Only the pure-text/coordinate
    EvidenceReport is returned to the parent graph.
    """
    sub_dir = Path(run_dir) / "sub" / f"r{round_}"
    sub_dir.mkdir(parents=True, exist_ok=True)

    subgraph = build_inspector_subgraph(vlm, settings, logger)
    sub_state = InspectorSubState(
        trace_id=trace_id,
        question=question,
        round=round_,
        sub_dir=str(sub_dir.resolve()),
        pages=pages,
        pdf_path=pdf_path,
        feedback=feedback,
        max_zooms=settings.max_zooms,
        max_sub_steps=settings.max_sub_steps,
    )

    recursion_limit = 2 * settings.max_sub_steps + 6
    output = subgraph.invoke(sub_state, config={"recursion_limit": recursion_limit})

    report = output.get("report") if isinstance(output, dict) else getattr(output, "report", None)
    if report is None:
        raise ZoomDocError(f"Inspector subgraph in round {round_} finished without producing EvidenceReport")

    if not settings.keep_sub_artifacts:
        if sub_dir.exists():
            shutil.rmtree(sub_dir, ignore_errors=True)

    return report
