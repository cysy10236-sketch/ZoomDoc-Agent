from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw

from zoomdoc.schemas import CropAsset, NormBBox, PageAsset
from zoomdoc.vision.geometry import norm_to_pixel, pad_and_clamp

if TYPE_CHECKING:
    from zoomdoc.config import Settings


def build_1d_fovea_map(
    src_len: int,
    out_len: int,
    focus_start: int,
    focus_end: int,
    anchor_end: int = 0,
    focus_share: float = 0.65,
    anchor_share: float = 0.15,
) -> tuple[np.ndarray, list[float], list[float]]:
    """Build 1D piecewise monotonic sampling coordinate map and knot points.

    Returns:
        (src_coords, u_knots, x_knots)
        src_coords: array of length out_len, where src_coords[u] is the continuous source coordinate.
        u_knots: knot points in output domain [0, out_len].
        x_knots: knot points in source domain [0, src_len].
    """
    if src_len < 2 or out_len < 2:
        raise ValueError(f"Dimensions must be >= 2, got src_len={src_len}, out_len={out_len}")
    if not (0 <= focus_start < focus_end <= src_len):
        raise ValueError(f"Invalid focus interval [{focus_start}, {focus_end}] for src_len={src_len}")
    if anchor_end < 0 or anchor_end > src_len:
        raise ValueError(f"Invalid anchor_end={anchor_end} for src_len={src_len}")

    eff_anchor = min(anchor_end, focus_start)
    gap1 = focus_start - eff_anchor
    focus_len = focus_end - focus_start
    gap2 = src_len - focus_end

    min_gaps = (1 if gap1 > 0 else 0) + (1 if gap2 > 0 else 0)
    eff_anchor_share = (
        min(anchor_share, (eff_anchor / src_len) * 1.5) if eff_anchor > 0 else 0.0
    )
    target_u_anchor = max(1, round(eff_anchor_share * out_len)) if eff_anchor > 0 else 0
    u_anchor = min(target_u_anchor, max(0, out_len - 2 - min_gaps)) if eff_anchor > 0 else 0

    target_u_focus = max(2, round(focus_share * out_len))
    target_u_focus = min(target_u_focus, max(2, out_len - u_anchor - min_gaps))
    rem = max(min_gaps, out_len - u_anchor - target_u_focus)

    gap_sum = gap1 + gap2
    if gap1 > 0 and gap2 > 0:
        u_gap1 = max(1, min(rem - 1, round(rem * (gap1 / gap_sum))))
        u_gap2 = max(1, rem - u_gap1)
        u_focus = out_len - u_anchor - u_gap1 - u_gap2
    elif gap1 > 0:
        u_gap1 = max(1, rem)
        u_gap2 = 0
        u_focus = out_len - u_anchor - u_gap1
    elif gap2 > 0:
        u_gap1 = 0
        u_gap2 = max(1, rem)
        u_focus = out_len - u_anchor - u_gap2
    else:
        u_gap1 = 0
        u_gap2 = 0
        u_focus = out_len - u_anchor

    x_knots: list[float] = [0.0]
    u_knots: list[float] = [0.0]

    if eff_anchor > 0:
        x_knots.append(float(eff_anchor))
        u_knots.append(float(u_anchor))

    if gap1 > 0:
        x_knots.append(float(focus_start))
        u_knots.append(float(u_knots[-1] + u_gap1))

    x_knots.append(float(focus_end))
    u_knots.append(float(u_knots[-1] + u_focus))

    if gap2 > 0:
        x_knots.append(float(src_len))
        u_knots.append(float(out_len))

    # Ensure last knot is exactly (src_len, out_len)
    x_knots[-1] = float(src_len)
    u_knots[-1] = float(out_len)

    # Compute sampling coordinates via piecewise linear interpolation
    u_grid = np.arange(out_len, dtype=np.float64)
    src_coords = np.interp(u_grid, u_knots, x_knots)
    np.clip(src_coords, 0.0, float(src_len - 1), out=src_coords)

    return src_coords, u_knots, x_knots


