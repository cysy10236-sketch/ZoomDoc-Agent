from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from zoomdoc.config import Settings
from zoomdoc.schemas import NormBBox, PageAsset
from zoomdoc.vision.zoom import zoom


def test_zoom_execution_and_idempotency(tmp_path: Path) -> None:
    # 1. 创建 1700x2200 白底图像并在 (200, 500)-(400, 540) 绘制红色矩形
    source_img = Image.new("RGB", (1700, 2200), color=(255, 255, 255))
    draw = ImageDraw.Draw(source_img)
    draw.rectangle([200, 500, 400, 540], fill=(255, 0, 0))

    source_path = tmp_path / "p001_source.png"
    source_img.save(source_path, format="PNG")

    page = PageAsset(
        page=1,
        source_path=str(source_path),
        view_path=str(source_path),
        thumb_path=str(source_path),
        source_size=(1700, 2200),
        view_size=(1024, 1325),
        thumb_size=(448, 580),
    )

    settings = Settings(
        vlm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        vlm_api_key="mock",
        vlm_model="qwen2.5-vl-72b-instruct",
        zoom_pad_ratio=0.1,
        zoom_min_norm_side=0.05,
        zoom_target_long_side=1024,
        zoom_max_upscale=4.0,
    )

    req_box = NormBBox(x1=0.1, y1=0.2, x2=0.3, y2=0.25)
    crop_asset = zoom(
        page=page,
        page_box_requested=req_box,
        settings=settings,
        out_dir=str(tmp_path),
        crop_id="c0",
        target="serial number table",
    )

    # 断言尺寸为 (1024, 331)
    assert crop_asset.size == (1024, 331)

    # 验证红色像素存在
    with Image.open(crop_asset.path) as crop_img:
        rgb = crop_img.convert("RGB")
        w, h = rgb.size
        red_count = 0
        for x in range(0, w, 5):
            for y in range(0, h, 5):
                r, g, b = rgb.getpixel((x, y))
                if r > 200 and g < 50 and b < 50:
                    red_count += 1
        assert red_count > 0

    # 第二次执行测试幂等性
    crop_asset_2 = zoom(
        page=page,
        page_box_requested=req_box,
        settings=settings,
        out_dir=str(tmp_path),
        crop_id="c0",
        target="serial number table",
    )
    assert crop_asset_2.path == crop_asset.path
