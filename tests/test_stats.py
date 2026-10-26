"""Tests for bench/stats.py. Standard library plus pytest only."""
import importlib.util
import json
import math
import pathlib
import random
import sys

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "bench" / "stats.py"
_spec = importlib.util.spec_from_file_location("bench_stats", _PATH)
stats = importlib.util.module_from_spec(_spec)
sys.modules["bench_stats"] = stats
_spec.loader.exec_module(stats)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def make_runs(n_tasks=8, reps=3, factor=0.6, noise=0.03, trt_success_every=1,
              seed=1, base_cond="baseline", trt_cond="both"):
    """Synthetic runs. Task sizes differ by 8x so averaging raw tokens would
    be dominated by the biggest task. Treatment succeeds on every
    `trt_success_every`-th repetition (1 means always)."""
    rng = random.Random(seed)
    runs = []
    for t in range(n_tasks):
        size = 1000 * (t + 1)
        for rep in range(reps):
            runs.append({"task": "task-%d" % t, "cond": base_cond, "rep": rep,
                         "tokens": size * rng.uniform(1 - noise, 1 + noise),
                         "success": True})
            runs.append({"task": "task-%d" % t, "cond": trt_cond, "rep": rep,
                         "tokens": size * factor * rng.uniform(1 - noise, 1 + noise),
                         "success": rep % trt_success_every == 0})
    return runs


def const_runs(base, trt, tasks=4, reps=2):
    runs = []
    for t in range(tasks):
        for rep in range(reps):
            runs.append({"task": "t%d" % t, "cond": "baseline", "rep": rep,
                         "tokens": base, "success": True})
            runs.append({"task": "t%d" % t, "cond": "both", "rep": rep,
                         "tokens": trt, "success": True})
    return runs


def multi_runs():
    runs = make_runs(factor=0.8, trt_cond="map_only", seed=1)
    extra = make_runs(factor=0.6, trt_cond="both", seed=2)
    return runs + [r for r in extra if r["cond"] == "both"]


# --------------------------------------------------------------------------
# Core arithmetic
# --------------------------------------------------------------------------

def test_known_reduction():
    res = stats.compare(const_runs(100, 50), "baseline", "both", n_boot=200, n_noise=50)
    assert res["reduction"] == pytest.approx(0.5)
    assert res["share_of_baseline"] == pytest.approx(0.5)
    assert res["n_tasks"] == 4


def test_geometric_mean_cancels_symmetric_ratios():
    # One task halves and one doubles: net effect is zero, not +25%.
    assert abs(stats.geomean_reduction([0.5, 2.0])) < 1e-12


def test_median_used_per_task_not_mean():
    runs = []
    for rep, tokens in enumerate([100, 100, 1000]):
        runs.append({"task": "a", "cond": "baseline", "rep": rep, "tokens": tokens})
    for rep in range(3):
        runs.append({"task": "a", "cond": "both", "rep": rep, "tokens": 50})
    ratios, _, _ = stats.per_task_ratios(runs, "baseline", "both")
    assert ratios["a"] == pytest.approx(0.5)


def test_tasks_not_tokens_are_the_unit():
    runs = []
    # One enormous task with no change, three small tasks that halve.
    for task, base, trt in [("big", 1_000_000, 1_000_000), ("s1", 100, 50),
                            ("s2", 100, 50), ("s3", 100, 50)]:
        for rep in range(2):
            runs.append({"task": task, "cond": "baseline", "rep": rep, "tokens": base})
            runs.append({"task": task, "cond": "both", "rep": rep, "tokens": trt})
    res = stats.compare(runs, "baseline", "both", n_boot=200, n_noise=50)
    assert res["reduction"] == pytest.approx(1 - 0.5 ** 0.75)
    # Pooled tokens would show a reduction near 0.015%.
    assert res["reduction"] > 0.4


def test_ci_is_ordered_and_contains_point():
    res = stats.compare(make_runs(), "baseline", "both", n_boot=1000, n_noise=100)
    assert res["ci_low"] <= res["reduction"] + 1e-9
    assert res["reduction"] <= res["ci_high"] + 1e-9


def test_ci_collapses_when_every_task_has_same_ratio():
    low, high = stats.bootstrap_ci([0.5, 0.5, 0.5, 0.5], n_boot=500, seed=3)
    assert low == pytest.approx(0.5)
    assert high == pytest.approx(0.5)


def test_results_are_deterministic_for_a_seed():
    a = stats.compare(make_runs(), "baseline", "both", n_boot=500, n_noise=100, seed=7)
    b = stats.compare(make_runs(), "baseline", "both", n_boot=500, n_noise=100, seed=7)
    assert a == b


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

