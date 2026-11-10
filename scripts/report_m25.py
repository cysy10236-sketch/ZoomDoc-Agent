from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Calculate Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    # z for 95% = 1.95996
    z = 1.95996 if abs(confidence - 0.95) < 1e-4 else 1.96
    p = k / n
    denom = 1.0 + (z**2) / n
    center = (p + (z**2) / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt((p * (1.0 - p)) / n + (z**2) / (4.0 * (n**2)))
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


def mcnemar_exact_p_value(b: int, c: int) -> float:
    """Calculate exact two-sided p-value for McNemar test using Binomial distribution."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # Two-sided p-value: 2 * sum_{i=0}^k C(n, i) * 0.5^n
    prob = sum(math.comb(n, i) for i in range(k + 1)) * (0.5**n)
    return round(min(1.0, 2.0 * prob), 4)


def point_biserial_correlation(confidences: list[float], correctness: list[int]) -> float:
    """Calculate Pearson / point-biserial correlation between confidence and binary correctness."""
    n = len(confidences)
    if n < 2:
        return 0.0

    mean_x = sum(confidences) / n
    mean_y = sum(correctness) / n

    cov = sum((confidences[i] - mean_x) * (correctness[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in confidences)
    var_y = sum((y - mean_y) ** 2 for y in correctness)

    if var_x <= 0 or var_y <= 0:
        return 0.0

    return round(cov / math.sqrt(var_x * var_y), 4)


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


def load_group_results(results_dir: Path, group: str) -> list[dict[str, Any]]:
    grp_dir = results_dir / group
    if not grp_dir.is_dir():
        return []
    records = []
    for f in sorted(grp_dir.glob("case_*.json")):
        with open(f, encoding="utf-8") as fp:
            records.append(json.load(fp))
    return records


def analyze_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {
            "n": 0,
            "acc": 0.0,
            "ci": (0.0, 0.0),
            "zoom_rate": 0.0,
            "avg_prompt_tokens": 0.0,
            "avg_completion_tokens": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "missing_usage": 0,
            "errors": 0,
        }

    correct_cnt = sum(1 for r in records if r.get("is_correct", False))
    acc = correct_cnt / n
    ci = wilson_score_interval(correct_cnt, n)
    zoom_cnt = sum(1 for r in records if r.get("is_zoomed", False))
    zoom_rate = zoom_cnt / n

    p_tokens = [r["usage"]["prompt_tokens"] for r in records if r.get("usage") and r["usage"].get("prompt_tokens")]
    c_tokens = [r["usage"]["completion_tokens"] for r in records if r.get("usage") and r["usage"].get("completion_tokens")]
    latencies = [r["usage"]["latency_ms"] for r in records if r.get("usage") and r["usage"].get("latency_ms")]
    missing = sum(r["usage"].get("missing_usage_calls", 0) for r in records if r.get("usage"))
    errors = sum(1 for r in records if r.get("status") == "error")

    return {
        "n": n,
        "correct": correct_cnt,
        "acc": round(acc, 4),
        "ci": ci,
        "zoom_rate": round(zoom_rate, 4),
        "avg_prompt_tokens": round(sum(p_tokens) / len(p_tokens), 1) if p_tokens else 0.0,
        "avg_completion_tokens": round(sum(c_tokens) / len(c_tokens), 1) if c_tokens else 0.0,
        "p50_latency_ms": round(percentile(latencies, 50), 1) if latencies else 0.0,
        "p95_latency_ms": round(percentile(latencies, 95), 1) if latencies else 0.0,
        "missing_usage": missing,
        "errors": errors,
    }


def compute_mcnemar(records_a: list[dict], records_b: list[dict]) -> tuple[int, int, float]:
    """Paired McNemar test on common case_ids: a is new (G1 or G2), b is baseline (G0 or G1)."""
    dict_b = {r["case_id"]: r.get("is_correct", False) for r in records_b}
    b_better = 0  # a correct, b incorrect
    c_worse = 0   # a incorrect, b correct

    for r_a in records_a:
        cid = r_a["case_id"]
        if cid not in dict_b:
            continue
        corr_a = r_a.get("is_correct", False)
        corr_b = dict_b[cid]
        if corr_a and not corr_b:
            b_better += 1
        elif not corr_a and corr_b:
            c_worse += 1

    p_val = mcnemar_exact_p_value(b_better, c_worse)
    return (b_better, c_worse, p_val)


def analyze_confidence_bins(records: list[dict]) -> tuple[dict[str, dict[str, Any]], float]:
    confs: list[float] = []
    corrs: list[int] = []

    bin_data: dict[str, list[bool]] = {
        "[0, 0.6)": [],
        "[0.6, 0.8)": [],
        "[0.8, 1.0]": [],
    }

    for r in records:
        conf = r.get("confidence")
        if conf is None:
            continue
        corr = 1 if r.get("is_correct", False) else 0
        confs.append(float(conf))
        corrs.append(corr)

        if conf < 0.6:
            bin_data["[0, 0.6)"].append(corr == 1)
        elif conf < 0.8:
            bin_data["[0.6, 0.8)"].append(corr == 1)
        else:
            bin_data["[0.8, 1.0]"].append(corr == 1)

    bin_stats: dict[str, dict[str, Any]] = {}
    for b_name, vals in bin_data.items():
        total = len(vals)
        corr_cnt = sum(1 for v in vals if v)
        acc = round(corr_cnt / total, 4) if total > 0 else 0.0
        bin_stats[b_name] = {"n": total, "correct": corr_cnt, "acc": acc}

    corr_coef = point_biserial_correlation(confs, corrs)
    return (bin_stats, corr_coef)


def generate_report(
    results_dir: str = "eval/m25/results",
    out_dir: str = "eval/m25",
    lock_file: str = "requirements.lock",
    vlm_model: str = "qwen2.5-vl-72b-instruct",
) -> str:
    r_dir = Path(results_dir)
    o_dir = Path(out_dir)
    o_dir.mkdir(parents=True, exist_ok=True)

    g0_records = load_group_results(r_dir, "G0")
    g1_records = load_group_results(r_dir, "G1")
    g2_records = load_group_results(r_dir, "G2")

    all_records = g0_records + g1_records + g2_records
    recorded_models = [r["vlm_model"] for r in all_records if r.get("vlm_model")]
    actual_model = recorded_models[0] if recorded_models else vlm_model

    stats_g0 = analyze_group(g0_records)
    stats_g1 = analyze_group(g1_records)
    stats_g2 = analyze_group(g2_records)

    # Paired McNemar tests
    b_g1, c_g1, p_mcnemar_g1_g0 = compute_mcnemar(g1_records, g0_records)
    b_g2, c_g2, p_mcnemar_g2_g1 = compute_mcnemar(g2_records, g1_records)

    # Confidence bin analysis: use G1 (unbiased single-round baseline without Critic survival bias)
    eval_conf_records = g1_records if g1_records else (g1_records + g2_records)
    conf_bins, r_pb = analyze_confidence_bins(eval_conf_records)

    # Bad cases export
    bad_cases_path = o_dir / "bad_cases.jsonl"
    with open(bad_cases_path, "w", encoding="utf-8") as fp:
        for r in g0_records + g1_records + g2_records:
            if not r.get("is_correct", False):
                bad_entry = {
                    "case_id": r["case_id"],
                    "group": r["group"],
                    "answer": r["answer"],
                    "gold": r["gold"],
                    "is_zoomed": r["is_zoomed"],
                    "confidence": r["confidence"],
                    "trace_id": r["trace_id"],
                    "error": r["error"],
                }
                fp.write(json.dumps(bad_entry, ensure_ascii=False) + "\n")

    # Lock sha256
    lock_path = Path(lock_file)
    lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()[:16] if lock_path.is_file() else "unknown"

    # Decision rule from §10.4
    acc_diff_h2 = stats_g1["acc"] - stats_g0["acc"]
    acc_diff_critic = stats_g2["acc"] - stats_g1["acc"]
    high_bin_acc = conf_bins["[0.8, 1.0]"]["acc"]
    low_bin_acc = conf_bins["[0, 0.6)"]["acc"]

    if acc_diff_h2 < 0.10 or p_mcnemar_g1_g0 >= 0.05:
        decision = "FAIL-H2"
        action = "停止进入 M3；回到设计评审（检查 view 分辨率、bbox 格式适配、zoom 参数）。"
    elif acc_diff_critic < 0.05 or (conf_bins["[0, 0.6)"]["n"] > 0 and high_bin_acc <= low_bin_acc):
        decision = "PASS-WEAK-CRITIC"
        action = "进入 M3；Critic 置信度规则标记为待替换（自报置信度未体现强相关性，按 H3 证伪路径推进）。"
    else:
        decision = "PASS"
        action = "全量指标通过；正式进入 Milestone 3（树索引与百页 PDF 导航）。"

    # Format Markdown Report
    lines = [
        f"## DECISION: {decision}",
        "",
        f"> **后续动作**：{action}",
        f"> **评测环境**：Model Snapshot=`{actual_model}`, `requirements.lock` sha256=`{lock_sha}`",
        "",
        "### 1. 组别消融综合对比 (G0 vs G1 vs G2)",
        "",
        "| 指标 | G0 (no-zoom) | G1 (zoom) | G2 (zoom+critic) |",
        "|---|---|---|---|",
        f"| 样本量 $N$ | {stats_g0['n']} | {stats_g1['n']} | {stats_g2['n']} |",
        f"| 准确率 (Accuracy) | {stats_g0['acc']*100:.1f}% | {stats_g1['acc']*100:.1f}% | {stats_g2['acc']*100:.1f}% |",
        f"| 95% Wilson 置信区间 | [{stats_g0['ci'][0]*100:.1f}%, {stats_g0['ci'][1]*100:.1f}%] | [{stats_g1['ci'][0]*100:.1f}%, {stats_g1['ci'][1]*100:.1f}%] | [{stats_g2['ci'][0]*100:.1f}%, {stats_g2['ci'][1]*100:.1f}%] |",
        f"| Zoom 触发率 | {stats_g0['zoom_rate']*100:.1f}% | {stats_g1['zoom_rate']*100:.1f}% | {stats_g2['zoom_rate']*100:.1f}% |",
        f"| 平均 Prompt Tokens | {stats_g0['avg_prompt_tokens']:.0f} | {stats_g1['avg_prompt_tokens']:.0f} | {stats_g2['avg_prompt_tokens']:.0f} |",
        f"| 平均 Completion Tokens | {stats_g0['avg_completion_tokens']:.0f} | {stats_g1['avg_completion_tokens']:.0f} | {stats_g2['avg_completion_tokens']:.0f} |",
        f"| 延迟 P50 (ms) | {stats_g0['p50_latency_ms']:.0f} | {stats_g1['p50_latency_ms']:.0f} | {stats_g2['p50_latency_ms']:.0f} |",
        f"| 延迟 P95 (ms) | {stats_g0['p95_latency_ms']:.0f} | {stats_g1['p95_latency_ms']:.0f} | {stats_g2['p95_latency_ms']:.0f} |",
        f"| 缺失 Token 统计调用数 | {stats_g0['missing_usage']} | {stats_g1['missing_usage']} | {stats_g2['missing_usage']} |",
        f"| 异常运行数 (Errors) | {stats_g0['errors']} | {stats_g1['errors']} | {stats_g2['errors']} |",
        "",
        "### 2. 统计显著性配对检验 (McNemar's Exact Test)",
        "",
        f"- **H2 假设检验 (G1 vs G0)**: G1胜={b_g1}, G0胜={c_g1}, 准确率差值 = `{(stats_g1['acc']-stats_g0['acc'])*100:+.1f}%`, **McNemar p = {p_mcnemar_g1_g0}**",
        f"- **Critic 增益检验 (G2 vs G1)**: G2胜={b_g2}, G1胜={c_g2}, 准确率差值 = `{(stats_g2['acc']-stats_g1['acc'])*100:+.1f}%`, **McNemar p = {p_mcnemar_g2_g1}**",
        "",
        "### 3. 置信度校准与相关性分析 (H3 检验)",
        "",
        f"- **点二列相关系数 $r_{{pb}}$ (Confidence vs. Correctness)**: `{r_pb}`",
        "- **置信度分桶准确率分布**:",
        f"  - `[0.0, 0.6)` (低置信): 样本数={conf_bins['[0, 0.6)']['n']}, 准确率=`{conf_bins['[0, 0.6)']['acc']*100:.1f}%`",
        f"  - `[0.6, 0.8)` (中置信): 样本数={conf_bins['[0.6, 0.8)']['n']}, 准确率=`{conf_bins['[0.6, 0.8)']['acc']*100:.1f}%`",
        f"  - `[0.8, 1.0]` (高置信): 样本数={conf_bins['[0.8, 1.0]']['n']}, 准确率=`{conf_bins['[0.8, 1.0]']['acc']*100:.1f}%`",
        "",
        f"### 4. 失败样例归档",
        f"已记录全量未完全匹配样例至 `{bad_cases_path.resolve()}`。",
    ]

    report_content = "\n".join(lines) + "\n"
    report_file = o_dir / "report.md"
    with open(report_file, "w", encoding="utf-8") as fp:
        fp.write(report_content)

    print(f"Report successfully written to: {report_file.resolve()}")
    return report_content


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Milestone 2.5 Evaluation Report.")
    parser.add_argument("--results-dir", default="eval/m25/results", help="Directory of evaluation results")
    parser.add_argument("--out-dir", default="eval/m25", help="Output directory for report")
    parser.add_argument("--model", default="qwen2.5-vl-72b-instruct", help="Model snapshot identifier")
    args = parser.parse_args()

    generate_report(results_dir=args.results_dir, out_dir=args.out_dir, vlm_model=args.model)
