from __future__ import annotations

import math
from typing import TYPE_CHECKING

from zoomdoc.errors import GeometryError
from zoomdoc.schemas import NormBBox

if TYPE_CHECKING:
    from zoomdoc.config import BBoxFormat

EPS: float = 1e-6


def vlm_bbox_to_norm(
    raw: tuple[float, float, float, float] | list[float],
    fmt: BBoxFormat,
    sent_size: tuple[int, int] | None = None,
) -> NormBBox:
    """Convert raw coordinates from VLM response into normalized [0, 1] NormBBox."""
    if len(raw) != 4:
        raise GeometryError(f"Expected 4 coordinates, got {len(raw)}: {raw}")

    a, b, c, d = raw

    if fmt == "xyxy_1000":
        x1, y1, x2, y2 = a / 1000.0, b / 1000.0, c / 1000.0, d / 1000.0
    elif fmt == "yxyx_1000":
        x1, y1, x2, y2 = b / 1000.0, a / 1000.0, d / 1000.0, c / 1000.0
    elif fmt == "xyxy_abs":
        if sent_size is None or sent_size[0] <= 0 or sent_size[1] <= 0:
            raise GeometryError(f"sent_size must be positive for xyxy_abs, got {sent_size}")
        w_s, h_s = sent_size
        x1, y1, x2, y2 = a / float(w_s), b / float(h_s), c / float(w_s), d / float(h_s)
    else:
        raise GeometryError(f"Unsupported bbox_format: {fmt}")

    # Clamp to [0, 1]
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))

    # Swap inverted bounds
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    if x1 >= x2 or y1 >= y2:
        raise GeometryError(f"Degenerate bounding box: ({x1}, {y1}, {x2}, {y2}) from raw {raw}")

    return NormBBox(x1=x1, y1=y1, x2=x2, y2=y2)


def crop_box_to_page_box(local: NormBBox, crop_page_box: NormBBox) -> NormBBox:
    """Convert coordinates local to a crop into whole-page normalized coordinates."""
    b_w = crop_page_box.x2 - crop_page_box.x1
    b_h = crop_page_box.y2 - crop_page_box.y1

    x1 = crop_page_box.x1 + local.x1 * b_w
    y1 = crop_page_box.y1 + local.y1 * b_h
    x2 = crop_page_box.x1 + local.x2 * b_w
    y2 = crop_page_box.y1 + local.y2 * b_h

    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))

    return NormBBox(x1=x1, y1=y1, x2=x2, y2=y2)


def pad_and_clamp(box: NormBBox, pad_ratio: float, min_side: float) -> NormBBox:
    """Expand bounding box with pad_ratio, ensure min_side, and shift/clamp to [0, 1]."""
    w = box.x2 - box.x1
    h = box.y2 - box.y1

    x1_prime = box.x1 - pad_ratio * w
    x2_prime = box.x2 + pad_ratio * w
    y1_prime = box.y1 - pad_ratio * h
    y2_prime = box.y2 + pad_ratio * h

    if (x2_prime - x1_prime) < min_side:
        cx = (x1_prime + x2_prime) / 2.0
        x1_prime = cx - min_side / 2.0
        x2_prime = cx + min_side / 2.0

    if (y2_prime - y1_prime) < min_side:
        cy = (y1_prime + y2_prime) / 2.0
        y1_prime = cy - min_side / 2.0
        y2_prime = cy + min_side / 2.0

    # Shift into [0, 1]
    if x1_prime < 0.0:
        shift_x = -x1_prime
        x1_prime += shift_x
        x2_prime += shift_x
    if x2_prime > 1.0:
        shift_x = x2_prime - 1.0
        x1_prime -= shift_x
        x2_prime -= shift_x

    if y1_prime < 0.0:
        shift_y = -y1_prime
        y1_prime += shift_y
        y2_prime += shift_y
    if y2_prime > 1.0:
        shift_y = y2_prime - 1.0
        y1_prime -= shift_y
        y2_prime -= shift_y

    x1_prime = max(0.0, min(1.0, x1_prime))
    x2_prime = max(0.0, min(1.0, x2_prime))
    y1_prime = max(0.0, min(1.0, y1_prime))
    y2_prime = max(0.0, min(1.0, y2_prime))

    return NormBBox(x1=x1_prime, y1=y1_prime, x2=x2_prime, y2=y2_prime)


def norm_to_pixel(box: NormBBox, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Convert normalized box to source pixel integer coordinates (X1, Y1, X2, Y2)."""
    w, h = size
    x1 = math.floor(box.x1 * w + EPS)
    y1 = math.floor(box.y1 * h + EPS)
    x2 = math.ceil(box.x2 * w - EPS)
    y2 = math.ceil(box.y2 * h - EPS)

    x1 = max(0, min(w - 1, x1))
    y1 = max(0, min(h - 1, y1))
    x2 = max(x1 + 1, min(w, x2))
    y2 = max(y1 + 1, min(h, y2))

    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise GeometryError(f"Computed invalid pixel box ({x1}, {y1}, {x2}, {y2}) for size {size}")

    return (x1, y1, x2, y2)


def upsample_size(
    pixel_box: tuple[int, int, int, int],
    target_long_side: int,
    max_upscale: float,
) -> tuple[int, int]:
    """Calculate target resize dimensions for cropped region."""
    x1, y1, x2, y2 = pixel_box
    w_px = x2 - x1
    h_px = y2 - y1

    s = min(max_upscale, target_long_side / float(max(w_px, h_px)))
    return (round(w_px * s), round(h_px * s))


def grid_point_to_page(
    u: float,
    v: float,
    n: int,
    arrangement: tuple[int, int],
    cell_size: tuple[int, int],
    line_width: int,
) -> tuple[int, float, float]:
    """Map normalized point (u, v) on composite grid image back to (page_index, norm_x, norm_y)."""
    rows, columns = arrangement
    w, h = cell_size
    line = line_width

    total_width = columns * w + (columns - 1) * line
    total_height = rows * h + (rows - 1) * line

    x = u * total_width
    y = v * total_height

    c = int(math.floor(x / (w + line)))
    r = int(math.floor(y / (h + line)))

    lx = x - c * (w + line)
    ly = y - r * (h + line)

    if lx > w or ly > h:
        raise GeometryError(f"Point ({u}, {v}) falls on divider line (lx={lx}, ly={ly})")

    idx = r * columns + c
    if idx >= n:
        raise GeometryError(f"Point falls on empty grid padding cell {idx} >= {n}")

    norm_x = lx / float(w)
    norm_y = ly / float(h)
    return (idx, norm_x, norm_y)


def grid_box_to_page(
    box: NormBBox,
    n: int,
    arrangement: tuple[int, int],
    cell_size: tuple[int, int],
    line_width: int,
) -> tuple[int, NormBBox]:
    """Map normalized bbox on composite grid image back to (page_index, page_norm_box)."""
    idx1, x1, y1 = grid_point_to_page(box.x1, box.y1, n, arrangement, cell_size, line_width)
    idx2, x2, y2 = grid_point_to_page(box.x2, box.y2, n, arrangement, cell_size, line_width)

    if idx1 != idx2:
        raise GeometryError(f"BBox spans across multiple grid cells ({idx1} != {idx2})")

    return (idx1, NormBBox(x1=x1, y1=y1, x2=x2, y2=y2))
