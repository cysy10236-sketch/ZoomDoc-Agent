from __future__ import annotations

import math
import random
import re
import statistics
from typing import Any


def normalize_text(text: str | None) -> str:
    """Normalize answer string: strip whitespace, uppercase, remove outer punctuation."""
    if not text:
        return ""
    s = text.strip().upper()
    s = s.strip("\"'`* \t\r\n")
    s = re.sub(r"^[^\w]+|[^\w]+$", "", s)
    return s


def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Calculate Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    z = 1.95996 if abs(confidence - 0.95) < 1e-4 else 1.96
    p = k / n
    denom = 1.0 + (z**2) / n
    center = (p + (z**2) / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt((p * (1.0 - p)) / n + (z**2) / (4.0 * (n**2)))
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


def percentile(data: list[float], q: float) -> float:
    """Compute q-th percentile of data (q between 0 and 100)."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * (q / 100.0)
    low = int(math.floor(idx))
    high = int(math.ceil(idx))
    if low == high:
        return s[low]
    weight = idx - low
    return (1.0 - weight) * s[low] + weight * s[high]


def paired_bootstrap_ratio_ci(
    numerator_vals: list[float],
    denominator_vals: list[float],
    *,
    n_boot: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> dict[str, float]:
    """Calculate paired bootstrap ratio R = sum(num) / sum(den) and savings S = 1 - R with confidence intervals.

    Preconditions:
    - len(numerator_vals) == len(denominator_vals) >= 1
    - sum(denominator_vals) > 0
    Raises ValueError on violation.
    """
    n = len(numerator_vals)
    if n != len(denominator_vals) or n == 0:
        raise ValueError(
            f"Length mismatch or empty: len(num)={n}, len(den)={len(denominator_vals)}"
        )
    sum_den = sum(denominator_vals)
    if sum_den <= 0:
        raise ValueError(f"Sum of denominator values must be > 0, got {sum_den}")

    point_ratio = sum(numerator_vals) / sum_den

    rng = random.Random(seed)
    boot_ratios: list[float] = []

    for _ in range(n_boot):
        indices = [rng.randrange(n) for _ in range(n)]
        boot_num = sum(numerator_vals[i] for i in indices)
        boot_den = sum(denominator_vals[i] for i in indices)
        if boot_den > 0:
            boot_ratios.append(boot_num / boot_den)
        else:
            boot_ratios.append(point_ratio)

    boot_ratios.sort()
    alpha = (1.0 - confidence) / 2.0
    low_idx = max(0, min(int(math.floor(alpha * len(boot_ratios))), len(boot_ratios) - 1))
    high_idx = max(0, min(int(math.ceil((1.0 - alpha) * len(boot_ratios))) - 1, len(boot_ratios) - 1))

    r_low = boot_ratios[low_idx]
    r_high = boot_ratios[high_idx]

    return {
        "ratio": round(point_ratio, 4),
        "ratio_ci_low": round(r_low, 4),
        "ratio_ci_high": round(r_high, 4),
        "savings": round(1.0 - point_ratio, 4),
        "savings_ci_low": round(1.0 - r_high, 4),
        "savings_ci_high": round(1.0 - r_low, 4),
    }


def compute_ngram_overlap(
    candidate_texts: list[str],
    reference_corpus: str,
    n: int = 4,
) -> dict[str, Any]:
    """Data contamination audit: compute exact matches and character n-gram overlap against reference corpus.

    Returns:
    - exact_match_count: int
    - exact_match_rate: float
    - char_ngram_overlap_rate: float
    - leaked_items: list[str]
    """
    ref_lower = reference_corpus.lower()
    total_candidates = len(candidate_texts)
    if total_candidates == 0:
        return {
            "exact_match_count": 0,
            "exact_match_rate": 0.0,
            "char_ngram_overlap_rate": 0.0,
            "leaked_items": [],
        }

    exact_matches: list[str] = []
    total_ngrams = 0
    matched_ngrams = 0

    for cand in candidate_texts:
        cleaned = cand.strip()
        if not cleaned:
            continue
        if cleaned.lower() in ref_lower:
            exact_matches.append(cleaned)

        # Character n-grams
        cand_norm = re.sub(r"\s+", " ", cleaned.lower())
        if len(cand_norm) < n:
            total_ngrams += 1
            if cand_norm in ref_lower:
                matched_ngrams += 1
        else:
            for i in range(len(cand_norm) - n + 1):
                ngram = cand_norm[i : i + n]
                total_ngrams += 1
                if ngram in ref_lower:
                    matched_ngrams += 1

    exact_count = len(exact_matches)
    exact_rate = round(exact_count / total_candidates, 4)
    char_overlap = round(matched_ngrams / max(total_ngrams, 1), 4)

    return {
        "exact_match_count": exact_count,
        "exact_match_rate": exact_rate,
        "char_ngram_overlap_rate": char_overlap,
        "leaked_items": exact_matches,
    }


def evaluate_m5_trigger(
    render_latencies_ms: list[float],
    total_wall_latencies_ms: list[float],
    *,
    render_ratio_threshold: float = 0.20,
    p95_wall_threshold_ms: float = 120000.0,
) -> dict[str, Any]:
    """Determine whether Milestone 5 (Redis Content-Addressed Cache) is triggered (§2.5 / §13.4).

    Trigger conditions:
    - mean(render_latency_ms / total_wall_ms) >= 0.20 OR
    - p95(total_wall_ms) >= 120,000 ms (2 minutes)
    """
    if not render_latencies_ms or not total_wall_latencies_ms:
        return {
            "triggered": False,
            "decision": "OPTIONAL_SKIPPED",
            "mean_render_share": 0.0,
            "p95_wall_ms": 0.0,
            "reason": "No latency profile measurements provided.",
        }

    shares: list[float] = [
        r / max(w, 1e-6) for r, w in zip(render_latencies_ms, total_wall_latencies_ms)
    ]
    mean_render_share = statistics.mean(shares)
    p95_wall_ms = percentile(total_wall_latencies_ms, 95)

    triggered_ratio = mean_render_share >= render_ratio_threshold
    triggered_p95 = p95_wall_ms >= p95_wall_threshold_ms
    triggered = triggered_ratio or triggered_p95

    reasons: list[str] = []
    if triggered_ratio:
        reasons.append(
            f"Mean render share ({mean_render_share*100:.1f}%) >= threshold ({render_ratio_threshold*100:.1f}%)"
        )
    if triggered_p95:
        reasons.append(
            f"P95 wall-clock latency ({p95_wall_ms:.1f} ms) >= threshold ({p95_wall_threshold_ms:.1f} ms)"
        )

    decision = "REQUIRED" if triggered else "OPTIONAL_SKIPPED"
    reason_str = "; ".join(reasons) if reasons else "Render share and wall latency within acceptable bounds."

    return {
        "triggered": triggered,
        "decision": decision,
        "mean_render_share": round(mean_render_share, 4),
        "p95_wall_ms": round(p95_wall_ms, 2),
        "reason": reason_str,
    }


def compute_recall_funnel(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute retrieval recall funnel: Navigation Recall -> Seeker Recall -> Accuracy."""
    n = len(records)
    if n == 0:
        return {
            "n": 0,
            "nav_recall": 0.0,
            "seeker_recall": 0.0,
            "accuracy": 0.0,
        }

    nav_hits = 0
    seeker_hits = 0
    correct_hits = 0

    for r in records:
        gold_page = r.get("gold_page")
        candidate_pages = r.get("candidate_pages") or []
        selected_pages = r.get("selected_pages") or r.get("reference_pages") or []
        is_corr = r.get("is_correct", False)

        if gold_page in candidate_pages:
            nav_hits += 1
        if gold_page in selected_pages:
            seeker_hits += 1
        if is_corr:
            correct_hits += 1

    return {
        "n": n,
        "nav_recall": round(nav_hits / n, 4),
        "seeker_recall": round(seeker_hits / n, 4),
        "accuracy": round(correct_hits / n, 4),
    }