def test_verdict_improvement():
    res = stats.compare(make_runs(factor=0.6), "baseline", "both", n_boot=1000, n_noise=200)
    assert res["verdict"] == "improvement"
    assert res["reduction"] == pytest.approx(0.4, abs=0.05)
    assert res["ci_low"] > 0
    assert res["success"]["guardrail_ok"] is True


def test_identical_conditions_are_not_an_improvement():
    res = stats.compare(make_runs(factor=1.0, noise=0.05), "baseline", "both",
                        n_boot=1000, n_noise=200)
    assert res["verdict"] in ("no_measurable_change", "within_noise_floor")


def test_verdict_regression():
    res = stats.compare(make_runs(factor=2.0), "baseline", "both", n_boot=500, n_noise=100)
    assert res["verdict"] == "regression"
    assert res["reduction"] < 0


def test_cheaper_but_failing_is_not_an_improvement():
    runs = make_runs(factor=0.6, trt_success_every=2)  # 2 of 3 reps succeed
    res = stats.compare(runs, "baseline", "both", n_boot=500, n_noise=100)
    assert res["verdict"] == "success_dropped"
    assert res["success"]["guardrail_ok"] is False


def test_success_tolerance_can_allow_a_small_drop():
    runs = make_runs(factor=0.6, trt_success_every=2)
    res = stats.compare(runs, "baseline", "both", n_boot=500, n_noise=100,
                        success_tolerance=0.4)
    assert res["verdict"] == "improvement"


def test_missing_success_data_is_flagged_unverified():
    runs = make_runs(factor=0.6)
    for run in runs:
        run.pop("success")
    res = stats.compare(runs, "baseline", "both", n_boot=500, n_noise=100)
    assert res["verdict"] == "improvement_unverified"
    assert res["success"]["guardrail_ok"] is None
    assert res["per_solved"]["baseline"] is None


def test_too_few_tasks_is_insufficient_data():
    res = stats.compare(make_runs(n_tasks=2), "baseline", "both", n_boot=200, n_noise=50)
    assert res["verdict"] == "insufficient_data"


def test_effect_inside_noise_floor_is_not_called_an_improvement():
    # decide() directly: interval above zero but the point estimate is below the floor.
    assert stats.decide(0.03, 0.01, 0.05, 0.10, True, 8) == "within_noise_floor"
    assert stats.decide(0.30, 0.10, 0.50, 0.10, True, 8) == "improvement"
    assert stats.decide(0.30, 0.10, 0.50, None, True, 8) == "improvement"


# --------------------------------------------------------------------------
# Data handling
# --------------------------------------------------------------------------

def test_null_metric_runs_are_skipped_with_a_warning():
    runs = make_runs()
    victim = next(r for r in runs if r["cond"] == "both")
    victim["tokens"] = None
    res = stats.compare(runs, "baseline", "both", n_boot=200, n_noise=50)
    assert any("skipped" in w and "tokens" in w for w in res["warnings"])
    assert res["n_tasks"] == 8


def test_task_missing_from_treatment_is_skipped_with_a_warning():
    runs = [r for r in make_runs() if not (r["cond"] == "both" and r["task"] == "task-0")]
    res = stats.compare(runs, "baseline", "both", n_boot=200, n_noise=50)
    assert res["n_tasks"] == 7
    assert any("task-0" in w for w in res["warnings"])


def test_unknown_condition_raises_and_lists_known_ones():
    with pytest.raises(ValueError, match="nope.*baseline"):
        stats.compare(make_runs(), "baseline", "nope")


def test_same_condition_raises():
    with pytest.raises(ValueError):
        stats.compare(make_runs(), "baseline", "baseline")


@pytest.mark.parametrize("bad", [
    [],
    "not a list",
    [1, 2, 3],
    [{"cond": "a", "tokens": 1}],                        # no task
    [{"task": "t", "cond": "a", "tokens": -5}],          # negative
    [{"task": "t", "cond": "a", "tokens": True}],        # bool is not a number here
    [{"task": "t", "cond": "a", "tokens": "12"}],        # string
    [{"task": "t", "cond": "a", "tokens": float("inf")}],
])
def test_invalid_input_is_rejected(bad):
    with pytest.raises(ValueError):
        stats.validate_runs(bad)


def test_zero_median_task_is_skipped():
    runs = const_runs(100, 50)
    for run in runs:
        if run["task"] == "t0" and run["cond"] == "both":
            run["tokens"] = 0
    res = stats.compare(runs, "baseline", "both", n_boot=200, n_noise=50)
    assert res["n_tasks"] == 3
    assert any("t0" in w for w in res["warnings"])


# --------------------------------------------------------------------------
# Noise floor and per-solved cost
# --------------------------------------------------------------------------

