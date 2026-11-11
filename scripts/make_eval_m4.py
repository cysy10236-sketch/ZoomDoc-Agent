from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import pymupdf as fitz


def generate_code(rng: random.Random) -> str:
    """Generate deterministic random code ZD-XXXX-YY, strictly excluding ZD-7319-QX."""
    num = rng.randint(1000, 9999)
    # Ensure num != 7319
    if num == 7319:
        num = 7320
    suffix = rng.choice(["TR", "WP", "NK", "MZ", "HM", "RT", "PX", "KY", "QD", "DX", "SZ", "BV"])
    # Ensure not QX
    if suffix == "QX":
        suffix = "TR"
    return f"ZD-{num}-{suffix}"


def draw_table_cell(
    page: fitz.Page,
    rect: fitz.Rect,
    code: str,
    status: str,
    batch: str,
    font_pt: float,
    is_gold: bool,
) -> None:
    """Draw a 3x2 verified table."""
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

    # Headers
    page.insert_text(fitz.Point(x0 + 5, y0 + 15), "Security ID", fontsize=5, color=(0, 0, 0))
    page.insert_text(fitz.Point(mid_x + 5, y0 + 15), "Validation", fontsize=5, color=(0, 0, 0))

    # Row 1
    text_color = (0.8, 0, 0) if is_gold else (0.3, 0.3, 0.3)
    page.insert_text(fitz.Point(x0 + 5, row1_y + 15), code, fontsize=font_pt, color=text_color)
    page.insert_text(fitz.Point(mid_x + 5, row1_y + 15), status, fontsize=5, color=(0, 0, 0))

    # Row 2
    page.insert_text(fitz.Point(x0 + 5, row2_y + 15), "Audit Batch", fontsize=5, color=(0, 0, 0))
    page.insert_text(fitz.Point(mid_x + 5, row2_y + 15), batch, fontsize=5, color=(0, 0, 0))


