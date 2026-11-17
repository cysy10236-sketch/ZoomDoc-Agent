from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw

from scripts.make_synthetic_long_pdf import create_synthetic_long_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.cache import ContentAddressedCache
from zoomdoc.config import Settings
from zoomdoc.graph.inspector_subgraph import run_inspector_round
from zoomdoc.graph.navigator_subgraph import run_navigator_round
from zoomdoc.graph.supervisor import run_m5
from zoomdoc.index.tree import build_document_tree, get_leaf_nodes
from zoomdoc.llm.vlm_client import Part, VLMResponse, compute_answer_span_entropy
from zoomdoc.schemas import NormBBox, VLMUsage
from zoomdoc.vision.fovea import (
    build_1d_fovea_map,
    fovea_box_to_page_box,
    fovea_warp_image,
)
from zoomdoc.vision.render import render_pdf_pages
from zoomdoc.vision.residual import (
    locate_target_zoom_box,
    optical_edit_distance,
    verify_and_correct_answer,
)


class FakeVLMWithUsage(FakeVLM):
    """Custom FakeVLM supporting custom VLMUsage per scripted call."""

    def __init__(self, scripted: list[tuple[str, VLMUsage] | str]) -> None:
        self.scripted_items = list(scripted)
        self.calls: list[list[Part]] = []
        self.extra_messages_history: list[list[dict] | None] = []
        self.call_count = 0

    def generate(
        self, parts: list[Part], *, extra_messages: list[dict] | None = None
    ) -> VLMResponse:
        self.calls.append(list(parts))
        self.extra_messages_history.append(list(extra_messages) if extra_messages else None)

        if self.call_count >= len(self.scripted_items):
            raise IndexError(
                f"FakeVLMWithUsage ran out of responses: called {self.call_count + 1} times, "
                f"only {len(self.scripted_items)} scripted."
            )

        item = self.scripted_items[self.call_count]
        self.call_count += 1

        if isinstance(item, tuple):
            text, usage = item
        else:
            text = item
            usage = VLMUsage(
                prompt_tokens=100,
                completion_tokens=50,
                latency_ms=10.0,
                model="fake-vlm-model",
            )
        return VLMResponse(text=text, usage=usage)


