from __future__ import annotations

import pytest
from pydantic import ValidationError

from zoomdoc.graph.states import (
    CriticVerdict,
    EvidenceReport,
    InspectorSubState,
    SupervisorState,
    merge_usage,
    usage_from_sink,
)
from zoomdoc.schemas import CitedRegion, FinalResultM2, NormBBox, StepRecord, UsageTotals, VLMUsage


def test_merge_usage() -> None:
    u1 = UsageTotals(
        calls=2,
        prompt_tokens=100,
        completion_tokens=50,
        missing_usage_calls=0,
        latency_ms=120.5,
    )
    u2 = UsageTotals(
        calls=1,
        prompt_tokens=200,
        completion_tokens=80,
        missing_usage_calls=1,
        latency_ms=80.3,
    )
    merged = merge_usage(u1, u2)

    assert merged.calls == 3
    assert merged.prompt_tokens == 300
    assert merged.completion_tokens == 130
    assert merged.missing_usage_calls == 1
    assert merged.latency_ms == 200.8

    # Ensure immutability (purity)
    assert u1.calls == 2
    assert u2.calls == 1


def test_usage_from_sink() -> None:
    sink = [
        VLMUsage(prompt_tokens=100, completion_tokens=50, latency_ms=10.0, model="mock"),
        VLMUsage(prompt_tokens=None, completion_tokens=None, latency_ms=15.0, model="mock"),
        VLMUsage(prompt_tokens=200, completion_tokens=40, latency_ms=12.0, model="mock"),
    ]
    u = usage_from_sink(sink)
    assert u.calls == 3
    assert u.prompt_tokens == 300
    assert u.completion_tokens == 90
    assert u.missing_usage_calls == 1
    assert u.latency_ms == 37.0


def test_evidence_report_valid() -> None:
    rep = EvidenceReport(
        round=1,
        status="answered",
        answer="ZD-7319-QX",
        confidence=0.95,
        reason="Detected serial code in high-res table",
        reference_pages=[3],
        cited_regions=[
            CitedRegion(
                page=3,
                page_box=NormBBox(x1=0.6, y1=0.8, x2=0.8, y2=0.9),
                target="serial code",
            )
        ],
        zoom_count=1,
        sub_steps=2,
        usage=UsageTotals(calls=2, prompt_tokens=200, completion_tokens=100),
    )
    assert rep.status == "answered"
    assert rep.confidence == 0.95
    assert len(rep.cited_regions) == 1


def test_evidence_report_leak_prevention() -> None:
    valid_kwargs = dict(
        round=1,
        status="answered",
        answer="Valid Answer",
        confidence=0.9,
        reason="Normal reasoning text",
        reference_pages=[1],
        cited_regions=[],
        zoom_count=0,
        sub_steps=1,
        usage=UsageTotals(),
    )

    # 1. Image path leaked into reason
    with pytest.raises(ValidationError, match="image payload leaked"):
        bad_kwargs = dict(valid_kwargs, reason="see runs/x/sub/r1/crops/c0.png")
        EvidenceReport(**bad_kwargs)

    # 2. Base64 leaked into answer
    with pytest.raises(ValidationError, match="image payload leaked"):
        bad_kwargs = dict(valid_kwargs, answer="data:image/png;base64,AAA")
        EvidenceReport(**bad_kwargs)

    # 3. Base64 marker in seek_hint
    with pytest.raises(ValidationError, match="image payload leaked"):
        bad_kwargs = dict(valid_kwargs, seek_hint="here is base64,xyz image")
        EvidenceReport(**bad_kwargs)

    # 4. Extra field forbidden
    with pytest.raises(ValidationError):
        bad_kwargs = dict(valid_kwargs, extra_image_crop="something")
        EvidenceReport(**bad_kwargs)


def test_supervisor_state_field_contract() -> None:
    # Ensure SupervisorState has no image or crop fields
    forbidden_fields = {"crops", "view", "pending_zoom", "last_output", "sub_step", "zoom_count"}
    assert set(SupervisorState.model_fields.keys()).isdisjoint(forbidden_fields)


from zoomdoc.graph.critic import evaluate_report


