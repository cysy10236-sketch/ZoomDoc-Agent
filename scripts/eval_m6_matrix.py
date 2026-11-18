from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openai import APIError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.make_eval_m6_matrix import make_eval_m6_matrix
from zoomdoc.config import Settings, get_settings
from zoomdoc.errors import ZoomDocError
from zoomdoc.eval.metrics import (
    compute_mcnemar,
    normalize_text,
    percentile,
    wilson_score_interval,
)
from zoomdoc.graph.supervisor import run_m3, run_m5

M6_GROUPS = ["NoZoom", "LinearCrop", "FoveaDoc_v2"]


def run_m6_case(
    case: dict[str, Any],
    group: str,
    out_dir: Path,
    runs_dir: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    cid = case["case_id"]
    gold = case["gold"]
    pdf_path = case["pdf_path"]
    question = case["question"]
    trace_id = f"m6-{group}-{cid}"

    base_cfg = get_settings().model_copy(
        update={
            "runs_dir": str(runs_dir.resolve()),
            "cache_dir": str(cache_dir.resolve()),
        }
    )

    status = "error"
    pred_answer: str | None = None
    ref_pages: list[int] = []
    zoom_count = 0
    residual_corrections = 0
    wall_ms = 0.0
    usage_dict: dict[str, Any] | None = None
    err_msg: str | None = None

    try:
        if group == "NoZoom":
            cfg = base_cfg.model_copy(
                update={
                    "use_tree_index": True,
                    "max_zooms": 0,
                    "max_inspect_rounds": 1,
                    "enable_tree_flattening": True,
                    "annotate_seeker_grid": True,
                    "enable_content_cache": True,
                }
            )
            res, _ = run_m3(question=question, pdf_path=pdf_path, settings=cfg, trace_id=trace_id)
        elif group == "LinearCrop":
            cfg = base_cfg.model_copy(
                update={
                    "use_tree_index": True,
                    "zoom_mode": "crop",
                    "zoom_pad_ratio": 0.06,
                    "zoom_min_norm_side": 0.06,
                    "enable_logprobs_entropy": True,
                    "enable_sboed_gate": True,
                    "force_zoom_on_alnum_code": True,
                    "enable_residual_verification": False,
                    "enable_tree_flattening": True,
                    "annotate_seeker_grid": True,
                    "enable_content_cache": True,
                }
            )
            res, _ = run_m5(question=question, pdf_path=pdf_path, settings=cfg, trace_id=trace_id)
        elif group == "FoveaDoc_v2":
            cfg = base_cfg.model_copy(
                update={
                    "use_tree_index": True,
                    "zoom_mode": "fovea",
                    "zoom_pad_ratio": 0.06,
                    "zoom_min_norm_side": 0.06,
                    "fovea_auto_anchors": True,
                    "fovea_adaptive_aspect": True,
                    "enable_logprobs_entropy": True,
                    "enable_sboed_gate": True,
                    "force_zoom_on_alnum_code": True,
                    "enable_residual_verification": True,
                    "enable_tree_flattening": True,
                    "annotate_seeker_grid": True,
                    "enable_content_cache": True,
                }
            )
            res, _ = run_m5(question=question, pdf_path=pdf_path, settings=cfg, trace_id=trace_id)
        else:
            raise ValueError(f"Unknown M6 group: {group}")

        status = res.status
        pred_answer = res.answer
        ref_pages = res.reference_pages
        zoom_count = res.zoom_count
        residual_corrections = res.residual_corrections
        wall_ms = res.total_wall_ms
        usage_dict = res.usage.model_dump()
    except (ZoomDocError, APIError) as exc:
        err_msg = f"{type(exc).__name__}: {exc}"

    is_correct = (
        normalize_text(pred_answer) == normalize_text(gold)
        if status in ("answered", "synthesized")
        else False
    )

    rec = {
        "case_id": cid,
        "group": group,
        "modality": case["modality"],
        "gold": gold,
        "gold_page": case["gold_page"],
        "row_metric": case["row_metric"],
        "col_header": case["col_header"],
        "status": status,
        "answer": pred_answer,
        "is_correct": is_correct,
        "reference_pages": ref_pages,
        "zoom_count": zoom_count,
        "residual_corrections": residual_corrections,
        "total_wall_ms": wall_ms,
        "usage": usage_dict,
        "error": err_msg,
        "trace_id": trace_id,
    }
    grp_dir = out_dir / group
    grp_dir.mkdir(parents=True, exist_ok=True)
    (grp_dir / f"{cid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def summarize_m6(results_dir: Path, out_dir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"groups": {}, "pairwise": {}}
    recs_by_grp: dict[str, list[dict[str, Any]]] = {}

    for grp in M6_GROUPS:
        gdir = results_dir / grp
        recs = [
            json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(gdir.glob("m6_case_*.json"))
        ] if gdir.is_dir() else []
        recs_by_grp[grp] = recs
        if not recs:
            continue
        n = len(recs)
        corr = sum(1 for r in recs if r.get("is_correct"))
        vec_recs = [r for r in recs if r.get("modality") == "vector_matrix"]
        ras_recs = [r for r in recs if r.get("modality") == "raster_scan_matrix"]
        vec_acc = sum(1 for r in vec_recs if r.get("is_correct")) / max(1, len(vec_recs))
        ras_acc = sum(1 for r in ras_recs if r.get("is_correct")) / max(1, len(ras_recs))
        ptoks = [r["usage"]["prompt_tokens"] for r in recs if r.get("usage")]
        ctoks = [r["usage"]["completion_tokens"] for r in recs if r.get("usage")]
        walls = [r["total_wall_ms"] for r in recs if r.get("total_wall_ms")]
        ci = wilson_score_interval(corr, n)

        summary["groups"][grp] = {
            "n": n,
            "correct": corr,
            "acc": round(corr / n, 4),
            "ci": ci,
            "vector_acc": round(vec_acc, 4),
            "raster_acc": round(ras_acc, 4),
            "zoom_rate": round(sum(1 for r in recs if r.get("zoom_count", 0) > 0) / n, 4),
            "residual_corrections": sum(r.get("residual_corrections", 0) for r in recs),
            "avg_prompt_tokens": round(sum(ptoks) / len(ptoks), 1) if ptoks else 0.0,
            "avg_completion_tokens": round(sum(ctoks) / len(ctoks), 1) if ctoks else 0.0,
            "p50_wall_ms": round(percentile(walls, 50), 1) if walls else 0.0,
            "p95_wall_ms": round(percentile(walls, 95), 1) if walls else 0.0,
            "errors": sum(1 for r in recs if r.get("status") == "error"),
        }

    if recs_by_grp.get("FoveaDoc_v2") and recs_by_grp.get("LinearCrop"):
        b, c, p = compute_mcnemar(recs_by_grp["FoveaDoc_v2"], recs_by_grp["LinearCrop"])
        summary["pairwise"]["FoveaDoc_v2_vs_LinearCrop"] = {"wins": b, "losses": c, "mcnemar_p": p}
    if recs_by_grp.get("FoveaDoc_v2") and recs_by_grp.get("NoZoom"):
        b, c, p = compute_mcnemar(recs_by_grp["FoveaDoc_v2"], recs_by_grp["NoZoom"])
        summary["pairwise"]["FoveaDoc_v2_vs_NoZoom"] = {"wins": b, "losses": c, "mcnemar_p": p}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if len(summary["groups"]) == len(M6_GROUPS):
        g_nz = summary["groups"]["NoZoom"]
        g_lc = summary["groups"]["LinearCrop"]
        g_fv = summary["groups"]["FoveaDoc_v2"]
        pw_lc = summary["pairwise"].get("FoveaDoc_v2_vs_LinearCrop", {})
        pw_nz = summary["pairwise"].get("FoveaDoc_v2_vs_NoZoom", {})
        md = [
            "# Milestone 6：宽矩阵多列消歧与纯扫描栅格页基准报告 (`manifest_m6.jsonl`, $N=12$)",
            "",
            "> **评测配置**：`qwen3.8-flash`，`temperature = 0`，`seed = 42`，50 页长文档内嵌 `18 行 x 5 列` 3.8pt 密集矩阵表（6 例原生矢量页 + 6 例纯位图扫描页 `words == []`）。",
            "",
            "| 指标 | NoZoom (1024px View) | LinearCrop (S-BOED + 矩形裁剪) | FoveaDoc v2.0 (双锚定凹面镜 + 自适应长宽比) |",
            "|---|---|---|---|",
            f"| 总样本量 $N$ | {g_nz['n']} | {g_lc['n']} | {g_fv['n']} |",
            f"| **总体准确率 (Overall Acc)** | {g_nz['acc']*100:.1f}% ({g_nz['correct']}/{g_nz['n']}) | {g_lc['acc']*100:.1f}% ({g_lc['correct']}/{g_lc['n']}) | **{g_fv['acc']*100:.1f}% ({g_fv['correct']}/{g_fv['n']})** |",
            f"| 95% Wilson 置信区间 | [{g_nz['ci'][0]*100:.1f}%, {g_nz['ci'][1]*100:.1f}%] | [{g_lc['ci'][0]*100:.1f}%, {g_lc['ci'][1]*100:.1f}%] | **[{g_fv['ci'][0]*100:.1f}%, {g_fv['ci'][1]*100:.1f}%]** |",
            f"| 矢量矩阵子集准确率 ($N=6$) | {g_nz['vector_acc']*100:.1f}% | {g_lc['vector_acc']*100:.1f}% | **{g_fv['vector_acc']*100:.1f}%** |",
            f"| **纯栅格扫描子集准确率 ($N=6$, 零矢量层)** | {g_nz['raster_acc']*100:.1f}% | {g_lc['raster_acc']*100:.1f}% | **{g_fv['raster_acc']*100:.1f}%** |",
            f"| Zoom 触发率 | {g_nz['zoom_rate']*100:.1f}% | {g_lc['zoom_rate']*100:.1f}% | {g_fv['zoom_rate']*100:.1f}% |",
            f"| 跨模态残差纠偏次数 | 0 | 0 | {g_fv['residual_corrections']} |",
            f"| 平均 Prompt Tokens | {g_nz['avg_prompt_tokens']:.0f} | {g_lc['avg_prompt_tokens']:.0f} | {g_fv['avg_prompt_tokens']:.0f} |",
            f"| 端到端耗时 P50 / P95 (s) | {g_nz['p50_wall_ms']/1000:.1f} / {g_nz['p95_wall_ms']/1000:.1f} | {g_lc['p50_wall_ms']/1000:.1f} / {g_lc['p95_wall_ms']/1000:.1f} | {g_fv['p50_wall_ms']/1000:.1f} / {g_fv['p95_wall_ms']/1000:.1f} |",
            f"| 异常运行数 (Errors) | {g_nz['errors']} | {g_lc['errors']} | {g_fv['errors']} |",
            "",
            "### 配对 McNemar 精确检验",
            f"- **FoveaDoc v2.0 vs LinearCrop**：胜={pw_lc.get('wins', 0)}，负={pw_lc.get('losses', 0)}，准确率增益 `{(g_fv['acc'] - g_lc['acc'])*100:+.1f}%`，**McNemar $p = {pw_lc.get('mcnemar_p', 1.0):.4f}$**",
            f"- **FoveaDoc v2.0 vs NoZoom**：胜={pw_nz.get('wins', 0)}，负={pw_nz.get('losses', 0)}，准确率增益 `{(g_fv['acc'] - g_nz['acc'])*100:+.1f}%`，**McNemar $p = {pw_nz.get('mcnemar_p', 1.0):.4f}$**",
            "",
        ]
        (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M6 Wide-Matrix & Pure-Raster Stress Benchmark.")
    parser.add_argument("--group", choices=M6_GROUPS + ["all", "report_only"], default="all")
    parser.add_argument("--out-dir", default="eval/m6_matrix")
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    args = parser.parse_args()

    base = Path(args.out_dir)
    manifest_file = base / "manifest_m6.jsonl"
    if not manifest_file.is_file():
        make_eval_m6_matrix(out_dir=str(base))

    results_dir = base / "results"
    runs_dir = base / "runs"

    if args.group == "report_only":
        s = summarize_m6(results_dir, base)
        print(json.dumps(s, indent=2))
        return

    cases = [
        json.loads(ln)
        for ln in manifest_file.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    groups = M6_GROUPS if args.group == "all" else [args.group]

    for grp in groups:
        cdir = Path(f".cache/zoomdoc_m6_{grp}")
        print(f"=== M6 Group {grp} ({len(cases)} cases) ===")
        for case in cases:
            cid = case["case_id"]
            rfile = results_dir / grp / f"{cid}.json"
            if args.resume and rfile.is_file() and rfile.stat().st_size > 0:
                print(f"[{grp}][{cid}] Resumed")
                continue
            rec = run_m6_case(case, grp, results_dir, runs_dir, cdir)
            mark = "[OK]" if rec["is_correct"] else "[X]"
            print(f"[{grp}][{cid}] {mark} ({rec['modality']}) pred={rec['answer']} gold={rec['gold']}")

    summarize_m6(results_dir, base)


if __name__ == "__main__":
    main()