def test_m5_t1_anisotropic_topological_foveation_collinearity_and_inverse() -> None:
    """T1: 凹面镜正交共线性与逆映射闭环检验。

    1. 正交共线性：表头列标线在形变图上与焦点区列坐标严格 100% 垂直共线 (|u_hdr - u_focus| == 0)；
    2. 放大率：焦点区像素跨度放大倍率 > 2.0x；
    3. 逆映射：fovea_box_to_page_box 还原原图归一化坐标误差 < 10^-3。
    """
    src_w, src_h = 1200, 1500
    img = Image.new("RGB", (src_w, src_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # 绘制表头垂直列线 x = 600 (红色 3px)
    draw.line([(600, 0), (600, src_h)], fill=(255, 0, 0), width=3)
    # 绘制行名水平线 y = 700 (蓝色 3px)
    draw.line([(0, 700), (src_w, 700)], fill=(0, 0, 255), width=3)

    pixel_box = (500, 600, 700, 800)
    out_size = (1024, 1024)

    warped_img, meta = fovea_warp_image(
        src_img=img,
        pixel_box=pixel_box,
        out_size=out_size,
        header_ratio=0.12,
        stub_ratio=0.15,
        focus_share=0.65,
        anchor_share=0.15,
        draw_seam=False,
    )

    # 1. 焦点区域放大倍率断言
    # 原图 focus 宽度 = 200 / 1200 = 16.67%
    u_f1 = float(np.interp(500, meta["x_knots"], meta["u_knots"]))
    u_f2 = float(np.interp(700, meta["x_knots"], meta["u_knots"]))
    out_focus_w = u_f2 - u_f1
    amplification = (out_focus_w / out_size[0]) / (200.0 / src_w)
    assert amplification > 2.0, f"Amplification {amplification:.2f} <= 2.0"

    # 2. 正交共线性数学与物理像素断言
    # 理论解析映射值
    u_col_math = float(np.interp(600, meta["x_knots"], meta["u_knots"]))
    warped_arr = np.asarray(warped_img)

    # 物理像素检索：在表头行 (y=40) 寻找红色像素峰值所在列
    row_hdr = warped_arr[40, :, :]
    col_hdr_idx = int(np.argmax(row_hdr[:, 0] - row_hdr[:, 1]))

    # 在焦点行 (y=512) 寻找红色像素峰值所在列
    row_focus = warped_arr[512, :, :]
    col_focus_idx = int(np.argmax(row_focus[:, 0] - row_focus[:, 1]))

    assert abs(col_hdr_idx - col_focus_idx) == 0, (
        f"Collinearity broken: col_hdr={col_hdr_idx} vs col_focus={col_focus_idx}"
    )
    assert abs(col_hdr_idx - round(u_col_math)) <= 5

    # 3. 逆映射精确闭环断言
    v_f1 = float(np.interp(600, meta["y_knots"], meta["v_knots"]))
    v_f2 = float(np.interp(800, meta["y_knots"], meta["v_knots"]))

    local_box = NormBBox(
        x1=u_f1 / out_size[0],
        y1=v_f1 / out_size[1],
        x2=u_f2 / out_size[0],
        y2=v_f2 / out_size[1],
    )
    recovered = fovea_box_to_page_box(
        local_box=local_box,
        warp_meta=meta,
        src_size=(src_w, src_h),
        out_size=out_size,
    )

    expected_norm = NormBBox(
        x1=500.0 / src_w,
        y1=600.0 / src_h,
        x2=700.0 / src_w,
        y2=800.0 / src_h,
    )
    assert abs(recovered.x1 - expected_norm.x1) < 1e-3
    assert abs(recovered.y1 - expected_norm.y1) < 1e-3
    assert abs(recovered.x2 - expected_norm.x2) < 1e-3
    assert abs(recovered.y2 - expected_norm.y2) < 1e-3

    # 4. 极端参数下节点严格单调递增与近边缘锚定缩放契约检查
    _, u_extreme, x_extreme = build_1d_fovea_map(
        src_len=1000,
        out_len=1024,
        focus_start=400,
        focus_end=600,
        anchor_end=150,
        focus_share=0.85,
        anchor_share=0.25,
    )
    assert all(u_extreme[i] < u_extreme[i + 1] for i in range(len(u_extreme) - 1))
    assert all(x_extreme[i] < x_extreme[i + 1] for i in range(len(x_extreme) - 1))
    assert u_extreme[-1] == 1024.0 and x_extreme[-1] == 1000.0

    _, u_edge, _ = build_1d_fovea_map(
        src_len=1000,
        out_len=1000,
        focus_start=200,
        focus_end=400,
        anchor_end=5,
        focus_share=0.65,
        anchor_share=0.15,
    )
    assert all(u_edge[i] < u_edge[i + 1] for i in range(len(u_edge) - 1))
    assert u_edge[1] <= 20  # (5/1000)*1.5*1000 = 7.5px, 不再强占 15% 画布


def test_m5_t2_token_logprob_shannon_entropy_and_sboed_gate(tmp_path: Path) -> None:
    """T2: Token 香农熵与 S-BOED 强制聚焦门禁检验。

    1. 纯函数 compute_answer_span_entropy 计算低熵与高熵混淆序列；
    2. 子图内前瞻门禁：高言语置信度但高熵/字母数字编码未放大盲猜被拒绝，强制触发 1 次 fovea 放大。
    """
    # 1. 纯函数测试
    low_ent_items = [
        {
            "token": "ZD-",
            "logprob": -0.001,
            "top_logprobs": [{"token": "ZD-", "logprob": -0.001}, {"token": "XX-", "logprob": -10.0}],
        },
        {
            "token": "2664",
            "logprob": -0.001,
            "top_logprobs": [{"token": "2664", "logprob": -0.001}, {"token": "9999", "logprob": -10.0}],
        },
    ]
    raw_text_low = '{"action": "answer", "reason": "ok", "answer": "ZD-2664", "reference": [0]}'
    max_e_low, _ = compute_answer_span_entropy(raw_text_low, low_ent_items)
    assert max_e_low < 0.05

    high_ent_items = [
        {
            "token": '{"action": "answer", "reason": "ok", "answer": "ZD-2664-',
            "logprob": -0.01,
            "top_logprobs": [{"token": "foo", "logprob": -0.01}],
        },
        {
            "token": "0",
            "logprob": -0.7,
            "top_logprobs": [{"token": "0", "logprob": -0.7}, {"token": "D", "logprob": -0.7}],
        },
        {
            "token": 'X", "reference": [0], "confidence": 0.95}',
            "logprob": -0.01,
            "top_logprobs": [{"token": "bar", "logprob": -0.01}],
        },
    ]
    raw_text_high = '{"action": "answer", "reason": "ok", "answer": "ZD-2664-0X", "reference": [0], "confidence": 0.95}'
    max_e_high, _ = compute_answer_span_entropy(raw_text_high, high_ent_items)
    assert max_e_high > 0.80

    # 带前置思维链（Thinking Stream）与 None logprob 的偏移对齐验证：
    # 前置推理段高熵不能污染答案区间 ("ZD-2664-DX") 的低熵度量
    thinking_items = [
        {
            "token": "Let me inspect the page carefully... ",
            "logprob": -0.7,
            "top_logprobs": [{"token": "Let", "logprob": -0.7}, {"token": "Check", "logprob": -0.7}],
        },
        {
            "token": '{"action": "answer", "reason": "confirmed", "answer": "',
            "logprob": -0.001,
            "top_logprobs": [{"token": '{"action"', "logprob": -0.001}],
        },
        {
            "token": "ZD-2664-DX",
            "logprob": -0.001,
            "top_logprobs": [
                {"token": "ZD-2664-DX", "logprob": -0.001},
                {"token": "ZD-2664-0X", "logprob": -10.0},
            ],
        },
        {
            "token": '", "reference": [0], "confidence": 0.99}',
            "logprob": -0.001,
            "top_logprobs": None,
        },
    ]
    raw_text_think = '{"action": "answer", "reason": "confirmed", "answer": "ZD-2664-DX", "reference": [0], "confidence": 0.99}'
    max_e_think, _ = compute_answer_span_entropy(raw_text_think, thinking_items)
    assert max_e_think < 0.05

    # 2. S-BOED 子图内门禁测试
    pdf_path = tmp_path / "sboed_synth.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595.3, height=841.9)
    page.insert_text(fitz.Point(100, 200), "Header: Table of Components")
    page.insert_text(fitz.Point(100, 300), "Target Row: ZD-2664-DX with 4pt text")
    doc.save(str(pdf_path))
    doc.close()

    settings = Settings(
        enable_sboed_gate=True,
        force_zoom_on_alnum_code=True,
        entropy_zoom_threshold=0.15,
        zoom_mode="fovea",
        runs_dir=str(tmp_path),
    )
    pages = render_pdf_pages(str(pdf_path), [1], str(tmp_path), settings)

    # Call 1: 盲猜并给出高置信度 0.95，但带有高熵 0.92
    c1_usage = VLMUsage(
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=10.0,
        model="fake",
        max_token_entropy=0.92,
    )
    c1_text = '{"action": "answer", "reason": "first pass", "answer": "ZD-2664-0X", "reference": [0], "confidence": 0.95}'

    # Call 2: 放大后确认正确答案
    c2_usage = VLMUsage(
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=10.0,
        model="fake",
        max_token_entropy=0.01,
    )
    c2_text = '{"action": "answer", "reason": "foveated view confirms DX", "answer": "ZD-2664-DX", "reference": [1], "confidence": 0.99}'

    vlm = FakeVLMWithUsage([(c1_text, c1_usage), (c2_text, c2_usage)])
    from zoomdoc.observability import TraceLogger

    logger = TraceLogger(str(tmp_path), "trace_t2")

    report = run_inspector_round(
        question="What is the component code?",
        round_=1,
        pages=pages,
        feedback=None,
        run_dir=str(tmp_path),
        trace_id="trace_t2",
        vlm=vlm,
        settings=settings,
        logger=logger,
        pdf_path=str(pdf_path),
    )

    assert report.zoom_count == 1
    assert report.status == "answered"
    assert report.answer == "ZD-2664-DX"


def test_m5_t3_geometric_cross_modal_residual_verification(tmp_path: Path) -> None:
    """T3: M4 实测 5 类光学形近错例 100% 残差纠偏与负例防误纠检验。"""
    pdf_path = tmp_path / "gold_catalog.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595.3, height=841.9)
    gold_codes = [
        "ZD-2664-DX",
        "ZD-9841-KY",
        "ZD-1458-BV",
        "ZD-6269-BV",
        "ZD-1763-BV",
    ]
    for i, code in enumerate(gold_codes):
        page.insert_text(fitz.Point(100, 100 + i * 50), f"Item {i+1}: Serial {code}")
    doc.save(str(pdf_path))
    doc.close()

    bad_cases_m4 = [
        ("The serial number is ZD-2664-0X.", "The serial number is ZD-2664-DX."),
        ("Component code ZD-8841-KY.", "Component code ZD-9841-KY."),
        ("Unit 2D-1458-BV inspected.", "Unit ZD-1458-BV inspected."),
        ("Sensor ZO-6269-BV active.", "Sensor ZD-6269-BV active."),
        ("Battery pack ZD-1163-BV.", "Battery pack ZD-1763-BV."),
    ]

    for erroneous_text, expected_corrected_text in bad_cases_m4:
        corrected_ans, is_corr = verify_and_correct_answer(
            answer=erroneous_text,
            pdf_path=str(pdf_path),
            reference_pages=[1],
            cited_regions=[],
            max_optical_distance=1.5,
        )
        assert is_corr is True
        assert corrected_ans == expected_corrected_text

    # 负例测试：距离大于 1.5 的非相关编码不应被篡改
    unrelated_text = "Unrelated model ZD-9999-ZZ."
    corrected_neg, is_corr_neg = verify_and_correct_answer(
        answer=unrelated_text,
        pdf_path=str(pdf_path),
        reference_pages=[1],
        cited_regions=[],
        max_optical_distance=1.5,
    )
    assert is_corr_neg is False
    assert corrected_neg == unrelated_text


def test_m5_t4_adaptive_tree_flattening_single_hop(tmp_path: Path) -> None:
    """T4: 自适应单步目录树展平检验（节点数 <= 40 时 1 步直达目标叶子）。"""
    pdf_path = tmp_path / "synth_60p.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=60,
        gold_page=45,
        gold_code="ZD-4500-FLAT",
    )
    tree = build_document_tree(str(pdf_path), max_leaf_pages=20)
    assert len(tree.roots) > 0

    from zoomdoc.observability import TraceLogger

    # 1. 对照组：关闭 tree flattening，必须经历 expand -> select 两步
    target_root = next(r for r in tree.roots if r.start_page <= 45 <= r.end_page)
    assert len(target_root.children) > 0
    leaf_nodes = get_leaf_nodes(tree)
    target_leaf = next(l for l in leaf_nodes if l.start_page <= 45 <= l.end_page)

    settings_hier = Settings(
        enable_tree_flattening=False,
        tree_flatten_max_nodes=40,
        runs_dir=str(tmp_path),
    )
    scripted_hier = [
        f'{{"action": "expand", "reason": "Drill down into Part 2", "node_ids": ["{target_root.node_id}"]}}',
        f'{{"action": "select", "reason": "Select leaf section 2.2", "node_ids": ["{target_leaf.node_id}"]}}',
    ]
    vlm_hier = FakeVLM(scripted_hier)
    logger_hier = TraceLogger(str(tmp_path), "trace_hier")

    rep_hier = run_navigator_round(
        question="Find component code on page 45",
        nav_round=1,
        doc_tree=tree,
        visited_node_ids=[],
        feedback=None,
        trace_id="trace_hier",
        vlm=vlm_hier,
        settings=settings_hier,
        logger=logger_hier,
    )
    assert rep_hier.status == "located"
    assert rep_hier.sub_steps == 2

    # 2. 实验组：开启 tree flattening，单步直达目标叶子
    settings_flat = Settings(
        enable_tree_flattening=True,
        tree_flatten_max_nodes=40,
        runs_dir=str(tmp_path),
    )
    # 单步直选响应
    scripted_flat = [
        f'{{"action": "select", "reason": "Flattened tree allows 1-step direct leaf selection", "node_ids": ["{target_leaf.node_id}"]}}'
    ]
    vlm_flat = FakeVLM(scripted_flat)
    logger_flat = TraceLogger(str(tmp_path), "trace_flat")

    rep_flat = run_navigator_round(
        question="Find component code on page 45",
        nav_round=1,
        doc_tree=tree,
        visited_node_ids=[],
        feedback=None,
        trace_id="trace_flat",
        vlm=vlm_flat,
        settings=settings_flat,
        logger=logger_flat,
    )

    assert rep_flat.status == "located"
    assert rep_flat.sub_steps == 1
    assert len(rep_flat.candidate_pages) > 0
    assert rep_flat.candidate_pages == rep_hier.candidate_pages


def test_m5_t5_content_addressed_cache_hit_and_speedup(tmp_path: Path) -> None:
    """T5: 内容寻址持久化缓存命中与耗时下降 > 80% 检验。"""
    pdf_path = tmp_path / "cache_test.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=30,
        gold_page=15,
        gold_code="ZD-1500-CACHE",
    )

    cache_dir = tmp_path / "content_cache"
    settings = Settings(
        enable_content_cache=True,
        cache_dir=str(cache_dir),
        runs_dir=str(tmp_path),
    )

    tree5 = build_document_tree(str(pdf_path), max_leaf_pages=20)
    leaf5 = next(l for l in get_leaf_nodes(tree5) if l.start_page <= 15 <= l.end_page)
    page_15_idx = 15 - leaf5.start_page
    scripted = [
        f'{{"action": "select", "reason": "Select section", "node_ids": ["{leaf5.node_id}"]}}',
        f'{{"reason": "Find page 15", "summary": "thumbnails", "choice": [{page_15_idx}]}}',
        '{"action": "answer", "reason": "first pass", "answer": "ZD-1500-CACHE", "reference": [0], "confidence": 0.95}',
        '{"action": "answer", "reason": "done", "answer": "ZD-1500-CACHE", "reference": [1], "confidence": 0.99}',
    ]

    # Run 1: 首次运行，缓存未命中
    vlm1 = FakeVLM(scripted)
    res1, state1 = run_m5(
        question="Find code in section 1",
        pdf_path=str(pdf_path),
        vlm=vlm1,
        settings=settings,
        trace_id="run1_cache",
    )
    assert res1.cache_hit is False
    total_latency_1 = res1.tree_latency_ms + res1.render_latency_ms

    # Run 2: 同一份文档不同 trace_id 再次运行，命中缓存
    vlm2 = FakeVLM(scripted)
    res2, state2 = run_m5(
        question="Find code in section 1",
        pdf_path=str(pdf_path),
        vlm=vlm2,
        settings=settings,
        trace_id="run2_cache",
    )
    assert res2.cache_hit is True
    total_latency_2 = res2.tree_latency_ms + res2.render_latency_ms

    # 缓存命中后渲染与建树开销大幅下降 (>80%)
    if total_latency_1 > 5.0:
        speedup_ratio = (total_latency_1 - total_latency_2) / total_latency_1
        assert speedup_ratio > 0.80, f"Speedup ratio {speedup_ratio:.2%} <= 80%"


