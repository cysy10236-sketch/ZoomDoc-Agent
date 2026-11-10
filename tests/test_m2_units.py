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
from zoomdoc.schemas import CitedRegion, NormBBox, UsageTotals, VLMUsage


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
