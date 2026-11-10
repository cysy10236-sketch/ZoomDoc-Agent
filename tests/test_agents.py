from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tests.fake_vlm import FakeVLM
from zoomdoc.agents.inspector import Inspector, InspectorView
from zoomdoc.agents.seeker import Seeker
from zoomdoc.config import Settings
from zoomdoc.errors import AgentOutputError
from zoomdoc.observability import TraceLogger
from zoomdoc.schemas import InspectorAnswer, PageAsset, SeekerOutput


def test_agent_retry_and_correction(tmp_path: Path) -> None:
    # 模拟第 1 次返回乱码，第 2 次返回正确 JSON
    scripted = [
        "Thinking about it... this is not json",
        json.dumps({"reason": "found it", "summary": "it is a ticket", "choice": [0]}),
    ]
    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock")
    logger = TraceLogger(str(tmp_path), "test-trace")

    # 创建一个测试页面
    img = Image.new("RGB", (448, 580), color=(200, 200, 200))
    img_path = tmp_path / "thumb.png"
    img.save(img_path)

    page = PageAsset(
        page=1,
        source_path=str(img_path),
        view_path=str(img_path),
        thumb_path=str(img_path),
        source_size=(1000, 1000),
        view_size=(1024, 1024),
        thumb_size=(448, 580),
    )

    seeker = Seeker(fake_vlm, settings, logger)
    output, selected_pages, resp, attempts = seeker.run(
        question="Where is the train ticket?",
        candidate=[page],
        feedback=None,
        out_dir=str(tmp_path),
    )

    assert attempts == 2
    assert selected_pages == [1]
    assert output.choice == [0]
    # 校验第二次调用包含了纠错对话 (extra_messages)
    assert fake_vlm.extra_messages_history[1] is not None
    assert "Your output was invalid" in fake_vlm.extra_messages_history[1][1]["content"]


def test_agent_max_attempts_exhausted(tmp_path: Path) -> None:
    # 连续 3 次返回非法内容
    scripted = [
        "not json 1",
        "not json 2",
        "not json 3",
    ]
    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock", max_attempts=3)
    logger = TraceLogger(str(tmp_path), "test-trace-fail")

    img = Image.new("RGB", (448, 580), color=(200, 200, 200))
    img_path = tmp_path / "thumb.png"
    img.save(img_path)

    page = PageAsset(
        page=1,
        source_path=str(img_path),
        view_path=str(img_path),
        thumb_path=str(img_path),
        source_size=(1000, 1000),
        view_size=(1024, 1024),
        thumb_size=(448, 580),
    )

    seeker = Seeker(fake_vlm, settings, logger)
    with pytest.raises(AgentOutputError) as exc_info:
        seeker.run(
            question="Find document",
            candidate=[page],
            feedback=None,
            out_dir=str(tmp_path),
        )
    assert exc_info.value.attempts == 3


def test_seeker_out_of_bounds_triggers_retry(tmp_path: Path) -> None:
    # 第 1 次 choice 越界 [99]（候选页只有 1 页），第 2 次合法 [0]
    scripted = [
        json.dumps({"reason": "wrong page", "summary": "none", "choice": [99]}),
        json.dumps({"reason": "right page", "summary": "ok", "choice": [0]}),
    ]
    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock")
    logger = TraceLogger(str(tmp_path), "test-trace-oob")

    img = Image.new("RGB", (448, 580), color=(200, 200, 200))
    img_path = tmp_path / "thumb.png"
    img.save(img_path)

    page = PageAsset(
        page=1,
        source_path=str(img_path),
        view_path=str(img_path),
        thumb_path=str(img_path),
        source_size=(1000, 1000),
        view_size=(1024, 1024),
        thumb_size=(448, 580),
    )

    seeker = Seeker(fake_vlm, settings, logger)
    output, selected_pages, resp, attempts = seeker.run(
        question="Find document",
        candidate=[page],
        feedback=None,
        out_dir=str(tmp_path),
    )
    assert attempts == 2
    assert selected_pages == [1]


def test_inspector_zoom_when_budget_zero_triggers_retry(tmp_path: Path) -> None:
    # zoom_left 为 0 时模型返回 zoom，应在 validate 抛出 ValueError 触发重试，第 2 次返回 answer
    scripted = [
        json.dumps({
            "action": "zoom",
            "reason": "want zoom",
            "image": 0,
            "bbox_2d": [100, 100, 200, 200],
            "target": "text",
        }),
        json.dumps({
            "action": "answer",
            "reason": "enough info",
            "answer": "Beijing",
            "reference": [0],
        }),
    ]
    fake_vlm = FakeVLM(scripted)
    settings = Settings(vlm_api_key="mock", vlm_model="mock")
    logger = TraceLogger(str(tmp_path), "test-trace-zoom-budget")

    img = Image.new("RGB", (1024, 1024), color=(200, 200, 200))
    img_path = tmp_path / "view.png"
    img.save(img_path)

    page = PageAsset(
        page=1,
        source_path=str(img_path),
        view_path=str(img_path),
        thumb_path=str(img_path),
        source_size=(1000, 1000),
        view_size=(1024, 1024),
        thumb_size=(448, 580),
    )

    inspector = Inspector(fake_vlm, settings, logger)
    view = InspectorView(items=[("page", 1)])

    output, resp, attempts = inspector.run(
        question="What city?",
        view=view,
        pages={1: page},
        crops={},
        zoom_left=0,
    )

    assert attempts == 2
    assert isinstance(output, InspectorAnswer)
    assert output.answer == "Beijing"
