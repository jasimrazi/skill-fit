#!/usr/bin/env python3
"""Skill match orchestrator executing the 5-step resolution order.

Resolution order:
    1. Lockfile exists AND manifest_hash equals scanner's current hash -> use lockfile (no network).
    2. Cache hit for this tag set, within TTL (default 7 days) -> use cache (no network).
    3. Live search (capped queries) -> score -> shortlist -> write cache.
    4. Degraded mode on network failure -> fall back to stale cache with visible stale marker.
    5. Bundled seed catalog fallback -> use pre-vetted references/seed-catalog.json so
       offline/cold-start runs never return 0 skills for known stacks.

Flags:
    --refresh      Bypass lockfile and cache reads
    --offline      Lockfile, cache, and bundled seed only; never touch network
    --budget       Idle token budget for greedy selection (default 1500)
    --min-installs Minimum install count filter (default from rank.py, auto-relaxes on 0 match)
    --max-queries  Max live search queries to prevent CLI timeouts (default 4)
    --cache-dir    Override cache directory
    --human        Print human-readable table

Runtime: Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Bootstrap scripts
sys.path.insert(0, os.path.dirname(__file__))
import cache  # noqa: E402
import lock  # noqa: E402
import queries  # noqa: E402
import rank  # noqa: E402
from audit import audit  # noqa: E402
from registry import Registry  # noqa: E402
from scan import scan  # noqa: E402

MAX_OUTPUT_ESTIMATED_TOKENS = 1000
DEFAULT_MAX_LIVE_QUERIES = 4
SEED_CATALOG_PATH = Path(__file__).resolve().parent.parent / "references" / "seed-catalog.json"


def normalize_tag(raw_tag: str) -> str:
    """Normalize stack tags: lowercase, strip version constraints and namespace prefixes."""
    clean = str(raw_tag).lower().strip()
    # Strip version constraints like "^6.1.2", ">=3.0.0", "~1.2"
    clean = re.split(r"[\s^~>=<@!]+", clean)[0]
    # Keep both base and suffix clean (e.g., "flutter:provider" -> "provider")
    if ":" in clean:
        clean = clean.split(":")[-1]
    return clean


def normalize_tag_list(tags: List[str]) -> List[str]:
    """Return deduplicated list of normalized tags while preserving original order."""
    seen = set()
    result: List[str] = []
    for t in tags:
        norm = normalize_tag(t)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
        # Also include base package suffix if tag is a path (e.g. github.com/gin-gonic/gin -> gin)
        if "/" in norm:
            suffix = norm.split("/")[-1]
            if suffix and suffix not in seen:
                seen.add(suffix)
                result.append(suffix)
    return result


def load_seed_candidates(stack_tags: List[str]) -> List[Dict[str, Any]]:
    """Load pre-vetted candidates from references/seed-catalog.json matching stack_tags."""
    if not SEED_CATALOG_PATH.exists():
        return []
    try:
        raw = json.loads(SEED_CATALOG_PATH.read_text(encoding="utf-8"))
        all_candidates = raw.get("candidates", [])
        if not isinstance(all_candidates, list):
            return []

        norm_stack = set(normalize_tag_list(stack_tags))
        matched: List[Dict[str, Any]] = []
        for cand in all_candidates:
            cand_tags = {normalize_tag(t) for t in cand.get("tags", [])}
            # Include if tags overlap with detected stack or if marked universal
            if cand_tags & norm_stack or "universal" in cand_tags:
                matched.append(cand)
        return matched
    except Exception:
        return []


def rank_with_adaptive_floor(
    candidates: List[Dict[str, Any]],
    installed_skills: List[Dict[str, Any]],
    stack_tags: List[str],
    budget: int,
    min_install_count: int,
    now_epoch: Optional[float],
    warnings: List[str],
    strong_threshold: int = rank.DEFAULT_STRONG_THRESHOLD,
) -> Any:
    """Rank candidates; if min_install_count rejects all candidates, retry with floor=0."""
    rank_result = rank.rank_candidates(
        candidates=candidates,
        installed_skills=installed_skills,
        stack_tags=stack_tags,
        budget=budget,
        min_install_count=min_install_count,
        now_epoch=now_epoch,
        strong_threshold=strong_threshold,
    )
    if (
        not rank_result.shortlist
        and not rank_result.possible
        and candidates
        and min_install_count > 0
    ):
        relaxed_result = rank.rank_candidates(
            candidates=candidates,
            installed_skills=installed_skills,
            stack_tags=stack_tags,
            budget=budget,
            min_install_count=0,
            now_epoch=now_epoch,
            strong_threshold=strong_threshold,
        )
        if relaxed_result.shortlist or relaxed_result.possible:
            warnings.append(
                f"Relaxed min_install_count from {min_install_count} to 0 to surface valid matches"
            )
            return relaxed_result
    return rank_result


def enforce_output_token_budget(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure serialized JSON output stays strictly under MAX_OUTPUT_ESTIMATED_TOKENS."""
    serialized = json.dumps(data)
    est_tokens = len(serialized) // 4

    if est_tokens <= MAX_OUTPUT_ESTIMATED_TOKENS:
        data["estimated_output_tokens"] = est_tokens
        return data

    # Compact representation to trim excess tokens
    compact = copy.deepcopy(data)

    # Pass 1: Trim signals and descriptions across all candidate lists
    for key in ("shortlist", "strong", "possible"):
        for item in compact.get(key, []):
            if "signals" in item and isinstance(item["signals"], dict):
                sig = item["signals"]
                fit_val = sig.get("fit", {}).get("total", 0) if isinstance(sig.get("fit"), dict) else 0
                qual_val = sig.get("quality", {}).get("total", 0) if isinstance(sig.get("quality"), dict) else 0
                pop_val = sig.get("popularity", {}).get("score", 0) if isinstance(sig.get("popularity"), dict) else 0
                item["signals"] = {"fit": fit_val, "quality": qual_val, "pop": pop_val}
            if "description" in item and len(item["description"]) > 80:
                item["description"] = item["description"][:77] + "..."

    # Trim lists if long
    if len(compact.get("rejected", [])) > 5:
        compact["rejected"] = compact["rejected"][:5]

    if len(compact.get("possible", [])) > 5:
        compact["possible"] = compact["possible"][:5]

    serialized_compact = json.dumps(compact)
    est_tokens = len(serialized_compact) // 4
    if est_tokens <= MAX_OUTPUT_ESTIMATED_TOKENS:
        compact["estimated_output_tokens"] = est_tokens
        return compact

    # Pass 2: More aggressive compression if still over budget
    for key in ("shortlist", "strong", "possible"):
        for item in compact.get(key, []):
            item.pop("signals", None)

    if len(compact.get("possible", [])) > 3:
        compact["possible"] = [
            {"id": p.get("id"), "layer": p.get("layer"), "score": p.get("score")}
            for p in compact["possible"][:3]
        ]

    if len(compact.get("rejected", [])) > 3:
        compact["rejected"] = [
            {"id": r.get("id"), "reason": r.get("reason")}
            for r in compact["rejected"][:3]
        ]

    serialized_compact = json.dumps(compact)
    compact["estimated_output_tokens"] = len(serialized_compact) // 4
    return compact


