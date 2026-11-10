from __future__ import annotations

from zoomdoc.vision.grid import ARRANGEMENT, build_page_map


def test_build_page_map_n1() -> None:
    # build_page_map(1) 等于 ViDoRAG 原文
    assert build_page_map(1, ARRANGEMENT[1]) == "Your choice is empty as [] or this image as [0]!"


def test_build_page_map_n3() -> None:
    # n=3 修复了 ViDoRAG 源码遗漏的 "The first row"
    msg = build_page_map(3, ARRANGEMENT[3])
    assert "The first row" in msg
    assert "The second row, the first one is number 2." in msg
    assert "The image numbers are from 0-2!" in msg


def test_build_page_map_n4() -> None:
    # n=4 时包含 "The second row, the first one is number 2" 与 "from 0-3!"
    msg = build_page_map(4, ARRANGEMENT[4])
    assert "The first row, the first one is number 0, the second one is number 1." in msg
    assert "The second row, the first one is number 2, the second one is number 3." in msg
    assert "The image numbers are from 0-3!" in msg