def test_noise_floor_is_none_with_single_repetitions():
    assert stats.noise_floor(make_runs(reps=1), "baseline") is None


def test_noise_floor_is_zero_when_repetitions_agree():
    assert stats.noise_floor(const_runs(100, 100), "baseline", n_iter=100) == 0.0


def test_noise_floor_grows_with_run_to_run_noise():
    quiet = stats.noise_floor(make_runs(noise=0.01), "baseline", n_iter=300)
    noisy = stats.noise_floor(make_runs(noise=0.30), "baseline", n_iter=300)
    assert noisy > quiet
    assert noisy > 0.05


def test_noise_floor_is_deterministic_for_a_seed():
    a = stats.noise_floor(make_runs(noise=0.2), "baseline", n_iter=200, seed=4)
    b = stats.noise_floor(make_runs(noise=0.2), "baseline", n_iter=200, seed=4)
    assert a == b


def test_tokens_per_solved_penalises_cheap_failures():
    runs = [
        {"task": "t", "cond": "baseline", "tokens": 100, "success": True},
        {"task": "t", "cond": "baseline", "tokens": 100, "success": True},
        {"task": "t", "cond": "both", "tokens": 60, "success": True},
        {"task": "t", "cond": "both", "tokens": 60, "success": False},
    ]
    assert stats.tokens_per_solved(runs, "baseline", "tokens", {"t"}) == pytest.approx(100)
    assert stats.tokens_per_solved(runs, "both", "tokens", {"t"}) == pytest.approx(120)


def test_tokens_per_solved_is_none_when_nothing_succeeds():
    runs = [{"task": "t", "cond": "both", "tokens": 60, "success": False}]
    assert stats.tokens_per_solved(runs, "both", "tokens", {"t"}) is None


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _write(tmp_path, runs, name="runs.json"):
    path = tmp_path / name
    path.write_text(json.dumps(runs))
    return str(path)


def test_cli_compares_every_other_condition_by_default(tmp_path, capsys):
    path = _write(tmp_path, multi_runs())
    code = stats.main([path, "--boot", "300", "--noise-iters", "50"])
    out = capsys.readouterr().out
    assert code == 0
    assert "baseline -> both" in out
    assert "baseline -> map_only" in out
    assert "Verdict" in out


def test_cli_json_output_parses(tmp_path, capsys):
    path = _write(tmp_path, multi_runs())
    assert stats.main([path, "--json", "--boot", "300", "--noise-iters", "50"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert [r["treatment"] for r in parsed] == ["both", "map_only"]
    assert all("verdict" in r for r in parsed)


def test_cli_markdown_output_has_a_table(tmp_path, capsys):
    path = _write(tmp_path, multi_runs())
    assert stats.main([path, "--markdown", "--boot", "300", "--noise-iters", "50"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| Condition vs baseline |")
    assert "| both |" in out and "| map_only |" in out


def test_cli_output_is_reproducible(tmp_path, capsys):
    path = _write(tmp_path, multi_runs())
    args = [path, "--boot", "300", "--noise-iters", "50", "--seed", "5"]
    stats.main(args)
    first = capsys.readouterr().out
    stats.main(args)
    assert capsys.readouterr().out == first


def test_cli_require_improvement_exit_codes(tmp_path):
    good = _write(tmp_path, make_runs(factor=0.6), "good.json")
    flat = _write(tmp_path, make_runs(factor=1.0, noise=0.05), "flat.json")
    base = ["--treatment", "both", "--boot", "500", "--noise-iters", "100",
            "--require-improvement"]
    assert stats.main([good] + base) == 0
    assert stats.main([flat] + base) == 1


def test_cli_accepts_wrapped_runs_object(tmp_path, capsys):
    wrapped = {"_note": "SYNTHETIC", "runs": make_runs()}
    path = _write(tmp_path, wrapped)
    assert stats.main([path, "--boot", "200", "--noise-iters", "50"]) == 0


def test_cli_reports_errors_with_exit_code_2(tmp_path, capsys):
    assert stats.main([str(tmp_path / "missing.json")]) == 2
    assert "error" in capsys.readouterr().err

    path = _write(tmp_path, make_runs())
    assert stats.main([path, "--treatment", "nope"]) == 2
    assert "nope" in capsys.readouterr().err

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert stats.main([str(bad)]) == 2


def test_cli_alternate_metric(tmp_path, capsys):
    runs = make_runs()
    for run in runs:
        run["tool_calls"] = run["tokens"] / 100.0
    path = _write(tmp_path, runs)
    assert stats.main([path, "--metric", "tool_calls", "--json",
                       "--boot", "200", "--noise-iters", "50"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed[0]["metric"] == "tool_calls"
    assert math.isclose(parsed[0]["reduction"], 0.4, abs_tol=0.05)
