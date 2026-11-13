from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf as fitz

from zoomdoc.schemas import CitedRegion, NormBBox

if TYPE_CHECKING:
    pass

OPTICAL_CLUSTERS: list[set[str]] = [
    {"0", "D", "O", "Q"},
    {"8", "9", "B"},
    {"2", "Z"},
    {"1", "7", "I", "L"},
    {"5", "S"},
    {"6", "G"},
]


def optical_edit_distance(s1: str, s2: str) -> float:
    """Calculate weighted Levenshtein distance accounting for optical OCR confusion.

    Substitution costs:
    - 0.0: identical character (case-insensitive)
    - 0.5: characters belong to the same optical confusion cluster
    - 1.0: ordinary character substitution
    Insertion / Deletion cost:
    - 1.5
    """
    u1 = s1.upper()
    u2 = s2.upper()
    n = len(u1)
    m = len(u2)

    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i * 1.5
    for j in range(m + 1):
        dp[0][j] = j * 1.5

    for i in range(1, n + 1):
        c1 = u1[i - 1]
        for j in range(1, m + 1):
            c2 = u2[j - 1]
            if c1 == c2:
                sub_cost = 0.0
            elif any(c1 in cluster and c2 in cluster for cluster in OPTICAL_CLUSTERS):
                sub_cost = 0.5
            else:
                sub_cost = 1.0

            dp[i][j] = min(
                dp[i - 1][j] + 1.5,
                dp[i][j - 1] + 1.5,
                dp[i - 1][j - 1] + sub_cost,
            )

    return round(float(dp[n][m]), 2)


def extract_bbox_vector_tokens(
    pdf_path: str,
    page_num: int,
    page_box: NormBBox | None = None,
    expand_margin: float = 0.05,
) -> list[str]:
    """Extract vector text tokens within a spatial bounding box on the specified page."""
    p = Path(pdf_path)
    if not p.is_file():
        return []

    try:
        doc = fitz.open(pdf_path)
    except (OSError, RuntimeError):
        return []

    if not (1 <= page_num <= len(doc)):
        doc.close()
        return []

    page = doc.load_page(page_num - 1)
    page_rect = page.rect

    if page_box is not None:
        x1 = max(0.0, page_box.x1 - expand_margin) * page_rect.width
        y1 = max(0.0, page_box.y1 - expand_margin) * page_rect.height
        x2 = min(1.0, page_box.x2 + expand_margin) * page_rect.width
        y2 = min(1.0, page_box.y2 + expand_margin) * page_rect.height
        clip_rect = fitz.Rect(x1, y1, x2, y2)
        words = page.get_text("words", clip=clip_rect)
    else:
        words = page.get_text("words")

    doc.close()

    tokens: list[str] = []
    for item in words:
        raw_word = item[4]
        cleaned = re.sub(r"^[^\w\-.]+|[^\w\-.]+$", "", raw_word)
        if cleaned:
            tokens.append(cleaned)
    return tokens


def verify_and_correct_answer(
    answer: str,
    pdf_path: str,
    reference_pages: list[int],
    cited_regions: list[CitedRegion],
    max_optical_distance: float = 1.5,
) -> tuple[str, bool]:
    """Verify visual answer tokens against PDF vector text layer and correct optical near-misses.

    Restricts search strictly to geometric micro-boxes when available, preventing
    unconstrained whole-page leakage.
    Returns:
        (corrected_answer, is_corrected)
    """
    if not answer or not pdf_path or not Path(pdf_path).is_file():
        return answer, False

    candidate_tokens: set[str] = set()
    bbox_regions = [reg for reg in cited_regions if reg.page_box is not None]

    if bbox_regions:
        # Strictly extract only from designated geometric micro-boxes
        for reg in bbox_regions:
            tokens = extract_bbox_vector_tokens(pdf_path, reg.page, reg.page_box)
            candidate_tokens.update(tokens)
    else:
        # Only fallback to whole page tokens if no geometric bounding boxes exist
        pages_to_scan = sorted(list(set(reference_pages) | {reg.page for reg in cited_regions}))
        for p in pages_to_scan:
            tokens = extract_bbox_vector_tokens(pdf_path, p, None)
            candidate_tokens.update(tokens)

    if not candidate_tokens:
        return answer, False

    # Regex targeting structured code tokens (e.g. ZD-2664-DX, 2D-1458-BV)
    code_pattern = re.compile(r"\b[A-Za-z0-9]{2,}(?:-[A-Za-z0-9]+)+\b")

    sorted_candidates = sorted(candidate_tokens)
    cand_upper_map: dict[str, str] = {c.upper(): c for c in sorted_candidates}
    corrected = False
    current_answer = answer

    for match in code_pattern.finditer(answer):
        tok = match.group(0)
        tok_u = tok.upper()
        if tok_u in cand_upper_map:
            # Exact or case match exists in vector layer
            if tok != cand_upper_map[tok_u]:
                current_answer = current_answer.replace(tok, cand_upper_map[tok_u], 1)
                corrected = True
            continue

        # Look for candidate with same hyphen structure and length
        tok_hyphen_pos = [i for i, ch in enumerate(tok) if ch == "-"]
        matched_cands: list[tuple[float, str]] = []

        for cand in sorted_candidates:
            if len(cand) == len(tok) and [i for i, ch in enumerate(cand) if ch == "-"] == tok_hyphen_pos:
                d = optical_edit_distance(tok, cand)
                if 0.0 < d <= max_optical_distance:
                    matched_cands.append((d, cand))

        if matched_cands:
            matched_cands.sort(key=lambda x: (x[0], x[1]))
            best_cand = matched_cands[0][1]
            current_answer = current_answer.replace(tok, best_cand, 1)
            corrected = True

    return current_answer, corrected