def match_skills(
    repo_path: Union[str, Path] = ".",
    lockfile_path: Optional[Union[str, Path]] = None,
    cache_dir: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    offline: bool = False,
    budget: int = rank.DEFAULT_BUDGET,
    min_install_count: int = rank.DEFAULT_MIN_INSTALL_COUNT,
    max_queries: int = DEFAULT_MAX_LIVE_QUERIES,
    time_budget_s: float = 10.0,
    strong_threshold: int = rank.DEFAULT_STRONG_THRESHOLD,
    now_epoch: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute the resolution order and return deterministic shortlist payload."""
    repo = Path(repo_path).resolve()
    lock_file = Path(lockfile_path) if lockfile_path else (repo / lock.DEFAULT_LOCKFILE_NAME)

    warnings: List[str] = []

    # Step 0: Scan repository to extract stack tags and manifest hash
    scan_result = scan(str(repo))
    manifest_hash = scan_result.get("hash", "")
    raw_extracted_tags = queries.extract_tags_from_scan(scan_result)
    extracted_tags = normalize_tag_list(raw_extracted_tags)

    # Load installed skills for collision check
    installed_report = audit(project_root=str(repo))
    installed_skills = installed_report.get("skills", [])

    # Step 1: Check Lockfile
    if not refresh and lock_file.exists():
        lock_data = lock.read_lockfile(lock_file)
        if lock_data and lock_data.get("manifest_hash") == manifest_hash:
            locked_skills = lock_data.get("skills", [])
            output = {
                "resolution_source": "lockfile",
                "manifest_hash": manifest_hash,
                "stack_tags": extracted_tags,
                "has_strong_matches": bool(locked_skills),
                "message": f"Resolved {len(locked_skills)} locked skill{'s' if len(locked_skills) != 1 else ''} from lockfile.",
                "shortlist": locked_skills,
                "strong": locked_skills,
                "possible": [],
                "gaps": [],
                "rejected": [],
                "total_description_tokens": sum(
                    s.get("description_tokens", 0) for s in locked_skills
                ),
                "budget": budget,
                "is_stale": False,
                "warnings": warnings,
            }
            return enforce_output_token_budget(output)
        elif lock_data:
            old_h = lock_data.get("manifest_hash")
            warnings.append(
                f"Lockfile hash mismatch ({old_h} != {manifest_hash}); using cache/search"
            )

    # Step 2: Check Cache (within TTL)
    if not refresh:
        cache_entry, cache_warnings = cache.get_cache(
            tags=extracted_tags,
            cache_dir=cache_dir,
            now_s=now_epoch,
        )
        warnings.extend(cache_warnings)
        if cache_entry and not cache_entry.is_stale and cache_entry.candidates:
            rank_result = rank_with_adaptive_floor(
                candidates=cache_entry.candidates,
                installed_skills=installed_skills,
                stack_tags=extracted_tags,
                budget=budget,
                min_install_count=min_install_count,
                now_epoch=now_epoch,
                warnings=warnings,
                strong_threshold=strong_threshold,
            )
            if rank_result.shortlist or rank_result.possible:
                output = {
                    "resolution_source": "cache",
                    "manifest_hash": manifest_hash,
                    "stack_tags": extracted_tags,
                    "has_strong_matches": rank_result.has_strong_matches,
                    "message": rank_result.message,
                    "shortlist": rank_result.shortlist,
                    "strong": rank_result.strong,
                    "possible": rank_result.possible,
                    "gaps": rank_result.gaps,
                    "rejected": rank_result.rejected,
                    "total_description_tokens": rank_result.total_description_tokens,
                    "budget": budget,
                    "is_stale": False,
                    "warnings": warnings + rank_result.warnings,
                }
                return enforce_output_token_budget(output)

    # Step 3: Live Search (unless offline)
    if not offline:
        try:
            q_res = queries.build_queries(scan_result)
            full_query_list = q_res.get("queries", [])
            query_list = full_query_list[:max_queries]
            if len(full_query_list) > max_queries:
                warnings.append(
                    f"Capped live queries from {len(full_query_list)} to {max_queries} for speed"
                )

            query_details = q_res.get("query_details", [])
            queries_with_layers = [
                (d["query"], d.get("layer", "general"))
                for d in query_details
                if d["query"] in query_list
            ]

            registry = Registry()
            pool_result = registry.generate_candidates(
                queries_with_layers=queries_with_layers,
                tags=extracted_tags,
                time_budget_s=time_budget_s,
            )
            warnings.extend(pool_result.get("warnings", []))
            candidates: List[Dict[str, Any]] = pool_result.get("candidates", [])

            # Merge bundled seed candidates if live search came back empty or partial
            seed_cands = load_seed_candidates(extracted_tags)
            if seed_cands:
                existing_ids = {c.get("id") for c in candidates}
                for sc in seed_cands:
                    if sc.get("id") not in existing_ids:
                        candidates.append(sc)

            pool_result["candidates"] = candidates

            # Write results to cache if non-empty
            if candidates:
                cache.set_candidate_pool_cache(
                    tags=extracted_tags,
                    pool_data=pool_result,
                    cache_dir=cache_dir,
                )
                cache.set_cache(
                    tags=extracted_tags,
                    candidates=candidates,
                    cache_dir=cache_dir,
                )

            rank_result = rank_with_adaptive_floor(
                candidates=candidates,
                installed_skills=installed_skills,
                stack_tags=extracted_tags,
                budget=budget,
                min_install_count=min_install_count,
                now_epoch=now_epoch,
                warnings=warnings,
                strong_threshold=strong_threshold,
            )
            if rank_result.shortlist or rank_result.possible:
                output = {
                    "resolution_source": "live",
                    "manifest_hash": manifest_hash,
                    "stack_tags": extracted_tags,
                    "has_strong_matches": rank_result.has_strong_matches,
                    "message": rank_result.message,
                    "shortlist": rank_result.shortlist,
                    "strong": rank_result.strong,
                    "possible": rank_result.possible,
                    "gaps": rank_result.gaps,
                    "rejected": rank_result.rejected,
                    "total_description_tokens": rank_result.total_description_tokens,
                    "budget": budget,
                    "is_stale": False,
                    "queries_completed": pool_result.get("queries_completed", len(query_list)),
                    "queries_total": pool_result.get("queries_total", len(query_list)),
                    "query_statuses": pool_result.get("query_statuses", []),
                    "warnings": warnings + rank_result.warnings,
                }
                return enforce_output_token_budget(output)

        except Exception as exc:
            warnings.append(f"Network live search failed ({exc}); falling back to degraded mode")

    # Step 4: Degraded Mode / Offline Fallback -> Stale Cache
    stale_entry, stale_warnings = cache.get_cache(
        tags=extracted_tags,
        cache_dir=cache_dir,
        offline=True,
        allow_stale=True,
        now_s=now_epoch,
    )
    warnings.extend(stale_warnings)

    if stale_entry and stale_entry.candidates:
        rank_result = rank_with_adaptive_floor(
            candidates=stale_entry.candidates,
            installed_skills=installed_skills,
            stack_tags=extracted_tags,
            budget=budget,
            min_install_count=min_install_count,
            now_epoch=now_epoch,
            warnings=warnings,
            strong_threshold=strong_threshold,
        )
        if rank_result.shortlist or rank_result.possible:
            output = {
                "resolution_source": "stale_cache",
                "manifest_hash": manifest_hash,
                "stack_tags": extracted_tags,
                "has_strong_matches": rank_result.has_strong_matches,
                "message": rank_result.message,
                "shortlist": rank_result.shortlist,
                "strong": rank_result.strong,
                "possible": rank_result.possible,
                "gaps": rank_result.gaps,
                "rejected": rank_result.rejected,
                "total_description_tokens": rank_result.total_description_tokens,
                "budget": budget,
                "is_stale": True,
                "warnings": warnings + rank_result.warnings,
            }
            return enforce_output_token_budget(output)

    # Step 5: Bundled Seed Catalog Fallback (Zero-Network Guarantee)
    seed_candidates = load_seed_candidates(extracted_tags)
    if seed_candidates:
        rank_result = rank_with_adaptive_floor(
            candidates=seed_candidates,
            installed_skills=installed_skills,
            stack_tags=extracted_tags,
            budget=budget,
            min_install_count=0,
            now_epoch=now_epoch,
            warnings=warnings,
            strong_threshold=strong_threshold,
        )
        if rank_result.shortlist or rank_result.possible:
            warnings.append("Resolved from bundled seed catalog (no live cache or network match)")
            output = {
                "resolution_source": "bundled_seed",
                "manifest_hash": manifest_hash,
                "stack_tags": extracted_tags,
                "has_strong_matches": rank_result.has_strong_matches,
                "message": rank_result.message,
                "shortlist": rank_result.shortlist,
                "strong": rank_result.strong,
                "possible": rank_result.possible,
                "gaps": rank_result.gaps,
                "rejected": rank_result.rejected,
                "total_description_tokens": rank_result.total_description_tokens,
                "budget": budget,
                "is_stale": False,
                "warnings": warnings + rank_result.warnings,
            }
            return enforce_output_token_budget(output)

    # Empty fallback when offline or complete miss
    source = "offline_miss" if offline else "failed"
    if offline:
        warnings.append("Offline mode active and no lockfile, cache, or seed entry available")

    output = {
        "resolution_source": source,
        "manifest_hash": manifest_hash,
        "stack_tags": extracted_tags,
        "has_strong_matches": False,
        "message": "No skills found or offline mode with no local cache/seed match",
        "shortlist": [],
        "strong": [],
        "possible": [],
        "gaps": extracted_tags,
        "rejected": [],
        "total_description_tokens": 0,
        "budget": budget,
        "is_stale": False,
        "warnings": warnings,
    }
    return enforce_output_token_budget(output)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_human(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    source = result.get("resolution_source", "unknown").upper()
    is_stale = " [STALE]" if result.get("is_stale") else ""
    lines.append(f"=== SKILL MATCH SHORTLIST ({source}{is_stale}) ===")
    lines.append(f"Manifest Hash: {result.get('manifest_hash', 'unknown')}")
    lines.append(f"Stack Tags:    {', '.join(result.get('stack_tags', []))}")
    lines.append(
        f"Total Tokens:  {result.get('total_description_tokens', 0)} / {result.get('budget', 0)} "
        f"(Output est: {result.get('estimated_output_tokens', 0)} tok)"
    )
    lines.append("")

    has_strong = result.get("has_strong_matches", bool(result.get("shortlist")))

    if not has_strong or not result.get("shortlist"):
        lines.append("=== NO STRONG MATCH ===")
        lines.append(result.get("message", "No skills met the threshold for strong recommendation."))
        lines.append("")

        possible = result.get("possible", [])
        if possible:
            lines.append(f"Near Misses / Possible Skills ({len(possible)}):")
            for idx, item in enumerate(possible, 1):
                cid = item.get("id", "")
                layer = item.get("layer", "")
                tokens = item.get("description_tokens", 0)
                score = item.get("score", 0)
                desc = item.get("description", "")
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                reason = item.get("tier_reason", "")
                reason_str = f" [{reason}]" if reason else ""
                lines.append(f"  {idx}. [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok){reason_str}")
                lines.append(f"     \"{desc}\"")

        gaps = result.get("gaps", [])
        if gaps:
            lines.append("")
            lines.append("Coverage Gaps (Detected tags with no candidates):")
            for g in gaps:
                lines.append(f"  - {g}")
    else:
        lines.append("Shortlisted Skills (Strong Recommendations):")
        for idx, item in enumerate(result.get("shortlist", []), 1):
            cid = item.get("id", "")
            layer = item.get("layer", "")
            tokens = item.get("description_tokens", 0)
            score = item.get("score", 0)
            desc = item.get("description", "")
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"  {idx}. [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok)")
            lines.append(f"     \"{desc}\"")

        possible = result.get("possible", [])
        if possible:
            lines.append("")
            lines.append(f"Possible Alternatives ({len(possible)}):")
            for idx, item in enumerate(possible, 1):
                cid = item.get("id", "")
                layer = item.get("layer", "")
                score = item.get("score", 0)
                tokens = item.get("description_tokens", 0)
                lines.append(f"  - [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok)")

        gaps = result.get("gaps", [])
        if gaps:
            lines.append("")
            lines.append("Coverage Gaps (Detected tags with no candidates):")
            for g in gaps:
                lines.append(f"  - {g}")

    rejected = result.get("rejected", [])
    if rejected:
        lines.append("")
        lines.append(f"Rejected ({len(rejected)}):")
        for r in rejected:
            lines.append(f"  - {r.get('id', ''):<35} -> {r.get('reason', '')}")

    warnings = result.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  ! {w}")

    return "\n".join(lines)

    rejected = result.get("rejected", [])
    if rejected:
        lines.append("")
        lines.append(f"Rejected ({len(rejected)}):")
        for r in rejected:
            lines.append(f"  - {r.get('id', ''):<35} -> {r.get('reason', '')}")

    warnings = result.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  ! {w}")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-fit live match orchestrator")
    parser.add_argument("repo", nargs="?", default=".", help="Path to repository root")
    parser.add_argument("--lockfile", default=None, help="Path to skill-fit.lock")
    parser.add_argument("--cache-dir", default=None, help="Override cache directory")
    parser.add_argument("--refresh", action="store_true", help="Bypass lockfile and cache")
    parser.add_argument("--offline", action="store_true", help="Offline mode; no network")
    parser.add_argument("--budget", type=int, default=rank.DEFAULT_BUDGET, help="Token budget")
    parser.add_argument(
        "--min-installs",
        type=int,
        default=rank.DEFAULT_MIN_INSTALL_COUNT,
        help="Min install count",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=DEFAULT_MAX_LIVE_QUERIES,
        help="Max live queries to prevent timeout",
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=10.0,
        help="Total time budget in seconds for candidate generation",
    )
    parser.add_argument("--human", action="store_true", help="Print human-readable table")

    args = parser.parse_args(argv)

    result = match_skills(
        repo_path=args.repo,
        lockfile_path=args.lockfile,
        cache_dir=args.cache_dir,
        refresh=args.refresh,
        offline=args.offline,
        budget=args.budget,
        min_install_count=args.min_installs,
        max_queries=args.max_queries,
        time_budget_s=args.time_budget,
    )

    if args.human:
        print(render_human(result))
    else:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())