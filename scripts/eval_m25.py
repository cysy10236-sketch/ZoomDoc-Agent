from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from openai import APIError

from zoomdoc.config import Settings
from zoomdoc.errors import ZoomDocError
from zoomdoc.graph.supervisor import run_m2


def normalize_answer(text: str | None) -> str:
    """Normalize answer string: strip whitespace, uppercase, remove outer punctuation."""
    if not text:
        return ""
    s = text.strip().upper()
    s = s.strip("\"'`* \t\r\n")
    s = re.sub(r"^[^\w]+|[^\w]+$", "", s)
    return s


def is_answer_correct(pred: str | None, gold: str) -> bool:
    """Deterministic exact match after normalization."""
    return normalize_answer(pred) == normalize_answer(gold)


def get_group_settings(group: str, runs_dir: str) -> Settings:
    """Return Settings for specified M2.5 evaluation group."""
    if group == "G0":
        # no-zoom: zoom disabled, 1 inspect round
        return Settings(
            runs_dir=runs_dir,
            max_zooms=0,
            max_inspect_rounds=1,
            max_seek_rounds=2,
            max_sub_steps=4,
        )
    elif group == "G1":
        # zoom enabled: max_zooms=3, 1 inspect round (Critic does not trigger reinspect)
        return Settings(
            runs_dir=runs_dir,
            max_zooms=3,
            max_inspect_rounds=1,
            max_seek_rounds=2,
            max_sub_steps=6,
        )
    elif group == "G2":
        # zoom + critic: max_zooms=3, max_inspect_rounds=2, critic threshold=0.6
        return Settings(
            runs_dir=runs_dir,
            max_zooms=3,
            max_inspect_rounds=2,
            critic_min_confidence=0.6,
            max_seek_rounds=2,
            max_sub_steps=6,
        )
    else:
        raise ValueError(f"Unknown group: {group}. Must be G0, G1, or G2.")


def run_eval_case(
    case: dict[str, Any],
    group: str,
    out_dir: Path,
    runs_dir: Path,
    vlm_client: Any = None,
) -> dict[str, Any]:
    """Execute a single evaluation case under the group configuration."""
    case_id = case["case_id"]
    gold = case["gold"]
    pdf_path = case["pdf_path"]
    question = case["question"]

    trace_id = f"m25-{group}-{case_id}"
    settings = get_group_settings(group, str(runs_dir))

    status = "error"
    pred_answer: str | None = None
    confidence: float | None = None
    confidences: list[float] = []
    ref_pages: list[int] = []
    cited_regions_count = 0
    zoom_count = 0
    usage_dict: dict[str, Any] | None = None
    error_msg: str | None = None

    try:
        result, state = run_m2(
            question=question,
            pdf_path=pdf_path,
            vlm=vlm_client,
            settings=settings,
            trace_id=trace_id,
        )
        status = result.status
        pred_answer = result.answer
        ref_pages = result.reference_pages
        cited_regions_count = len(result.cited_regions)
        usage_dict = result.usage.model_dump()
        if state.evidence:
            zoom_count = sum(e.zoom_count for e in state.evidence)
            confidences = [e.confidence for e in state.evidence if e.confidence is not None]
            confidence = state.evidence[-1].confidence

    except (ZoomDocError, APIError) as exc:
        error_msg = f"{type(exc).__name__}: {str(exc)}"

    correct = is_answer_correct(pred_answer, gold) if status in ("answered", "synthesized") else False

    record = {
        "case_id": case_id,
        "group": group,
        "vlm_model": settings.vlm_model,
        "gold": gold,
        "gold_page": case["gold_page"],
        "font_pt": case["font_pt"],
        "region": case["region"],
        "confusable": case["confusable"],
        "status": status,
        "answer": pred_answer,
        "is_correct": correct,
        "reference_pages": ref_pages,
        "zoom_count": zoom_count,
        "is_zoomed": zoom_count > 0,
        "confidence": confidence,
        "confidences": confidences,
        "usage": usage_dict,
        "error": error_msg,
        "trace_id": trace_id,
    }

    group_out_dir = out_dir / group
    group_out_dir.mkdir(parents=True, exist_ok=True)
    res_path = group_out_dir / f"{case_id}.json"
    with open(res_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=2))

    return record


def run_eval(
    group_arg: str,
    manifest_path: str,
    out_dir: str = "eval/m25/results",
    runs_dir: str = "eval/m25/runs",
    resume: bool = True,
    vlm_client: Any = None,
) -> dict[str, list[dict]]:
    """Run evaluation over cases in manifest."""
    m_path = Path(manifest_path)
    if not m_path.is_file():
        raise FileNotFoundError(f"Manifest file not found: {m_path}")

    cases = [json.loads(line) for line in m_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    target_groups = ["G0", "G1", "G2"] if group_arg == "all" else [group_arg]
    out_base = Path(out_dir)
    runs_base = Path(runs_dir)

    all_results: dict[str, list[dict]] = {}

    for grp in target_groups:
        grp_dir = out_base / grp
        grp_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict] = []

        print(f"=== Starting Evaluation for Group {grp} ({len(cases)} cases) ===")
        for case in cases:
            case_id = case["case_id"]
            res_file = grp_dir / f"{case_id}.json"
            if resume and res_file.is_file() and res_file.stat().st_size > 0:
                print(f"[{grp}][{case_id}] Skipping (resumed from {res_file.name})")
                with open(res_file, encoding="utf-8") as f:
                    results.append(json.load(f))
                continue

            rec = run_eval_case(case, grp, out_base, runs_base, vlm_client=vlm_client)
            results.append(rec)
            status_symbol = "✓" if rec["is_correct"] else "✗"
            print(f"[{grp}][{case_id}] Result: {status_symbol} (pred={rec['answer']}, gold={rec['gold']}, zoomed={rec['is_zoomed']})")

        all_results[grp] = results

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Milestone 2.5 Evaluation Benchmark.")
    parser.add_argument("--group", choices=["G0", "G1", "G2", "all"], default="all", help="Group to evaluate")
    parser.add_argument("--manifest", default="eval/m25/manifest.jsonl", help="Path to manifest.jsonl")
    parser.add_argument("--out-dir", default="eval/m25/results", help="Directory for evaluation results")
    parser.add_argument("--runs-dir", default="eval/m25/runs", help="Directory for execution traces")
    parser.add_argument("--resume", action="store_true", default=True, help="Skip already processed cases")
    args = parser.parse_args()

    run_eval(
        group_arg=args.group,
        manifest_path=args.manifest,
        out_dir=args.out_dir,
        runs_dir=args.runs_dir,
        resume=args.resume,
    )
