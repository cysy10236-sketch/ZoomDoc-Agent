from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.make_synthetic_pdf import create_synthetic_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.config import Settings
from zoomdoc.graph.builder import run
from zoomdoc.llm.vlm_client import ImagePart, TextPart


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> str:
    pdf_path = tmp_path / "test_synth.pdf"
    create_synthetic_pdf(str(pdf_path))
    return str(pdf_path)


def test_scenario_z_active_visual_zoom_loop(synthetic_pdf: str, tmp_path: Path) -> None:
    # 场景 Z（主动视觉闭环）:
    # 1. Seeker 选择第 3 页 (index 2)
    # 2. Inspector 请求对该页右下角执行 zoom
    # 3. Inspector 再次检视 (包含整页 view 和 crop 图)，给出最终答案
    scripted = [
        json.dumps({
            "reason": "Section 3 has table layout",
            "summary": "Table on page 3",
            "choice": [2],
        }),
        json.dumps({
            "action": "zoom",
            "reason": "Text in bottom-right table is unreadable at view resolution",
            "image": 0,
            "bbox_2d": [638, 831, 940, 926],  # 对应右下角小表格
            "target": "item code in the table",
        }),
        json.dumps({
            "action": "answer",
            "reason": "Cropped image clearly displays serial code ZD-7319-QX",
            "answer": "ZD-7319-QX",
            "reference": [0, 1],  # 引用了整页和高清 crop
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        vlm_api_key="mock",
        vlm_model="mock",
        runs_dir=str(tmp_path),
        max_zooms=3,
    )

    result = run(
        question="What is the item code on page 3?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-scenario-z",
    )

    assert result.status == "answered"
    assert result.answer == "ZD-7319-QX"
    assert len(result.crops) == 1
    assert 3 in result.reference_pages

    # 验证第三次调用的输入包含 2 张图，且标签包含 ZOOMED CROP
    third_call_parts = fake_vlm.calls[2]
    image_parts = [p for p in third_call_parts if isinstance(p, ImagePart)]
    text_parts = [p for p in third_call_parts if isinstance(p, TextPart)]
    assert len(image_parts) == 2
    # 查找关于 Image 1 的标签
    crop_label_present = any("Image 1: ZOOMED CROP" in p.text for p in text_parts)
    assert crop_label_present

    # 验证 trace.jsonl 包含 node == "zoom"
    trace_file = tmp_path / "trace-scenario-z" / "trace.jsonl"
    assert trace_file.exists()
    trace_lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line]
    assert any(line["node"] == "zoom" for line in trace_lines)


def test_scenario_s_feedback_reselection(synthetic_pdf: str, tmp_path: Path) -> None:
    # 场景 S（反馈重选）:
    # 1. Seeker 误选了第 1 页 (choice [0])
    # 2. Inspector 判定不够，seek feedback: "need page with table", choice []
    # 3. Seeker 依据反馈，从剩余候选页中选中第 2 页 (index 1)
    # 4. Inspector 回答
    scripted = [
        json.dumps({"reason": "first look", "summary": "page 1", "choice": [0]}),
        json.dumps({
            "action": "seek",
            "reason": "page 1 does not have the table",
            "information": "Need page with data table",
            "choice": [],
        }),
        json.dumps({"reason": "found table with feedback", "summary": "table page", "choice": [1]}),
        json.dumps({
            "action": "answer",
            "reason": "found answer",
            "answer": "OK",
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock", runs_dir=str(tmp_path))

    result = run(
        question="Find table",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-scenario-s",
    )

    assert result.status == "answered"
    # 第二次 Seeker 调用 (索引 2) 的 prompt 必须包含反馈段落
    second_seeker_text = [p.text for p in fake_vlm.calls[2] if isinstance(p, TextPart)][0]
    assert "## Additional Information" in second_seeker_text
    assert "Need page with data table" in second_seeker_text


def test_scenario_p_partial_reference_synthesizer(synthetic_pdf: str, tmp_path: Path) -> None:
    # 场景 P（部分引用）:
    # Seeker 选 [0, 1]，Inspector 在 answer 中仅引用 [0]，触发 synthesizer
    scripted = [
        json.dumps({"reason": "select two pages", "summary": "pages 1 and 2", "choice": [0, 1]}),
        json.dumps({
            "action": "answer",
            "reason": "only page 0 is needed",
            "answer": "Draft Answer",
            "reference": [0],  # 子集引用
        }),
        json.dumps({
            "reason": "synthesizing final answer from page 1",
            "answer": "Final Synthesized Answer",
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock", runs_dir=str(tmp_path))

    result = run(
        question="Summarize",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-scenario-p",
    )

    assert result.status == "synthesized"
    assert result.answer == "Final Synthesized Answer"


def test_scenario_b_zoom_budget_exhausted(synthetic_pdf: str, tmp_path: Path) -> None:
    # 场景 B（预算）:
    # max_zooms=2, Inspector 连续请求 zoom。
    # 达到 2 次后，第 3 次 zoom 请求在校验阶段被拒绝并提示超额，随后返回 answer
    scripted = [
        json.dumps({"reason": "select page", "summary": "page 1", "choice": [0]}),
        # Zoom 1
        json.dumps({
            "action": "zoom",
            "reason": "zoom 1",
            "image": 0,
            "bbox_2d": [100, 100, 200, 200],
            "target": "crop 1",
        }),
        # Zoom 2
        json.dumps({
            "action": "zoom",
            "reason": "zoom 2",
            "image": 0,
            "bbox_2d": [200, 200, 300, 300],
            "target": "crop 2",
        }),
        # Zoom 3 (超额，validate 会抛出错误触发重试)
        json.dumps({
            "action": "zoom",
            "reason": "zoom 3 exceeds",
            "image": 0,
            "bbox_2d": [300, 300, 400, 400],
            "target": "crop 3",
        }),
        # 重试后提供合法 answer
        json.dumps({
            "action": "answer",
            "reason": "stopping zoom and answering",
            "answer": "Budget Answer",
            "reference": [0, 1, 2],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        vlm_api_key="mock",
        vlm_model="mock",
        runs_dir=str(tmp_path),
        max_zooms=2,
    )

    result = run(
        question="Deep zoom",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-scenario-b",
    )

    assert result.status == "answered"
    assert len(result.crops) == 2


def test_scenario_n_no_selection_terminates(synthetic_pdf: str, tmp_path: Path) -> None:
    # 场景 N: Seeker 直接返回 []
    scripted = [
        json.dumps({
            "reason": "None of the pages are relevant to quantum physics",
            "summary": "Irrelevant document",
            "choice": [],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock", runs_dir=str(tmp_path))

    result = run(
        question="What is quantum entanglement?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-scenario-n",
    )

    assert result.status == "no_answer"
    assert result.answer is None
