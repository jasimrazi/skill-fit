#!/usr/bin/env python3
"""Deterministic offline scoring and selection for candidate agent skills.

Hard filters:
    1. Layer collision with installed skill (infer installed skill's layer)
    2. Failed audit status
    3. Missing or empty description
    4. Install count below configurable floor (default 1000)

Scoring signals (offline & deterministic):
    - Tag match strength (framework/backend/orm > generic)
    - Description tokens (lower is better)
    - SKILL.md line count (<300 preferred)
    - References/ present (modularity)
    - Scripts flag (informational only; 0 points)
    - Last commit age (freshness)
    - Audit status (clean)
    * install_count is FLOOR ONLY and NEVER affects score or rank.

Usage:
    rank.py <candidates.json> [--installed FILE] [--tags react,next] [--budget 1500] [--human]

Runtime: Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

# Bootstrap queries
sys.path.insert(0, os.path.dirname(__file__))
from queries import load_tag_queries_table  # noqa: E402

DEFAULT_BUDGET = 1500
DEFAULT_MIN_INSTALL_COUNT = 1000

# Layer weight priorities for tag-match strength
LAYER_PRIORITIES: Dict[str, int] = {
    "framework": 30,
    "backend": 30,
    "orm": 28,
    "styling": 25,
    "testing": 25,
    "infra": 18,
    "ci": 18,
    "language": 15,
}


@dataclasses.dataclass
class RankResult:
    shortlist: List[Dict[str, Any]]
    rejected: List[Dict[str, Any]]
    total_description_tokens: int
    budget: int
    installed_layer_conflicts: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shortlist": self.shortlist,
            "rejected": self.rejected,
            "total_description_tokens": self.total_description_tokens,
            "budget": self.budget,
            "installed_layer_conflicts": self.installed_layer_conflicts,
            "warnings": self.warnings,
        }


def infer_installed_skills_layers(
    installed_skills: Sequence[Dict[str, Any]],
    tag_queries_table: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Map installed skill names to their inferred layer based on tag-queries.

    Returns:
        (skill_name_to_layer, warnings_for_unknown)
    """
    table = tag_queries_table if tag_queries_table is not None else load_tag_queries_table()
    layer_map: Dict[str, str] = {}
    warnings: List[str] = []

    for s in installed_skills:
        name = s.get("name", "").strip().lower()
        desc = s.get("description", "").strip().lower()
        matched_layer: Optional[str] = None

        # Check tag matches
        for tag, details in table.items():
            tag_lower = tag.lower()
            pattern = rf"\b{re.escape(tag_lower)}\b"
            if re.search(pattern, name) or re.search(pattern, desc):
                matched_layer = details.get("layer", "unknown")
                break

        if matched_layer and matched_layer != "unknown":
            layer_map[name] = matched_layer
        else:
            warnings.append(f"Unknown layer for installed skill '{name}' (no collision enforced)")

    return layer_map, warnings


def parse_commit_age_days(
    last_commit: Optional[str], now_epoch: Optional[float] = None
) -> Optional[int]:
    """Calculate age of last commit in days."""
    if not last_commit:
        return None
    try:
        norm = last_commit.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(norm)
        ref_time = now_epoch if now_epoch is not None else datetime.datetime.now(
            datetime.timezone.utc
        ).timestamp()
        diff_s = ref_time - dt.timestamp()
        return max(0, int(diff_s / 86400))
    except (ValueError, TypeError, AttributeError):
        return None


