from __future__ import annotations

import pytest

from zoomdoc.errors import GeometryError
from zoomdoc.schemas import NormBBox
from zoomdoc.vision.geometry import (
    crop_box_to_page_box,
    grid_box_to_page,
    grid_point_to_page,
    norm_to_pixel,
    pad_and_clamp,
    upsample_size,
    vlm_bbox_to_norm,
)


def test_vlm_bbox_to_norm_xyxy_1000() -> None:
    # ① vlm_bbox_to_norm((100, 200, 300, 250), "xyxy_1000") = (0.1, 0.2, 0.3, 0.25)
    box = vlm_bbox_to_norm((100, 200, 300, 250), "xyxy_1000")
    assert (box.x1, box.y1, box.x2, box.y2) == (0.1, 0.2, 0.3, 0.25)

    # ⑤ yxyx_1000 输入 (200, 100, 250, 300) 与 ① 结果相同
    box_yxyx = vlm_bbox_to_norm((200, 100, 250, 300), "yxyx_1000")
    assert (box_yxyx.x1, box_yxyx.y1, box_yxyx.x2, box_yxyx.y2) == (0.1, 0.2, 0.3, 0.25)

    # ⑥ 颠倒顺序的 (300, 250, 100, 200) 被交换为 ①
    box_reversed = vlm_bbox_to_norm((300, 250, 100, 200), "xyxy_1000")
    assert (box_reversed.x1, box_reversed.y1, box_reversed.x2, box_reversed.y2) == (0.1, 0.2, 0.3, 0.25)

    # ⑦ (100, 100, 100, 300) 抛 GeometryError（退化线段）
    with pytest.raises(GeometryError):
        vlm_bbox_to_norm((100, 100, 100, 300), "xyxy_1000")


def test_pad_and_clamp_and_pixel_conversion() -> None:
    # ② pad_and_clamp(①, 0.1, 0.05) ≈ (0.08, 0.195, 0.32, 0.255)，误差 1e-9
    box = vlm_bbox_to_norm((100, 200, 300, 250), "xyxy_1000")
    padded = pad_and_clamp(box, 0.1, 0.05)
    assert padded.x1 == pytest.approx(0.08, abs=1e-9)
    assert padded.y1 == pytest.approx(0.195, abs=1e-9)
    assert padded.x2 == pytest.approx(0.32, abs=1e-9)
    assert padded.y2 == pytest.approx(0.255, abs=1e-9)

    # ③ norm_to_pixel(②, (1700, 2200)) == (136, 429, 544, 561)
    pixel_box = norm_to_pixel(padded, (1700, 2200))
    assert pixel_box == (136, 429, 544, 561)

    # ④ upsample_size((136, 429, 544, 561), 1024, 4.0) == (1024, 331)
    up_size = upsample_size(pixel_box, 1024, 4.0)
    assert up_size == (1024, 331)


def test_pad_and_clamp_edge_and_min_side() -> None:
    # ⑧ 框 (0.98, 0.5, 1.0, 0.51) 经 pad_and_clamp 后 x2 <= 1 且宽度 >= 0.05
    box = NormBBox(x1=0.98, y1=0.5, x2=1.0, y2=0.51)
    padded = pad_and_clamp(box, pad_ratio=0.1, min_side=0.05)
    assert padded.x2 <= 1.0
    assert padded.x1 >= 0.0
    assert padded.width >= 0.05 - 1e-9
    assert padded.height >= 0.05 - 1e-9


def test_crop_box_to_page_box() -> None:
    # ⑨ crop_box_to_page_box((0.5, 0.5, 1, 1), (0.2, 0.2, 0.4, 0.6)) ≈ (0.3, 0.4, 0.4, 0.6)
    local = NormBBox(x1=0.5, y1=0.5, x2=1.0, y2=1.0)
    crop_page = NormBBox(x1=0.2, y1=0.2, x2=0.4, y2=0.6)
    mapped = crop_box_to_page_box(local, crop_page)
    assert mapped.x1 == pytest.approx(0.3, abs=1e-9)
    assert mapped.y1 == pytest.approx(0.4, abs=1e-9)
    assert mapped.x2 == pytest.approx(0.4, abs=1e-9)
    assert mapped.y2 == pytest.approx(0.6, abs=1e-9)


def test_grid_mapping() -> None:
    # n=3, 单格 (512, 662), L=40, 排布 (2, 2)
    # 画布尺寸：columns=2 -> 2*512 + 40 = 1064; rows=2 -> 2*662 + 40 = 1364
    # 点 (256/1064, 1033/1364) 映射到 (i=2, 0.5, 0.5)
    # Y = 1033 -> r = floor(1033 / 702) = 1, ly = 1033 - 702 = 331 -> 331/662 = 0.5
    # X = 256 -> c = floor(256 / 552) = 0, lx = 256 -> 256/512 = 0.5
    # i = 1*2 + 0 = 2
    idx, px, py = grid_point_to_page(
        u=256.0 / 1064.0,
        v=1033.0 / 1364.0,
        n=3,
        arrangement=(2, 2),
        cell_size=(512, 662),
        line_width=40,
    )
    assert idx == 2
    assert px == pytest.approx(0.5, abs=1e-6)
    assert py == pytest.approx(0.5, abs=1e-6)

    # Y=680 的点落在行分隔线上 (662 < y < 702) 抛 GeometryError
    with pytest.raises(GeometryError, match="divider"):
        grid_point_to_page(
            u=0.2,
            v=680.0 / 1364.0,
            n=3,
            arrangement=(2, 2),
            cell_size=(512, 662),
            line_width=40,
        )

    # 落在填充格 (r=1, c=1, i=3 >= n=3) 抛 GeometryError
    # X in cell 1: 552 + 256 = 808
    with pytest.raises(GeometryError, match="padding"):
        grid_point_to_page(
            u=808.0 / 1064.0,
            v=1033.0 / 1364.0,
            n=3,
            arrangement=(2, 2),
            cell_size=(512, 662),
            line_width=40,
        )

    # 跨两格的框抛 GeometryError
    box_cross = NormBBox(x1=100.0 / 1064.0, y1=100.0 / 1364.0, x2=800.0 / 1064.0, y2=200.0 / 1364.0)
    with pytest.raises(GeometryError, match="spans across"):
        grid_box_to_page(
            box=box_cross,
            n=3,
            arrangement=(2, 2),
            cell_size=(512, 662),
            line_width=40,
        )