def compute_fovea_warp_meta(
    src_size: tuple[int, int],
    pixel_box: tuple[int, int, int, int],
    out_size: tuple[int, int] = (1024, 1024),
    header_ratio: float = 0.12,
    stub_ratio: float = 0.15,
    focus_share: float = 0.65,
    anchor_share: float = 0.15,
) -> dict[str, list[float]]:
    """Compute 1D knot mappings along x and y dimensions."""
    x1, y1, x2, y2 = pixel_box
    w_src, h_src = src_size
    w_out, h_out = out_size

    anchor_x = min(x1, int(round(stub_ratio * w_src)))
    anchor_y = min(y1, int(round(header_ratio * h_src)))

    _, u_knots_x, x_knots_x = build_1d_fovea_map(
        src_len=w_src,
        out_len=w_out,
        focus_start=x1,
        focus_end=x2,
        anchor_end=anchor_x,
        focus_share=focus_share,
        anchor_share=anchor_share,
    )
    _, v_knots_y, y_knots_y = build_1d_fovea_map(
        src_len=h_src,
        out_len=h_out,
        focus_start=y1,
        focus_end=y2,
        anchor_end=anchor_y,
        focus_share=focus_share,
        anchor_share=anchor_share,
    )
    return {
        "u_knots": u_knots_x,
        "x_knots": x_knots_x,
        "v_knots": v_knots_y,
        "y_knots": y_knots_y,
    }


def fovea_warp_image(
    src_img: Image.Image,
    pixel_box: tuple[int, int, int, int],
    out_size: tuple[int, int] = (1024, 1024),
    header_ratio: float = 0.12,
    stub_ratio: float = 0.15,
    focus_share: float = 0.65,
    anchor_share: float = 0.15,
    draw_seam: bool = True,
) -> tuple[Image.Image, dict[str, list[float]]]:
    """Warp source image using 2D separable anisotropic topological foveation.

    Preserves orthogonal collinearity:
    Vertical columns in header band remain perfectly collinear with focus columns.
    Horizontal rows in stub band remain perfectly collinear with focus rows.
    """
    x1, y1, x2, y2 = pixel_box
    w_src, h_src = src_img.size
    w_out, h_out = out_size

    anchor_x = min(x1, int(round(stub_ratio * w_src)))
    anchor_y = min(y1, int(round(header_ratio * h_src)))

    map_x, u_knots_x, x_knots_x = build_1d_fovea_map(
        src_len=w_src,
        out_len=w_out,
        focus_start=x1,
        focus_end=x2,
        anchor_end=anchor_x,
        focus_share=focus_share,
        anchor_share=anchor_share,
    )
    map_y, v_knots_y, y_knots_y = build_1d_fovea_map(
        src_len=h_src,
        out_len=h_out,
        focus_start=y1,
        focus_end=y2,
        anchor_end=anchor_y,
        focus_share=focus_share,
        anchor_share=anchor_share,
    )

    src_arr = np.asarray(src_img.convert("RGB"), dtype=np.float32)

    # Separable bilinear interpolation: step 1 horizontal along rows
    x_floor = np.floor(map_x).astype(np.int64)
    x_ceil = np.clip(x_floor + 1, 0, w_src - 1)
    wx = (map_x - x_floor).astype(np.float32).reshape((1, w_out, 1))

    interp_h = src_arr[:, x_floor, :] * (1.0 - wx) + src_arr[:, x_ceil, :] * wx

    # Step 2 vertical along columns
    y_floor = np.floor(map_y).astype(np.int64)
    y_ceil = np.clip(y_floor + 1, 0, h_src - 1)
    wy = (map_y - y_floor).astype(np.float32).reshape((h_out, 1, 1))

    warped = interp_h[y_floor, :, :] * (1.0 - wy) + interp_h[y_ceil, :, :] * wy
    warped_uint8 = np.clip(warped, 0.0, 255.0).astype(np.uint8)
    warped_img = Image.fromarray(warped_uint8, mode="RGB")

    # Draw topological fold seam indicator if requested
    if draw_seam:
        u_f1 = float(np.interp(x1, x_knots_x, u_knots_x))
        u_f2 = float(np.interp(x2, x_knots_x, u_knots_x))
        v_f1 = float(np.interp(y1, y_knots_y, v_knots_y))
        v_f2 = float(np.interp(y2, y_knots_y, v_knots_y))
        draw = ImageDraw.Draw(warped_img)
        draw.rectangle(
            [round(u_f1), round(v_f1), round(u_f2), round(v_f2)],
            outline=(0, 229, 255),
            width=2,
        )

    meta = {
        "u_knots": u_knots_x,
        "x_knots": x_knots_x,
        "v_knots": v_knots_y,
        "y_knots": y_knots_y,
    }
    return warped_img, meta