def test_m5_t6_end_to_end_run_m5_and_zero_regression(tmp_path: Path) -> None:
    """T6: run_m5 端到端闭环、全状态零图像泄漏与残差统计检验。"""
    pdf_path = tmp_path / "e2e_m5.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=40,
        gold_page=22,
        gold_code="ZD-2200-E2E",
    )

    tree6 = build_document_tree(str(pdf_path), max_leaf_pages=20)
    leaf6 = next(l for l in get_leaf_nodes(tree6) if l.start_page <= 22 <= l.end_page)
    page_22_idx = 22 - leaf6.start_page

    # 模拟 VLM:
    # 1. Navigator 1-step
    # 2. Seeker 选择第 1 张缩略图
    # 3. Inspector 首次返回带光学形近错读 "ZD-2200-E28" 与高熵
    # 4. S-BOED 强制触发 1 次 foveate zoom
    # 5. Inspector 第二次确认但仍有微小单字符混淆
    # 6. pack_report 通过矢量残差纠偏纠正为金标 "ZD-2200-E2E"
    scripted_items: list[tuple[str, VLMUsage] | str] = [
        f'{{"action": "select", "reason": "Target section located", "node_ids": ["{leaf6.node_id}"]}}',
        f'{{"reason": "Page selected", "summary": "thumbnails", "choice": [{page_22_idx}]}}',
        (
            '{"action": "answer", "reason": "first look", "answer": "ZD-2200-E28", "reference": [0], "confidence": 0.90}',
            VLMUsage(prompt_tokens=100, completion_tokens=50, latency_ms=10.0, model="fake", max_token_entropy=0.88),
        ),
        (
            '{"action": "answer", "reason": "foveated view", "answer": "ZD-2200-E28", "reference": [1], "confidence": 0.98}',
            VLMUsage(prompt_tokens=100, completion_tokens=50, latency_ms=10.0, model="fake", max_token_entropy=0.02),
        ),
    ]

    vlm = FakeVLMWithUsage(scripted_items)
    settings = Settings(
        cache_dir=str(tmp_path / "cache"),
        runs_dir=str(tmp_path),
    )

    res, final_state = run_m5(
        question="Find gold code on page 22",
        pdf_path=str(pdf_path),
        vlm=vlm,
        settings=settings,
        trace_id="trace_e2e_m5",
    )

    assert res.status == "answered"
    assert res.answer == "ZD-2200-E2E"  # 残差纠偏成功修正 8 -> E (or exact gold code)
    assert res.residual_corrections >= 1
    assert res.zoom_count >= 1
    assert res.total_wall_ms > 0.0
    assert final_state.result is not None
    assert final_state.result.total_wall_ms == res.total_wall_ms

    # 验证 supervisor_state.json 严格杜绝图像信息泄漏且同步了真实 total_wall_ms
    sup_state_path = Path(final_state.run_dir) / "supervisor_state.json"
    assert sup_state_path.is_file()
    with open(sup_state_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "data:image" not in content
    assert "base64," not in content
    sup_data = json.loads(content)
    assert sup_data["result"]["total_wall_ms"] == res.total_wall_ms

    # 验证 run_m5 尊重调用方显式传入的 Settings 字段（例如关闭缓存）
    settings_no_cache = Settings(
        enable_content_cache=False,
        cache_dir=str(tmp_path / "cache"),
        runs_dir=str(tmp_path),
    )
    res_no_cache, _ = run_m5(
        question="Find gold code on page 22",
        pdf_path=str(pdf_path),
        vlm=FakeVLMWithUsage(scripted_items),
        settings=settings_no_cache,
        trace_id="trace_e2e_m5_nocache",
    )
    assert res_no_cache.cache_hit is False
    assert res_no_cache.status == "answered"


def test_m5_locate_target_zoom_box_optical_misspell_resilience(tmp_path: Path) -> None:
    """Audit P0 验证：当 draft_answer 发生单字符光学误读（如 ZD-9841-AY vs 页内真值 ZD-9841-KY）时，

    locate_target_zoom_box 必须通过 Tier 1 光学形近词元匹配精准命中底部表格，
    绝对禁止退化误裁页面顶部的章节标题栏。
    """
    pdf_path = tmp_path / "misspell_test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595.3, height=841.9)
    # 顶部章节标题与回溯提示 (y = 50..120)
    page.insert_text(fitz.Point(50, 60), "1.2 Risk Factors & Statutory Tax Disclosures", fontsize=14)
    page.insert_text(fitz.Point(50, 90), "[RECLASSIFICATION NOTICE] Note: Statutory tax reconciliation table relocated", fontsize=10)

    # 底部数据表格 (y = 700..760)
    page.insert_text(fitz.Point(100, 720), "Table: Statutory Tax Reconciliation", fontsize=11)
    page.insert_text(fitz.Point(100, 745), "Security ID: ZD-9841-KY   Validation: CONFIRMED", fontsize=9)
    doc.save(str(pdf_path))
    doc.close()

    # 1. 模拟 VLM 首轮输出带形近错字 "ZD-9841-AY" (K 误认为 A)
    box_approx = locate_target_zoom_box(
        pdf_path=str(pdf_path),
        page_num=1,
        question="Refer to Risk Disclosures: What is the exact Security ID in the statutory tax reconciliation table?",
        draft_answer="ZD-9841-AY",
    )
    # 断言：必须精准定位到底部表格区域 (y1 > 0.60)，绝不能命中顶部 y < 0.20
    assert box_approx.y1 > 0.60, f"Expected box in bottom table (y1 > 0.60), got y1={box_approx.y1}"
    assert box_approx.y2 <= 1.0

    # 2. 模拟 draft_answer 为 None 时，Tier 2 表格锚点（Security, Validation）仍正确定位到底部表格
    box_anchor = locate_target_zoom_box(
        pdf_path=str(pdf_path),
        page_num=1,
        question="Find the Security ID in the table",
        draft_answer=None,
    )
    assert box_anchor.y1 > 0.60, f"Expected table anchor in bottom table, got y1={box_anchor.y1}"


def test_m5_fovea_crop_on_crop_nonlinear_inversion(tmp_path: Path) -> None:
    """Audit P1 验证：在 zoom_mode == 'fovea' 下，二次放大（Crop-on-Crop）

    必须由 zoom_node 通过 parent_crop.warp_meta 进行分段非线性逆映射，精确还原全页归一化坐标并产出二阶 CropAsset。
    """
    src_w, src_h = 1000, 1000
    img = Image.new("RGB", (src_w, src_h), color=(255, 255, 255))
    src_path = tmp_path / "parent_source.png"
    img.save(str(src_path))

    from zoomdoc.agents.inspector import InspectorView
    from zoomdoc.graph.inspector_subgraph import zoom_node
    from zoomdoc.graph.states import InspectorSubState
    from zoomdoc.observability import TraceLogger
    from zoomdoc.schemas import CropAsset, InspectorZoom, PageAsset
    from zoomdoc.vision.fovea import (
        compute_fovea_warp_meta,
        fovea_box_to_page_box,
        fovea_warp_image,
    )
    from zoomdoc.vision.geometry import crop_box_to_page_box

    page_asset = PageAsset(
        page=1,
        source_path=str(src_path),
        view_path=str(src_path),
        thumb_path=str(src_path),
        source_size=(src_w, src_h),
        view_size=(src_w, src_h),
        thumb_size=(200, 200),
    )

    pixel_box = (400, 600, 600, 800)
    warped_parent, meta = fovea_warp_image(
        src_img=img,
        pixel_box=pixel_box,
        out_size=(1024, 1024),
        draw_seam=False,
    )
    parent_crop_path = tmp_path / "parent_fovea.png"
    warped_parent.save(str(parent_crop_path))

    parent_crop = CropAsset(
        crop_id="r1c0",
        page=1,
        page_box=NormBBox(x1=0.4, y1=0.6, x2=0.6, y2=0.8),
        requested_box=NormBBox(x1=0.4, y1=0.6, x2=0.6, y2=0.8),
        path=str(parent_crop_path),
        size=(1024, 1024),
        target="table",
        pixel_box=pixel_box,
        warp_meta=meta,
    )

    # 局部选框（相对于 1024x1024 凹面镜图的中心微小区域）
    local_box = NormBBox(x1=0.48, y1=0.48, x2=0.52, y2=0.52)
    expected_page_box = fovea_box_to_page_box(
        local_box=local_box,
        warp_meta=parent_crop.warp_meta,
        src_size=page_asset.source_size,
        out_size=parent_crop.size,
    )
    naive_linear_box = crop_box_to_page_box(local_box, parent_crop.page_box)

    # 直接调用 zoom_node 执行凹面镜二次放大
    settings = Settings(
        zoom_mode="fovea",
        bbox_format="xyxy_1000",
        zoom_pad_ratio=0.0,
        zoom_min_norm_side=0.01,
    )
    logger = TraceLogger(runs_dir=str(tmp_path / "runs"), trace_id="test_crop_on_crop")
    sub_state = InspectorSubState(
        trace_id="test_crop_on_crop",
        question="Read cell value",
        round=1,
        sub_dir=str(tmp_path / "sub"),
        pages={1: page_asset},
        crops={"r1c0": parent_crop},
        view=InspectorView(items=[("page", 1), ("crop", "r1c0")]),
        pending_zoom=InspectorZoom(
            action="zoom",
            reason="crop on foveated crop",
            image=1,
            bbox_2d=(480.0, 480.0, 520.0, 520.0),
            target="micro cell",
        ),
        sub_step=1,
        zoom_count=1,
        max_zooms=3,
        max_sub_steps=6,
    )

    out = zoom_node(sub_state, settings, logger)
    assert out["zoom_count"] == 2
    assert out["pending_zoom"] is None
    assert "r1c1" in out["crops"]
    child_crop = out["crops"]["r1c1"]

    # 验证 zoom_node 输出的 requested_box 严格等于分段非线性逆映射结果，而非退化线性映射
    assert abs(child_crop.requested_box.x1 - expected_page_box.x1) < 1e-6
    assert abs(child_crop.requested_box.y1 - expected_page_box.y1) < 1e-6
    assert abs(child_crop.requested_box.x2 - expected_page_box.x2) < 1e-6
    assert abs(child_crop.requested_box.y2 - expected_page_box.y2) < 1e-6
    assert abs(child_crop.requested_box.x1 - naive_linear_box.x1) > 0.01
    assert abs(child_crop.requested_box.y1 - naive_linear_box.y1) > 0.02

    # 验证逆映射出的 requested_box 与 pixel_box 落在父焦点区 [0.4, 0.6] x [0.6, 0.8] / [400, 600] x [600, 800] 内部
    assert 0.40 <= child_crop.requested_box.x1 < child_crop.requested_box.x2 <= 0.60
    assert 0.60 <= child_crop.requested_box.y1 < child_crop.requested_box.y2 <= 0.80
    assert 400 <= child_crop.pixel_box[0] < child_crop.pixel_box[2] <= 600
    assert 600 <= child_crop.pixel_box[1] < child_crop.pixel_box[3] <= 800
    assert child_crop.warp_meta is not None
    assert compute_fovea_warp_meta(src_size=(src_w, src_h), pixel_box=pixel_box, out_size=(1024, 1024)) == meta


def test_m5_r1_build_view_items_preserves_active_zoom_crops_on_page_overflow() -> None:
    """R1: 当选中页数 >= max_inspector_images 时，build_view_items 必须保留最新放大图及其父页。"""
    from zoomdoc.agents.inspector import Inspector
    from zoomdoc.schemas import CropAsset, PageAsset

    pages = [
        PageAsset(
            page=p,
            source_path=f"p{p}.png",
            view_path=f"p{p}.png",
            thumb_path=f"p{p}.png",
            source_size=(1000, 1000),
            view_size=(1000, 1000),
            thumb_size=(400, 400),
        )
        for p in range(68, 80)  # 12 pages
    ]
    crop_p70 = CropAsset(
        crop_id="r1c0",
        page=70,
        page_box=NormBBox(x1=0.5, y1=0.7, x2=0.8, y2=0.9),
        requested_box=NormBBox(x1=0.5, y1=0.7, x2=0.8, y2=0.9),
        path="r1c0.png",
        size=(1024, 1024),
        target="table cell",
        pixel_box=(500, 700, 800, 900),
    )

    view = Inspector.build_view_items(pages, [crop_p70], max_inspector_images=6)
    assert len(view.items) == 6
    assert ("crop", "r1c0") in view.items
    assert ("page", 70) in view.items

    # 当单页裁剪数超过 max_limit - 1 时，淘汰最早裁剪图并保留至少 1 张全页与最新裁剪图
    many_crops = [
        CropAsset(
            crop_id=f"c{i}",
            page=68,
            page_box=NormBBox(x1=0.1, y1=0.1, x2=0.3, y2=0.3),
            requested_box=NormBBox(x1=0.1, y1=0.1, x2=0.3, y2=0.3),
            path=f"c{i}.png",
            size=(512, 512),
            target=f"t{i}",
            pixel_box=(100, 100, 300, 300),
        )
        for i in range(6)
    ]
    view_many = Inspector.build_view_items([pages[0]], many_crops, max_inspector_images=6)
    assert len(view_many.items) == 6
    assert view_many.items[0] == ("page", 68)
    assert ("crop", "c0") not in view_many.items
    assert view_many.items[-1] == ("crop", "c5")


def test_m5_r2_seeker_overflow_pages_queued_in_candidate_pages(tmp_path: Path) -> None:
    """R2: Seeker 选页超过 max_inspector_images 时，溢出页保留在 candidate_pages 头部供后续 seek 巡检。"""
    from zoomdoc.graph.supervisor import run_m2

    pdf_path = tmp_path / "overflow_10p.pdf"
    doc = fitz.open()
    for p_idx in range(10):
        page = doc.new_page(width=595.3, height=841.9)
        page.insert_text(fitz.Point(50, 60), f"Page {p_idx + 1}", fontsize=14)
        if p_idx + 1 == 8:
            page.insert_text(fitz.Point(380, 700), "ZD-8080-RT", fontsize=5)
    doc.save(str(pdf_path))
    doc.close()

    scripted = [
        # Round 1 Seeker: selects 8 pages [1..8] (indices 0..7); active batch is [1..6], overflow [7, 8] stays in candidates
        json.dumps({"reason": "broad selection", "summary": "many pages", "choice": [0, 1, 2, 3, 4, 5, 6, 7]}),
        # Round 1 Inspector on [1..6]: target table missing -> seek
        json.dumps({"action": "seek", "reason": "not in pages 1-6", "information": "check remaining pages", "choice": []}),
        # Round 2 Seeker on remaining [7, 8, 9, 10]: page 8 is index 1
        json.dumps({"reason": "page 8 has table", "summary": "found on index 1", "choice": [1]}),
        # Round 2 Inspector on [8]: answers gold code
        json.dumps({"action": "answer", "reason": "found on page 8", "answer": "ZD-8080-RT", "confidence": 0.95, "reference": [0]}),
    ]
    vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_inspector_images=6,
        max_seek_rounds=2,
        max_inspect_rounds=2,
    )
    res, state = run_m2(
        question="What is the code on page 8?",
        pdf_path=str(pdf_path),
        vlm=vlm,
        settings=settings,
        trace_id="trace_r2_overflow",
    )
    assert res.status == "answered"
    assert res.answer == "ZD-8080-RT"
    assert res.reference_pages == [8]
    assert 8 in state.all_selected_pages


