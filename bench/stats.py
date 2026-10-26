#!/usr/bin/env python3
"""Compare benchmark conditions and report an honest improvement percentage.

Standard library only, Python 3.8+.

Input: a JSON list of runs (or {"runs": [...]}). Each run is an object:

    {"task": "add-endpoint", "cond": "baseline", "rep": 1,
     "tokens": 41200, "success": true}

`tokens` (or whatever --metric names) may be null when the CLI did not report
it; such runs are skipped and counted in the warnings. `success` may be
omitted, in which case the success guardrail is reported as unchecked.

Method, in short:
  * Tasks are the independent unit, not repetitions.
  * Per task, take the median metric of each condition and form a ratio
    treatment / baseline.
  * Across tasks, combine ratios with the geometric mean, so one huge task
    cannot dominate and a task that doubles counts as much as one that halves.
  * Bootstrap a 95% interval by resampling tasks.
  * Estimate a noise floor from an A/A split of the baseline's own repetitions.
  * An "improvement" requires: interval above zero, point estimate above the
    noise floor, and task success that did not drop.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys

DEFAULT_BOOT = 5000
DEFAULT_NOISE_ITERS = 1000
MIN_TASKS = 3

VERDICT_TEXT = {
    "improvement": "Improvement: the interval is above zero, the effect exceeds "
                   "the noise floor, and task success held.",
    "improvement_unverified": "Tokens improved, but task success was not reported, "
                              "so the quality guardrail is unchecked.",
    "within_noise_floor": "The interval is above zero, but the effect is no larger "
                          "than run-to-run noise. Treat as unproven.",
    "no_measurable_change": "No measurable change: the interval includes zero.",
    "regression": "Regression: the treatment used more of the metric than baseline.",
    "success_dropped": "Not an improvement: task success dropped versus baseline.",
    "insufficient_data": "Insufficient data: need at least %d comparable tasks." % MIN_TASKS,
}


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------

def validate_runs(data, metric="tokens"):
    """Return `data` if it is a usable list of runs, else raise ValueError."""
    if not isinstance(data, list) or not data:
        raise ValueError("input must be a non-empty JSON list of run objects")
    for i, run in enumerate(data):
        if not isinstance(run, dict):
            raise ValueError("run %d is not an object" % i)
        for key in ("task", "cond"):
            if not isinstance(run.get(key), str) or not run[key]:
                raise ValueError("run %d: missing or invalid %r" % (i, key))
        value = run.get(metric)
        if value is not None:
            bad = (isinstance(value, bool)
                   or not isinstance(value, (int, float))
                   or not math.isfinite(value)
                   or value < 0)
            if bad:
                raise ValueError(
                    "run %d: %r must be a non-negative number or null" % (i, metric))
    return data


def load_runs(path, metric="tokens"):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "runs" in data:
        data = data["runs"]
    return validate_runs(data, metric)


# --------------------------------------------------------------------------
# Core statistics
# --------------------------------------------------------------------------

def _group(runs, cond, metric):
    """task -> list of metric values for `cond` (null values skipped)."""
    out = {}
    for run in runs:
        if run["cond"] != cond:
            continue
        value = run.get(metric)
        if value is None:
            continue
        out.setdefault(run["task"], []).append(float(value))
    return out


def per_task_ratios(runs, baseline, treatment, metric="tokens"):
    """Return (ratios, details, warnings) for tasks present in both conditions."""
    base = _group(runs, baseline, metric)
    trt = _group(runs, treatment, metric)
    ratios, details, warnings = {}, {}, []
    for task in sorted(set(base) | set(trt)):
        if task not in base or task not in trt:
            missing = baseline if task not in base else treatment
            warnings.append("task %r skipped: no usable runs in %r" % (task, missing))
            continue
        b = statistics.median(base[task])
        t = statistics.median(trt[task])
        if b <= 0 or t <= 0:
            warnings.append("task %r skipped: a median of zero cannot form a ratio" % task)
            continue
        ratios[task] = t / b
        details[task] = {
            "baseline": b,
            "treatment": t,
            "n_baseline": len(base[task]),
            "n_treatment": len(trt[task]),
        }
    return ratios, details, warnings


def geomean_reduction(ratios):
    """1 - geometric mean of ratios. 0.31 means 31% less than baseline."""
    values = list(ratios)
    if not values:
        return None
    return 1.0 - math.exp(sum(math.log(r) for r in values) / len(values))


def bootstrap_ci(ratios, n_boot=DEFAULT_BOOT, seed=0, alpha=0.05):
    """Percentile bootstrap over tasks. Returns (low, high) for the reduction."""
    logs = [math.log(r) for r in ratios]
    if not logs:
        return None, None
    rng = random.Random(seed)
    k = len(logs)
    reductions = sorted(
        1.0 - math.exp(sum(rng.choices(logs, k=k)) / k) for _ in range(n_boot))
    low = reductions[int((alpha / 2.0) * n_boot)]
    high = reductions[min(n_boot - 1, int((1.0 - alpha / 2.0) * n_boot))]
    return low, high


def noise_floor(runs, cond, metric="tokens", tasks=None,
                n_iter=DEFAULT_NOISE_ITERS, seed=0, quantile=0.95):
    """A/A noise floor: how big an 'improvement' appears when comparing a
    condition's own repetitions against each other.

    For each task with at least two runs, shuffle its runs, split them into two
    halves, and compare the halves exactly as a real comparison would. Repeat
    and return the given quantile of the absolute reduction. None if no task
    has at least two runs.
    """
    groups = {
        task: values
        for task, values in _group(runs, cond, metric).items()
        if len(values) >= 2 and (tasks is None or task in tasks)
    }
    if not groups:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_iter):
        logs = []
        for task in sorted(groups):
            values = groups[task][:]
            rng.shuffle(values)
            half = len(values) // 2
            a = statistics.median(values[:half])
            b = statistics.median(values[half:])
            if a > 0 and b > 0:
                logs.append(math.log(b / a))
        if logs:
            diffs.append(abs(1.0 - math.exp(sum(logs) / len(logs))))
    if not diffs:
        return None
    diffs.sort()
    return diffs[min(len(diffs) - 1, int(quantile * len(diffs)))]


def success_stats(runs, cond, tasks):
    total = wins = unknown = 0
    for run in runs:
        if run["cond"] != cond or run["task"] not in tasks:
            continue
        success = run.get("success")
        if success is None:
            unknown += 1
            continue
        total += 1
        wins += 1 if success else 0
    return {
        "runs": total,
        "wins": wins,
        "unknown": unknown,
        "rate": (wins / total) if total else None,
    }


def tokens_per_solved(runs, cond, metric, tasks):
    """Total metric across runs divided by successful runs. None if success is
    unknown for any run in scope, or if nothing succeeded."""
    spent = 0.0
    wins = 0
    for run in runs:
        if run["cond"] != cond or run["task"] not in tasks:
            continue
        success = run.get("success")
        if success is None:
            return None
        value = run.get(metric)
        if value is not None:
            spent += float(value)
        wins += 1 if success else 0
    return (spent / wins) if wins else None


def _task_success_rate(runs, cond, task):
    stats = success_stats(runs, cond, {task})
    return stats["rate"]


def decide(point, low, high, floor, success_ok, n_tasks):
    """Map the numbers to a verdict key. Order matters: data sufficiency and
    the success guardrail come before any claim about the metric."""
    if point is None or n_tasks < MIN_TASKS:
        return "insufficient_data"
    if success_ok is False:
        return "success_dropped"
    if high < 0:
        return "regression"
    if low > 0 and (floor is None or point > floor):
        return "improvement" if success_ok else "improvement_unverified"
    if low > 0:
        return "within_noise_floor"
    return "no_measurable_change"


def compare(runs, baseline, treatment, metric="tokens", n_boot=DEFAULT_BOOT,
            n_noise=DEFAULT_NOISE_ITERS, seed=0, success_tolerance=0.0):
    """Compare one treatment condition against the baseline. Returns a dict."""
    validate_runs(runs, metric)
    if baseline == treatment:
        raise ValueError("baseline and treatment must be different conditions")
    conditions = {run["cond"] for run in runs}
    for cond in (baseline, treatment):
        if cond not in conditions:
            raise ValueError("condition %r not found (have: %s)"
                             % (cond, ", ".join(sorted(conditions))))

    ratios, details, warnings = per_task_ratios(runs, baseline, treatment, metric)
    skipped = sum(1 for r in runs
                  if r["cond"] in (baseline, treatment) and r.get(metric) is None)
    if skipped:
        warnings.append("%d run(s) skipped: %r not reported" % (skipped, metric))

    tasks = set(ratios)
    point = geomean_reduction(ratios.values())
    low, high = bootstrap_ci(ratios.values(), n_boot, seed)
    floor = noise_floor(runs, baseline, metric, tasks, n_noise, seed)

    base_success = success_stats(runs, baseline, tasks)
    trt_success = success_stats(runs, treatment, tasks)
    if (base_success["rate"] is None or trt_success["rate"] is None
            or base_success["unknown"] or trt_success["unknown"]):
        success_ok = None
    else:
        success_ok = trt_success["rate"] >= base_success["rate"] - success_tolerance

    per_task = []
    for task in sorted(tasks):
        per_task.append({
            "task": task,
            "baseline": details[task]["baseline"],
            "treatment": details[task]["treatment"],
            "reduction": 1.0 - ratios[task],
            "success_baseline": _task_success_rate(runs, baseline, task),
            "success_treatment": _task_success_rate(runs, treatment, task),
        })

    return {
        "metric": metric,
        "baseline": baseline,
        "treatment": treatment,
        "n_tasks": len(tasks),
        "reduction": point,
        "ci_low": low,
        "ci_high": high,
        "share_of_baseline": None if point is None else 1.0 - point,
        "noise_floor": floor,
        "success": {
            "baseline": base_success,
            "treatment": trt_success,
            "tolerance": success_tolerance,
            "guardrail_ok": success_ok,
        },
        "per_solved": {
            "baseline": tokens_per_solved(runs, baseline, metric, tasks),
            "treatment": tokens_per_solved(runs, treatment, metric, tasks),
        },
        "per_task": per_task,
        "verdict": decide(point, low, high, floor, success_ok, len(tasks)),
        "warnings": warnings,
        "settings": {"n_boot": n_boot, "n_noise": n_noise, "seed": seed},
    }


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def _pct(x):
    return "n/a" if x is None else "%.1f%%" % (x * 100.0)


def _num(x):
    return "n/a" if x is None else "{:,.0f}".format(x)


def _rate(x):
    return "n/a" if x is None else "%.0f%%" % (x * 100.0)


def _guardrail(res):
    ok = res["success"]["guardrail_ok"]
    return {True: "OK", False: "DROPPED", None: "unchecked"}[ok]


def format_text(res):
    lines = ["%s -> %s   (metric: %s)" % (res["baseline"], res["treatment"], res["metric"])]
    if res["reduction"] is None:
        lines.append("  No comparable tasks.")
    else:
        lines += [
            "  Tasks compared:        %d (median per task, then geometric mean)" % res["n_tasks"],
            "  Reduction:             %s   (95%% CI %s to %s)"
            % (_pct(res["reduction"]), _pct(res["ci_low"]), _pct(res["ci_high"])),
            "  Share of baseline:     %s" % _pct(res["share_of_baseline"]),
            "  Noise floor (A/A):     %s" % _pct(res["noise_floor"]),
            "  Success:               %s -> %s   guardrail %s"
            % (_rate(res["success"]["baseline"]["rate"]),
               _rate(res["success"]["treatment"]["rate"]), _guardrail(res)),
            "  Per solved task:       %s -> %s"
            % (_num(res["per_solved"]["baseline"]), _num(res["per_solved"]["treatment"])),
        ]
    lines.append("  Verdict:               %s" % VERDICT_TEXT[res["verdict"]])
    if res["per_task"]:
        lines.append("")
        lines.append("  %-28s %12s %12s %10s  %s"
                     % ("task", "baseline", "treatment", "reduction", "success"))
        for row in res["per_task"]:
            lines.append("  %-28s %12s %12s %10s  %s -> %s" % (
                row["task"][:28], _num(row["baseline"]), _num(row["treatment"]),
                _pct(row["reduction"]), _rate(row["success_baseline"]),
                _rate(row["success_treatment"])))
    for warning in res["warnings"]:
        lines.append("  warning: %s" % warning)
    return "\n".join(lines)


def format_markdown(results):
    first = results[0]
    out = [
        "| Condition vs %s | Reduction (95%% CI) | Share of baseline | Noise floor | Success | Verdict |"
        % first["baseline"],
        "|---|---|---|---|---|---|",
    ]
    for res in results:
        out.append("| %s | %s (%s to %s) | %s | %s | %s -> %s | %s |" % (
            res["treatment"], _pct(res["reduction"]), _pct(res["ci_low"]),
            _pct(res["ci_high"]), _pct(res["share_of_baseline"]),
            _pct(res["noise_floor"]), _rate(res["success"]["baseline"]["rate"]),
            _rate(res["success"]["treatment"]["rate"]), res["verdict"]))
    out.append("")
    out.append("Metric: %s. Tasks are the unit of analysis (median per task, geometric mean "
               "across tasks, bootstrap over tasks). Settings: %d bootstrap draws, seed %d."
               % (first["metric"], first["settings"]["n_boot"], first["settings"]["seed"]))
    for res in results:
        for warning in res["warnings"]:
            out.append("- warning (%s): %s" % (res["treatment"], warning))
    return "\n".join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Compare benchmark conditions with a confidence interval, "
                    "a noise floor, and a task-success guardrail.")
    parser.add_argument("runs", help="JSON file: list of {task, cond, rep, tokens, success}")
    parser.add_argument("--baseline", default="baseline", help="baseline condition name")
    parser.add_argument("--treatment", nargs="+", default=None,
                        help="treatment condition(s); default: every condition except the baseline")
    parser.add_argument("--metric", default="tokens", help="numeric field to compare")
    parser.add_argument("--boot", type=int, default=DEFAULT_BOOT, help="bootstrap draws")
    parser.add_argument("--noise-iters", type=int, default=DEFAULT_NOISE_ITERS,
                        help="A/A noise-floor iterations")
    parser.add_argument("--seed", type=int, default=0, help="random seed (results are reproducible)")
    parser.add_argument("--success-tolerance", type=float, default=0.0,
                        help="allowed drop in success rate before the guardrail fails")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown summary table")
    parser.add_argument("--require-improvement", action="store_true",
                        help="exit 1 unless every comparison's verdict is 'improvement'")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        runs = load_runs(args.runs, args.metric)
        conditions = {run["cond"] for run in runs}
        treatments = args.treatment or sorted(conditions - {args.baseline})
        if not treatments:
            raise ValueError("no treatment conditions found")
        results = [
            compare(runs, args.baseline, treatment, args.metric, args.boot,
                    args.noise_iters, args.seed, args.success_tolerance)
            for treatment in treatments
        ]
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    elif args.markdown:
        print(format_markdown(results))
    else:
        print("\n\n".join(format_text(res) for res in results))

    if args.require_improvement and any(r["verdict"] != "improvement" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
