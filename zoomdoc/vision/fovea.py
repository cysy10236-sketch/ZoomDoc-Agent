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
    *,
    secondary_band: tuple[int, int] | None = None,
    secondary_share: float = 0.14,
) -> tuple[np.ndarray, list[float], list[float]]:
    """Build 1D piecewise monotonic sampling coordinate map and knot points.

    Supports an optional secondary structural anchor band `secondary_band = (b1, b2)`
    between `anchor_end` and `focus_start` (e.g., mid-page table column-header band),
    preserving strict monotonicity so `np.interp` inversion remains exact.

    Returns:
        (src_coords, u_knots, x_knots)
    """
    if src_len < 2 or out_len < 2:
        raise ValueError(f"Dimensions must be >= 2, got src_len={src_len}, out_len={out_len}")
    if not (0 <= focus_start < focus_end <= src_len):
        raise ValueError(f"Invalid focus interval [{focus_start}, {focus_end}] for src_len={src_len}")
    if anchor_end < 0 or anchor_end > src_len:
        raise ValueError(f"Invalid anchor_end={anchor_end} for src_len={src_len}")

    eff_anchor = min(anchor_end, focus_start)

    # Validate optional mid-page secondary band (must lie strictly inside [eff_anchor, focus_start])
    valid_sec: tuple[int, int] | None = None
    if secondary_band is not None:
        sb1 = max(eff_anchor, int(secondary_band[0]))
        sb2 = min(focus_start, int(secondary_band[1]))
        if sb2 - sb1 >= 4 and out_len >= 16:
            valid_sec = (sb1, sb2)

    if valid_sec is None:
        gap1 = focus_start - eff_anchor
        gap2 = src_len - focus_end

        min_gaps = (1 if gap1 > 0 else 0) + (1 if gap2 > 0 else 0)
        focus_ratio = (focus_end - focus_start) / float(src_len)
        eff_focus_share = max(focus_share, min(0.95, focus_ratio))
        if eff_anchor > 0:
            max_anc_share = max(0.02, 0.97 - eff_focus_share)
            eff_anchor_share = min(anchor_share, (eff_anchor / src_len) * 1.5, max_anc_share)
        else:
            eff_anchor_share = 0.0
        target_u_anchor = max(1, round(eff_anchor_share * out_len)) if eff_anchor > 0 else 0
        u_anchor = min(target_u_anchor, max(0, out_len - 2 - min_gaps)) if eff_anchor > 0 else 0

        target_u_focus = max(2, round(eff_focus_share * out_len))
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
    else:
        sb1, sb2 = valid_sec
        # Build non-empty segments: (x_start, x_end, kind)
        raw_segs: list[tuple[int, int, str]] = []
        if eff_anchor > 0:
            raw_segs.append((0, eff_anchor, "anchor"))
        if sb1 > eff_anchor:
            raw_segs.append((eff_anchor, sb1, "gap"))
        raw_segs.append((sb1, sb2, "sec"))
        if focus_start > sb2:
            raw_segs.append((sb2, focus_start, "gap"))
        raw_segs.append((focus_start, focus_end, "focus"))
        if src_len > focus_end:
            raw_segs.append((focus_end, src_len, "gap"))

        focus_ratio = (focus_end - focus_start) / float(src_len)
        n_gaps = sum(1 for _, _, k in raw_segs if k == "gap")
        eff_anc_s = min(anchor_share * 0.65, (eff_anchor / src_len) * 1.3) if eff_anchor > 0 else 0.0
        eff_sec_s = min(secondary_share, ((sb2 - sb1) / src_len) * 2.2)
        base_foc_s = min(focus_share, 0.88 - eff_anc_s - eff_sec_s)
        eff_foc_s = max(base_foc_s, min(0.94 - eff_anc_s - eff_sec_s, focus_ratio))

        u_anc = max(2, round(eff_anc_s * out_len)) if eff_anchor > 0 else 0
        u_sec = max(2, round(eff_sec_s * out_len))
        u_foc = max(4, round(eff_foc_s * out_len))

        fixed_sum = u_anc + u_sec + u_foc
        if fixed_sum + n_gaps > out_len:
            excess = fixed_sum + n_gaps - out_len
            u_foc = max(4, u_foc - excess)

        rem_gaps = max(n_gaps, out_len - (u_anc + u_sec + u_foc))
        total_gap_src = max(1, sum(b - a for a, b, k in raw_segs if k == "gap"))

        x_knots = [0.0]
        u_knots = [0.0]
        gaps_seen = 0
        gaps_allocated = 0

        for a, b, kind in raw_segs:
            if kind == "anchor":
                du = u_anc
            elif kind == "sec":
                du = u_sec
            elif kind == "focus":
                du = u_foc
            else:
                gaps_seen += 1
                if gaps_seen == n_gaps:
                    du = max(1, rem_gaps - gaps_allocated)
                else:
                    du = max(1, min(rem_gaps - gaps_allocated - (n_gaps - gaps_seen), round(rem_gaps * ((b - a) / total_gap_src))))
                    gaps_allocated += du
            x_knots.append(float(b))
            u_knots.append(float(u_knots[-1] + du))

    # Ensure last knot is exactly (src_len, out_len)
    x_knots[-1] = float(src_len)
    u_knots[-1] = float(out_len)

    # Compute sampling coordinates via piecewise linear interpolation
    u_grid = np.arange(out_len, dtype=np.float64)
    src_coords = np.interp(u_grid, u_knots, x_knots)
    np.clip(src_coords, 0.0, float(src_len - 1), out=src_coords)

    return src_coords, u_knots, x_knots