def test_m5_r3_logprobs_scoped_strictly_to_inspector(tmp_path: Path) -> None:
    """R3: OpenAICompatVLM 仅在 agent == 'inspector' 时请求 logprobs，杜绝上游节点扰动。"""
    from zoomdoc.llm.vlm_client import OpenAICompatVLM

    class RecordingOpenAIVLM(OpenAICompatVLM):
        def __init__(self, settings: Settings, scripted: list[str]) -> None:
            self.settings = settings
            self.scripted = list(scripted)
            self.idx = 0
            self.recorded_request_logprobs: list[bool | None] = []

        def generate(
            self,
            parts: list[Part],
            *,
            extra_messages: list[dict] | None = None,
            request_logprobs: bool | None = None,
        ) -> VLMResponse:
            self.recorded_request_logprobs.append(request_logprobs)
            txt = self.scripted[self.idx]
            self.idx += 1
            return VLMResponse(
                text=txt,
                usage=VLMUsage(
                    prompt_tokens=80,
                    completion_tokens=20,
                    latency_ms=5.0,
                    model="mock-openai-vlm",
                    max_token_entropy=0.9 if request_logprobs else None,
                ),
            )

    pdf_path = tmp_path / "logprob_scope_30p.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=30,
        gold_page=15,
        gold_code="ZD-1515-LP",
    )
    tree = build_document_tree(str(pdf_path), max_leaf_pages=20)
    leaf = next(l for l in get_leaf_nodes(tree) if l.start_page <= 15 <= l.end_page)
    p15_idx = 15 - leaf.start_page

    scripted = [
        json.dumps({"action": "select", "reason": "leaf", "node_ids": [leaf.node_id]}),
        json.dumps({"reason": "thumb", "summary": "s", "choice": [p15_idx]}),
        json.dumps({"action": "answer", "reason": "r1", "answer": "ZD-1515-LP", "confidence": 0.9, "reference": [0]}),
        json.dumps({"action": "answer", "reason": "r2", "answer": "ZD-1515-LP", "confidence": 0.99, "reference": [0, 1]}),
    ]
    settings = Settings(runs_dir=str(tmp_path), cache_dir=str(tmp_path / "cache"))
    rec_vlm = RecordingOpenAIVLM(settings, scripted)

    res, _ = run_m5(
        question="Find code",
        pdf_path=str(pdf_path),
        vlm=rec_vlm,
        settings=settings,
        trace_id="trace_r3_lp",
    )
    assert res.status == "answered"
    # Call order: navigator (False), seeker (False), inspector step 1 (True), inspector step 2 (True)
    assert rec_vlm.recorded_request_logprobs == [False, False, True, True]


