from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PIL import Image

from zoomdoc.vision.grid import ARRANGEMENT, concat_grid


def test_grid_canvas_size_and_composition() -> None:
    # 3 张 (512, 662) 图像，排布为 (2, 2)，间隔线 40
    # 画布预期：宽 = 2*512 + 40 = 1064，高 = 2*662 + 40 = 1364
    images = [
        Image.new("RGB", (512, 662), color=(255, 0, 0)),
        Image.new("RGB", (512, 662), color=(0, 255, 0)),
        Image.new("RGB", (512, 662), color=(0, 0, 255)),
    ]

    canvas, cell_size = concat_grid(images, arrangement=(2, 2), line_width=40)
    assert cell_size == (512, 662)
    assert canvas.size == (1064, 1364)

    # 验证左上角第一格是红色
    assert canvas.getpixel((10, 10)) == (255, 0, 0)
    # 验证右上角第二格是绿色 (x = 512 + 40 + 10 = 562)
    assert canvas.getpixel((562, 10)) == (0, 255, 0)
    # 验证左下角第三格是蓝色 (y = 662 + 40 + 10 = 712)
    assert canvas.getpixel((10, 712)) == (0, 0, 255)
    # 验证右下角填充格是白色 (255, 255, 255)
    assert canvas.getpixel((562, 712)) == (255, 255, 255)
    # 验证行间分隔线是黑色 (y = 680)
    assert canvas.getpixel((100, 680)) == (0, 0, 0)


def test_arrangement_matches_vidorag() -> None:
    vidorag_map_path = Path("references/ViDoRAG/agent/map_dict.py")
    if not vidorag_map_path.exists():
        pytest.skip("references/ViDoRAG/agent/map_dict.py not found")

    content = vidorag_map_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    ref_arrangement: dict[int, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "arrangement_map_dict":
                    if isinstance(node.value, ast.Dict):
                        for k, v in zip(node.value.keys, node.value.values):
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Tuple):
                                ref_arrangement[k.value] = tuple(elt.value for elt in v.elts)  # type: ignore

    for n in range(1, 21):
        assert n in ARRANGEMENT, f"Missing arrangement for n={n}"
        assert ARRANGEMENT[n] == ref_arrangement[n], (
            f"Arrangement mismatch for n={n}: {ARRANGEMENT[n]} != {ref_arrangement[n]}"
        )