def build_long_doc(
    out_path: Path,
    total_pages: int,
    toc_specs: list[list[Any]],
    gold_targets: dict[int, dict[str, Any]],
    distractor_pages: set[int],
    cross_page_intros: dict[int, str] | None = None,
    trap_notes: dict[int, str] | None = None,
    is_multicolumn: bool = False,
) -> None:
    """Generate multi-page synthetic document with TOC bookmarks, body text, and target tables."""
    doc = fitz.open()
    width, height = 595.3, 841.9  # A4

    def get_section_title(p: int) -> str:
        cur = "Overview"
        for item in toc_specs:
            if p >= item[2]:
                cur = item[1]
        return cur

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        page = doc.new_page(width=width, height=height)
        sec_title = get_section_title(page_num)

        # Title
        page.insert_text(fitz.Point(50, 55), sec_title, fontsize=18, color=(0, 0, 0))
        page.insert_text(
            fitz.Point(50, 75),
            f"Page {page_num} of {total_pages} - Confidential Corporate Operational Report",
            fontsize=8,
            color=(0.4, 0.4, 0.4),
        )

        # Cross-page continuation banner or backtrack trap / reclassification notice
        if cross_page_intros and page_num in cross_page_intros:
            page.insert_text(
                fitz.Point(50, 95),
                cross_page_intros[page_num],
                fontsize=8,
                color=(0.1, 0.3, 0.7),
            )
        elif trap_notes and page_num in trap_notes:
            page.insert_text(
                fitz.Point(50, 95),
                trap_notes[page_num],
                fontsize=8,
                color=(0.7, 0.2, 0.1),
            )

        if is_multicolumn:
            # 2-column layout
            col1 = (
                f"Section: {sec_title}. This report synthesizes institutional audit telemetry for fiscal 2026.\n"
                f"Analytical telemetry benchmark {page_num}.1 indicates normal dispersion across segments.\n"
                "All internal procedures adhere strictly to the regulatory governance framework."
            )
            col2 = (
                "Continuous monitoring of operational metrics confirms statistical stability.\n"
                "Subsidiary entries are consolidated under applicable international statutory accounts.\n"
                f"Audit checkpoint {page_num}.B validates ledger consistency."
            )
            page.insert_text(fitz.Point(50, 110), col1, fontsize=9, color=(0.2, 0.2, 0.2))
            page.insert_text(fitz.Point(310, 110), col2, fontsize=9, color=(0.2, 0.2, 0.2))
        else:
            # Single-column layout
            body = (
                f"Chapter overview: {sec_title}. Documenting enterprise metrics and audit disclosures.\n"
                f"Telemetry observation {page_num}.1 confirms compliance with statutory governance protocols.\n"
                "Consolidated assets and off-balance obligations are categorized under their respective notes.\n"
                f"Record {page_num}.2 details operational telemetry and analytical disclosures for fiscal period 2026."
            )
            page.insert_text(fitz.Point(50, 110), body, fontsize=10, color=(0.2, 0.2, 0.2))

        # Check for gold table or distractor table
        if page_num in gold_targets:
            target_info = gold_targets[page_num]
            table_rect = fitz.Rect(380, 680, 560, 760)
            draw_table_cell(
                page=page,
                rect=table_rect,
                code=target_info["code"],
                status="CONFIRMED",
                batch=f"BATCH-{page_num:03d}",
                font_pt=target_info["font_pt"],
                is_gold=True,
            )
        elif page_num in distractor_pages:
            table_rect = fitz.Rect(380, 680, 560, 760)
            draw_table_cell(
                page=page,
                rect=table_rect,
                code=f"DIS-{page_num:04d}-XX",
                status="ARCHIVED",
                batch=f"BATCH-{page_num:03d}",
                font_pt=5.0,
                is_gold=False,
            )

    doc.set_toc(toc_specs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    doc.close()


def make_eval_m4(
    out_dir: str = "eval/m4",
    n: int = 16,
    seed: int = 2026111004,
) -> list[dict[str, Any]]:
    """Generate Track A (Synthetic Long Hard) and Track B (Realistic Benchmark) evaluation dataset."""
    rng = random.Random(seed)
    base_dir = Path(out_dir)
    pdf_dir = base_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # 1. Document 1: 100 pages (Financial & Audit)
    pdf1_path = pdf_dir / "doc1_financial_100p.pdf"
    toc1 = [
        [1, "Part 1: Corporate Governance & Board Strategy", 1],
        [2, "1.1 Executive Overview", 1],
        [2, "1.2 Risk Factors & Regulatory Disclosures", 13],
        [1, "Part 2: Logistics & Telemetry Network", 26],
        [2, "2.1 Regional Logistics Hubs", 26],
        [2, "2.2 Warehouse Telemetry", 41],
        [1, "Part 3: Financial Statements & Audit Notes", 56],
        [2, "3.1 Consolidated Balance Sheet Disclosures", 56],
        [2, "3.2 Off-Balance-Sheet Derivative Instruments", 69],
        [2, "3.3 Statutory Tax Reconciliation", 81],
        [1, "Part 4: Appendices & Regulatory Filings", 86],
        [2, "4.1 Subsidiary List & Foreign Operations", 86],
    ]
    # Gold targets in Doc 1
    code1_1 = generate_code(rng)
    code1_2 = generate_code(rng)
    code1_3 = generate_code(rng)
    code1_4 = generate_code(rng)

    targets_doc1 = {
        73: {"code": code1_1, "font_pt": 4.0},
        62: {"code": code1_2, "font_pt": 5.0},
        83: {"code": code1_3, "font_pt": 4.0},
        94: {"code": code1_4, "font_pt": 5.0},
    }
    intros_doc1 = {
        61: "[CONTINUATION SCHEDULE] Consolidated Balance Sheet Disclosures schedule begins here; verified target table continues on Page 62."
    }
    traps_doc1 = {
        13: "[RECLASSIFICATION NOTICE] Note: Statutory tax reconciliation table has been relocated to Section 3.3 (Page 83)."
    }
    build_long_doc(
        pdf1_path,
        total_pages=100,
        toc_specs=toc1,
        gold_targets=targets_doc1,
        distractor_pages={5, 20, 35, 50, 65, 88},
        cross_page_intros=intros_doc1,
        trap_notes=traps_doc1,
    )

    # 2. Document 2: 80 pages (Supply Chain & Infrastructure)
    pdf2_path = pdf_dir / "doc2_supply_chain_80p.pdf"
    toc2 = [
        [1, "Chapter 1: Network Topology & Facilities", 1],
        [2, "1.1 Primary Infrastructure Grid", 1],
        [2, "1.2 Fiber Routing & Telemetry Nodes", 15],
        [1, "Chapter 2: Telemetry & Datacenter Operations", 26],
        [2, "2.1 Server Cluster Metrics", 26],
        [2, "2.2 Power Distribution Telemetry", 41],
        [1, "Chapter 3: Incident Management & Disaster Recovery", 56],
        [2, "3.1 Outage Post-Mortems", 56],
        [2, "3.2 Redundancy Verification Notes", 68],
    ]
    code2_1 = generate_code(rng)
    code2_2 = generate_code(rng)
    code2_3 = generate_code(rng)
    code2_4 = generate_code(rng)

    targets_doc2 = {
        34: {"code": code2_1, "font_pt": 4.0},
        49: {"code": code2_2, "font_pt": 5.0},
        64: {"code": code2_3, "font_pt": 4.0},
        74: {"code": code2_4, "font_pt": 5.0},
    }
    intros_doc2 = {
        48: "[CONTINUATION SCHEDULE] Power Distribution Telemetry data series begins here; full verification table continues on Page 49."
    }
    traps_doc2 = {
        15: "[RECLASSIFICATION NOTICE] Note: Incident telemetry verification records have been transferred to Chapter 3 Outage Post-Mortems (Page 64)."
    }
    build_long_doc(
        pdf2_path,
        total_pages=80,
        toc_specs=toc2,
        gold_targets=targets_doc2,
        distractor_pages={8, 22, 38, 52, 70},
        cross_page_intros=intros_doc2,
        trap_notes=traps_doc2,
    )

    # 3. Document 3: 60 pages (Compliance & Audit)
    pdf3_path = pdf_dir / "doc3_compliance_60p.pdf"
    toc3 = [
        [1, "Section 1: Governance & Ethical Standards", 1],
        [2, "1.1 Code of Conduct Disclosures", 1],
        [1, "Section 2: Internal Control Assessment", 20],
        [2, "2.1 Risk Mitigation Matrix", 20],
        [2, "2.2 Compliance Verification Filings", 35],
        [1, "Section 3: Independent Auditor Statements", 45],
        [2, "3.1 External Audit Opinions", 45],
        [2, "3.2 Key Audit Matters & Annex", 52],
    ]
    code3_1 = generate_code(rng)
    code3_2 = generate_code(rng)
    code3_3 = generate_code(rng)
    code3_4 = generate_code(rng)

    targets_doc3 = {
        27: {"code": code3_1, "font_pt": 4.0},
        39: {"code": code3_2, "font_pt": 5.0},
        48: {"code": code3_3, "font_pt": 4.0},
        56: {"code": code3_4, "font_pt": 5.0},
    }
    intros_doc3 = {
        38: "[CONTINUATION SCHEDULE] Compliance Verification filings summary begins here; full confirmation table continues on Page 39."
    }
    traps_doc3 = {
        1: "[AUDIT DISCLOSURE] Note: Independent external auditor statements are published under Section 3.1 (Page 48)."
    }
    build_long_doc(
        pdf3_path,
        total_pages=60,
        toc_specs=toc3,
        gold_targets=targets_doc3,
        distractor_pages={10, 24, 42, 58},
        cross_page_intros=intros_doc3,
        trap_notes=traps_doc3,
    )

    # 4. Document 4: 50 pages (Track B: Realistic Multi-column Layout)
    pdf4_path = pdf_dir / "doc4_multicolumn_bench_50p.pdf"
    toc4 = [
        [1, "Module A: Institutional Research Disclosures", 1],
        [2, "A.1 Multi-factor Quantitative Analysis", 1],
        [1, "Module B: Portfolio Risk & Stress Testing", 18],
        [2, "B.1 Value at Risk Disclosures", 18],
        [2, "B.2 Counterparty Exposure Verification", 32],
        [1, "Module C: Regulatory Statutory Exhibits", 40],
        [2, "C.1 Capital Adequacy Tables", 40],
    ]
    code4_1 = generate_code(rng)
    code4_2 = generate_code(rng)
    code4_3 = generate_code(rng)
    code4_4 = generate_code(rng)

    targets_doc4 = {
        12: {"code": code4_1, "font_pt": 4.0},
        24: {"code": code4_2, "font_pt": 5.0},
        36: {"code": code4_3, "font_pt": 4.0},
        45: {"code": code4_4, "font_pt": 5.0},
    }
    intros_doc4 = {
        23: "[CONTINUATION SCHEDULE] Value at Risk disclosures schedule begins here; table continues on Page 24."
    }
    build_long_doc(
        pdf4_path,
        total_pages=50,
        toc_specs=toc4,
        gold_targets=targets_doc4,
        distractor_pages={6, 16, 28, 41},
        cross_page_intros=intros_doc4,
        is_multicolumn=True,
    )

    # Manifest cases construction
    raw_cases = [
        # Track A: Doc 1 (100p)
        {
            "track": "TrackA",
            "pdf_path": str(pdf1_path.resolve()),
            "total_pages": 100,
            "gold_page": 73,
            "gold": code1_1,
            "font_pt": 4.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the verified table under Section 3.2 Off-Balance-Sheet Derivative Instruments?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf1_path.resolve()),
            "total_pages": 100,
            "gold_page": 62,
            "gold": code1_2,
            "font_pt": 5.0,
            "query_type": "cross_page_table",
            "question": "What is the exact Security ID in the confirmed table under Section 3.1 Consolidated Balance Sheet Disclosures?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf1_path.resolve()),
            "total_pages": 100,
            "gold_page": 83,
            "gold": code1_3,
            "font_pt": 4.0,
            "query_type": "backtrack_trap",
            "question": "Refer to Risk Disclosures or Section 3.3: What is the exact Security ID in the statutory tax reconciliation table?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf1_path.resolve()),
            "total_pages": 100,
            "gold_page": 94,
            "gold": code1_4,
            "font_pt": 5.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the verified table under Section 4.1 Subsidiary List?",
        },
        # Track A: Doc 2 (80p)
        {
            "track": "TrackA",
            "pdf_path": str(pdf2_path.resolve()),
            "total_pages": 80,
            "gold_page": 34,
            "gold": code2_1,
            "font_pt": 4.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the telemetry table under Section 2.1 Server Cluster Metrics?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf2_path.resolve()),
            "total_pages": 80,
            "gold_page": 49,
            "gold": code2_2,
            "font_pt": 5.0,
            "query_type": "cross_page_table",
            "question": "What is the exact Security ID in the power distribution telemetry table under Section 2.2?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf2_path.resolve()),
            "total_pages": 80,
            "gold_page": 64,
            "gold": code2_3,
            "font_pt": 4.0,
            "query_type": "backtrack_trap",
            "question": "Review Outage Post-Mortems in Chapter 3: What is the exact Security ID confirmed in the incident telemetry table?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf2_path.resolve()),
            "total_pages": 80,
            "gold_page": 74,
            "gold": code2_4,
            "font_pt": 5.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the redundancy verification table under Section 3.2?",
        },
        # Track A: Doc 3 (60p)
        {
            "track": "TrackA",
            "pdf_path": str(pdf3_path.resolve()),
            "total_pages": 60,
            "gold_page": 27,
            "gold": code3_1,
            "font_pt": 4.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the verified risk mitigation table under Section 2.1?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf3_path.resolve()),
            "total_pages": 60,
            "gold_page": 39,
            "gold": code3_2,
            "font_pt": 5.0,
            "query_type": "cross_page_table",
            "question": "What is the exact Security ID in the compliance verification table under Section 2.2?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf3_path.resolve()),
            "total_pages": 60,
            "gold_page": 48,
            "gold": code3_3,
            "font_pt": 4.0,
            "query_type": "backtrack_trap",
            "question": "Inquire external auditor opinions under Section 3.1: What is the exact Security ID confirmed in the table?",
        },
        {
            "track": "TrackA",
            "pdf_path": str(pdf3_path.resolve()),
            "total_pages": 60,
            "gold_page": 56,
            "gold": code3_4,
            "font_pt": 5.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the Key Audit Matters Annex under Section 3.2?",
        },
        # Track B: Doc 4 (50p Multi-column Benchmark)
        {
            "track": "TrackB",
            "pdf_path": str(pdf4_path.resolve()),
            "total_pages": 50,
            "gold_page": 12,
            "gold": code4_1,
            "font_pt": 4.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the multi-factor quantitative table under Module A.1?",
        },
        {
            "track": "TrackB",
            "pdf_path": str(pdf4_path.resolve()),
            "total_pages": 50,
            "gold_page": 24,
            "gold": code4_2,
            "font_pt": 5.0,
            "query_type": "cross_page_table",
            "question": "What is the exact Security ID in the Value at Risk disclosures table under Module B.1?",
        },
        {
            "track": "TrackB",
            "pdf_path": str(pdf4_path.resolve()),
            "total_pages": 50,
            "gold_page": 36,
            "gold": code4_3,
            "font_pt": 4.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the counterparty exposure verification table under Module B.2?",
        },
        {
            "track": "TrackB",
            "pdf_path": str(pdf4_path.resolve()),
            "total_pages": 50,
            "gold_page": 45,
            "gold": code4_4,
            "font_pt": 5.0,
            "query_type": "direct_section",
            "question": "What is the exact Security ID in the capital adequacy table under Module C.1?",
        },
    ]

    # Fill or slice to exactly n
    manifest_records: list[dict[str, Any]] = []
    for i in range(n):
        src = raw_cases[i % len(raw_cases)]
        case_id = f"case_{i + 1:03d}"
        rec = dict(src)
        rec["case_id"] = case_id
        manifest_records.append(rec)

    manifest_path = base_dir / "manifest.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as f:
        for r in manifest_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Generated {len(manifest_records)} test cases in {manifest_path}")

    # Generate 8-case orthogonal balanced subset (manifest_sub8.jsonl)
    # Covering Doc1~Doc4 (2 each), direct_section / cross_page_table / backtrack_trap, 4.0pt / 5.0pt
    sub8_indices = [1, 2, 4, 5, 10, 11, 12, 13]
    sub8_records: list[dict[str, Any]] = []
    for idx_num, src_idx in enumerate(sub8_indices):
        rec = dict(raw_cases[src_idx])
        rec["case_id"] = f"case_{idx_num + 1:03d}"
        rec["origin_case_index"] = src_idx + 1
        sub8_records.append(rec)

    sub8_path = base_dir / "manifest_sub8.jsonl"
    with open(sub8_path, "w", encoding="utf-8") as f:
        for r in sub8_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Generated {len(sub8_records)} orthogonal balanced test cases in {sub8_path}")

    return manifest_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Milestone 4 evaluation benchmark dataset.")
    parser.add_argument("--out", default="eval/m4", help="Output directory")
    parser.add_argument("--n", type=int, default=16, help="Number of test cases (default 16 for all distinct cases)")
    parser.add_argument("--seed", type=int, default=2026111004, help="Random seed")
    args = parser.parse_args()

    make_eval_m4(out_dir=args.out, n=args.n, seed=args.seed)