def fovea_box_to_page_box(
    local_box: NormBBox,
    warp_meta: dict[str, list[float]],
    src_size: tuple[int, int],
    out_size: tuple[int, int],
) -> NormBBox:
    """Invert normalized bounding box on foveated image back to whole-page normalized coordinates."""
    w_src, h_src = src_size
    w_out, h_out = out_size

    u_px = [local_box.x1 * w_out, local_box.x2 * w_out]
    v_px = [local_box.y1 * h_out, local_box.y2 * h_out]

    x_src = np.interp(u_px, warp_meta["u_knots"], warp_meta["x_knots"])
    y_src = np.interp(v_px, warp_meta["v_knots"], warp_meta["y_knots"])

    norm_x1 = max(0.0, min(1.0, float(x_src[0]) / w_src))
    norm_x2 = max(0.0, min(1.0, float(x_src[1]) / w_src))
    norm_y1 = max(0.0, min(1.0, float(y_src[0]) / h_src))
    norm_y2 = max(0.0, min(1.0, float(y_src[1]) / h_src))

    if norm_x2 <= norm_x1:
        norm_x2 = min(1.0, norm_x1 + 1e-4)
    if norm_y2 <= norm_y1:
        norm_y2 = min(1.0, norm_y1 + 1e-4)

    return NormBBox(x1=norm_x1, y1=norm_y1, x2=norm_x2, y2=norm_y2)


def foveate_zoom(
    page: PageAsset,
    page_box_requested: NormBBox,
    settings: Settings,
    out_dir: str,
    crop_id: str,
    target: str,
) -> CropAsset:
    """Generate anisotropic topological foveated image preserving table header and row stubs."""
    padded_box = pad_and_clamp(
        page_box_requested,
        pad_ratio=settings.zoom_pad_ratio,
        min_side=settings.zoom_min_norm_side,
    )
    pixel_box = norm_to_pixel(padded_box, page.source_size)
    x1, y1, x2, y2 = pixel_box

    crops_dir = Path(out_dir) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    fovea_filename = f"{crop_id}_fovea_p{page.page}_{x1}_{y1}_{x2}_{y2}.png"
    crop_path = crops_dir / fovea_filename

    target_size = (settings.zoom_target_long_side, settings.zoom_target_long_side)

    if crop_path.exists():
        meta = compute_fovea_warp_meta(
            src_size=page.source_size,
            pixel_box=pixel_box,
            out_size=target_size,
            header_ratio=settings.fovea_header_ratio,
            stub_ratio=settings.fovea_stub_ratio,
            focus_share=settings.fovea_focus_share,
            anchor_share=settings.fovea_anchor_share,
        )
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
                warp_meta=meta,
            )

    with Image.open(page.source_path) as source_img:
        warped_img, meta = fovea_warp_image(
            src_img=source_img,
            pixel_box=pixel_box,
            out_size=target_size,
            header_ratio=settings.fovea_header_ratio,
            stub_ratio=settings.fovea_stub_ratio,
            focus_share=settings.fovea_focus_share,
            anchor_share=settings.fovea_anchor_share,
            draw_seam=True,
        )
        warped_img.save(crop_path, format="PNG")

        return CropAsset(
            crop_id=crop_id,
            page=page.page,
            page_box=padded_box,
            requested_box=page_box_requested,
            path=str(crop_path.resolve()),
            size=warped_img.size,
            target=target,
            pixel_box=pixel_box,
            warp_meta=meta,
        )
