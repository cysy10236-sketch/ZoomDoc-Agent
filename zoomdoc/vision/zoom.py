from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from zoomdoc.schemas import CropAsset, NormBBox, PageAsset
from zoomdoc.vision.geometry import norm_to_pixel, pad_and_clamp, upsample_size

if TYPE_CHECKING:
    from zoomdoc.config import Settings


def zoom(
    page: PageAsset,
    page_box_requested: NormBBox,
    settings: Settings,
    out_dir: str,
    crop_id: str,
    target: str,
) -> CropAsset:
    """Crop high-resolution source image at normalized coordinates, pad/clamp, and upsample.

    Idempotent: returns existing CropAsset if file already exists on disk.
    """
    padded_box = pad_and_clamp(
        page_box_requested,
        pad_ratio=settings.zoom_pad_ratio,
        min_side=settings.zoom_min_norm_side,
    )
    pixel_box = norm_to_pixel(padded_box, page.source_size)
    x1, y1, x2, y2 = pixel_box

    crops_dir = Path(out_dir) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    crop_filename = f"{crop_id}_p{page.page}_{x1}_{y1}_{x2}_{y2}.png"
    crop_path = crops_dir / crop_filename

    if crop_path.exists():
        with Image.open(crop_path) as existing_img:
            return CropAsset(
                crop_id=crop_id,
                page=page.page,
                page_box=padded_box,
                requested_box=page_box_requested,
                path=str(crop_path.resolve()),
                size=existing_img.size,
                target=target,
                pixel_box=pixel_box,
            )

    with Image.open(page.source_path) as source_img:
        source_rgb = source_img.convert("RGB")
        region = source_rgb.crop(pixel_box)

        target_size = upsample_size(
            pixel_box,
            target_long_side=settings.zoom_target_long_side,
            max_upscale=settings.zoom_max_upscale,
        )
        upsampled = region.resize(target_size, Image.Resampling.LANCZOS)
        upsampled.save(crop_path, format="PNG")

        return CropAsset(
            crop_id=crop_id,
            page=page.page,
            page_box=padded_box,
            requested_box=page_box_requested,
            path=str(crop_path.resolve()),
            size=upsampled.size,
            target=target,
            pixel_box=pixel_box,
        )
