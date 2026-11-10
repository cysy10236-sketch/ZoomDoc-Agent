from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zoomdoc.agents.inspector import Inspector, InspectorView
from zoomdoc.agents.seeker import Seeker
from zoomdoc.agents.synthesizer import Synthesizer
from zoomdoc.graph.state import AgentState
from zoomdoc.schemas import (
    FinalResult,
    InspectorAnswer,
    InspectorSeek,
    InspectorZoom,
    StepRecord,
)
from zoomdoc.vision.geometry import crop_box_to_page_box, vlm_bbox_to_norm
from zoomdoc.vision.render import render_pdf
from zoomdoc.vision.zoom import zoom

if TYPE_CHECKING:
    from zoomdoc.config import Settings
    from zoomdoc.llm.vlm_client import VLMClient
    from zoomdoc.observability import TraceLogger


def ingest_node(state: AgentState, settings: Settings) -> dict[str, Any]:
    pages = render_pdf(state.pdf_path, state.run_dir, settings)
    pages_dict = {p.page: p for p in pages}
    return {
        "pages": pages_dict,
        "candidate_pages": [p.page for p in pages],
    }


def route_after_ingest(state: AgentState) -> str:
    if not state.candidate_pages:
        return "finalize"
    return "seeker"


def seeker_node(
    state: AgentState, vlm: VLMClient, settings: Settings, logger: TraceLogger
) -> dict[str, Any]:
    candidate_assets = [state.pages[p] for p in state.candidate_pages]
    seeker = Seeker(vlm, settings, logger)
    output, selected_pages, resp, attempts = seeker.run(
        question=state.question,
        candidate=candidate_assets,
        feedback=state.feedback,
        out_dir=state.run_dir,
        step=state.step + 1,
    )

    # 选中页从候选池删除 (ViDoRAG vidorag_agents.py:54)
    new_candidates = [p for p in state.candidate_pages if p not in selected_pages]
    # 追加到 inspector_pages 并去重保留原序 (ViDoRAG vidorag_agents.py:84)
    new_inspector = list(state.inspector_pages)
    for p in selected_pages:
        if p not in new_inspector:
            new_inspector.append(p)

    step_rec = StepRecord(
        step=state.step + 1,
        node="seeker",
        input_images=[p.thumb_path for p in candidate_assets],
        output=output.model_dump(),
        usage=resp.usage,
        attempts=attempts,
    )

    prompt_toks = state.prompt_tokens + (resp.usage.prompt_tokens or 0)
    compl_toks = state.completion_tokens + (resp.usage.completion_tokens or 0)

    return {
        "candidate_pages": new_candidates,
        "inspector_pages": new_inspector,
        "feedback": None,
        "step": state.step + 1,
        "history": state.history + [step_rec],
        "prompt_tokens": prompt_toks,
        "completion_tokens": compl_toks,
    }


def route_after_seeker(state: AgentState) -> str:
    # 检查本轮 Seeker 是否选中了页面
    last_step = state.history[-1] if state.history else None
    selected = last_step.output.get("choice", []) if last_step else []

    if len(selected) == 0:
        if len(state.inspector_pages) == 0:
            return "finalize"  # 没有任何相关候选页
        else:
            return "synthesizer"  # 候选池无新发现，但既往有累积页，直接兜底合成
    return "inspector"


def inspector_node(
    state: AgentState, vlm: VLMClient, settings: Settings, logger: TraceLogger
) -> dict[str, Any]:
    inspector = Inspector(vlm, settings, logger)
    pages_for_view = [state.pages[p] for p in state.inspector_pages]
    crops_for_view = list(state.crops.values())
    view = inspector.build_view(pages_for_view, crops_for_view)

    zoom_left = max(0, settings.max_zooms - state.zoom_count)
    output, resp, attempts = inspector.run(
        question=state.question,
        view=view,
        pages=state.pages,
        crops=state.crops,
        zoom_left=zoom_left,
    )

    input_paths: list[str] = []
    for item in view.items:
        if item[0] == "page":
            input_paths.append(state.pages[item[1]].view_path)
        else:
            input_paths.append(state.crops[item[1]].path)

    step_rec = StepRecord(
        step=state.step + 1,
        node="inspector",
        input_images=input_paths,
        output=output.model_dump(),
        usage=resp.usage,
        attempts=attempts,
    )

    prompt_toks = state.prompt_tokens + (resp.usage.prompt_tokens or 0)
    compl_toks = state.completion_tokens + (resp.usage.completion_tokens or 0)

    updates: dict[str, Any] = {
        "last_view": view,
        "step": state.step + 1,
        "history": state.history + [step_rec],
        "prompt_tokens": prompt_toks,
        "completion_tokens": compl_toks,
    }

    if isinstance(output, InspectorAnswer):
        updates["draft_answer"] = output.answer
        if len(output.reference) == len(view.items):
            # 全量引用匹配：直接进入 finalize
            ref_pages: list[int] = []
            for r in output.reference:
                it = view.items[r]
                ref_pages.append(it[1] if it[0] == "page" else state.crops[it[1]].page)

            result = FinalResult(
                status="answered",
                answer=output.answer,
                reason=output.reason,
                reference_pages=sorted(list(set(ref_pages))),
                crops=list(state.crops.values()),
                steps=state.step + 1,
                total_prompt_tokens=prompt_toks,
                total_completion_tokens=compl_toks,
                trace_id=state.trace_id,
            )
            updates["result"] = result
            updates["route"] = "finalize"
        else:
            # 局部引用匹配：由 synthesizer 提炼
            synth_items = [view.items[r] for r in output.reference]
            updates["synth_view"] = InspectorView(items=synth_items)
            updates["route"] = "synthesizer"

    elif isinstance(output, InspectorZoom):
        updates["pending_zoom"] = output
        updates["route"] = "zoom"

    elif isinstance(output, InspectorSeek):
        # 提取选择保留的页与 crop
        chosen_pages: set[int] = set()
        for ch in output.choice:
            it = view.items[ch]
            if it[0] == "page":
                chosen_pages.add(it[1])
            elif it[0] == "crop":
                chosen_pages.add(state.crops[it[1]].page)

        new_insp = [p for p in state.inspector_pages if p in chosen_pages]
        new_crops = {cid: c for cid, c in state.crops.items() if c.page in chosen_pages}

        updates["inspector_pages"] = new_insp
        updates["crops"] = new_crops
        updates["feedback"] = output.information

        if state.candidate_pages:
            updates["route"] = "seeker"
        elif new_insp:
            updates["route"] = "synthesizer"
        else:
            updates["route"] = "finalize"

    return updates


