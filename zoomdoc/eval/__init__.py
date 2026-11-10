"""ZoomDoc evaluation and benchmark package."""

from zoomdoc.eval.baselines import run_sliding_grid_baseline
from zoomdoc.eval.metrics import (
    audit_prompt_contamination,
    compute_mcnemar,
    compute_ngram_overlap,
    compute_recall_funnel,
    evaluate_m5_trigger,
    mcnemar_exact_p_value,
    normalize_text,
    paired_bootstrap_ratio_ci,
    wilson_score_interval,
)

__all__ = [
    "run_sliding_grid_baseline",
    "paired_bootstrap_ratio_ci",
    "compute_ngram_overlap",
    "audit_prompt_contamination",
    "mcnemar_exact_p_value",
    "compute_mcnemar",
    "evaluate_m5_trigger",
    "compute_recall_funnel",
    "wilson_score_interval",
    "normalize_text",
]

