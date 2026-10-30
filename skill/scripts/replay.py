#!/usr/bin/env python3
"""Offline replay and evaluation against recorded candidate pools.

Capabilities:
1. Replay mode: Evaluates candidate pools completely offline without network.
2. Labeling sheet generator: Produces an unlabelled evaluation sheet for user review.
3. Evaluation metrics: Computes junk rate, precision@3 per stack, and empty-when-good-exists rate.

Usage:
    replay.py [--pools-dir DIR] [--sheet] [--labels FILE] [--threshold 70]

Runtime: Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Bootstrap scripts
sys.path.insert(0, os.path.dirname(__file__))
import rank  # noqa: E402

DEFAULT_POOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "eval_pools"


def load_pool(pool_input: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Load candidate pool from dict or JSON file."""
    if isinstance(pool_input, dict):
        return pool_input
    p = Path(pool_input)
    if not p.exists():
        raise FileNotFoundError(f"Pool file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def replay_pool(
    pool_input: Union[str, Path, Dict[str, Any]],
    strong_threshold: int = rank.DEFAULT_STRONG_THRESHOLD,
    budget: int = rank.DEFAULT_BUDGET,
    min_install_count: int = rank.DEFAULT_MIN_INSTALL_COUNT,
    now_epoch: Optional[float] = None,
) -> Dict[str, Any]:
    """Run offline deterministic ranking against a single candidate pool."""
    pool_data = load_pool(pool_input)
    stack_name = pool_data.get("stack_name", "Unknown Stack")
    tags = pool_data.get("tags", [])
    candidates = pool_data.get("candidates", [])

    rank_result = rank.rank_candidates(
        candidates=candidates,
        stack_tags=tags,
        budget=budget,
        min_install_count=min_install_count,
        now_epoch=now_epoch,
        strong_threshold=strong_threshold,
    )

    # Adaptive floor if all candidates rejected by min_install_count
    if (
        not rank_result.shortlist
        and not rank_result.possible
        and candidates
        and min_install_count > 0
    ):
        relaxed = rank.rank_candidates(
            candidates=candidates,
            stack_tags=tags,
            budget=budget,
            min_install_count=0,
            now_epoch=now_epoch,
            strong_threshold=strong_threshold,
        )
        if relaxed.shortlist or relaxed.possible:
            rank_result = relaxed

    return {
        "stack_name": stack_name,
        "tags": tags,
        "has_strong_matches": rank_result.has_strong_matches,
        "message": rank_result.message,
        "shortlist": rank_result.shortlist,
        "strong": rank_result.strong,
        "possible": rank_result.possible,
        "gaps": rank_result.gaps,
        "rejected": rank_result.rejected,
        "total_description_tokens": rank_result.total_description_tokens,
        "budget": rank_result.budget,
        "candidates": candidates,
    }


def replay_all_pools(
    pools_dir: Optional[Union[str, Path]] = None,
    strong_threshold: int = rank.DEFAULT_STRONG_THRESHOLD,
    budget: int = rank.DEFAULT_BUDGET,
    min_install_count: int = rank.DEFAULT_MIN_INSTALL_COUNT,
    now_epoch: Optional[float] = None,
) -> Dict[str, Dict[str, Any]]:
    """Replay all recorded pools in a directory offline."""
    directory = Path(pools_dir) if pools_dir else DEFAULT_POOLS_DIR
    if not directory.exists():
        raise FileNotFoundError(f"Pools directory not found: {directory}")

    results: Dict[str, Dict[str, Any]] = {}
    pool_files = sorted(directory.glob("*.json"))
    for file_path in pool_files:
        if file_path.name.startswith("blind_"):
            continue
        try:
            pool_data = load_pool(file_path)
            if not isinstance(pool_data, dict) or "candidates" not in pool_data:
                continue
            res = replay_pool(
                pool_data,
                strong_threshold=strong_threshold,
                budget=budget,
                min_install_count=min_install_count,
                now_epoch=now_epoch,
            )
            results[res["stack_name"]] = res
        except Exception:
            continue

    return results


