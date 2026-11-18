from __future__ import annotations

import argparse
import io
import json
import random
from pathlib import Path
from typing import Any

import pymupdf as fitz

ROW_METRICS: list[str] = [
    "Core Tier1 Capital",
    "Risk Weighted Assets",
    "Leverage Exposure",
    "Liquidity Coverage",
    "Net Stable Funding",
    "Counterparty VaR",
    "Stressed Credit Spread",
    "Sovereign Bond Buffer",
    "Derivative Margin Pool",
    "FX Hedging Reserve",
    "Collateral Haircut Cap",
    "Intraday Settlement",
    "Offshore Custody Pool",
    "Subordinated Capital",
    "Deferred Tax Shield",
    "Securitized Tranche B",
    "Operational Loss Cap",
    "Pension Liability Gap",
]

COL_HEADERS: list[str] = ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]


def _gen_code(rng: random.Random, used: set[str]) -> str:
    # Include high optical-confusion characters (0/D/O/Q, 6/8/B/9, 2/Z, 5/S)
    ambig_digits = ["0", "2", "5", "6", "8", "9", "1", "7"]
    ambig_alpha = ["D", "Q", "B", "S", "Z", "G", "R", "K", "P", "V", "X", "T"]
    while True:
        d_part = "".join(rng.choice(ambig_digits) for _ in range(4))
        s_part = "".join(rng.choice(ambig_alpha) for _ in range(2))
        code = f"ZD-{d_part}-{s_part}"
        if code != "ZD-7319-QX" and code not in used:
            used.add(code)
            return code


def _draw_dense_matrix_page(
    page: fitz.Page,
    sec_title: str,
    page_num: int,
    total_pages: int,
    matrix_codes: list[list[str]],
) -> None:
    page.insert_text(fitz.Point(45, 52), sec_title, fontsize=16, color=(0.05, 0.08, 0.15))
    page.insert_text(
        fitz.Point(45, 70),
        f"Page {page_num} of {total_pages} - Consolidated Quantitative Risk & Capital Matrix",
        fontsize=8,
        color=(0.4, 0.4, 0.45),
    )
    intro = (
        f"Schedule {page_num}.A reports audited multi-year statutory identifiers across {len(ROW_METRICS)} risk lines.\n"
        "Each cell records the authoritative 10-character registry code under the corresponding fiscal column.\n"
        "All entries below are active and confirmed; verify exact row metric and fiscal year alignment."
    )
    page.insert_text(fitz.Point(45, 95), intro, fontsize=8.5, color=(0.2, 0.22, 0.25))

    x_left = 42.0
    x_stub_end = 166.0
    col_w = 78.0
    n_cols = len(COL_HEADERS)
    x_right = x_stub_end + n_cols * col_w

    y_top = 195.0
    hdr_h = 24.0
    row_h = 28.0
    n_rows = len(ROW_METRICS)
    y_bot = y_top + hdr_h + n_rows * row_h

    # Outer border & header fill
    page.draw_rect(
        fitz.Rect(x_left, y_top, x_right, y_top + hdr_h),
        color=(0.1, 0.12, 0.18),
        fill=(0.93, 0.95, 0.98),
        width=1.2,
    )
    page.draw_rect(fitz.Rect(x_left, y_top, x_right, y_bot), color=(0.1, 0.12, 0.18), width=1.2)
    page.draw_line(
        fitz.Point(x_left, y_top + hdr_h),
        fitz.Point(x_right, y_top + hdr_h),
        color=(0.1, 0.12, 0.18),
        width=1.2,
    )

    # Vertical dividers (stub divider thicker so structural detector locks cleanly)
    page.draw_line(
        fitz.Point(x_stub_end, y_top),
        fitz.Point(x_stub_end, y_bot),
        color=(0.1, 0.12, 0.18),
        width=1.2,
    )
    for c_idx in range(1, n_cols):
        cx = x_stub_end + c_idx * col_w
        page.draw_line(fitz.Point(cx, y_top), fitz.Point(cx, y_bot), color=(0.65, 0.68, 0.74), width=0.5)

    # Header labels
    page.insert_text(fitz.Point(x_left + 5, y_top + 15), "Risk Metric Line", fontsize=7.5, color=(0, 0, 0))
    for c_idx, cname in enumerate(COL_HEADERS):
        cx = x_stub_end + c_idx * col_w
        page.insert_text(fitz.Point(cx + 16, y_top + 15), cname, fontsize=8.0, color=(0.05, 0.1, 0.25))

    # Data rows
    for r_idx, rname in enumerate(ROW_METRICS):
        ry0 = y_top + hdr_h + r_idx * row_h
        if r_idx > 0:
            page.draw_line(
                fitz.Point(x_left, ry0),
                fitz.Point(x_right, ry0),
                color=(0.75, 0.78, 0.82),
                width=0.4,
            )
        page.insert_text(fitz.Point(x_left + 5, ry0 + 17), rname, fontsize=6.5, color=(0.08, 0.08, 0.12))
        for c_idx in range(n_cols):
            cx = x_stub_end + c_idx * col_w
            code_val = matrix_codes[r_idx][c_idx]
            page.insert_text(fitz.Point(cx + 14, ry0 + 16.5), code_val, fontsize=3.8, color=(0.15, 0.15, 0.18))