def test_evaluate_report_branches() -> None:
    valid_pages = {1, 2, 3}
    min_conf = 0.6
    base_kwargs = dict(
        round=1,
        status="answered",
        answer="Valid Answer",
        confidence=0.8,
        reason="Clear text visible",
        reference_pages=[2],
        cited_regions=[],
        zoom_count=0,
        sub_steps=1,
        usage=UsageTotals(),
    )

    # 1. budget_exhausted
    rep_budget = EvidenceReport(**dict(base_kwargs, status="budget_exhausted", answer=None))
    v_budget = evaluate_report(rep_budget, valid_pages, min_conf)
    assert not v_budget.accepted
    assert v_budget.reason_code == "budget_exhausted"

    # 2. need_more_pages
    rep_seek = EvidenceReport(**dict(base_kwargs, status="need_more_pages", answer=None, seek_hint="need page 4"))
    v_seek = evaluate_report(rep_seek, valid_pages, min_conf)
    assert not v_seek.accepted
    assert v_seek.reason_code == "need_more_pages"
    assert "need page 4" in v_seek.detail

    # 3. no_answer
    rep_no_ans = EvidenceReport(**dict(base_kwargs, answer="   "))
    v_no_ans = evaluate_report(rep_no_ans, valid_pages, min_conf)
    assert not v_no_ans.accepted
    assert v_no_ans.reason_code == "no_answer"

    # 4. invalid_reference (empty or not subset)
    rep_inv_ref = EvidenceReport(**dict(base_kwargs, reference_pages=[99]))
    v_inv_ref = evaluate_report(rep_inv_ref, valid_pages, min_conf)
    assert not v_inv_ref.accepted
    assert v_inv_ref.reason_code == "invalid_reference"

    # 5. low_confidence
    rep_low_conf = EvidenceReport(**dict(base_kwargs, confidence=0.35))
    v_low_conf = evaluate_report(rep_low_conf, valid_pages, min_conf)
    assert not v_low_conf.accepted
    assert v_low_conf.reason_code == "low_confidence"
    assert "0.35" in v_low_conf.detail

    # 6. ok
    rep_ok = EvidenceReport(**dict(base_kwargs, confidence=0.9))
    v_ok = evaluate_report(rep_ok, valid_pages, min_conf)
    assert v_ok.accepted
    assert v_ok.reason_code == "ok"


import json
from pathlib import Path
from scripts.make_synthetic_pdf import create_synthetic_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.config import Settings
from zoomdoc.graph.inspector_subgraph import run_inspector_round
from zoomdoc.observability import TraceLogger
from zoomdoc.vision.render import render_pdf