def locate_target_zoom_box(
    pdf_path: str,
    page_num: int,
    question: str,
    draft_answer: str | None = None,
) -> NormBBox:
    """Locate candidate zoom bounding box on page using tiered optical approximate matching.

    Tier 1: Optical approximate code matching against vector layer words.
    Tier 2: Data table structural anchor words (e.g. 'Security', 'Validation', 'CONFIRMED').
    Tier 3: Question keywords excluding page header banner zone.
    """
    default_box = NormBBox(x1=0.08, y1=0.20, x2=0.92, y2=0.88)
    if not pdf_path or not Path(pdf_path).is_file():
        return default_box

    try:
        doc = fitz.open(pdf_path)
    except (OSError, RuntimeError):
        return default_box

    if not (1 <= page_num <= len(doc)):
        doc.close()
        return default_box

    page = doc.load_page(page_num - 1)
    page_rect = page.rect
    w_pt = page_rect.width
    h_pt = page_rect.height
    header_threshold_y = 0.22 * h_pt

    words = page.get_text("words")  # (x0, y0, x1, y1, word, block_no, line_no, word_no)
    hit_rects: list[fitz.Rect] = []

    # --- Tier 1: Optical approximate code matching ---
    draft_codes: list[str] = []
    if draft_answer:
        draft_codes = re.findall(r"[A-Za-z0-9]{2,}(?:-[A-Za-z0-9]+)+", draft_answer)

    if draft_codes and words:
        for code in draft_codes:
            code_clean = code.strip().upper()
            code_hyphen_pos = [i for i, ch in enumerate(code_clean) if ch == "-"]
            best_dist = 999.0
            best_word_rect: fitz.Rect | None = None

            for w in words:
                raw_w = w[4]
                cleaned_w = re.sub(r"^[^\w\-.]+|[^\w\-.]+$", "", raw_w).upper()
                if not cleaned_w:
                    continue

                if cleaned_w == code_clean:
                    hit_rects.append(fitz.Rect(w[0], w[1], w[2], w[3]))
                    break

                # Approximate match with same hyphen structure and length
                w_hyphens = [i for i, ch in enumerate(cleaned_w) if ch == "-"]
                if len(cleaned_w) == len(code_clean) and w_hyphens == code_hyphen_pos:
                    d = optical_edit_distance(code_clean, cleaned_w)
                    if d < best_dist and d <= 2.0:
                        best_dist = d
                        best_word_rect = fitz.Rect(w[0], w[1], w[2], w[3])

            if best_word_rect is not None and not hit_rects:
                hit_rects.append(best_word_rect)
            if hit_rects:
                break

    # --- Tier 2: Table structure / data cell anchors ---
    if not hit_rects and words:
        table_anchors = {"SECURITY", "VALIDATION", "CONFIRMED", "BATCH", "AUDIT", "OBSERVATION", "METRICS"}
        anchor_rects: list[fitz.Rect] = []
        for w in words:
            cleaned_w = re.sub(r"^[^\w]+|[^\w]+$", "", w[4]).upper()
            if cleaned_w in table_anchors and w[1] >= header_threshold_y:
                anchor_rects.append(fitz.Rect(w[0], w[1], w[2], w[3]))

        if anchor_rects:
            hit_rects.extend(anchor_rects)

    # --- Tier 3: Question keywords, excluding top header band ---
    if not hit_rects:
        search_terms: list[str] = []
        for word in re.findall(r"[A-Za-z0-9]{4,}", question):
            if word.upper() not in {
                "WHAT", "WHICH", "PAGE", "FIND", "CODE", "TABLE", "THE", "AND",
                "FOR", "THIS", "FROM", "UNDER", "WITH", "THAT", "HAVE", "BEEN",
            }:
                search_terms.append(word)

        for term in search_terms:
            rects = page.search_for(term)
            filtered = [r for r in rects if r.y0 >= header_threshold_y]
            if filtered:
                hit_rects.extend(filtered)
                break
            elif rects and not hit_rects:
                hit_rects.extend(rects)

    doc.close()

    if not hit_rects:
        return default_box

    union_rect = hit_rects[0]
    for r in hit_rects[1:]:
        union_rect |= r

    # Expand by 15% margin and clamp
    margin_x = union_rect.width * 0.15 + 20.0
    margin_y = union_rect.height * 0.15 + 20.0

    x1 = max(0.0, (union_rect.x0 - margin_x) / w_pt)
    y1 = max(0.0, (union_rect.y0 - margin_y) / h_pt)
    x2 = min(1.0, (union_rect.x1 + margin_x) / w_pt)
    y2 = min(1.0, (union_rect.y1 + margin_y) / h_pt)

    if x2 <= x1 + 0.05:
        x2 = min(1.0, x1 + 0.10)
    if y2 <= y1 + 0.05:
        y2 = min(1.0, y1 + 0.10)

    try:
        return NormBBox(x1=x1, y1=y1, x2=x2, y2=y2)
    except ValueError:
        return default_box