def score_candidate(
    candidate: Dict[str, Any],
    stack_tags: Sequence[str],
    now_epoch: Optional[float] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Compute deterministic score and signal breakdown for a single candidate.

    install_count is STRICTLY excluded from score calculation.
    """
    meta = candidate.get("meta") or {}
    layer = candidate.get("layer", "general").lower()
    query = candidate.get("found_by_query", "").lower()

    signals: Dict[str, Any] = {}

    # 1. Tag match strength
    base_layer_score = LAYER_PRIORITIES.get(layer, 15)
    exact_tag_bonus = 0
    for tag in stack_tags:
        t_clean = tag.strip().lower()
        if t_clean and t_clean in query:
            exact_tag_bonus = 20
            break
    tag_score = base_layer_score + exact_tag_bonus
    signals["tag_match"] = {
        "score": tag_score,
        "base_layer": base_layer_score,
        "exact_tag_bonus": exact_tag_bonus,
    }

    # 2. Description tokens (lower is better to avoid idle bloat)
    desc_tokens = meta.get("description_tokens", 25)
    if desc_tokens <= 15:
        token_score = 25
    elif desc_tokens <= 30:
        token_score = 18
    elif desc_tokens <= 50:
        token_score = 10
    else:
        token_score = 5
    signals["description_tokens"] = {
        "score": token_score,
        "tokens": desc_tokens,
    }

    # 3. Line count (<300 preferred)
    line_count = meta.get("skill_md_lines", 150)
    if line_count < 300:
        line_score = 20
    elif line_count <= 500:
        line_score = 10
    else:
        line_score = 0
    signals["line_count"] = {
        "score": line_score,
        "lines": line_count,
    }

    # 4. References present (modularity bonus)
    has_refs = bool(meta.get("has_references", False))
    ref_score = 15 if has_refs else 0
    signals["references"] = {
        "score": ref_score,
        "has_references": has_refs,
    }

    # 5. Scripts flag (informational only; 0 points bonus)
    has_scripts = bool(meta.get("has_scripts", False))
    signals["scripts"] = {
        "score": 0,
        "has_scripts": has_scripts,
        "note": "informational only",
    }

    # 6. Commit age (freshness)
    last_commit = meta.get("last_commit")
    age_days = parse_commit_age_days(last_commit, now_epoch=now_epoch)
    if age_days is None:
        freshness_score = 5
    elif age_days <= 90:
        freshness_score = 15
    elif age_days <= 180:
        freshness_score = 10
    elif age_days <= 365:
        freshness_score = 5
    else:
        freshness_score = 0
    signals["freshness"] = {
        "score": freshness_score,
        "age_days": age_days,
    }

    # 7. Audit status
    audit_status = meta.get("audit_status", "clean")
    audit_score = 10 if audit_status == "clean" else 0
    signals["audit_status"] = {
        "score": audit_score,
        "status": audit_status,
    }

    total_score = (
        tag_score
        + token_score
        + line_score
        + ref_score
        + freshness_score
        + audit_score
    )

    return total_score, signals


def rank_candidates(
    candidates: Sequence[Union[Dict[str, Any], Any]],
    installed_skills: Optional[Sequence[Dict[str, Any]]] = None,
    stack_tags: Optional[Sequence[str]] = None,
    tag_queries_table: Optional[Dict[str, Dict[str, Any]]] = None,
    budget: int = DEFAULT_BUDGET,
    min_install_count: int = DEFAULT_MIN_INSTALL_COUNT,
    now_epoch: Optional[float] = None,
) -> RankResult:
    """Filter, score, and greedily select candidate skills deterministically."""
    clean_tags = list(stack_tags) if stack_tags else []
    installed = list(installed_skills) if installed_skills else []
    table = tag_queries_table if tag_queries_table is not None else load_tag_queries_table()

    # Step 1: Infer installed skills layers
    installed_layers_map, unknown_layer_warnings = infer_installed_skills_layers(installed, table)
    installed_layers: Set[str] = set(installed_layers_map.values())
    installed_names: Set[str] = {s.get("name", "").strip().lower() for s in installed}

    # Normalize candidates to dicts
    cand_dicts: List[Dict[str, Any]] = []
    for c in candidates:
        if hasattr(c, "to_dict") and callable(c.to_dict):
            cand_dicts.append(c.to_dict())
        elif isinstance(c, dict):
            cand_dicts.append(c)
        else:
            cand_dicts.append(dataclasses.asdict(c))

    rejected: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []

    # Step 2: Apply Hard Filters
    for cand in cand_dicts:
        cid = cand.get("id", "")
        skill_name = cand.get("skill_name", "").strip().lower()
        layer = cand.get("layer", "general").strip().lower()
        meta = cand.get("meta") or {}
        description = meta.get("description", "").strip()
        audit_status = meta.get("audit_status", "clean").strip().lower()
        install_count = cand.get("install_count")

        # Hard Filter A: Already installed
        if skill_name in installed_names or cid in installed_names:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Skill '{skill_name}' is already installed",
            })
            continue

        # Hard Filter B: Layer collision with installed skill
        if layer in installed_layers:
            matched = [k for k, v in installed_layers_map.items() if v == layer][0]
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Layer collision with installed skill '{matched}' (layer: {layer})",
            })
            continue

        # Hard Filter C: Audit status failed
        if audit_status == "failed":
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Failed audit status: '{audit_status}'",
            })
            continue

        # Hard Filter D: Missing description
        if not description:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": "Missing or empty skill description",
            })
            continue

        # Hard Filter E: Install count floor
        if install_count is not None and install_count < min_install_count:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Install count ({install_count}) below floor ({min_install_count})",
            })
            continue

        eligible.append(cand)

    # Step 3: Score eligible candidates
    scored_candidates: List[Tuple[int, str, Dict[str, Any], Dict[str, Any]]] = []
    for cand in eligible:
        score, signals = score_candidate(cand, clean_tags, now_epoch=now_epoch)
        cid = cand.get("id", "")
        # Store tuple: (score, negative_id_or_id for stable tie breaking)
        scored_candidates.append((score, cid, cand, signals))

    # Sort deterministically: score DESCENDING, id ASCENDING (tie-break)
    # In python: sort key = (-score, cid)
    scored_candidates.sort(key=lambda item: (-item[0], item[1]))

    # Step 4: Greedy selection under budget (one skill per layer)
    shortlist: List[Dict[str, Any]] = []
    selected_layers: Set[str] = set()
    current_tokens = 0

    for score, cid, cand, signals in scored_candidates:
        layer = cand.get("layer", "general").strip().lower()
        meta = cand.get("meta") or {}
        desc_tokens = meta.get("description_tokens", 25)

        if layer in selected_layers:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Layer collision in selection: layer '{layer}' already represented",
            })
            continue

        if current_tokens + desc_tokens > budget:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Exceeds token budget ({current_tokens} + {desc_tokens} > {budget})",
            })
            continue

        # Add to shortlist
        selected_layers.add(layer)
        current_tokens += desc_tokens
        shortlist.append({
            "id": cid,
            "source_repo": cand.get("source_repo", ""),
            "skill_name": cand.get("skill_name", ""),
            "layer": layer,
            "found_by_query": cand.get("found_by_query", ""),
            "install_count": cand.get("install_count"),
            "description": meta.get("description", ""),
            "description_tokens": desc_tokens,
            "score": score,
            "signals": signals,
        })

    return RankResult(
        shortlist=shortlist,
        rejected=rejected,
        total_description_tokens=current_tokens,
        budget=budget,
        installed_layer_conflicts=list(installed_layers),
        warnings=unknown_layer_warnings,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_human(result: RankResult) -> str:
    lines: List[str] = []
    lines.append("=== SHORTLIST (Greedy Selection) ===")
    lines.append(f"Total Description Tokens: {result.total_description_tokens} / {result.budget}")
    lines.append("")

    if not result.shortlist:
        lines.append("  (No candidates qualified for shortlist)")
    else:
        for idx, item in enumerate(result.shortlist, 1):
            cid = item["id"]
            layer = item["layer"]
            score = item["score"]
            tokens = item["description_tokens"]
            desc = item["description"]
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"  {idx}. [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok)")
            lines.append(f"     \"{desc}\"")

    if result.rejected:
        lines.append("")
        lines.append(f"=== REJECTED ({len(result.rejected)}) ===")
        for r in result.rejected:
            cid = r["id"]
            reason = r["reason"]
            lines.append(f"  - {cid:<35} -> {reason}")

    if result.warnings:
        lines.append("")
        lines.append("=== WARNINGS ===")
        for w in result.warnings:
            lines.append(f"  ! {w}")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-fit candidate scoring and ranking")
    parser.add_argument("candidates", help="Path to JSON file containing candidate list")
    parser.add_argument("--installed", default=None, help="Path to JSON file from audit.py")
    parser.add_argument("--tags", default="", help="Comma-separated stack tags")
    parser.add_argument(
        "--budget", type=int, default=DEFAULT_BUDGET, help="Token budget (default 1500)"
    )
    parser.add_argument(
        "--min-installs",
        type=int,
        default=DEFAULT_MIN_INSTALL_COUNT,
        help="Min install count floor",
    )
    parser.add_argument("--human", action="store_true", help="Print human-readable table")

    args = parser.parse_args(argv)

    # Load candidates
    try:
        with open(args.candidates, "r", encoding="utf-8") as f:
            candidates_data = json.load(f)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to load candidates file: {exc}"}), file=sys.stderr)
        return 1

    installed_skills = []
    if args.installed:
        try:
            with open(args.installed, "r", encoding="utf-8") as f:
                inst_data = json.load(f)
                if isinstance(inst_data, dict):
                    installed_skills = inst_data.get("skills", [])
                else:
                    installed_skills = inst_data
        except Exception as exc:
            print(json.dumps({"error": f"Failed to load installed skills: {exc}"}), file=sys.stderr)
            return 1

    stack_tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    result = rank_candidates(
        candidates=candidates_data,
        installed_skills=installed_skills,
        stack_tags=stack_tags,
        budget=args.budget,
        min_install_count=args.min_installs,
    )

    if args.human:
        print(render_human(result))
    else:
        print(json.dumps(result.to_dict(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