def test_inspector_subgraph_isolated_round(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    create_synthetic_pdf(str(pdf_path))

    settings = Settings(
        runs_dir=str(tmp_path),
        max_zooms=2,
        max_sub_steps=4,
        keep_sub_artifacts=True,
    )
    pages = render_pdf(str(pdf_path), str(tmp_path), settings)
    pages_dict = {p.page: p for p in pages if p.page == 3}

    scripted = [
        json.dumps({
            "action": "zoom",
            "reason": "Zoom in on table",
            "image": 0,
            "bbox_2d": [638, 831, 940, 926],
            "target": "serial code",
        }),
        json.dumps({
            "action": "answer",
            "reason": "Serial code is ZD-7319-QX",
            "answer": "ZD-7319-QX",
            "confidence": 0.95,
            "reference": [0, 1],
        }),
    ]
    fake_vlm = FakeVLM(scripted)
    logger = TraceLogger(str(tmp_path), "test-trace-sub")

    report = run_inspector_round(
        question="What is the code?",
        round_=1,
        pages=pages_dict,
        feedback=None,
        run_dir=str(tmp_path),
        trace_id="test-trace-sub",
        vlm=fake_vlm,
        settings=settings,
        logger=logger,
    )

    assert report.status == "answered"
    assert report.answer == "ZD-7319-QX"
    assert report.confidence == 0.95
    assert report.zoom_count == 1
    assert report.sub_steps == 3
    assert report.reference_pages == [3]
    assert len(report.cited_regions) == 2
    # Crop item has page_box
    assert report.cited_regions[1].page_box is not None
    assert report.usage.calls == 2
    assert report.usage.prompt_tokens == 200


from zoomdoc.graph.supervisor import decide_next


def test_decide_next_truth_table() -> None:
    settings = Settings(
        max_steps=8,
        max_inspect_rounds=2,
        max_seek_rounds=2,
    )
    base_kwargs = dict(
        trace_id="t1",
        question="q",
        pdf_path="p.pdf",
        run_dir="runs/t1",
    )

    dummy_answered_evidence = EvidenceReport(
        round=1,
        status="answered",
        answer="A1",
        confidence=0.9,
        reason="r",
        reference_pages=[1],
        cited_regions=[],
        zoom_count=0,
        sub_steps=1,
        usage=UsageTotals(),
    )

    # R0: Result already exists
    s_r0 = SupervisorState(**base_kwargs, result=FinalResultM2(
        status="answered", answer="A", reason="R", reference_pages=[1],
        cited_regions=[], inspect_rounds=1, seek_rounds=1, usage=UsageTotals(), trace_id="t1"
    ))
    nxt, upd = decide_next(s_r0, settings)
    assert nxt == "finalize"

    # R1: Step budget exceeded
    s_r1_ans = SupervisorState(**base_kwargs, step=8, evidence=[dummy_answered_evidence])
    nxt, upd = decide_next(s_r1_ans, settings)
    assert nxt == "synthesizer"

    s_r1_noans = SupervisorState(**base_kwargs, step=8, evidence=[])
    nxt, upd = decide_next(s_r1_noans, settings)
    assert nxt == "finalize"

    # R2: Initial state with candidates
    s_r2 = SupervisorState(**base_kwargs, candidate_pages=[1, 2, 3])
    nxt, upd = decide_next(s_r2, settings)
    assert nxt == "seeker"

    s_r2_empty = SupervisorState(**base_kwargs, candidate_pages=[])
    nxt, upd = decide_next(s_r2_empty, settings)
    assert nxt == "finalize"

    # R3: Seeker selected pages
    s_r3 = SupervisorState(
        **base_kwargs,
        selected_pages=[2],
        history=[StepRecord(step=1, node="seeker", input_images=[], output={}, usage=None, attempts=1)],
    )
    nxt, upd = decide_next(s_r3, settings)
    assert nxt == "inspector"
    assert upd.get("inspect_rounds") == 1

    # R4: Seeker selected nothing
    s_r4 = SupervisorState(
        **base_kwargs,
        selected_pages=[],
        evidence=[dummy_answered_evidence],
        history=[StepRecord(step=1, node="seeker", input_images=[], output={}, usage=None, attempts=1)],
    )
    nxt, upd = decide_next(s_r4, settings)
    assert nxt == "synthesizer"

    # R5: Critic accepted
    s_r5 = SupervisorState(
        **base_kwargs,
        evidence=[dummy_answered_evidence],
        verdict=CriticVerdict(accepted=True, reason_code="ok", detail="Passed"),
        history=[StepRecord(step=2, node="critic", input_images=[], output={}, usage=None, attempts=1)],
    )
    nxt, upd = decide_next(s_r5, settings)
    assert nxt == "finalize"
    assert upd.get("final_answer") == "A1"

    # R6: Critic rejected with low confidence and inspect_rounds < max
    s_r6 = SupervisorState(
        **base_kwargs,
        evidence=[dummy_answered_evidence],
        inspect_rounds=1,
        verdict=CriticVerdict(accepted=False, reason_code="low_confidence", detail="Needs zoom"),
        history=[StepRecord(step=2, node="critic", input_images=[], output={}, usage=None, attempts=1)],
    )
    nxt, upd = decide_next(s_r6, settings)
    assert nxt == "inspector"
    assert upd.get("inspect_rounds") == 2
    assert upd.get("feedback") == "Needs zoom"

    # R7: Critic requires more pages and candidate_pages exist
    rep_need_more = EvidenceReport(
        round=1,
        status="need_more_pages",
        answer=None,
        confidence=None,
        reason="Missing page",
        seek_hint="Look for appendix",
        reference_pages=[],
        cited_regions=[],
        zoom_count=0,
        sub_steps=1,
        usage=UsageTotals(),
    )
    s_r7 = SupervisorState(
        **base_kwargs,
        candidate_pages=[3, 4],
        seek_rounds=1,
        evidence=[rep_need_more],
        verdict=CriticVerdict(accepted=False, reason_code="need_more_pages", detail="Needs more pages"),
        history=[StepRecord(step=2, node="critic", input_images=[], output={}, usage=None, attempts=1)],
    )
    nxt, upd = decide_next(s_r7, settings)
    assert nxt == "seeker"
    assert upd.get("feedback") == "Look for appendix"

    # R8: Fallback when rounds exhausted
    s_r8 = SupervisorState(
        **base_kwargs,
        candidate_pages=[3, 4],
        seek_rounds=2,  # equals max_seek_rounds
        evidence=[dummy_answered_evidence],
        verdict=CriticVerdict(accepted=False, reason_code="need_more_pages", detail="Needs more pages"),
        history=[StepRecord(step=2, node="critic", input_images=[], output={}, usage=None, attempts=1)],
    )
    nxt, upd = decide_next(s_r8, settings)
    assert nxt == "synthesizer"