def detect_table_anchor_bands(
    src_img: Image.Image,
    pixel_box: tuple[int, int, int, int],
    header_ratio: float = 0.12,
    stub_ratio: float = 0.15,
) -> tuple[int, tuple[int, int] | None]:
    """Detect optimal left row-stub boundary and mid-page table column-header band.

    1. Left stub boundary `stub_end_x`: if `x1` lies to the right of `stub_ratio * W`,
       scans `[stub_ratio * W, min(x1, 0.34 * W)]` for a vertical grid rule or
       whitespace column gutter so multi-word row labels are not truncated mid-word.
    2. Mid-page table header band `(ty1, ty2)`: scans `[header_ratio * H, y1]` for
       the topmost horizontal table border / dense header row band so tables starting
       mid-page retain their actual column headers collinearly above the focus cell.
    """
    x1, y1, x2, _ = pixel_box
    w_src, h_src = src_img.size
    base_stub_x = min(x1, int(round(stub_ratio * w_src)))
    base_hdr_y = min(y1, int(round(header_ratio * h_src)))

    gray = np.asarray(src_img.convert("L"), dtype=np.float32)
    ink = gray < 205.0  # boolean mask of ink / border pixels

    # --- 1. Refine left row-stub end coordinate ---
    stub_end_x = base_stub_x
    max_stub_cand = min(x1 - 4, int(round(0.33 * w_src)))
    if max_stub_cand > base_stub_x + 12 and h_src >= 64:
        y_top_scan = max(base_hdr_y, int(round(0.14 * h_src)))
        y_bot_scan = min(h_src, max(y_top_scan + 20, pixel_box[3]))
        band_x = ink[y_top_scan:y_bot_scan, :max_stub_cand]
        col_ink_density = band_x.mean(axis=0)

        # Look for vertical table divider line or whitespace gutter after first text stub block
        search_start = max(int(round(0.14 * w_src)), base_stub_x)
        sub_density = col_ink_density[search_start:max_stub_cand]
        if sub_density.size >= 8:
            # High-density vertical rule line (e.g. table column divider > 0.35 height)
            v_rules = np.where(sub_density > 0.35)[0]
            if v_rules.size > 0:
                stub_end_x = int(search_start + v_rules[0] + 2)
            else:
                # Find rightmost ink column of the left stub followed by >= 6px whitespace gap
                has_ink_cols = np.where(col_ink_density[:max_stub_cand] > 0.012)[0]
                if has_ink_cols.size > 0:
                    # Walk from base_stub_x rightward until a clean whitespace gap (>= 6 cols < 0.005)
                    for cx in range(search_start, max_stub_cand - 6):
                        if np.all(col_ink_density[cx : cx + 6] < 0.005) and np.any(
                            col_ink_density[int(0.05 * w_src) : cx] > 0.015
                        ):
                            stub_end_x = cx + 3
                            break

    stub_end_x = max(0, min(x1, stub_end_x))

    # --- 2. Detect mid-page table column-header band above y1 ---
    sec_band_y: tuple[int, int] | None = None
    scan_y1 = base_hdr_y + 6
    scan_y2 = y1 - 8
    if scan_y2 - scan_y1 >= int(round(0.05 * h_src)):
        x_left = max(0, min(stub_end_x, int(round(0.06 * w_src))))
        x_right = min(w_src, max(x2, int(round(0.88 * w_src))))
        if x_right - x_left >= 40:
            roi_ink = ink[scan_y1:scan_y2, x_left:x_right]
            row_density = roi_ink.mean(axis=1)

            # Horizontal rule lines have high horizontal ink continuity (> 0.30 across table width)
            h_rules = np.where(row_density > 0.30)[0]
            if h_rules.size > 0:
                top_rule = int(scan_y1 + h_rules[0])
                # Look for second horizontal rule (bottom of header row) within 8% of page height
                max_hdr_span = max(24, int(round(0.075 * h_src)))
                lower_rules = [
                    int(scan_y1 + r)
                    for r in h_rules
                    if 10 <= (int(scan_y1 + r) - top_rule) <= max_hdr_span
                ]
                ty1 = max(base_hdr_y, top_rule - 6)
                ty2 = min(y1, (lower_rules[0] + 6) if lower_rules else (top_rule + int(round(0.045 * h_src))))
                if ty2 - ty1 >= 12:
                    sec_band_y = (ty1, ty2)

    return stub_end_x, sec_band_y


