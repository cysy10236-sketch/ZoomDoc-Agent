"""ZoomDoc evaluation and benchmark package."""

from zoomdoc.eval.baselines import run_sliding_grid_baseline
from zoomdoc.eval.metrics import (
    compute_ngram_overlap,
    compute_recall_funnel,
    evaluate_m5_trigger,
    normalize_text,
    paired_bootstrap_ratio_ci,
    wilson_score_interval,
)

__all__ = [
    "run_sliding_grid_baseline",
    "paired_bootstrap_ratio_ci",
    "compute_ngram_overlap",
    "evaluate_m5_trigger",
    "compute_recall_funnel",
    "wilson_score_interval",
    "normalize_text",
]
