from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw

from scripts.make_synthetic_long_pdf import create_synthetic_long_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.config import Settings
from zoomdoc.graph.supervisor import run_m5
from zoomdoc.index.tree import build_document_tree, get_leaf_nodes
from zoomdoc.schemas import NormBBox
from zoomdoc.vision.fovea import (
    build_1d_fovea_map,
    compute_adaptive_fovea_size,
    detect_table_anchor_bands,
    fovea_box_to_page_box,
    fovea_warp_image,
)
from zoomdoc.vision.residual import locate_target_zoom_box


def test_m6_t1_multiband_table_header_foveation_and_exact_inversion() -> None:
    """ALG-1: 验证页中表格列头自动检测、双锚定带严格单调双射与精确逆映射。"""
    w_src, h_src = 1600, 2200
    img = Image.new("RGB", (w_src, h_src), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # 页中数据表从 y = 600 开始，表头行为 [600, 680]，焦点在底部 y = [1700, 1860]
    draw.line([(120, 600), (1480, 600)], fill=(10, 10, 10), width=4)
    draw.line([(120, 680), (1480, 680)], fill=(10, 10, 10), width=4)
    # 左侧行名竖向分隔线位于 x = 420 (26.25% W > 默认 15% W)
    draw.line([(420, 600), (420, 1950)], fill=(10, 10, 10), width=4)
    # 垂直列对齐标线 x = 1150 (红色)
    draw.line([(1150, 600), (1150, 1950)], fill=(255, 0, 0), width=3)

    pixel_box = (1050, 1700, 1280, 1860)
    stub_end_x, sec_band_y = detect_table_anchor_bands(
        img, pixel_box=pixel_box, header_ratio=0.12, stub_ratio=0.15
    )

    # 必须自动扩展左侧行名边界至 x=420 分隔线附近，并识别页中表头 [~594, ~686]
    assert 415 <= stub_end_x <= 430, f"Expected stub_end_x near 420, got {stub_end_x}"
    assert sec_band_y is not None, "Expected mid-page table header band to be detected"
    assert 585 <= sec_band_y[0] <= 605 and 675 <= sec_band_y[1] <= 700

    warped, meta = fovea_warp_image(
        src_img=img,
        pixel_box=pixel_box,
        out_size=(1024, 1024),
        draw_seam=False,
        auto_detect_anchors=True,
    )

    # 严格单调性检验
    assert all(meta["v_knots"][i] < meta["v_knots"][i + 1] for i in range(len(meta["v_knots"]) - 1))
    assert all(meta["y_knots"][i] < meta["y_knots"][i + 1] for i in range(len(meta["y_knots"]) - 1))

    # 页中表头带垂直像素跨度未坍缩（保留充足可读高度 >= 70px）
    v_hdr1 = float(np.interp(600, meta["y_knots"], meta["v_knots"]))
    v_hdr2 = float(np.interp(680, meta["y_knots"], meta["v_knots"]))
    assert (v_hdr2 - v_hdr1) >= 70.0, f"Mid-page table header collapsed to {v_hdr2 - v_hdr1:.1f}px"

    # 页中表头列与底部焦点列保持 100% 垂直共线
    arr = np.asarray(warped)
    hdr_mid_v = int(round(0.5 * (v_hdr1 + v_hdr2)))
    v_foc1 = float(np.interp(1700, meta["y_knots"], meta["v_knots"]))
    v_foc2 = float(np.interp(1860, meta["y_knots"], meta["v_knots"]))
    foc_mid_v = int(round(0.5 * (v_foc1 + v_foc2)))

    col_hdr = int(np.argmax(arr[hdr_mid_v, :, 0].astype(np.int32) - arr[hdr_mid_v, :, 1].astype(np.int32)))
    col_foc = int(np.argmax(arr[foc_mid_v, :, 0].astype(np.int32) - arr[foc_mid_v, :, 1].astype(np.int32)))
    assert col_hdr == col_foc

    # 多节点分段逆映射精确还原检验
    u_f1 = float(np.interp(1050, meta["x_knots"], meta["u_knots"]))
    u_f2 = float(np.interp(1280, meta["x_knots"], meta["u_knots"]))
    recovered = fovea_box_to_page_box(
        local_box=NormBBox(
            x1=u_f1 / 1024.0,
            y1=v_foc1 / 1024.0,
            x2=u_f2 / 1024.0,
            y2=v_foc2 / 1024.0,
        ),
        warp_meta=meta,
        src_size=(w_src, h_src),
        out_size=(1024, 1024),
    )
    assert abs(recovered.x1 - 1050 / w_src) < 1e-4
    assert abs(recovered.y1 - 1700 / h_src) < 1e-4
    assert abs(recovered.x2 - 1280 / w_src) < 1e-4
    assert abs(recovered.y2 - 1860 / h_src) < 1e-4

    # 自适应长宽比与宽跨度非反向压缩不变量验证
    adapt_w, adapt_h = compute_adaptive_fovea_size(
        src_size=(w_src, h_src),
        pixel_box=(400, 1200, 1450, 1320),  # 宽扁行区域
        target_long_side=1024,
    )
    assert 1024 <= adapt_w <= 1536
    assert 680 <= adapt_h < 900  # 高度自适应收敛，节省像素 Token

    # 极端整行宽框 [30, 1570] (占原图宽度 96.25%) 不得被 focus_share=0.65 反向压缩空白边缘
    _, u_wide, x_wide = build_1d_fovea_map(
        src_len=1600,
        out_len=1280,
        focus_start=30,
        focus_end=1570,
        anchor_end=30,
        focus_share=0.65,
    )
    wide_focus_u = float(np.interp(1570, x_wide, u_wide) - np.interp(30, x_wide, u_wide))
    assert wide_focus_u / 1280.0 >= 0.93


def test_m6_t2_matrix_crosshair_and_pure_raster_scan_roi_locator(tmp_path: Path) -> None:
    """ALG-2: 验证宽矩阵表格行列十字交叉定位与纯扫描栅格页（零矢量文本）墨水能量定位。"""
    # 1. 密集多列矩阵表：即便首轮 draft_answer 错读相邻列，Cross-Hair 仍精准锁定目标 (Row, Col) 交点
    matrix_pdf = tmp_path / "matrix_test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595.3, height=841.9)
    cols = [("FY2023", 220), ("FY2024", 330), ("FY2025", 440)]
    for cname, cx in cols:
        page.insert_text(fitz.Point(cx, 190), cname, fontsize=9)

    rows = [
        ("Operating Cash Margin", 320, ["ZD-1001-AA", "ZD-1002-BB", "ZD-1003-CC"]),
        ("Subordinated Tier2 Buffer", 640, ["ZD-2001-DD", "ZD-2002-EE", "ZD-7491-KP"]),
    ]
    for rname, ry, rcodes in rows:
        page.insert_text(fitz.Point(48, ry), rname, fontsize=8)
        for (_, cx), cval in zip(cols, rcodes):
            page.insert_text(fitz.Point(cx, ry), cval, fontsize=4.5)
    doc.save(str(matrix_pdf))
    doc.close()

    box_cross = locate_target_zoom_box(
        pdf_path=str(matrix_pdf),
        page_num=1,
        question="What is the exact code for Subordinated Tier2 Buffer in column FY2025?",
        draft_answer="ZD-1001-AA",  # 故意传入左上角错行错列干扰编码
    )
    # 目标单元格位于 (x ≈ 440/595.3 = 0.739, y ≈ 640/841.9 = 0.760)
    assert box_cross.x1 > 0.60 and box_cross.y1 > 0.65

    # 2. 纯扫描栅格页（0 矢量文本词元）：将绘制了右下角密集微字表的图像嵌入新 PDF
    raster_img = Image.new("RGB", (1190, 1684), color=(255, 255, 255))
    rdraw = ImageDraw.Draw(raster_img)
    rdraw.text((80, 60), "SCANNED ARCHIVAL REPORT", fill=(20, 20, 20))
    # 在右下区域 (x: 720..1100, y: 1220..1500) 绘制密集网格与笔画
    for yy in range(1220, 1501, 28):
        rdraw.line([(720, yy), (1100, yy)], fill=(0, 0, 0), width=2)
    for xx in range(720, 1101, 76):
        rdraw.line([(xx, 1220), (xx, 1500)], fill=(0, 0, 0), width=2)

    img_bytes_path = tmp_path / "scan_page.png"
    raster_img.save(str(img_bytes_path))

    scan_pdf = tmp_path / "pure_raster_scan.pdf"
    sdoc = fitz.open()
    spage = sdoc.new_page(width=595.3, height=841.9)
    spage.insert_image(spage.rect, filename=str(img_bytes_path))
    assert spage.get_text("words") == []  # 物理确认零矢量文本层
    sdoc.save(str(scan_pdf))
    sdoc.close()

    box_scan = locate_target_zoom_box(
        pdf_path=str(scan_pdf),
        page_num=1,
        question="Read the bottom-right scanned table value",
        draft_answer=None,
    )
    # 必须收敛至右下表格邻域，而非退化为整页默认框 [0.08, 0.20, 0.92, 0.88]
    assert box_scan.x1 > 0.45 and box_scan.y1 > 0.60, f"Raster ROI too wide: {box_scan}"


def test_m6_t3_live_event_streaming_bus_isolation(tmp_path: Path) -> None:
    """AGENT-STREAM: 验证 on_event 实时流式事件总线完整捕获阶段与节点事件，且不污染磁盘 trace.jsonl。"""
    pdf_path = tmp_path / "stream_30p.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=30,
        gold_page=15,
        gold_code="ZD-3030-SSE",
    )
    tree = build_document_tree(str(pdf_path), max_leaf_pages=20)
    leaf = next(l for l in get_leaf_nodes(tree) if l.start_page <= 15 <= l.end_page)
    p15_idx = 15 - leaf.start_page

    scripted = [
        json.dumps({"action": "select", "reason": "leaf", "node_ids": [leaf.node_id]}),
        json.dumps({"reason": "thumb", "summary": "s", "choice": [p15_idx]}),
        json.dumps({"action": "answer", "reason": "r1", "answer": "ZD-3030-SSE", "confidence": 0.9, "reference": [0]}),
        json.dumps({"action": "answer", "reason": "r2", "answer": "ZD-3030-SSE", "confidence": 0.99, "reference": [0, 1]}),
    ]
    captured_events: list[dict] = []
    settings = Settings(runs_dir=str(tmp_path), cache_dir=str(tmp_path / "cache"))

    res, _ = run_m5(
        question="Find code",
        pdf_path=str(pdf_path),
        vlm=FakeVLM(scripted),
        settings=settings,
        trace_id="trace_stream_m6",
        on_event=captured_events.append,
    )
    assert res.status == "answered"
    stage_names = [e["stage"] for e in captured_events if e.get("event_type") == "stage"]
    assert stage_names == ["ingest", "tree_builder", "navigator", "seeker", "inspector", "finalize"]
    trace_events = [e for e in captured_events if e.get("event_type") == "trace"]
    assert len(trace_events) >= 5

    # 验证磁盘 trace.jsonl 仅包含标准 trace 记录，不含内存 stage 事件
    disk_lines = [
        json.loads(ln)
        for ln in (tmp_path / "trace_stream_m6" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert all("event_type" not in rec for rec in disk_lines)
