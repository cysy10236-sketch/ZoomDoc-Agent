from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zoomdoc.errors import ZoomDocError
from zoomdoc.graph.states import CriticVerdict, EvidenceReport, SupervisorState
from zoomdoc.schemas import StepRecord

if TYPE_CHECKING:
    from zoomdoc.config import Settings
    from zoomdoc.observability import TraceLogger


def evaluate_report(
    report: EvidenceReport,
    valid_pages: set[int],
    min_conf: float,
    *,
    max_entropy: float | None = None,
) -> CriticVerdict:
    """Pure deterministic function evaluating an EvidenceReport against rules and thresholds.

    Evaluation order:
    1. status == "budget_exhausted" -> (False, "budget_exhausted")
    2. status == "need_more_pages"  -> (False, "need_more_pages")
    3. answer is None or blank      -> (False, "no_answer")
    4. reference_pages empty or not subset of valid_pages -> (False, "invalid_reference")
    5. high entropy gate violation   -> (False, "high_entropy")
    6. confidence is None or < min_conf -> (False, "low_confidence")
    7. Otherwise -> (True, "ok")
    """
    if report.status == "budget_exhausted":
        return CriticVerdict(
            accepted=False,
            reason_code="budget_exhausted",
            detail=f"Inspector exhausted sub-step budget in round {report.round}.",
        )

    if report.status == "need_more_pages":
        hint = report.seek_hint or "no seek hint provided"
        return CriticVerdict(
            accepted=False,
            reason_code="need_more_pages",
            detail=f"Inspector requires additional pages. Hint: {hint}",
        )

    if not report.answer or not report.answer.strip():
        return CriticVerdict(
            accepted=False,
            reason_code="no_answer",
            detail="Inspector produced an empty or whitespace answer.",
        )

    if not report.reference_pages or not set(report.reference_pages).issubset(valid_pages):
        return CriticVerdict(
            accepted=False,
            reason_code="invalid_reference",
            detail=(
                f"Reference pages {report.reference_pages} are not a non-empty subset "
                f"of inspected valid pages {sorted(list(valid_pages))}."
            ),
        )

    if (
        max_entropy is not None
        and report.answer_entropy is not None
        and report.answer_entropy > max_entropy
        and report.zoom_count == 0
    ):
        return CriticVerdict(
            accepted=False,
            reason_code="high_entropy",
            detail=(
                f"answer_entropy={report.answer_entropy:.4f} > {max_entropy:.4f} with zoom_count=0; "
                f"re-inspect and zoom on the region supporting: {report.answer}"
            ),
        )

    if report.confidence is None or report.confidence < min_conf:
        c = report.confidence if report.confidence is not None else 0.0
        return CriticVerdict(
            accepted=False,
            reason_code="low_confidence",
            detail=(
                f"confidence={c:.2f} < {min_conf:.2f}; re-inspect and zoom on the region supporting: {report.answer}"
            ),
        )

    return CriticVerdict(
        accepted=True,
        reason_code="ok",
        detail="Report passed critic evaluation with sufficient confidence.",
    )


def critic_node(
    state: SupervisorState,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Critic node in Supervisor graph.

    Evaluates the most recent EvidenceReport using pure deterministic rules.
    Returns:
        {
            "verdict": CriticVerdict,
            "step": state.step + 1,
            "history": [StepRecord],
        }
    """
    if not state.evidence:
        raise ZoomDocError("critic_node called with empty evidence in state")

    report = state.evidence[-1]
    valid_pages = set(state.selected_pages)

    max_ent = (
        settings.entropy_zoom_threshold
        if getattr(settings, "enable_sboed_gate", False)
        else None
    )

    with logger.span("critic") as span:
        verdict = evaluate_report(
            report, valid_pages, settings.critic_min_confidence, max_entropy=max_ent
        )
        logger.event(
            span_id=span.span_id,
            node="critic",
            status="ok",
            latency_ms=span.elapsed_ms(),
            prompt_tokens=None,
            completion_tokens=None,
            attempt=1,
            payload=verdict.model_dump(),
            namespace="main",
        )

    step_rec = StepRecord(
        step=state.step + 1,
        node="critic",
        input_images=[],
        output=verdict.model_dump(),
        usage=None,
        attempts=1,
    )

    return {
        "verdict": verdict,
        "step": state.step + 1,
        "history": [step_rec],
    }