def generate_labeling_sheet(
    replay_results: Dict[str, Dict[str, Any]],
) -> str:
    """Produce an evaluation labeling sheet strictly UNLABELED for human review.

    Invariant: The agent MUST NOT pre-fill any labels.
    """
    lines: List[str] = []
    lines.append("# Evaluation Labeling Sheet")
    lines.append("")
    lines.append("Please mark each candidate as: `relevant`, `not relevant`, or `unsure`.")
    lines.append("")
    lines.append("| Stack | Candidate ID | Description | Source | Your Label |")
    lines.append("|---|---|---|---|---|")

    # Collect candidates across all stacks
    for stack_name, res in replay_results.items():
        candidates = res.get("candidates", [])
        for cand in candidates:
            cid = cand.get("id", "")
            meta = cand.get("meta") or {}
            desc = (meta.get("description") or cand.get("description") or "").strip()
            desc = desc.replace("|", "\\|")
            if len(desc) > 85:
                desc = desc[:82] + "..."
            source = cand.get("source_repo", cand.get("repo", "unknown"))

            # Blank / unselected checkbox choice for user labeling
            user_label_placeholder = "[ ] relevant / [ ] not relevant / [ ] unsure"
            lines.append(f"| {stack_name} | `{cid}` | {desc} | {source} | {user_label_placeholder} |")

    return "\n".join(lines)


