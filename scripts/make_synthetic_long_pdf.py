from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pymupdf as fitz


def create_synthetic_long_pdf(
    out_path: str,
    total_pages: int = 100,
    gold_page: int = 73,
    gold_code: str = "ZD-9042-M3",
) -> dict[str, Any]:
    """Generate a multi-page (default 100 pages) synthetic document with native bookmarks,

    multi-level hierarchical sections, distractor tables, and a 5pt target code table on gold_page.
    """
    doc = fitz.open()
    width, height = 595.3, 841.9  # A4

    # TOC definitions: [[level, title, start_page], ...]
    toc: list[list[Any]] = [
        [1, "Part 1: Corporate Governance & Strategy", 1],
        [2, "1.1 Board Overview", 1],
        [2, "1.2 Risk Factors & Compliance", 13],
        [1, "Part 2: Operational Infrastructure & Supply Chain", 26],
        [2, "2.1 Logistics Network", 26],
        [2, "2.2 Warehouse Telemetry", 41],
        [1, "Part 3: Financial Statements & Audit Notes", 56],
        [2, "3.1 Consolidated Balance Sheets", 56],
        [2, "3.2 Off-Balance-Sheet Derivative Instruments", 69],
        [2, "3.3 Tax Reconciliation", 81],
        [1, "Part 4: Appendices & Regulatory Filings", 86],
        [2, "4.1 Subsidiary List", 86],
    ]

    # Pre-compute section title mapping for each page
    def get_section_for_page(p: int) -> str:
        current_sec = "Overview"
        for item in toc:
            if p >= item[2]:
                current_sec = item[1]
        return current_sec

    # Distractor pages
    distractor_pages = {5, 30, 90}

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        page = doc.new_page(width=width, height=height)

        sec_title = get_section_for_page(page_num)

        # 20pt Section Title
        page.insert_text(fitz.Point(50, 60), sec_title, fontsize=20, color=(0, 0, 0))

        # Page index sub-header
        page.insert_text(
            fitz.Point(50, 85),
            f"Page {page_num} of {total_pages} - Corporate Annual Audit Documentation",
            fontsize=9,
            color=(0.4, 0.4, 0.4),
        )

        # 11pt Regular Body Paragraphs
        body = (
            f"This chapter details operational telemetry and analytical disclosures for fiscal period 2026.\n"
            f"Section context: {sec_title}. All corporate actions comply with regulatory disclosure acts.\n"
            f"Internal telemetry audit point {page_num}.A confirms reconciliation of assets and liabilities.\n"
            "Key financial indicators are consolidated according to international statutory accounting standards.\n"
            "Detailed off-balance commitments, if any, are categorized under appropriate subsidiary notes.\n"
            f"Paragraph {page_num}.2: Routine operational parameters remain stable throughout the reporting quarter."
        )
        page.insert_text(fitz.Point(50, 120), body, fontsize=11, color=(0.2, 0.2, 0.2))

        # Check for gold table or distractor table
        is_gold = page_num == gold_page
        is_distractor = (page_num in distractor_pages) and not is_gold

        if is_gold or is_distractor:
            target_text = gold_code if is_gold else "ZD-0000-XX"
            color_target = (0.8, 0, 0) if is_gold else (0.3, 0.3, 0.3)

            # Draw small table at bottom right
            table_rect = fitz.Rect(380, 700, 560, 780)
            page.draw_rect(table_rect, color=(0.1, 0.1, 0.1), width=1)
            page.draw_line(fitz.Point(380, 725), fitz.Point(560, 725), color=(0.5, 0.5, 0.5), width=0.5)
            page.draw_line(fitz.Point(380, 750), fitz.Point(560, 750), color=(0.5, 0.5, 0.5), width=0.5)
            page.draw_line(fitz.Point(470, 700), fitz.Point(470, 780), color=(0.5, 0.5, 0.5), width=0.5)

            # Insert microscopic 5pt text
            page.insert_text(fitz.Point(385, 715), "Security ID", fontsize=5, color=(0, 0, 0))
            page.insert_text(fitz.Point(475, 715), "Validation", fontsize=5, color=(0, 0, 0))

            page.insert_text(fitz.Point(385, 740), target_text, fontsize=5, color=color_target)
            page.insert_text(fitz.Point(475, 740), "CONFIRMED", fontsize=5, color=(0, 0, 0))

            page.insert_text(fitz.Point(385, 765), "Ledger #", fontsize=5, color=(0, 0, 0))
            page.insert_text(fitz.Point(475, 765), f"L-0{page_num}", fontsize=5, color=(0, 0, 0))

    doc.set_toc(toc)

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_file))
    doc.close()

    return {
        "pdf_path": str(out_file.resolve()),
        "total_pages": total_pages,
        "gold_page": gold_page,
        "gold_code": gold_code,
        "toc": toc,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic long PDF (100 pages).")
    parser.add_argument("--out", default="runs/synthetic_100p.pdf", help="Output path")
    parser.add_argument("--pages", type=int, default=100, help="Total pages")
    parser.add_argument("--gold-page", type=int, default=73, help="Gold page index")
    parser.add_argument("--gold-code", default="ZD-9042-M3", help="Target secret code")
    args = parser.parse_args()

    meta = create_synthetic_long_pdf(
        out_path=args.out,
        total_pages=args.pages,
        gold_page=args.gold_page,
        gold_code=args.gold_code,
    )
    print(f"Generated synthetic long PDF: {meta['pdf_path']} ({meta['total_pages']} pages)")
