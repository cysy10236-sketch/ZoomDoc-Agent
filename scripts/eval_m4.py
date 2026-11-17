from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openai import APIError

from zoomdoc.config import Settings, get_settings
from zoomdoc.errors import ZoomDocError
from zoomdoc.eval.baselines import run_sliding_grid_baseline
from zoomdoc.eval.metrics import normalize_text
from zoomdoc.graph.supervisor import run_m3, run_m5
from zoomdoc.schemas import FinalResultM3


def run_case_for_group(
    case: dict[str, Any],
    group: str,
    out_dir: Path,
    runs_dir: Path,
    vlm_client: Any = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute a single evaluation case under the designated group configuration."""
    case_id = case["case_id"]
    gold = case["gold"]
    gold_page = case["gold_page"]
    pdf_path = case["pdf_path"]
    question = case["question"]

    trace_id = f"m4-{group}-{case_id}"
    base_cfg = settings or get_settings()
    cfg = base_cfg.model_copy(update={"runs_dir": str(runs_dir.resolve())})

    status = "error"
    pred_answer: str | None = None
    ref_pages: list[int] = []
    candidate_pages: list[int] = []
    selected_pages: list[int] = []
    nav_rounds = 0
    seek_rounds = 0
    inspect_rounds = 0
    zoom_count = 0
    render_latency_ms = 0.0
    tree_latency_ms = 0.0
    total_wall_ms = 0.0
    usage_dict: dict[str, Any] | None = None
    error_msg: str | None = None

    try:
        if group == "B0":
            result, sel_pages = run_sliding_grid_baseline(
                question=question,
                pdf_path=pdf_path,
                mode="B0_fullres_nozoom",
                vlm=vlm_client,
                settings=cfg,
                trace_id=trace_id,
            )
            selected_pages = sel_pages
            candidate_pages = list(range(1, result.total_pages + 1))
        elif group == "B1":
            result, sel_pages = run_sliding_grid_baseline(
                question=question,
                pdf_path=pdf_path,
                mode="B1_thumb_zoom",
                vlm=vlm_client,
                settings=cfg,
                trace_id=trace_id,
            )
            selected_pages = sel_pages
            candidate_pages = list(range(1, result.total_pages + 1))
        elif group == "B2":
            b2_cfg = cfg.model_copy(
                update={
                    "max_zooms": 0,
                    "max_inspect_rounds": 1,
                    "use_tree_index": True,
                }
            )
            result, state = run_m3(
                question=question,
                pdf_path=pdf_path,
                vlm=vlm_client,
                settings=b2_cfg,
                trace_id=trace_id,
            )
            all_nav_pages: list[int] = []
            for nr in state.nav_reports:
                all_nav_pages.extend(nr.candidate_pages)
            candidate_pages = sorted(list(set(all_nav_pages)))
            selected_pages = list(
                dict.fromkeys(
                    state.all_selected_pages
                    + state.selected_pages
                    + [p for e in state.evidence for p in e.reference_pages]
                    + result.reference_pages
                )
            )
        elif group == "M3":
            m3_cfg = cfg.model_copy(
                update={
                    "max_zooms": 3,
                    "max_inspect_rounds": 2,
                    "critic_min_confidence": 0.6,
                    "use_tree_index": True,
                }
            )
            result, state = run_m3(
                question=question,
                pdf_path=pdf_path,
                vlm=vlm_client,
                settings=m3_cfg,
                trace_id=trace_id,
            )
            all_nav_pages = []
            for nr in state.nav_reports:
                all_nav_pages.extend(nr.candidate_pages)
            candidate_pages = sorted(list(set(all_nav_pages)))
            selected_pages = list(
                dict.fromkeys(
                    state.all_selected_pages
                    + state.selected_pages
                    + [p for e in state.evidence for p in e.reference_pages]
                    + result.reference_pages
                )
            )
        elif group == "M5":
            m5_cfg = cfg.model_copy(
                update={
                    "use_tree_index": True,
                    "max_steps": max(cfg.max_steps, 16),
                    "zoom_mode": "fovea",
                    "enable_logprobs_entropy": True,
                    "enable_sboed_gate": True,
                    "force_zoom_on_alnum_code": True,
                    "enable_residual_verification": True,
                    "enable_tree_flattening": True,
                    "annotate_seeker_grid": True,
                    "enable_content_cache": True,
                }
            )
            result, state = run_m5(
                question=question,
                pdf_path=pdf_path,
                vlm=vlm_client,
                settings=m5_cfg,
                trace_id=trace_id,
            )
            all_nav_pages = []
            for nr in state.nav_reports:
                all_nav_pages.extend(nr.candidate_pages)
            candidate_pages = sorted(list(set(all_nav_pages)))
            selected_pages = list(
                dict.fromkeys(
                    state.all_selected_pages
                    + state.selected_pages
                    + [p for e in state.evidence for p in e.reference_pages]
                    + result.reference_pages
                )
            )
        elif group == "M5_crop":
            m5_crop_cfg = cfg.model_copy(
                update={
                    "use_tree_index": True,
                    "max_steps": max(cfg.max_steps, 16),
                    "zoom_mode": "crop",
                    "enable_logprobs_entropy": True,
                    "enable_sboed_gate": True,
                    "force_zoom_on_alnum_code": True,
                    "enable_residual_verification": False,
                    "enable_tree_flattening": True,
                    "annotate_seeker_grid": True,
                    "enable_content_cache": True,
                }
            )
            result, state = run_m5(
                question=question,
                pdf_path=pdf_path,
                vlm=vlm_client,
                settings=m5_crop_cfg,
                trace_id=trace_id,
            )
            all_nav_pages = []
            for nr in state.nav_reports:
                all_nav_pages.extend(nr.candidate_pages)
            candidate_pages = sorted(list(set(all_nav_pages)))
            selected_pages = list(
                dict.fromkeys(
                    state.all_selected_pages
                    + state.selected_pages
                    + [p for e in state.evidence for p in e.reference_pages]
                    + result.reference_pages
                )
            )
        elif group == "M5_no_sboed":
            m5_nosboed_cfg = cfg.model_copy(
                update={
                    "use_tree_index": True,
                    "max_steps": max(cfg.max_steps, 16),
                    "zoom_mode": "fovea",
                    "enable_logprobs_entropy": False,
                    "enable_sboed_gate": False,
                    "force_zoom_on_alnum_code": False,
                    "enable_residual_verification": True,
                    "enable_tree_flattening": True,
                    "annotate_seeker_grid": True,
                    "enable_content_cache": True,
                }
            )
            result, state = run_m5(
                question=question,
                pdf_path=pdf_path,
                vlm=vlm_client,
                settings=m5_nosboed_cfg,
                trace_id=trace_id,
            )
            all_nav_pages = []
            for nr in state.nav_reports:
                all_nav_pages.extend(nr.candidate_pages)
            candidate_pages = sorted(list(set(all_nav_pages)))
            selected_pages = list(
                dict.fromkeys(
                    state.all_selected_pages
                    + state.selected_pages
                    + [p for e in state.evidence for p in e.reference_pages]
                    + result.reference_pages
                )
            )
        else:
            raise ValueError(f"Unknown evaluation group: {group}")

        status = result.status
        pred_answer = result.answer
        ref_pages = result.reference_pages
        nav_rounds = result.nav_rounds
        seek_rounds = result.seek_rounds
        inspect_rounds = result.inspect_rounds
        zoom_count = result.zoom_count
        render_latency_ms = result.render_latency_ms
        tree_latency_ms = result.tree_latency_ms
        total_wall_ms = result.total_wall_ms
        usage_dict = result.usage.model_dump()

    except (ZoomDocError, APIError) as exc:
        error_msg = f"{type(exc).__name__}: {str(exc)}"

    is_correct = (
        normalize_text(pred_answer) == normalize_text(gold)
        if status in ("answered", "synthesized")
        else False
    )

    record = {
        "case_id": case_id,
        "group": group,
        "track": case.get("track", "Unknown"),
        "total_pages": case.get("total_pages", 0),
        "pdf_path": pdf_path,
        "gold": gold,
        "gold_page": gold_page,
        "font_pt": case.get("font_pt", 5.0),
        "query_type": case.get("query_type", "direct_section"),
        "status": status,
        "answer": pred_answer,
        "is_correct": is_correct,
        "reference_pages": ref_pages,
        "candidate_pages": candidate_pages,
        "selected_pages": selected_pages,
        "nav_rounds": nav_rounds,
        "seek_rounds": seek_rounds,
        "inspect_rounds": inspect_rounds,
        "zoom_count": zoom_count,
        "is_zoomed": zoom_count > 0,
        "residual_corrections": getattr(result, "residual_corrections", 0) if "result" in locals() else 0,
        "cache_hit": getattr(result, "cache_hit", False) if "result" in locals() else False,
        "render_latency_ms": render_latency_ms,
        "tree_latency_ms": tree_latency_ms,
        "total_wall_ms": total_wall_ms,
        "usage": usage_dict,
        "error": error_msg,
        "trace_id": trace_id,
    }

    group_dir = out_dir / group
    group_dir.mkdir(parents=True, exist_ok=True)
    res_path = group_dir / f"{case_id}.json"
    with open(res_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=2))

    return record


def run_eval_m4(
    group_arg: str,
    manifest_path: str,
    out_dir: str = "eval/m4/results",
    runs_dir: str = "eval/m4/runs",
    resume: bool = True,
    limit: int | None = None,
    vlm_client: Any = None,
    settings: Settings | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Execute evaluation benchmark across specified groups."""
    m_path = Path(manifest_path)
    if not m_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {m_path}")

    cases = [
        json.loads(line)
        for line in m_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if limit is not None and limit > 0:
        cases = cases[:limit]

    if group_arg == "all":
        groups = ["B0", "B1", "B2", "M3"]
    elif group_arg == "m5_all":
        groups = ["M5_no_sboed", "M5_crop", "M5"]
    else:
        groups = [group_arg]
    out_base = Path(out_dir)
    runs_base = Path(runs_dir)

    all_results: dict[str, list[dict[str, Any]]] = {}

    for grp in groups:
        grp_dir = out_base / grp
        grp_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []

        print(f"=== Starting Evaluation: Group {grp} ({len(cases)} cases) ===")
        for case in cases:
            case_id = case["case_id"]
            res_file = grp_dir / f"{case_id}.json"
            if resume and res_file.is_file() and res_file.stat().st_size > 0:
                print(f"[{grp}][{case_id}] Skipping (resumed from {res_file.name})")
                with open(res_file, encoding="utf-8") as fp:
                    results.append(json.load(fp))
                continue

            rec = run_case_for_group(
                case,
                grp,
                out_base,
                runs_base,
                vlm_client=vlm_client,
                settings=settings,
            )
            results.append(rec)
            status_sym = "[OK]" if rec["is_correct"] else "[X]"
            print(
                f"[{grp}][{case_id}] {status_sym} (pred={rec['answer']}, gold={rec['gold']}, status={rec['status']})"
            )

        all_results[grp] = results

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Milestone 4 Benchmark Runner.")
    parser.add_argument(
        "--group",
        choices=["B0", "B1", "B2", "M3", "M5", "M5_crop", "M5_no_sboed", "m5_all", "all"],
        default="all",
        help="Evaluation group to execute",
    )
    parser.add_argument(
        "--manifest",
        default="eval/m4/manifest.jsonl",
        help="Path to manifest.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        "--out",
        default="eval/m4/results",
        dest="out_dir",
        help="Directory to save evaluation results",
    )
    parser.add_argument(
        "--runs-dir",
        default="eval/m4/runs",
        help="Directory for execution traces",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Skip already processed cases",
    )
    parser.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="Force re-execution even if result files exist",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of cases to evaluate",
    )
    args = parser.parse_args()

    run_eval_m4(
        group_arg=args.group,
        manifest_path=args.manifest,
        out_dir=args.out_dir,
        runs_dir=args.runs_dir,
        resume=args.resume,
        limit=args.limit,
    )
