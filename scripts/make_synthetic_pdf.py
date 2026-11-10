from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf as fitz


def create_synthetic_pdf(out_path: str) -> None:
    """Create a 4-page A4 test document with a microscopic 5pt serial number table on page 3."""
    doc = fitz.open()

    # A4 dimensions in points: 595.3 x 841.9
    width, height = 595.3, 841.9

    for page_idx in range(4):
        page_num = page_idx + 1
        page = doc.new_page(width=width, height=height)

        # 24pt Heading
        title = f"Section {page_num}: Document Overview and System Operations"
        page.insert_text(fitz.Point(50, 70), title, fontsize=24, color=(0, 0, 0))

        # 11pt Regular Body Text
        body = (
            f"This is official page {page_num} of the ZoomDoc-Agent evaluation report.\n"
            "Standard multi-modal document understanding models process pages by converting\n"
            "dense vector embeddings or downsampled visual representations.\n"
            "However, ultra-fine typography and compact table fields require specialized zooming.\n"
            f"Paragraph {page_num}.1: System integrity checks are operational and within nominal bounds.\n"
            f"Paragraph {page_num}.2: Visual tokens are conserved using hierarchical coarse-to-fine routing."
        )
        page.insert_text(fitz.Point(50, 120), body, fontsize=11, color=(0.2, 0.2, 0.2))

        # On Page 3, insert a small 3x2 table at the bottom right with 5pt text
        if page_num == 3:
            # Bottom right rectangle roughly [400, 720, 550, 780]
            table_rect = fitz.Rect(380, 700, 560, 780)
            page.draw_rect(table_rect, color=(0.1, 0.1, 0.1), width=1)

            # Table rows and columns
            page.draw_line(fitz.Point(380, 725), fitz.Point(560, 725), color=(0.5, 0.5, 0.5), width=0.5)
            page.draw_line(fitz.Point(380, 750), fitz.Point(560, 750), color=(0.5, 0.5, 0.5), width=0.5)
            page.draw_line(fitz.Point(470, 700), fitz.Point(470, 780), color=(0.5, 0.5, 0.5), width=0.5)

            # Insert microscopic 5pt text
            page.insert_text(fitz.Point(385, 715), "Item Code", fontsize=5, color=(0, 0, 0))
            page.insert_text(fitz.Point(475, 715), "Status", fontsize=5, color=(0, 0, 0))

            page.insert_text(fitz.Point(385, 740), "ZD-7319-QX", fontsize=5, color=(0.8, 0, 0))  # Secret target
            page.insert_text(fitz.Point(475, 740), "VERIFIED", fontsize=5, color=(0, 0, 0))

            page.insert_text(fitz.Point(385, 765), "Batch #", fontsize=5, color=(0, 0, 0))
            page.insert_text(fitz.Point(475, 765), "094-ALPHA", fontsize=5, color=(0, 0, 0))

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_file))
    doc.close()
    print(f"Successfully generated synthetic PDF at: {out_file.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic evaluation PDF.")
    parser.add_argument("--out", default="runs/synthetic.pdf", help="Output path for PDF")
    args = parser.parse_args()
    create_synthetic_pdf(args.out)