def compute_adaptive_fovea_size(
    src_size: tuple[int, int],
    pixel_box: tuple[int, int, int, int],
    target_long_side: int = 1024,
    header_ratio: float = 0.12,
    stub_ratio: float = 0.15,
) -> tuple[int, int]:
    """Compute aspect-ratio-aware output canvas size preserving native DPI on wide row strips."""
    w_src, h_src = src_size
    x1, y1, x2, y2 = pixel_box
    eff_w = (x2 - x1) + min(x1, int(round(stub_ratio * w_src)))
    eff_h = (y2 - y1) + min(y1, int(round(header_ratio * h_src)))
    raw_aspect = eff_w / max(1.0, float(eff_h))
    if raw_aspect > 1.6 and eff_w > target_long_side:
        wide_w = min(1536, eff_w)
        wide_h = max(round(target_long_side * 0.68), min(target_long_side, round(wide_w / min(2.2, raw_aspect))))
        return (wide_w, wide_h)
    aspect = max(0.68, min(1.48, raw_aspect))
    min_short = max(64, round(target_long_side * 0.68))
    if aspect >= 1.0:
        return (target_long_side, max(min_short, int(round(target_long_side / aspect))))
    return (max(min_short, int(round(target_long_side * aspect))), target_long_side)


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
    *,
    auto_detect_anchors: bool = False,
) -> tuple[Image.Image, dict[str, list[float]]]:
    """Warp source image using 2D separable anisotropic topological foveation.

    Preserves orthogonal collinearity:
    Vertical columns in header band remain perfectly collinear with focus columns.
    Horizontal rows in stub band remain perfectly collinear with focus rows.
    """
    x1, y1, x2, y2 = pixel_box
    w_src, h_src = src_img.size
    w_out, h_out = out_size

    sec_band_y: tuple[int, int] | None = None
    if auto_detect_anchors:
        anchor_x, sec_band_y = detect_table_anchor_bands(
            src_img,
            pixel_box=pixel_box,
            header_ratio=header_ratio,
            stub_ratio=stub_ratio,
        )
    else:
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
        secondary_band=sec_band_y,
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

    auto_anchors = bool(getattr(settings, "fovea_auto_anchors", False))
    adaptive_aspect = bool(getattr(settings, "fovea_adaptive_aspect", False))
    if adaptive_aspect:
        target_size = compute_adaptive_fovea_size(
            src_size=page.source_size,
            pixel_box=pixel_box,
            target_long_side=settings.zoom_target_long_side,
            header_ratio=settings.fovea_header_ratio,
            stub_ratio=settings.fovea_stub_ratio,
        )
    else:
        target_size = (settings.zoom_target_long_side, settings.zoom_target_long_side)

    meta_path = crops_dir / f"{crop_id}_fovea_p{page.page}_{x1}_{y1}_{x2}_{y2}.meta.json"

    if crop_path.exists():
        import json

        meta: dict[str, list[float]] | None = None
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = None
        if meta is None:
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
        import json

        warped_img, meta = fovea_warp_image(
            src_img=source_img,
            pixel_box=pixel_box,
            out_size=target_size,
            header_ratio=settings.fovea_header_ratio,
            stub_ratio=settings.fovea_stub_ratio,
            focus_share=settings.fovea_focus_share,
            anchor_share=settings.fovea_anchor_share,
            draw_seam=True,
            auto_detect_anchors=auto_anchors,
        )
        warped_img.save(crop_path, format="PNG")
        try:
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            pass

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