def make_eval_m6_matrix(
    out_dir: str = "eval/m6_matrix",
    seed: int = 2026111806,
) -> list[dict[str, Any]]:
    """Build N=12 Wide-Matrix & Pure-Raster Stress Benchmark (6 vector matrix + 6 pure-raster scan matrix)."""
    rng = random.Random(seed)
    base_dir = Path(out_dir)
    pdf_dir = base_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    used_codes: set[str] = set()
    toc = [
        [1, "Part 1: Governance & Macro Framework", 1],
        [2, "1.1 Supervisory Scope", 1],
        [2, "1.2 Methodology Notes", 11],
        [1, "Part 2: Capital & Liquidity Schedules", 21],
        [2, "2.1 Tier-1 & Leverage Matrix", 21],
        [2, "2.2 Counterparty & Subordinated Matrix", 31],
        [1, "Part 3: Market & Operational Schedules", 41],
        [2, "3.1 Custody & Settlement Matrix", 41],
    ]

    # Target matrix pages across the 50-page document: 25 (2.1), 35 (2.2), 45 (3.1)
    matrix_pages = [25, 35, 45]

    def _build_doc(pdf_path: Path, rasterize_matrix_pages: bool) -> dict[int, list[list[str]]]:
        doc = fitz.open()
        w_pt, h_pt = 595.3, 841.9
        page_matrices: dict[int, list[list[str]]] = {}

        def _sec_title(p: int) -> str:
            cur = "1.1 Supervisory Scope"
            for item in toc:
                if item[0] == 2 and p >= item[2]:
                    cur = item[1]
            return cur

        for p_idx in range(50):
            p_num = p_idx + 1
            sec = _sec_title(p_num)

            if p_num in matrix_pages:
                mat = [
                    [_gen_code(rng, used_codes) for _ in range(len(COL_HEADERS))]
                    for _ in range(len(ROW_METRICS))
                ]
                page_matrices[p_num] = mat

                if rasterize_matrix_pages:
                    # Render vector page to 220 DPI PNG in memory, then insert into an empty image-only PDF page!
                    tmp_doc = fitz.open()
                    tmp_page = tmp_doc.new_page(width=w_pt, height=h_pt)
                    _draw_dense_matrix_page(tmp_page, sec, p_num, 50, mat)
                    pix = tmp_page.get_pixmap(matrix=fitz.Matrix(220.0 / 72.0, 220.0 / 72.0), alpha=False)
                    png_bytes = pix.tobytes("png")
                    tmp_doc.close()

                    img_page = doc.new_page(width=w_pt, height=h_pt)
                    img_page.insert_image(img_page.rect, stream=io.BytesIO(png_bytes).getvalue())
                else:
                    v_page = doc.new_page(width=w_pt, height=h_pt)
                    _draw_dense_matrix_page(v_page, sec, p_num, 50, mat)
            else:
                n_page = doc.new_page(width=w_pt, height=h_pt)
                n_page.insert_text(fitz.Point(45, 52), sec, fontsize=16, color=(0.05, 0.08, 0.15))
                n_page.insert_text(
                    fitz.Point(45, 70),
                    f"Page {p_num} of 50 - Narrative Disclosure Notes",
                    fontsize=8,
                    color=(0.4, 0.4, 0.45),
                )
                n_page.insert_text(
                    fitz.Point(45, 110),
                    f"Analytical commentary for {sec} (page {p_num}). Quantitative matrix schedules appear on designated exhibit pages.",
                    fontsize=9.5,
                    color=(0.2, 0.2, 0.24),
                )

        doc.set_toc(toc)
        doc.save(str(pdf_path))
        doc.close()
        return page_matrices

    vec_pdf = pdf_dir / "doc_matrix_vector_50p.pdf"
    ras_pdf = pdf_dir / "doc_matrix_raster_scan_50p.pdf"

    vec_mats = _build_doc(vec_pdf, rasterize_matrix_pages=False)
    ras_mats = _build_doc(ras_pdf, rasterize_matrix_pages=True)

    # Target cells located in bottom/right interior (rows 10..17, cols 2..4) so header & stub are spatially distant!
    query_specs = [
        (25, "2.1 Tier-1 & Leverage Matrix", 11, 2),       # Intraday Settlement, FY2024
        (25, "2.1 Tier-1 & Leverage Matrix", 15, 4),       # Securitized Tranche B, FY2026
        (35, "2.2 Counterparty & Subordinated Matrix", 13, 3),  # Subordinated Capital, FY2025
        (35, "2.2 Counterparty & Subordinated Matrix", 16, 2),  # Operational Loss Cap, FY2024
        (45, "3.1 Custody & Settlement Matrix", 12, 3),    # Offshore Custody Pool, FY2025
        (45, "3.1 Custody & Settlement Matrix", 17, 4),    # Pension Liability Gap, FY2026
    ]

    records: list[dict[str, Any]] = []
    idx = 1
    for modality, pdf_p, mats in [
        ("vector_matrix", vec_pdf, vec_mats),
        ("raster_scan_matrix", ras_pdf, ras_mats),
    ]:
        for p_num, sec_label, r_idx, c_idx in query_specs:
            row_name = ROW_METRICS[r_idx]
            col_name = COL_HEADERS[c_idx]
            gold_code = mats[p_num][r_idx][c_idx]
            records.append({
                "case_id": f"m6_case_{idx:03d}",
                "modality": modality,
                "pdf_path": pdf_p.as_posix(),
                "total_pages": 50,
                "gold_page": p_num,
                "gold": gold_code,
                "row_metric": row_name,
                "col_header": col_name,
                "font_pt": 3.8,
                "question": (
                    f"Under Section {sec_label}, what is the exact registry code for row "
                    f"'{row_name}' in fiscal column '{col_name}'?"
                ),
            })
            idx += 1

    manifest_path = base_dir / "manifest_m6.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Generated {len(records)} M6 matrix & raster-scan cases at {manifest_path}")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate M6 Wide-Matrix & Pure-Raster Benchmark.")
    parser.add_argument("--out", default="eval/m6_matrix")
    parser.add_argument("--seed", type=int, default=2026111806)
    args = parser.parse_args()
    make_eval_m6_matrix(out_dir=args.out, seed=args.seed)