def compute_metrics(
    replay_results: Dict[str, Dict[str, Any]],
    user_labels: Dict[Tuple[str, str], str],
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compute junk rate, precision@3 per stack, and empty-when-good-exists rate.

    user_labels mapping: (stack_name, candidate_id) -> "relevant" | "not relevant" | "unsure"
    """
    thresholds = thresholds or {}
    total_recommended = 0
    total_junk = 0
    total_possible = 0
    total_possible_junk = 0
    unsure_counts: Dict[str, int] = {}
    precision_at_3_per_stack: Dict[str, float] = {}

    stacks_with_relevant_candidate = 0
    empty_when_good_exists_count = 0

    for stack_name, res in replay_results.items():
        strong = res.get("strong", [])
        possible = res.get("possible", [])
        candidates = res.get("candidates", [])

        # Check if the pool contains at least one relevant candidate
        pool_has_relevant = False
        unsure_for_stack = 0
        for c in candidates:
            cid = c.get("id", "")
            lbl = user_labels.get((stack_name, cid), "").strip().lower()
            if lbl == "relevant":
                pool_has_relevant = True
            elif lbl == "unsure":
                unsure_for_stack += 1
        unsure_counts[stack_name] = unsure_for_stack

        if pool_has_relevant:
            stacks_with_relevant_candidate += 1
            if len(strong) == 0:
                empty_when_good_exists_count += 1

        # Evaluate strong recommendations (precision@3 among labeled candidates)
        labeled_strong = [c for c in strong if (stack_name, c.get("id", "")) in user_labels]
        top3 = labeled_strong[:3]
        relevant_in_top3 = 0
        for cand in top3:
            cid = cand.get("id", "")
            lbl = user_labels.get((stack_name, cid), "").strip().lower()
            if lbl == "relevant":
                relevant_in_top3 += 1

        p3 = (relevant_in_top3 / len(top3)) if top3 else 0.0
        if top3 or pool_has_relevant:
            precision_at_3_per_stack[stack_name] = round(p3, 3)

        for cand in labeled_strong:
            total_recommended += 1
            cid = cand.get("id", "")
            lbl = user_labels.get((stack_name, cid), "").strip().lower()
            if lbl != "relevant":
                total_junk += 1

        for cand in possible:
            cid = cand.get("id", "")
            if (stack_name, cid) not in user_labels:
                continue
            total_possible += 1
            lbl = user_labels.get((stack_name, cid), "").strip().lower()
            if lbl != "relevant":
                total_possible_junk += 1

    junk_rate_pct = round((total_junk / total_recommended * 100), 1) if total_recommended else 0.0
    possible_junk_rate_pct = (
        round((total_possible_junk / total_possible * 100), 1) if total_possible else 0.0
    )
    macro_precision = (
        round(sum(precision_at_3_per_stack.values()) / len(precision_at_3_per_stack), 3)
        if precision_at_3_per_stack
        else 0.0
    )
    empty_rate_pct = (
        round((empty_when_good_exists_count / stacks_with_relevant_candidate * 100), 1)
        if stacks_with_relevant_candidate
        else 0.0
    )

    return {
        "n_stacks": len(replay_results),
        "n_evaluated_recommendations": total_recommended,
        "strong_junk_count": total_junk,
        "junk_rate_pct": junk_rate_pct,
        "n_evaluated_possible": total_possible,
        "possible_junk_count": total_possible_junk,
        "possible_junk_rate_pct": possible_junk_rate_pct,
        "unsure_counts": unsure_counts,
        "total_unsure": sum(unsure_counts.values()),
        "macro_precision_at_3": macro_precision,
        "precision_at_3": precision_at_3_per_stack,
        "empty_when_good_exists_pct": empty_rate_pct,
        "stacks_with_relevant_candidate": stacks_with_relevant_candidate,
        "empty_when_good_exists_count": empty_when_good_exists_count,
        "thresholds": thresholds,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-fit offline replay & evaluation")
    parser.add_argument("--pools-dir", default=str(DEFAULT_POOLS_DIR), help="Path to evaluation pools dir")
    parser.add_argument("--sheet", action="store_true", help="Generate unlabeled labeling sheet")
    parser.add_argument("--labels", default=None, help="JSON file mapping (stack, id) -> label")
    parser.add_argument(
        "--threshold", type=int, default=rank.DEFAULT_STRONG_THRESHOLD, help="Strong match threshold"
    )

    args = parser.parse_args(argv)
    results = replay_all_pools(args.pools_dir, strong_threshold=args.threshold)

    if args.sheet:
        sheet_text = generate_labeling_sheet(results)
        print(sheet_text)
        return 0

    if args.labels:
        labels_path = Path(args.labels)
        if not labels_path.exists():
            print(f"Error: labels file not found: {labels_path}", file=sys.stderr)
            return 1
        raw_labels = json.loads(labels_path.read_text(encoding="utf-8"))
        # Parse into tuple keys (supports both schema formats)
        user_labels: Dict[Tuple[str, str], str] = {}
        for entry in raw_labels:
            stack_k = entry.get("stack", "")
            id_k = entry.get("id") or entry.get("candidate_id", "")
            label_v = entry.get("label") or entry.get("user_label", "")
            if stack_k and id_k:
                user_labels[(stack_k, id_k)] = label_v

        metrics = compute_metrics(results, user_labels)
        print("=== EVALUATION METRICS REPORT ===")
        print(f"Stacks evaluated (n): {metrics['n_stacks']}")
        print(f"Total strong recommendations (n): {metrics['n_evaluated_recommendations']}")
        print(f"Junk rate: {metrics['junk_rate_pct']}%")
        print(f"Macro Precision@3: {metrics['macro_precision_at_3']}")
        print(f"Empty-when-good-exists rate: {metrics['empty_when_good_exists_pct']}%")
        print("\nPrecision@3 per stack:")
        for stack, p3 in metrics["precision_at_3"].items():
            print(f"  - {stack:<25}: {p3}")
        return 0

    print(f"Successfully replayed {len(results)} candidate pools offline.")
    for stack, res in results.items():
        n_strong = len(res["strong"])
        n_possible = len(res["possible"])
        n_rejected = len(res["rejected"])
        print(f"  - {stack:<25}: {n_strong} strong, {n_possible} possible, {n_rejected} rejected")

    return 0


if __name__ == "__main__":
    sys.exit(main())
