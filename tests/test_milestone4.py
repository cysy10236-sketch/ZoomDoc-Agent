from __future__ import annotations

import json
from pathlib import Path

import pymupdf as fitz
import pytest

from scripts.eval_m4 import run_eval_m4
from scripts.make_eval_m4 import make_eval_m4
from scripts.make_synthetic_long_pdf import create_synthetic_long_pdf
from scripts.report_m4 import generate_report_m4
from tests.fake_vlm import FakeVLM
from zoomdoc.config import Settings
from zoomdoc.eval.baselines import run_sliding_grid_baseline
from zoomdoc.eval.metrics import (
    compute_ngram_overlap,
    evaluate_m5_trigger,
    paired_bootstrap_ratio_ci,
)
from zoomdoc.graph.supervisor import run_m3
from zoomdoc.index.tree import build_document_tree, get_leaf_nodes


@pytest.fixture
def synthetic_100p_m4(tmp_path: Path) -> str:
    pdf_path = tmp_path / "synth_100p_m4.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=100,
        gold_page=73,
        gold_code="ZD-9042-M4",
    )
    return str(pdf_path)


def test_m4_t1_latency_profiling_in_run_m3(synthetic_100p_m4: str, tmp_path: Path) -> None:
    """T1: 验证 run_m3 执行耗时与按需渲染耗时细粒度埋点统计。"""
    tree = build_document_tree(synthetic_100p_m4, max_leaf_pages=20)
    part3_node = next(n for n in tree.roots if "Part 3" in n.title)
    leaf_target = next(n for n in get_leaf_nodes(tree) if n.start_page <= 73 <= n.end_page)
    page_73_index = 73 - leaf_target.start_page

    scripted = [
        # Nav expand
        json.dumps({
            "action": "expand",
            "reason": "Expand Part 3",
            "node_ids": [part3_node.node_id],
        }),
        # Nav select
        json.dumps({
            "action": "select",
            "reason": "Select Section 3.2",
            "node_ids": [leaf_target.node_id],
        }),
        # Seeker
        json.dumps({
            "reason": "Found target table",
            "summary": "Target found",
            "choice": [page_73_index],
        }),
        # Inspector answer
        json.dumps({
            "action": "answer",
            "reason": "Read code",
            "answer": "ZD-9042-M4",
            "confidence": 0.95,
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(runs_dir=str(tmp_path))

    result, state = run_m3(
        question="What is the code on page 73?",
        pdf_path=synthetic_100p_m4,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m4-t1",
    )

    assert result.status == "answered"
    assert result.render_latency_ms >= 0.0
    assert result.tree_latency_ms >= 0.0
    assert result.total_wall_ms >= result.render_latency_ms

    # Check persisted result.json
    res_path = tmp_path / "trace-m4-t1" / "result.json"
    assert res_path.is_file()
    data = json.loads(res_path.read_text(encoding="utf-8"))
    assert "render_latency_ms" in data
    assert "tree_latency_ms" in data
    assert "total_wall_ms" in data
    assert data["total_wall_ms"] == result.total_wall_ms


def test_m4_t2_sliding_grid_baselines_b0_and_b1(tmp_path: Path) -> None:
    """T2: 验证长文档滑窗网格基准运行器（B0 1024px 无放大 vs B1 448px 带放大）。"""
    # 构造 45 页合成文档，金标在第 33 页
    pdf_path = tmp_path / "synth_45p.pdf"
    doc = fitz.open()
    for p_idx in range(45):
        p_num = p_idx + 1
        page = doc.new_page(width=595.3, height=841.9)
        page.insert_text(fitz.Point(50, 60), f"Section {p_num}", fontsize=16)
        if p_num == 33:
            page.insert_text(fitz.Point(400, 720), "ZD-3333-TR", fontsize=5)
    doc.save(str(pdf_path))
    doc.close()

    # 1. Test B0: 3 windows of 20/20/5 pages. Window 2 has page 33 (index 12).
    scripted_b0 = [
        # Win 1 seeker (1..20): no selection
        json.dumps({"reason": "none", "summary": "", "choice": []}),
        # Win 2 seeker (21..40): select page 33 (relative index 12)
        json.dumps({"reason": "found table", "summary": "", "choice": [12]}),
        # Win 3 seeker (41..45): no selection
        json.dumps({"reason": "none", "summary": "", "choice": []}),
        # Inspector answer (max_zooms=0 in B0)
        json.dumps({
            "action": "answer",
            "reason": "read code",
            "answer": "ZD-3333-TR",
            "confidence": 0.9,
            "reference": [0],
        }),
    ]
    fake_b0 = FakeVLM(scripted_b0)
    settings = Settings(runs_dir=str(tmp_path))

    res_b0, sel_b0 = run_sliding_grid_baseline(
        question="What is the code on page 33?",
        pdf_path=str(pdf_path),
        mode="B0_fullres_nozoom",
        vlm=fake_b0,
        settings=settings,
        trace_id="trace-b0",
    )
    assert res_b0.status == "answered"
    assert res_b0.answer == "ZD-3333-TR"
    assert res_b0.seek_rounds == 3
    assert res_b0.inspect_rounds == 1
    assert sel_b0 == [33]
    assert len(res_b0.cited_regions) == 1

    # 2. Test B1: 3 windows, with zoom inside Inspector
    scripted_b1 = [
        # Win 1 seeker
        json.dumps({"reason": "none", "summary": "", "choice": []}),
        # Win 2 seeker: select page 33
        json.dumps({"reason": "found table", "summary": "", "choice": [12]}),
        # Win 3 seeker
        json.dumps({"reason": "none", "summary": "", "choice": []}),
        # Inspector zoom (max_zooms=3 in B1)
        json.dumps({
            "action": "zoom",
            "reason": "small text",
            "image": 0,
            "bbox_2d": [638, 831, 940, 926],
            "target": "code",
        }),
        # Inspector answer
        json.dumps({
            "action": "answer",
            "reason": "read from zoom",
            "answer": "ZD-3333-TR",
            "confidence": 0.95,
            "reference": [0, 1],
        }),
    ]
    fake_b1 = FakeVLM(scripted_b1)
    res_b1, sel_b1 = run_sliding_grid_baseline(
        question="What is the code on page 33?",
        pdf_path=str(pdf_path),
        mode="B1_thumb_zoom",
        vlm=fake_b1,
        settings=settings,
        trace_id="trace-b1",
    )
    assert res_b1.status == "answered"
    assert res_b1.answer == "ZD-3333-TR"
    assert res_b1.seek_rounds == 3
    assert res_b1.inspect_rounds == 1
    assert len(res_b1.cited_regions) >= 1


def test_m4_t3_paired_bootstrap_ratio_ci_and_h1_verdict() -> None:
    """T3: 验证 Paired Bootstrap 比值与置信区间统计学算法精确性。"""
    b0_tokens = [10000.0, 12000.0, 11000.0, 9000.0]
    m3_tokens = [3000.0, 3600.0, 3300.0, 2700.0]

    res = paired_bootstrap_ratio_ci(m3_tokens, b0_tokens, n_boot=2000, seed=42)
    assert abs(res["ratio"] - 0.30) < 1e-4
    assert abs(res["savings"] - 0.70) < 1e-4
    assert abs(res["ratio_ci_low"] - 0.30) < 1e-4
    assert abs(res["ratio_ci_high"] - 0.30) < 1e-4
    assert abs(res["savings_ci_low"] - 0.70) < 1e-4
    assert abs(res["savings_ci_high"] - 0.70) < 1e-4

    # 边界与异常
    with pytest.raises(ValueError, match="Length mismatch"):
        paired_bootstrap_ratio_ci([1.0], [1.0, 2.0])

    with pytest.raises(ValueError, match="Sum of denominator"):
        paired_bootstrap_ratio_ci([1.0, 2.0], [0.0, 0.0])


def test_m4_t4_contamination_audit_zero_leakage(tmp_path: Path) -> None:
    """T4: 防数据污染与记忆重放审计（准则第 18 条）：验证测试集答案与 Prompt 库 0 泄漏。"""
    eval_dir = tmp_path / "eval_m4"
    make_eval_m4(out_dir=str(eval_dir), n=20, seed=2026111004)

    manifest_file = eval_dir / "manifest.jsonl"
    assert manifest_file.is_file()

    gold_answers: list[str] = []
    for line in manifest_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            gold_answers.append(rec["gold"])

    # 确保 M1 历史调试字串 ZD-7319-QX 绝不出现
    assert "ZD-7319-QX" not in gold_answers

    prompts_path = Path("zoomdoc/prompts.py")
    assert prompts_path.is_file()
    corpus = prompts_path.read_text(encoding="utf-8")

    audit = compute_ngram_overlap(gold_answers, corpus, n=4)
    assert audit["exact_match_count"] == 0
    assert audit["exact_match_rate"] == 0.0
    assert audit["leaked_items"] == []


def test_m4_t5_m5_redis_trigger_gate_logic() -> None:
    """T5: 验证 Milestone 5 Redis 缓存触发门禁判断真值表。"""
    # 组 A：渲染耗时仅占 2%，P95 < 120s -> OPTIONAL_SKIPPED
    res_a = evaluate_m5_trigger(
        render_latencies_ms=[500.0, 600.0],
        total_wall_latencies_ms=[25000.0, 30000.0],
    )
    assert res_a["triggered"] is False
    assert res_a["decision"] == "OPTIONAL_SKIPPED"

    # 组 B：渲染耗时占 27% (>= 20%) -> REQUIRED
    res_b = evaluate_m5_trigger(
        render_latencies_ms=[8000.0, 9000.0],
        total_wall_latencies_ms=[30000.0, 32000.0],
    )
    assert res_b["triggered"] is True
    assert res_b["decision"] == "REQUIRED"

    # 组 C：渲染占比低但 P95 超过 120s -> REQUIRED
    res_c = evaluate_m5_trigger(
        render_latencies_ms=[500.0, 600.0],
        total_wall_latencies_ms=[130000.0, 140000.0],
    )
    assert res_c["triggered"] is True
    assert res_c["decision"] == "REQUIRED"


class SmartFakeVLM:
    """Mock VLM that dynamically generates valid responses matching the calling agent's schema."""

    def __init__(self, gold_code: str = "ZD-5555-M4") -> None:
        self.gold_code = gold_code
        self.calls: list[list] = []
        self.call_count = 0

    def generate(self, parts: list, *, extra_messages: list | None = None):
        from zoomdoc.llm.vlm_client import TextPart, VLMResponse
        from zoomdoc.schemas import VLMUsage

        self.calls.append(list(parts))
        self.call_count += 1
        text_prompt = ""
        for p in parts:
            if isinstance(p, TextPart):
                text_prompt += p.text

        if "Available Outline Nodes" in text_prompt or "document navigation agent" in text_prompt:
            import re
            node_ids = re.findall(r"\[node_id:\s*([0-9a-zA-Z_\-]+)\]", text_prompt)
            nid = node_ids[0] if node_ids else "0001"
            resp_text = json.dumps({
                "action": "select",
                "reason": "Target section selected",
                "node_ids": [nid],
            })
        elif "grid arrangement" in text_prompt or "Thumbnail Grid" in text_prompt or "page_map" in text_prompt or "Choice" in text_prompt:
            resp_text = json.dumps({
                "reason": "Target thumbnail located",
                "summary": "Target found",
                "choice": [0],
            })
        elif "SYNTHESIZER" in text_prompt or "Synthesize" in text_prompt:
            resp_text = json.dumps({
                "reason": "synthesized",
                "answer": self.gold_code,
                "reference_pages": [1],
            })
        else:
            # Inspector
            resp_text = json.dumps({
                "action": "answer",
                "reason": "Direct reading of gold code",
                "answer": self.gold_code,
                "confidence": 0.95,
                "reference": [0],
            })

        return VLMResponse(
            text=resp_text,
            usage=VLMUsage(
                prompt_tokens=100,
                completion_tokens=50,
                latency_ms=10.0,
                model="fake-vlm-model",
            ),
        )


def test_m4_t6_end_to_end_eval_and_report_m4_pipeline(tmp_path: Path) -> None:
    """T6: 端到端离线评测与报告流水线完整闭环验证（包含 resume 幂等与 Markdown 报告）。"""
    eval_dir = tmp_path / "eval_m4"
    eval_dir.mkdir(parents=True, exist_ok=True)

    # 构造 25 页长文档测试样本（> 20 页硬上限，同时触发滑窗网格与树索引）
    from scripts.make_eval_m4 import build_long_doc

    pdf_path = eval_dir / "test_long_25p.pdf"
    toc_25 = [
        [1, "Part 1: Overview & Governance", 1],
        [2, "1.1 Management Review", 1],
        [1, "Part 2: Operational Notes", 12],
        [2, "2.1 Telemetry Audit", 12],
    ]
    build_long_doc(
        out_path=pdf_path,
        total_pages=25,
        toc_specs=toc_25,
        gold_targets={15: {"code": "ZD-5555-M4", "font_pt": 5.0}},
        distractor_pages={5},
    )

    manifest_path = eval_dir / "manifest.jsonl"
    cases = [
        {
            "case_id": "case_001",
            "track": "TrackA",
            "pdf_path": str(pdf_path.resolve()),
            "total_pages": 25,
            "gold_page": 15,
            "gold": "ZD-5555-M4",
            "font_pt": 5.0,
            "query_type": "direct_section",
            "question": "What is the security ID on page 15?",
        },
        {
            "case_id": "case_002",
            "track": "TrackA",
            "pdf_path": str(pdf_path.resolve()),
            "total_pages": 25,
            "gold_page": 15,
            "gold": "ZD-5555-M4",
            "font_pt": 5.0,
            "query_type": "cross_page_table",
            "question": "What is the security ID on page 15?",
        },
    ]
    with open(manifest_path, "w", encoding="utf-8") as fp:
        for c in cases:
            fp.write(json.dumps(c) + "\n")

    fake_vlm = SmartFakeVLM(gold_code="ZD-5555-M4")
    settings = Settings(
        runs_dir=str(tmp_path / "runs"),
        max_steps=16,
    )

    # 1. Execute eval_m4 on all groups
    res_dict = run_eval_m4(
        group_arg="all",
        manifest_path=str(manifest_path),
        out_dir=str(eval_dir / "results"),
        runs_dir=str(tmp_path / "runs"),
        resume=True,
        limit=2,
        vlm_client=fake_vlm,
        settings=settings,
    )
    assert len(res_dict) == 4
    assert len(res_dict["B0"]) == 2
    assert len(res_dict["M3"]) == 2

    # 2. Resume test: calling again with resume=True should load without calling VLM
    prior_calls = fake_vlm.call_count
    run_eval_m4(
        group_arg="B0",
        manifest_path=str(manifest_path),
        out_dir=str(eval_dir / "results"),
        runs_dir=str(tmp_path / "runs"),
        resume=True,
        limit=2,
        vlm_client=fake_vlm,
        settings=settings,
    )
    assert fake_vlm.call_count == prior_calls

    # 3. Generate report
    report_text = generate_report_m4(
        results_dir=str(eval_dir / "results"),
        manifest_path=str(manifest_path),
        out_dir=str(eval_dir),
    )

    assert "## H1_VERDICT:" in report_text
    assert "## M5_REDIS_TRIGGER:" in report_text
    assert "| B0 (SlidingGrid-FullRes) |" in report_text
    assert "| M3-Full (ZoomDoc-M3) |" in report_text

    # Bad cases jsonl should exist
    assert (eval_dir / "bad_cases.jsonl").is_file()
