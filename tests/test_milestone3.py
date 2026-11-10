from __future__ import annotations

import json
from pathlib import Path

import pymupdf as fitz
import pytest
from pydantic import ValidationError

from scripts.make_synthetic_long_pdf import create_synthetic_long_pdf
from scripts.make_synthetic_pdf import create_synthetic_pdf
from tests.fake_vlm import FakeVLM
from zoomdoc.config import Settings
from zoomdoc.graph.states import NavigatorReport, SupervisorState, UsageTotals
from zoomdoc.graph.supervisor import run_m2, run_m3
from zoomdoc.index.tree import (
    build_document_tree,
    collect_candidate_pages,
    flatten_tree_nodes,
    get_leaf_nodes,
)


@pytest.fixture
def synthetic_100p_pdf(tmp_path: Path) -> str:
    pdf_path = tmp_path / "test_synth_100p.pdf"
    create_synthetic_long_pdf(
        out_path=str(pdf_path),
        total_pages=100,
        gold_page=73,
        gold_code="ZD-9042-M3",
    )
    return str(pdf_path)


def test_m3_t1_tree_builder_with_toc_and_large_node_split(synthetic_100p_pdf: str, tmp_path: Path) -> None:
    """T1: 验证原生 TOC 目录树解析与大节点自动拆分：

    1. 100 页带 TOC 的 PDF，构建整树后所有叶子节点跨度严格 <= 20 页；
    2. 无 TOC 的 55 页 PDF，自动切分后所有叶子节点跨度严格 <= 20 页，且全树叶子起止页码完整覆盖 1..55。
    """
    # 1. 100 页带 TOC PDF 检验
    tree = build_document_tree(synthetic_100p_pdf, max_leaf_pages=20, out_dir=str(tmp_path))
    assert tree.total_pages == 100
    assert len(tree.roots) == 4  # Part 1..4

    leaves = get_leaf_nodes(tree)
    assert len(leaves) > 0
    for leaf in leaves:
        assert 1 <= leaf.page_span <= 20, f"Leaf {leaf.node_id} page span {leaf.page_span} > 20"
        assert leaf.end_page >= leaf.start_page
        assert len(leaf.children) == 0

    # 2. 构造无 TOC 的 55 页纯文档
    no_toc_pdf = tmp_path / "no_toc_55p.pdf"
    doc = fitz.open()
    for _ in range(55):
        doc.new_page(width=595.3, height=841.9)
    doc.save(str(no_toc_pdf))
    doc.close()

    tree_no_toc = build_document_tree(str(no_toc_pdf), max_leaf_pages=20)
    assert tree_no_toc.total_pages == 55

    no_toc_leaves = get_leaf_nodes(tree_no_toc)
    assert len(no_toc_leaves) >= 3
    for leaf in no_toc_leaves:
        assert 1 <= leaf.page_span <= 20

    # 验证叶子节点连续覆盖 1..55
    covered_pages = set()
    for leaf in no_toc_leaves:
        covered_pages.update(range(leaf.start_page, leaf.end_page + 1))
    assert covered_pages == set(range(1, 56))