def test_m5_r4_sboed_neutral_target_and_fovea_crop_label(tmp_path: Path) -> None:
    """R4: S-BOED 强制聚焦不得将未验证低清盲猜写入 crop target，且凹面镜标签包含共线表头说明。"""
    from zoomdoc.llm.vlm_client import TextPart
    from zoomdoc.observability import TraceLogger

    pdf_path = tmp_path / "debiased_sboed.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595.3, height=841.9)
    page.insert_text(fitz.Point(100, 700), "Security ID: ZD-6269-BV   Validation: CONFIRMED")
    doc.save(str(pdf_path))
    doc.close()

    settings = Settings(
        enable_sboed_gate=True,
        force_zoom_on_alnum_code=True,
        zoom_mode="fovea",
        runs_dir=str(tmp_path),
    )
    pages = render_pdf_pages(str(pdf_path), [1], str(tmp_path), settings)
    vlm = FakeVLMWithUsage([
        (
            '{"action": "answer", "reason": "blurry draft", "answer": "ZD-6209-BV", "reference": [0], "confidence": 0.95}',
            VLMUsage(prompt_tokens=100, completion_tokens=30, latency_ms=5.0, model="fake", max_token_entropy=0.5),
        ),
        (
            '{"action": "answer", "reason": "verified on fovea", "answer": "ZD-6269-BV", "reference": [0, 1], "confidence": 0.99}',
            VLMUsage(prompt_tokens=150, completion_tokens=30, latency_ms=5.0, model="fake", max_token_entropy=0.01),
        ),
    ])
    logger = TraceLogger(str(tmp_path), "trace_r4")
    report = run_inspector_round(
        question="What is the exact Security ID?",
        round_=1,
        pages=pages,
        feedback=None,
        run_dir=str(tmp_path),
        trace_id="trace_r4",
        vlm=vlm,
        settings=settings,
        logger=logger,
        pdf_path=str(pdf_path),
    )
    assert report.answer == "ZD-6269-BV"
    step2_texts = [p.text for p in vlm.calls[1] if isinstance(p, TextPart)]
    combined_step2 = "\n".join(step2_texts)
    assert "ZD-6209-BV" not in combined_step2
    assert "Image 1: ZOOMED CROP" in combined_step2
    assert "collinearly aligned" in combined_step2


