from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval_m25 import is_answer_correct, normalize_answer, run_eval
from scripts.make_eval_set import make_eval_set
from scripts.report_m25 import (
    generate_report,
    mcnemar_exact_p_value,
    point_biserial_correlation,
    wilson_score_interval,
)
from tests.fake_vlm import FakeVLM


def test_normalize_and_scoring() -> None:
    # 1. Normalization tests
    assert normalize_answer(" ZD-1234-AB. ") == "ZD-1234-AB"
    assert normalize_answer("'zd-1234-ab'") == "ZD-1234-AB"
    assert normalize_answer('"ZD-9999-QX"\n') == "ZD-9999-QX"
    assert normalize_answer("") == ""
    assert normalize_answer(None) == ""

    # 2. Strict deterministic exact match
    assert is_answer_correct("zd-1234-ab", "ZD-1234-AB")
    assert is_answer_correct("ZD-1234-AB.", "ZD-1234-AB")
    assert not is_answer_correct("ZD-1234-CD", "ZD-1234-AB")
    assert not is_answer_correct("", "ZD-1234-AB")
    assert not is_answer_correct(None, "ZD-1234-AB")


def test_wilson_score_interval_hand_calculated() -> None:
    # For k=10, n=20: p=0.5, Wilson 95% CI is exactly [0.2993, 0.7007]
    ci_50 = wilson_score_interval(10, 20)
    assert ci_50 == (0.2993, 0.7007)

    # For k=0, n=10: lower bound clamped to 0.0
    ci_0 = wilson_score_interval(0, 10)
    assert ci_0[0] == 0.0
    assert 0.27 < ci_0[1] < 0.28


def test_mcnemar_exact_p_value() -> None:
    # 0 differences
    assert mcnemar_exact_p_value(0, 0) == 1.0

    # 4 vs 0 differences: 2 * (0.5^4) = 2 * 0.0625 = 0.125
    assert mcnemar_exact_p_value(4, 0) == 0.125

    # 8 vs 0 differences: 2 * (0.5^8) = 2 / 256 = 0.0078
    assert mcnemar_exact_p_value(8, 0) == 0.0078


def test_point_biserial_correlation() -> None:
    confs = [0.2, 0.3, 0.8, 0.9]
    corrs = [0, 0, 1, 1]
    r = point_biserial_correlation(confs, corrs)
    assert r > 0.9

    # Inverted correlation
    r_inv = point_biserial_correlation(confs, [1, 1, 0, 0])
    assert r_inv < -0.9


def test_eval_pipeline_with_fake_vlm(tmp_path: Path) -> None:
    # 1. Generate 2 synthetic test samples
    eval_dir = tmp_path / "eval"
    make_eval_set(str(eval_dir), n=2, seed=42)

    manifest_path = eval_dir / "manifest.jsonl"
    assert manifest_path.exists()
    cases = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 2

    gold_1 = cases[0]["gold"]
    gold_2 = cases[1]["gold"]
    gold_page_1 = cases[0]["gold_page"]
    gold_page_2 = cases[1]["gold_page"]

    # Scripted responses for FakeVLM across runs:
    # Case 1 in G0: seeker selects gold_page_1, inspector answers gold_1
    # Case 2 in G0: seeker selects gold_page_2, inspector answers wrong
    # (Repeated or sufficient scripted answers)
    scripted = [
        # G0 case 1:
        json.dumps({"reason": "find", "summary": "s", "choice": [gold_page_1 - 1]}),
        json.dumps({"action": "answer", "reason": "found", "answer": gold_1, "confidence": 0.9, "reference": [0]}),
        # G0 case 2:
        json.dumps({"reason": "find", "summary": "s", "choice": [gold_page_2 - 1]}),
        json.dumps({"action": "answer", "reason": "wrong", "answer": "WRONG-CODE", "confidence": 0.4, "reference": [0]}),
        # G1 case 1:
        json.dumps({"reason": "find", "summary": "s", "choice": [gold_page_1 - 1]}),
        json.dumps({"action": "zoom", "reason": "zoom", "image": 0, "bbox_2d": [100, 100, 500, 500], "target": "code"}),
        json.dumps({"action": "answer", "reason": "found", "answer": gold_1, "confidence": 0.95, "reference": [0, 1]}),
        # G1 case 2:
        json.dumps({"reason": "find", "summary": "s", "choice": [gold_page_2 - 1]}),
        json.dumps({"action": "zoom", "reason": "zoom", "image": 0, "bbox_2d": [100, 100, 500, 500], "target": "code"}),
        json.dumps({"action": "answer", "reason": "found", "answer": gold_2, "confidence": 0.85, "reference": [0, 1]}),
        # G2 case 1:
        json.dumps({"reason": "find", "summary": "s", "choice": [gold_page_1 - 1]}),
        json.dumps({"action": "answer", "reason": "found", "answer": gold_1, "confidence": 0.95, "reference": [0]}),
        # G2 case 2:
        json.dumps({"reason": "find", "summary": "s", "choice": [gold_page_2 - 1]}),
        json.dumps({"action": "answer", "reason": "found", "answer": gold_2, "confidence": 0.9, "reference": [0]}),
    ]
    fake_vlm = FakeVLM(scripted)

    results_dir = eval_dir / "results"
    runs_dir = eval_dir / "runs"

    # Run eval across all 3 groups
    res_dict = run_eval(
        group_arg="all",
        manifest_path=str(manifest_path),
        out_dir=str(results_dir),
        runs_dir=str(runs_dir),
        resume=True,
        vlm_client=fake_vlm,
    )

    assert set(res_dict.keys()) == {"G0", "G1", "G2"}
    assert len(res_dict["G0"]) == 2
    assert len(res_dict["G1"]) == 2
    assert len(res_dict["G2"]) == 2

    # Verify JSON files on disk
    for grp in ["G0", "G1", "G2"]:
        for c in cases:
            f = results_dir / grp / f"{c['case_id']}.json"
            assert f.exists()
            data = json.loads(f.read_text(encoding="utf-8"))
            assert data["case_id"] == c["case_id"]
            assert "is_correct" in data
            assert "is_zoomed" in data

    # Test resume: running again with resume=True should not consume any extra scripted calls
    pre_calls = fake_vlm.call_count
    run_eval(
        group_arg="all",
        manifest_path=str(manifest_path),
        out_dir=str(results_dir),
        runs_dir=str(runs_dir),
        resume=True,
        vlm_client=fake_vlm,
    )
    assert fake_vlm.call_count == pre_calls

    # Test report generation
    report_text = generate_report(
        results_dir=str(results_dir),
        out_dir=str(eval_dir),
        lock_file="requirements.lock",
        vlm_model="fake-vlm-model",
    )

    report_file = eval_dir / "report.md"
    assert report_file.exists()
    assert "## DECISION:" in report_text
    assert "G0 (no-zoom)" in report_text
    assert "G1 (zoom)" in report_text
    assert "G2 (zoom+critic)" in report_text

    bad_cases_file = eval_dir / "bad_cases.jsonl"
    assert bad_cases_file.exists()
    bad_lines = bad_cases_file.read_text(encoding="utf-8").splitlines()
    assert len(bad_lines) >= 1  # G0 case 2 was incorrect