def test_m3_t2_100_page_e2e_navigation_and_zoom(synthetic_100p_pdf: str, tmp_path: Path) -> None:
    """T2: 100 页长文档端到端检索与主动视觉放大主用例：

    金标位于第 73 页（位于 Part 3 的 3.2 节 [69..80]）。
    全流程断言：
    - 成功作答 ZD-9042-M3 并引用第 73 页；
    - 流入 Seeker 的候选页数严格 <= 20（实测 12 页）；
    - 惰性按需渲染：全书 100 页中仅渲染这 12 页，其余 88 页零 PNG 渲染；
    - 上下文隔离与正交性断言；
    - 主图 history 节点序列严格为 ['ingest', 'tree_builder', 'navigator', 'seeker', 'inspector', 'critic']。
    """
    tree = build_document_tree(synthetic_100p_pdf, max_leaf_pages=20)
    all_nodes = flatten_tree_nodes(tree)

    # 寻找 Part 3 根节点与包含第 73 页的叶子节点
    part3_node = next(n for n in tree.roots if "Part 3" in n.title)
    leaf_target = next(n for n in get_leaf_nodes(tree) if n.start_page <= 73 <= n.end_page)

    assert leaf_target.page_span <= 20
    # 第 73 页在候选缩略图中的 0-based 索引
    page_73_thumb_index = 73 - leaf_target.start_page

    scripted = [
        # Call 1: Navigator step 1 - expand Part 3
        json.dumps({
            "action": "expand",
            "reason": "Financial statements and derivative instruments are in Part 3",
            "node_ids": [part3_node.node_id],
        }),
        # Call 2: Navigator step 2 - select Section 3.2
        json.dumps({
            "action": "select",
            "reason": "Section 3.2 specifically covers off-balance-sheet derivative instruments",
            "node_ids": [leaf_target.node_id],
        }),
        # Call 3: Seeker on the 12 thumbnails of Section 3.2
        json.dumps({
            "reason": "Thumbnail shows microscopic table layout",
            "summary": "Table on target page",
            "choice": [page_73_thumb_index],
        }),
        # Call 4: Inspector round 1 - zoom full page view
        json.dumps({
            "action": "zoom",
            "reason": "Security ID table text is 5pt and blurry at medium view resolution",
            "image": 0,
            "bbox_2d": [638, 831, 940, 926],
            "target": "Security ID code",
        }),
        # Call 5: Inspector round 1 - confident answer from high-res crop
        json.dumps({
            "action": "answer",
            "reason": "Cropped image clearly displays security code ZD-9042-M3",
            "answer": "ZD-9042-M3",
            "confidence": 0.95,
            "reference": [0, 1],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_zooms=3,
        max_sub_steps=6,
        max_inspect_rounds=2,
        max_nav_steps=4,
        max_nav_rounds=3,
    )

    result, state = run_m3(
        question="What is the security ID on page 73?",
        pdf_path=synthetic_100p_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m3-t2",
    )

    # 1. 业务准确性
    assert result.status == "answered"
    assert result.answer == "ZD-9042-M3"
    assert 73 in result.reference_pages

    # 2. 候选页数硬上限断言（流向 Seeker 的页数 <= 20）
    seeker_step = next(s for s in state.history if s.node == "seeker")
    assert len(seeker_step.input_images) == leaf_target.page_span <= 20

    # 3. 按需惰性渲染断言（100 页 PDF 仅按需渲染了目标章节的 12 页）
    assert len(state.pages) == leaf_target.page_span
    rendered_page_numbers = sorted(list(state.pages.keys()))
    assert rendered_page_numbers == list(range(leaf_target.start_page, leaf_target.end_page + 1))

    # 4. 双子图上下文隔离断言
    dump = state.model_dump_json()
    assert "data:image" not in dump
    assert "base64," not in dump
    assert "/sub/" not in dump.replace("\\", "/")

    # 磁盘 supervisor_state.json 隔离
    sup_file = tmp_path / "trace-m3-t2" / "supervisor_state.json"
    assert sup_file.exists()
    sup_dump = sup_file.read_text(encoding="utf-8")
    assert "data:image" not in sup_dump
    assert "base64," not in sup_dump
    assert "/sub/" not in sup_dump.replace("\\", "/")

    # 字段正交性：主图不含有任何子图私有状态字段
    forbidden_fields = {
        "current_frontier",
        "crops",
        "view",
        "pending_zoom",
        "last_output",
        "sub_step",
        "zoom_count",
    }
    assert set(SupervisorState.model_fields.keys()).isdisjoint(forbidden_fields)

    # 5. 主图历史节点拓扑（子图内部细节不污染主图）
    assert [rec.node for rec in state.history] == [
        "ingest",
        "tree_builder",
        "navigator",
        "seeker",
        "inspector",
        "critic",
    ]


def test_m3_t3_tree_backtracking_on_seeker_miss_or_need_more_pages(
    synthetic_100p_pdf: str, tmp_path: Path
) -> None:
    """T3: 跨章树回溯测试：

    第 1 轮 Navigator 误选 Part 1（Pages 1..12）；
    Seeker 选 [0]，Inspector 判定缺失目标信息并触发 seek（need_more_pages），且候选池耗尽；
    系统根据 R7b 自动触发跨章树回溯重新路由回 Navigator；
    第 2 轮 Navigator 排除已访问的章节，选定正确的 3.2 节并最终成功回答。
    """
    tree = build_document_tree(synthetic_100p_pdf, max_leaf_pages=20)
    part1_node = next(n for n in tree.roots if "Part 1" in n.title)
    part3_node = next(n for n in tree.roots if "Part 3" in n.title)
    leaf_correct = next(n for n in get_leaf_nodes(tree) if n.start_page <= 73 <= n.end_page)

    page_73_index = 73 - leaf_correct.start_page

    scripted = [
        # --- Round 1 ---
        # 1. Nav r1: mistakenly selects Part 1 from frontier roots
        json.dumps({
            "action": "select",
            "reason": "Mistakenly try Part 1 corporate governance first",
            "node_ids": [part1_node.node_id],
        }),
        # 2. Seeker r1: selects index 0
        json.dumps({
            "reason": "Inspect page 1",
            "summary": "Page 1 overview",
            "choice": [0],
        }),
        # 3. Inspector r1: realizes info is missing, requests more pages
        json.dumps({
            "action": "seek",
            "reason": "Part 1 does not contain derivative instruments table; search financial notes",
            "information": "look for derivative instruments table in financial notes",
            "choice": [],
        }),
        # --- Round 2 (Backtracked to Navigator) ---
        # 4. Nav r2 step 1: expands Part 3
        json.dumps({
            "action": "expand",
            "reason": "Financial notes are located in Part 3",
            "node_ids": [part3_node.node_id],
        }),
        # 5. Nav r2 step 2: selects Section 3.2
        json.dumps({
            "action": "select",
            "reason": "Section 3.2 covers derivatives",
            "node_ids": [leaf_correct.node_id],
        }),
        # 6. Seeker r2 on Section 3.2 thumbnails: selects page 73
        json.dumps({
            "reason": "Found microscopic table on page 73",
            "summary": "Target table located",
            "choice": [page_73_index],
        }),
        # 7. Inspector r2: confident answer
        json.dumps({
            "action": "answer",
            "reason": "Direct reading of code ZD-9042-M3",
            "answer": "ZD-9042-M3",
            "confidence": 0.95,
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        max_steps=12,
        max_seek_rounds=1,  # Exhaust intra-section quickly so R7b triggers
        max_inspect_rounds=2,
        max_nav_rounds=3,
        max_nav_steps=4,
    )

    result, state = run_m3(
        question="Find the security ID",
        pdf_path=synthetic_100p_pdf,
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m3-t3",
    )

    assert state.nav_rounds == 2
    assert result.status == "answered"
    assert result.answer == "ZD-9042-M3"


def test_m3_t4_candidate_pages_hard_cap_20(synthetic_100p_pdf: str) -> None:
    """T4: 多节点选择页数严格截断至 <= 20 页测试。"""
    tree = build_document_tree(synthetic_100p_pdf, max_leaf_pages=20)
    leaves = get_leaf_nodes(tree)

    # 选取前 3 个叶子节点（跨度总和明显超过 20 页）
    chosen_ids = [l.node_id for l in leaves[:3]]
    total_raw_span = sum(l.page_span for l in leaves[:3])
    assert total_raw_span > 20

    pages, visited_ids = collect_candidate_pages(
        tree=tree,
        selected_node_ids=chosen_ids,
        visited_node_ids=[],
        max_pages=20,
    )
    assert len(pages) <= 20
    assert len(visited_ids) >= 1
    # 第一个叶子必须完整收纳
    assert set(range(leaves[0].start_page, leaves[0].end_page + 1)).issubset(set(pages))

    # 验证 NavigatorReport 契约校验
    with pytest.raises(ValidationError, match="exceeds hard limit 20"):
        NavigatorReport(
            nav_round=1,
            status="located",
            selected_node_ids=chosen_ids,
            candidate_pages=list(range(1, 22)),  # 21 pages
            reason="overflow test",
            sub_steps=1,
            usage=UsageTotals(),
        )


def test_m3_t5_section_recall_benchmark_10_queries(synthetic_100p_pdf: str) -> None:
    """T5: 10 个跨章节 Query 的章节召回率基准验证 (>= 90%)。"""
    tree = build_document_tree(synthetic_100p_pdf, max_leaf_pages=20)
    leaves = get_leaf_nodes(tree)

    target_gold_pages = [3, 10, 18, 28, 35, 48, 62, 73, 83, 95]
    hits = 0

    for gold in target_gold_pages:
        target_leaf = next(l for l in leaves if l.start_page <= gold <= l.end_page)
        pages, _ = collect_candidate_pages(
            tree=tree,
            selected_node_ids=[target_leaf.node_id],
            visited_node_ids=[],
            max_pages=20,
        )
        assert len(pages) <= 20
        if gold in pages:
            hits += 1

    recall = hits / len(target_gold_pages)
    assert recall >= 0.90, f"Section recall {recall:.2%} < 90%"
    assert recall == 1.0  # 100% stable recall


def test_m3_t6_m1_m2_zero_regression(tmp_path: Path) -> None:
    """T6: 确保 M1/M2 在 4 页短文档上完全保持零回归（不经过 tree_builder）。"""
    pdf_path = tmp_path / "synth_4p.pdf"
    create_synthetic_pdf(str(pdf_path))

    scripted = [
        # Call 1: Seeker
        json.dumps({
            "reason": "Section 3 has table layout",
            "summary": "Table on page 3",
            "choice": [2],
        }),
        # Call 2: Inspector answer
        json.dumps({
            "action": "answer",
            "reason": "Serial code clearly visible",
            "answer": "ZD-7319-QX",
            "confidence": 0.9,
            "reference": [0],
        }),
    ]

    fake_vlm = FakeVLM(scripted)
    settings = Settings(
        runs_dir=str(tmp_path),
        use_tree_index=False,
    )

    result, state = run_m2(
        question="What is the item code?",
        pdf_path=str(pdf_path),
        vlm=fake_vlm,
        settings=settings,
        trace_id="trace-m3-t6",
    )

    assert result.status == "answered"
    assert result.answer == "ZD-7319-QX"
    # 验证 M2 历史节点拓扑绝不包含 tree_builder 或 navigator
    assert [rec.node for rec in state.history] == ["ingest", "seeker", "inspector", "critic"]