def test_m5_r5_seeker_grid_index_badges_and_zero_prompt_contamination() -> None:
    """R5: 缩略图网格右上角索引角标绘制不污染原图与填充格，且更新后的 Prompt 保持 0.0% 数据污染。"""
    from zoomdoc.eval.metrics import audit_prompt_contamination
    from zoomdoc.vision.grid import concat_grid

    src_imgs = [Image.new("RGB", (300, 400), color=(240, 240, 240)) for _ in range(3)]
    grid_plain, _ = concat_grid(src_imgs, arrangement=(2, 2), line_width=20, annotate_indices=False)
    grid_badged, _ = concat_grid(src_imgs, arrangement=(2, 2), line_width=20, annotate_indices=True)

    # 原图对象未被就地修改
    assert src_imgs[0].getpixel((290, 10)) == (240, 240, 240)
    # 左上角像素保持不变，右上角角标区域发生变化，右下空白填充格保持纯白
    assert grid_badged.getpixel((10, 10)) == (240, 240, 240)
    assert np.any(np.asarray(grid_badged)[:40, 220:298] != np.asarray(grid_plain)[:40, 220:298])
    assert grid_badged.getpixel((320 + 280, 420 + 15)) == (255, 255, 255)

    for m_file in ("eval/m4/manifest.jsonl", "eval/m4/manifest_full16.jsonl"):
        mp = Path(m_file)
        if mp.is_file():
            golds = [json.loads(ln)["gold"] for ln in mp.read_text(encoding="utf-8").splitlines() if ln.strip()]
            audit = audit_prompt_contamination(golds, prompts_path="zoomdoc/prompts.py", n=4)
            assert audit["exact_match_count"] == 0
            assert audit["char_ngram_overlap_rate"] == 0.0


