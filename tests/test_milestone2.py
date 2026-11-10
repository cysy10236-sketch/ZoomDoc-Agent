from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.make_synthetic_pdf import create_synthetic_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.config import Settings
from zoomdoc.graph.states import EvidenceReport, SupervisorState
from zoomdoc.graph.supervisor import run_m2
from zoomdoc.llm.vlm_client import ImagePart, TextPart
from zoomdoc.schemas import UsageTotals


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> str:
    pdf_path = tmp_path / "test_synth.pdf"
    create_synthetic_pdf(str(pdf_path))
    return str(pdf_path)


def test_a1_context_isolation_and_nested_zoom(synthetic_pdf: str, tmp_path: Path) -> None:
    """A1 隔离主用例（两次裁剪）：

    Seeker [2] -> Inspector zoom (full page) -> Inspector zoom (nested on crop)
    -> Inspector answer (confidence=0.9, ref=[0, 2]).
    """
    scripted = [
        # Call 1: Seeker
        json.dumps({
            "reason": "Section 3 has table layout",
            "summary": "Table on page 3",
            "choice": [2],
        }),
        # Call 2: Inspector round 1 - zoom full page
        json.dumps({
            "action": "zoom",
            "reason": "Text in bottom-right table is unreadable at view resolution",
            "image": 0,
            "bbox_2d": [638, 831, 940, 926],
            "target": "item code in the table",
        }),
        # Call 3: Inspector round 1 - nested zoom on crop 0
        json.dumps({
            "action": "zoom",
            "reason": "Need higher zoom on serial code characters",
            "image": 1,
            "bbox_2d": [100, 100, 900, 900],
            "target": "exact serial characters",
        }),
        # Call 4: Inspector round 1 - answer
        json.dumps({
            "action": "answer",
            "reason": "Cropped image clearly displays serial code ZD-7319-QX",
            "answer": "ZD-7319-QX",
            "confidence": 0.9,
            "reference": [0, 2],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_zooms=3,
        max_sub_steps=6,
        max_inspect_rounds=2,
    )

    result, state = run_m2(
        question="What is the item code on page 3?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m2-a1",
    )

    # 1. 业务正确性断言
    assert result.status == "answered"
    assert result.answer == "ZD-7319-QX"
    assert 3 in result.reference_pages

    # 2. 调用计数与 Token 计量
    assert fake_vlm.call_count == 4
    assert result.usage.calls == 4
    assert result.usage.prompt_tokens == 400
    assert result.usage.completion_tokens == 200

    # 3. 子图证据与 Trace 审计
    assert state.evidence[-1].zoom_count == 2
    trace_file = tmp_path / "trace-m2-a1" / "trace.jsonl"
    assert trace_file.exists()
    trace_lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    zoom_events = [e for e in trace_lines if e.get("node") == "zoom" and e.get("namespace") == "inspector_sub/r1"]
    assert len(zoom_events) == 2
    assert all(e["latency_ms"] >= 0 for e in zoom_events)

    # 4. 子图内部第 4 次 VLM 确实看到了 3 张图 (1 full page + 2 crops)
    fourth_call_parts = fake_vlm.calls[3]
    fourth_image_parts = [p for p in fourth_call_parts if isinstance(p, ImagePart)]
    assert len(fourth_image_parts) == 3

    # 5. 上下文隔离契约断言
    dump = state.model_dump_json()
    assert "data:image" not in dump
    assert "base64," not in dump
    assert "/sub/" not in dump.replace("\\", "/")

    # 磁盘文件持久化检查
    sup_file = tmp_path / "trace-m2-a1" / "supervisor_state.json"
    assert sup_file.exists()
    file_dump = sup_file.read_text(encoding="utf-8")
    assert "data:image" not in file_dump
    assert "base64," not in file_dump
    assert "/sub/" not in file_dump.replace("\\", "/")

    # SupervisorState 字段集必须与子图专用字段完全正交
    forbidden_fields = {"crops", "view", "pending_zoom", "last_output", "sub_step", "zoom_count"}
    assert set(SupervisorState.model_fields.keys()).isdisjoint(forbidden_fields)

    # Crop 坐标以纯数值形式保留在 cited_regions
    assert any(c.page_box is not None for c in result.cited_regions)

    # 6. 主图历史节点拓扑（子图内部步骤绝不进入主图）
    assert [rec.node for rec in state.history] == ["ingest", "seeker", "inspector", "critic"]


def test_a2_low_confidence_reinspect(synthetic_pdf: str, tmp_path: Path) -> None:
    """A2 置信度不足打回重裁：

    Seeker [2] -> 子图 r1: answer confidence=0.3 -> Critic 拒绝 ->
    子图 r2: zoom -> answer confidence=0.85 -> 成功解答。
    """
    scripted = [
        # Call 1: Seeker
        json.dumps({
            "reason": "Page 3 has table",
            "summary": "Table on page 3",
            "choice": [2],
        }),
        # Call 2: Inspector round 1 - low confidence answer
        json.dumps({
            "action": "answer",
            "reason": "Looks like ZD-7319-DX but blurry",
            "answer": "ZD-7319-DX",
            "confidence": 0.3,
            "reference": [0],
        }),
        # Call 3: Inspector round 2 - first view after reinspect feedback
        json.dumps({
            "action": "zoom",
            "reason": "Zooming in due to critic feedback",
            "image": 0,
            "bbox_2d": [638, 831, 940, 926],
            "target": "serial code",
        }),
        # Call 4: Inspector round 2 - confident answer
        json.dumps({
            "action": "answer",
            "reason": "Clear serial code ZD-7319-QX",
            "answer": "ZD-7319-QX",
            "confidence": 0.85,
            "reference": [0, 1],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_inspect_rounds=2,
        critic_min_confidence=0.6,
    )

    result, state = run_m2(
        question="What is the item code?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m2-a2",
    )

    assert state.inspect_rounds == 2
    assert len(state.evidence) == 2

    # r2 第一次 Inspector 调用中只包含 1 张图（r1 的 crop 已在 r1 结束时就地销毁，不进入 r2）
    r2_first_call_parts = fake_vlm.calls[2]
    r2_images = [p for p in r2_first_call_parts if isinstance(p, ImagePart)]
    assert len(r2_images) == 1

    # Critic 反馈包含在 prompt 中
    r2_texts = [p for p in r2_first_call_parts if isinstance(p, TextPart)]
    assert any("confidence=0.30" in p.text for p in r2_texts)

    assert result.status == "answered"
    assert result.answer == "ZD-7319-QX"


def test_a3_subgraph_budget_exhausted(synthetic_pdf: str, tmp_path: Path) -> None:
    """A3 子图步数耗尽强制退出：

    设置 max_sub_steps=3, max_zooms=5, max_inspect_rounds=1。
    Seeker [2] -> zoom -> zoom (sub_step reaches 3).
    """
    scripted = [
        # Call 1: Seeker
        json.dumps({
            "reason": "Page 3",
            "summary": "Page 3",
            "choice": [2],
        }),
        # Call 2: Inspector zoom 1 (sub_step -> 1, zoom -> 2)
        json.dumps({
            "action": "zoom",
            "reason": "Zoom 1",
            "image": 0,
            "bbox_2d": [100, 100, 500, 500],
            "target": "part 1",
        }),
        # Call 3: Inspector zoom 2 (sub_step -> 3 >= max_sub_steps)
        json.dumps({
            "action": "zoom",
            "reason": "Zoom 2",
            "image": 0,
            "bbox_2d": [200, 200, 600, 600],
            "target": "part 2",
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_sub_steps=3,
        max_zooms=5,
        max_inspect_rounds=1,
    )

    result, state = run_m2(
        question="What is the code?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m2-a3",
    )

    assert state.evidence[-1].status == "budget_exhausted"
    assert state.verdict is not None
    assert state.verdict.reason_code == "budget_exhausted"
    assert result.status == "budget_exhausted"
    assert fake_vlm.call_count == 3


def test_a4_seek_routing_back_to_seeker(synthetic_pdf: str, tmp_path: Path) -> None:
    """A4 seek -> 回到 Seeker：

    Seeker [0] (选第 1 页) -> 子图 seek -> Seeker (剩余 3 页) 选 [1] (原第 3 页) -> answer.
    """
    scripted = [
        # Call 1: Seeker chooses page 1 (index 0)
        json.dumps({
            "reason": "Maybe page 1",
            "summary": "Introduction on page 1",
            "choice": [0],
        }),
        # Call 2: Inspector round 1 realizes info is missing, requests more pages
        json.dumps({
            "action": "seek",
            "reason": "Page 1 does not contain table data",
            "information": "look for the table page",
            "choice": [],
        }),
        # Call 3: Seeker round 2 on remaining 3 candidate pages [2, 3, 4]
        # Chooses index 1 (which corresponds to page 3)
        json.dumps({
            "reason": "Image 1 looks like the table page requested in feedback",
            "summary": "Table page found",
            "choice": [1],
        }),
        # Call 4: Inspector round 2 on page 3
        json.dumps({
            "action": "answer",
            "reason": "Found serial code on page 3",
            "answer": "ZD-7319-QX",
            "confidence": 0.9,
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_seek_rounds=2,
        max_inspect_rounds=2,
    )

    result, state = run_m2(
        question="Find the serial code",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m2-a4",
    )

    assert state.seek_rounds == 2
    # Verify second Seeker call candidate count was 3
    seeker_steps = [s for s in state.history if s.node == "seeker"]
    assert len(seeker_steps) == 2
    assert len(seeker_steps[1].input_images) == 3
    assert result.status == "answered"
    assert result.answer == "ZD-7319-QX"


def test_a5_retry_token_accounting(synthetic_pdf: str, tmp_path: Path) -> None:
    """A5 重试 Token 计量（修复 D1 缺陷验证）：

    Inspector 第一次返回无效 JSON，重试后返回有效 JSON，确保每次调用的 Token 均被计入。
    """
    scripted = [
        # Call 1: Seeker
        json.dumps({
            "reason": "Page 3 has table",
            "summary": "Table on page 3",
            "choice": [2],
        }),
        # Call 2: Inspector attempt 1 - INVALID JSON
        "this is not valid json at all",
        # Call 3: Inspector attempt 2 - valid answer
        json.dumps({
            "action": "answer",
            "reason": "Solved after retry",
            "answer": "ZD-7319-QX",
            "confidence": 0.95,
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_attempts=3,
    )

    result, state = run_m2(
        question="What is the code?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m2-a5",
    )

    # 1 seeker call + 2 inspector calls = 3 total VLM calls
    assert fake_vlm.call_count == 3
    assert result.usage.calls == 3
    assert result.usage.prompt_tokens == 300
    assert result.usage.completion_tokens == 150
    assert result.status == "answered"


def test_a6_anti_leak_validation() -> None:
    """A6 防泄漏契约校验：

    直接在 EvidenceReport 中注入图片路径或 Base64 均必须抛 ValidationError。
    """
    base_kwargs = dict(
        round=1,
        status="answered",
        confidence=0.9,
        reference_pages=[1],
        cited_regions=[],
        zoom_count=0,
        sub_steps=1,
        usage=UsageTotals(),
    )

    with pytest.raises(ValidationError, match="image payload leaked"):
        EvidenceReport(**base_kwargs, answer="normal", reason="see runs/x/sub/r1/crops/c0.png")

    with pytest.raises(ValidationError, match="image payload leaked"):
        EvidenceReport(**base_kwargs, answer="data:image/png;base64,AAA", reason="normal")


def test_a7_synthesizer_text_flow(synthetic_pdf: str, tmp_path: Path) -> None:
    """A7 纯文本 Synthesizer 兜底回环测试：

    当步数达到预算且存在 answered 证据时，走向 synthesizer_text_node，仅依赖纯文本证据合成最终答案。
    """
    scripted = [
        # Call 1: Seeker
        json.dumps({
            "reason": "Page 3 has table",
            "summary": "Table on page 3",
            "choice": [2],
        }),
        # Call 2: Inspector answer with moderate confidence
        json.dumps({
            "action": "answer",
            "reason": "Partial view of code",
            "answer": "ZD-7319",
            "confidence": 0.5,
            "reference": [0],
        }),
        # Call 3: Text Synthesizer
        json.dumps({
            "reason": "Synthesized from evidence",
            "answer": "ZD-7319-FINAL",
            "reference": [3],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_inspect_rounds=1,  # Prevent reinspect so R8 routes to synthesizer
        critic_min_confidence=0.6,
    )

    result, state = run_m2(
        question="What is the code?",
        pdf_path=synthetic_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m2-a7",
    )

    assert result.status == "synthesized"
    assert result.answer == "ZD-7319-FINAL"
    # Synthesizer call was purely text
    synth_call = fake_vlm.calls[2]
    assert len([p for p in synth_call if isinstance(p, ImagePart)]) == 0
    assert len([p for p in synth_call if isinstance(p, TextPart)]) == 1
