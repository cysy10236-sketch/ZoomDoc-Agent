from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    pass

ARRANGEMENT: dict[int, tuple[int, int]] = {
    20: (4, 5),
    19: (4, 5),
    18: (4, 5),
    17: (4, 5),
    16: (4, 4),
    15: (4, 4),
    14: (4, 4),
    13: (4, 4),
    12: (4, 3),
    11: (4, 3),
    10: (4, 3),
    9: (3, 3),
    8: (2, 4),
    7: (2, 4),
    6: (3, 2),
    5: (3, 2),
    4: (2, 2),
    3: (2, 2),
    2: (1, 2),
    1: (1, 1),
}

ORDINALS: list[str] = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
]


def _stamp_index_badge(img: Image.Image, idx: int) -> Image.Image:
    """Draw a compact high-contrast [idx] badge in the top-right margin of a thumbnail copy."""
    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)
    w, _ = annotated.size
    font_px = max(15, min(26, round(w * 0.058)))
    try:
        font = ImageFont.load_default(size=font_px)
    except TypeError:
        font = ImageFont.load_default()

    label = f"[{idx}]"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x, pad_y = 6, 4
    box_w = tw + 2 * pad_x
    box_h = th + 2 * pad_y
    x0 = max(2, w - box_w - 6)
    y0 = 6
    draw.rectangle(
        [x0, y0, x0 + box_w, y0 + box_h],
        fill=(15, 23, 42),
        outline=(255, 255, 255),
        width=1,
    )
    draw.text((x0 + pad_x - bbox[0], y0 + pad_y - bbox[1]), label, fill=(255, 255, 255), font=font)
    return annotated


def concat_grid(
    images: list[Image.Image],
    arrangement: tuple[int, int],
    line_width: int,
    *,
    annotate_indices: bool = False,
) -> tuple[Image.Image, tuple[int, int]]:
    """Compose thumbnail images into a single grid canvas with black dividers.

    Returns:
        (composite_image, cell_size)
    """
    if not images:
        raise ValueError("Cannot concatenate empty image list")

    rows, columns = arrangement
    total_cells = rows * columns

    # 1. Determine most common size
    sizes = [img.size for img in images]
    most_common_size = Counter(sizes).most_common(1)[0][0]
    cell_w, cell_h = most_common_size

    # 2. Resize all to most common size (and optionally stamp Set-of-Mark index badges)
    images_resized = [
        img.resize(most_common_size, Image.Resampling.LANCZOS) if img.size != most_common_size else img
        for img in images
    ]
    if annotate_indices:
        images_resized = [
            _stamp_index_badge(im, idx) for idx, im in enumerate(images_resized[:total_cells])
        ]

    # 3. Pad with white background or truncate
    if len(images_resized) < total_cells:
        pad_count = total_cells - len(images_resized)
        blank = Image.new("RGB", most_common_size, color=(255, 255, 255))
        images_resized.extend([blank] * pad_count)
    elif len(images_resized) > total_cells:
        images_resized = images_resized[:total_cells]

    # 4. Canvas dimensions with dividers
    total_w = columns * cell_w + (columns - 1) * line_width
    total_h = rows * cell_h + (rows - 1) * line_width
    canvas = Image.new("RGB", (total_w, total_h), color=(0, 0, 0))

    # 5. Paste cells
    for idx, img in enumerate(images_resized):
        r = idx // columns
        c = idx % columns
        x = c * (cell_w + line_width)
        y = r * (cell_h + line_width)
        canvas.paste(img, (x, y))

    return (canvas, most_common_size)


def build_page_map(n: int, arrangement: tuple[int, int]) -> str:
    """Generate dynamic grid layout textual instructions for VLM prompt."""
    if n == 1:
        return "Your choice is empty as [] or this image as [0]!"

    rows, columns = arrangement
    row_strings: list[str] = []

    for r in range(rows):
        start_idx = r * columns
        if start_idx >= n:
            break
        actual_in_row = min(columns, n - start_idx)

        cell_phrases: list[str] = []
        for c in range(actual_in_row):
            col_ordinal = ORDINALS[c] if c < len(ORDINALS) else f"{c+1}th"
            item_num = start_idx + c
            cell_phrases.append(f"the {col_ordinal} one is number {item_num}")

        row_ord = ORDINALS[r] if r < len(ORDINALS) else f"{r+1}th"
        row_str = f"The {row_ord} row, {', '.join(cell_phrases)}."
        row_strings.append(row_str)

    joined = " ".join(row_strings)
    return f"{joined} The image numbers are from 0-{n-1}!"