def test_m5_r6_single_indel_residual_correction_and_tie_safety(tmp_path: Path) -> None:
    """R6: 支持单字符插入/缺失（Indel）残差纠偏，且在同微框等距歧义候选下保持安全弃权。"""
    pdf_path = tmp_path / "indel_and_tie.pdf"
    doc = fitz.open()
    p1 = doc.new_page(width=595.3, height=841.9)
    p1.insert_text(fitz.Point(380, 720), "ZD-4722-WP   ZD-1763-BV")
    p2 = doc.new_page(width=595.3, height=841.9)
    # 两个与 ZD-5000-AA 等距 (d=1.0) 的候选，必须触发 tie-safety 弃权
    p2.insert_text(fitz.Point(100, 100), "ZD-5000-AB   ZD-5000-AC")
    doc.save(str(pdf_path))
    doc.close()

    corr_ins, ok_ins = verify_and_correct_answer("ZD-4722-WIP", str(pdf_path), [1], [], max_optical_distance=1.5)
    assert ok_ins is True and corr_ins == "ZD-4722-WP"

    corr_del, ok_del = verify_and_correct_answer("ZD-176-BV", str(pdf_path), [1], [], max_optical_distance=1.5)
    assert ok_del is True and corr_del == "ZD-1763-BV"

    corr_tie, ok_tie = verify_and_correct_answer("ZD-5000-AA", str(pdf_path), [2], [], max_optical_distance=1.5)
    assert ok_tie is False and corr_tie == "ZD-5000-AA"

    box_indel = locate_target_zoom_box(str(pdf_path), 1, "Find Security ID", draft_answer="ZD-170-BV")
    assert box_indel.y1 > 0.60


