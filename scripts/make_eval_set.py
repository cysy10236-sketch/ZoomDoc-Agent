from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path

import pymupdf as fitz


def get_region_rect(region: str) -> fitz.Rect:
    """Return bounding box for the table based on region name."""
    if region == "top_left":
        return fitz.Rect(50, 220, 230, 300)
    elif region == "center":
        return fitz.Rect(200, 420, 380, 500)
    elif region == "bottom_right":
        return fitz.Rect(380, 700, 560, 780)
    else:
        raise ValueError(f"Unknown region: {region}")


def generate_code(rng: random.Random, confusable: bool) -> str:
    """Generate ZD-XXXX-YY code."""
    num = rng.randint(1000, 9999)
    if confusable:
        # Confusable character pairs: Q/D, O/0, 1/l, 5/S, 8/B
        suffix_pair = rng.choice(["QD", "DX", "QO", "DO", "SZ", "BV", "IQ", "LO"])
    else:
        # Unambiguous characters
        suffix_pair = rng.choice(["TR", "WP", "NK", "MZ", "HM", "RT", "PX", "KY"])
    return f"ZD-{num}-{suffix_pair}"


def draw_table(
    page: fitz.Page,
    rect: fitz.Rect,
    code: str,
    status: str,
    batch: str,
    font_pt: float,
    is_gold: bool,
) -> None:
    """Draw a 3x2 table inside rect."""
    page.draw_rect(rect, color=(0.1, 0.1, 0.1), width=1)
    x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
    w = x1 - x0
    h = y1 - y0
    mid_x = x0 + w / 2
    row1_y = y0 + h / 3
    row2_y = y0 + 2 * h / 3

    page.draw_line(fitz.Point(x0, row1_y), fitz.Point(x1, row1_y), color=(0.5, 0.5, 0.5), width=0.5)
    page.draw_line(fitz.Point(x0, row2_y), fitz.Point(x1, row2_y), color=(0.5, 0.5, 0.5), width=0.5)
    page.draw_line(fitz.Point(mid_x, y0), fitz.Point(mid_x, y1), color=(0.5, 0.5, 0.5), width=0.5)

    # Header labels (always 5pt)
    page.insert_text(fitz.Point(x0 + 5, y0 + 15), "Item Code", fontsize=5, color=(0, 0, 0))
    page.insert_text(fitz.Point(mid_x + 5, y0 + 15), "Status", fontsize=5, color=(0, 0, 0))

    # Row 1 (Item code with specified font_pt)
    text_color = (0.8, 0, 0) if is_gold else (0.1, 0.1, 0.1)
    page.insert_text(fitz.Point(x0 + 5, row1_y + 15), code, fontsize=font_pt, color=text_color)
    page.insert_text(fitz.Point(mid_x + 5, row1_y + 15), status, fontsize=5, color=(0, 0, 0))

    # Row 2 (Batch info)
    page.insert_text(fitz.Point(x0 + 5, row2_y + 15), "Batch #", fontsize=5, color=(0, 0, 0))
    page.insert_text(fitz.Point(mid_x + 5, row2_y + 15), batch, fontsize=5, color=(0, 0, 0))


def create_eval_pdf(
    out_path: Path,
    gold_page: int,
    gold_code: str,
    font_pt: float,
    region: str,
    rng: random.Random,
) -> None:
    """Create a 4-page synthetic PDF containing target table on gold_page and distractors on other pages."""
    doc = fitz.open()
    width, height = 595.3, 841.9
    rect = get_region_rect(region)

    for page_idx in range(4):
        p_num = page_idx + 1
        page = doc.new_page(width=width, height=height)

        is_gold = p_num == gold_page
        if is_gold:
            title = f"Section {p_num}: Verified Components & Active Operations"
            body = (
                f"Official page {p_num} contains the active certified component specifications.\n"
                "All items listed under this section have passed dimensional and visual inspection.\n"
                "System administrators should verify the item code in the verified components table below."
            )
        else:
            title = f"Section {p_num}: Archived Documents & Non-Target Records"
            body = (
                f"Official page {p_num} contains archived reference documents and superseded entries.\n"
                "The records below are retained solely for historical audit logging.\n"
                "Do not use these values for active verification queries."
            )

        page.insert_text(fitz.Point(50, 70), title, fontsize=20, color=(0, 0, 0))
        page.insert_text(fitz.Point(50, 115), body, fontsize=10, color=(0.2, 0.2, 0.2))

        if is_gold:
            draw_table(page, rect, gold_code, "VERIFIED", f"B-{rng.randint(10, 99)}", font_pt, is_gold=True)
        else:
            distractor_code = f"DIS-{rng.randint(1000, 9999)}-{rng.choice(['AA', 'BB', 'CC', 'XX'])}"
            draw_table(page, rect, distractor_code, "ARCHIVED", f"B-{rng.randint(10, 99)}", font_pt, is_gold=False)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    doc.close()


def make_eval_set(out_dir: str, n: int = 24, seed: int = 20261110) -> None:
    rng = random.Random(seed)
    base_dir = Path(out_dir)
    pdf_dir = base_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    font_pts = [4, 6]
    regions = ["top_left", "center", "bottom_right"]
    confusables = [False, True]

    # 2 x 3 x 2 = 12 cells
    cells = list(itertools.product(font_pts, regions, confusables))
    samples_per_cell = n // len(cells)
    if samples_per_cell < 1:
        samples_per_cell = 1

    records: list[dict] = []
    case_idx = 1

    for font_pt, region, confusable in cells:
        for _ in range(samples_per_cell):
            if case_idx > n:
                break
            case_id = f"case_{case_idx:03d}"
            gold_code = generate_code(rng, confusable)
            gold_page = rng.choice([2, 3])  # vary between page 2 and page 3

            pdf_name = f"{case_id}.pdf"
            pdf_path = pdf_dir / pdf_name
            create_eval_pdf(
                out_path=pdf_path,
                gold_page=gold_page,
                gold_code=gold_code,
                font_pt=font_pt,
                region=region,
                rng=rng,
            )

            record = {
                "case_id": case_id,
                "pdf_path": str(pdf_path.resolve()),
                "question": "What is the exact Item Code in the table with Status VERIFIED?",
                "gold": gold_code,
                "gold_page": gold_page,
                "font_pt": font_pt,
                "region": region,
                "confusable": confusable,
            }
            records.append(record)
            case_idx += 1

    manifest_path = base_dir / "manifest.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Generated {len(records)} test cases in {manifest_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Milestone 2.5 evaluation dataset.")
    parser.add_argument("--out", default="eval/m25", help="Output directory")
    parser.add_argument("--n", type=int, default=24, help="Number of test samples")
    parser.add_argument("--seed", type=int, default=20261110, help="Random seed")
    args = parser.parse_args()

    make_eval_set(args.out, n=args.n, seed=args.seed)