def route_after_inspector(state: AgentState, settings: Settings) -> str:
    # 1. 步数预算判断
    if state.step >= settings.max_steps:
        if state.inspector_pages or state.crops:
            return "synthesizer"
        return "finalize"

    # 2. 依据节点内计算的 route
    if state.route:
        return state.route

    return "finalize"


def zoom_node(state: AgentState, settings: Settings, logger: TraceLogger) -> dict[str, Any]:
    pending = state.pending_zoom
    assert pending is not None, "zoom_node called without pending_zoom"
    assert state.last_view is not None, "zoom_node called without last_view"

    target_item = state.last_view.items[pending.image]
    if target_item[0] == "page":
        target_page = state.pages[target_item[1]]
        page_box = vlm_bbox_to_norm(pending.bbox_2d, settings.bbox_format, target_page.view_size)
    else:
        parent_crop = state.crops[target_item[1]]
        target_page = state.pages[parent_crop.page]
        local_box = vlm_bbox_to_norm(pending.bbox_2d, settings.bbox_format, parent_crop.size)
        page_box = crop_box_to_page_box(local_box, parent_crop.page_box)

    crop_id = f"c{state.zoom_count}"
    with logger.span("zoom") as span:
        crop_asset = zoom(
            page=target_page,
            page_box_requested=page_box,
            settings=settings,
            out_dir=state.run_dir,
            crop_id=crop_id,
            target=pending.target,
        )
        logger.event(
            span_id=span.span_id,
            node="zoom",
            status="ok",
            latency_ms=0.0,
            prompt_tokens=None,
            completion_tokens=None,
            attempt=1,
            payload={
                "crop_id": crop_id,
                "page": target_page.page,
                "pixel_box": crop_asset.pixel_box,
                "target": pending.target,
            },
        )

    new_crops = dict(state.crops)
    new_crops[crop_id] = crop_asset

    step_rec = StepRecord(
        step=state.step + 1,
        node="zoom",
        input_images=[crop_asset.path],
        output=crop_asset.model_dump(),
        usage=None,
        attempts=1,
    )

    return {
        "crops": new_crops,
        "zoom_count": state.zoom_count + 1,
        "pending_zoom": None,
        "step": state.step + 1,
        "history": state.history + [step_rec],
        "route": "inspector",
    }


def synthesizer_node(
    state: AgentState, vlm: VLMClient, settings: Settings, logger: TraceLogger
) -> dict[str, Any]:
    synthesizer = Synthesizer(vlm, settings, logger)
    view = state.synth_view or state.last_view
    if view is None or not view.items:
        pages_for_view = [state.pages[p] for p in state.inspector_pages]
        view = Inspector(vlm, settings, logger).build_view(pages_for_view, list(state.crops.values()))

    output, resp, attempts = synthesizer.run(
        question=state.question,
        draft=state.draft_answer,
        view=view,
        pages=state.pages,
        crops=state.crops,
    )

    ref_pages: list[int] = []
    for r in output.reference:
        it = view.items[r]
        ref_pages.append(it[1] if it[0] == "page" else state.crops[it[1]].page)

    prompt_toks = state.prompt_tokens + (resp.usage.prompt_tokens or 0)
    compl_toks = state.completion_tokens + (resp.usage.completion_tokens or 0)

    result = FinalResult(
        status="synthesized",
        answer=output.answer,
        reason=output.reason,
        reference_pages=sorted(list(set(ref_pages))),
        crops=list(state.crops.values()),
        steps=state.step + 1,
        total_prompt_tokens=prompt_toks,
        total_completion_tokens=compl_toks,
        trace_id=state.trace_id,
    )

    step_rec = StepRecord(
        step=state.step + 1,
        node="synthesizer",
        input_images=[],
        output=output.model_dump(),
        usage=resp.usage,
        attempts=attempts,
    )

    return {
        "result": result,
        "step": state.step + 1,
        "history": state.history + [step_rec],
        "prompt_tokens": prompt_toks,
        "completion_tokens": compl_toks,
        "route": "finalize",
    }


def finalize_node(state: AgentState, settings: Settings) -> dict[str, Any]:
    result = state.result
    if result is None:
        status = "budget_exhausted" if state.step >= settings.max_steps else "no_answer"
        result = FinalResult(
            status=status,
            answer=None,
            reason="Execution completed without conclusive answer.",
            reference_pages=[],
            crops=list(state.crops.values()),
            steps=state.step,
            total_prompt_tokens=state.prompt_tokens,
            total_completion_tokens=state.completion_tokens,
            trace_id=state.trace_id,
        )

    # 持久化 result.json
    res_path = Path(state.run_dir) / "result.json"
    res_path.parent.mkdir(parents=True, exist_ok=True)
    with open(res_path, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))

    return {"result": result}
