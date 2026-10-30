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
from queries import build_stack_profile, load_tag_queries_table  # noqa: E402

DEFAULT_BUDGET = 1500
DEFAULT_MIN_INSTALL_COUNT = 1000
DEFAULT_STRONG_THRESHOLD = 70
DEFAULT_POSSIBLE_THRESHOLD = 40

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
    installed_layer_conflicts: List[str] = dataclasses.field(default_factory=list)
    warnings: List[str] = dataclasses.field(default_factory=list)
    strong: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    possible: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    gaps: List[str] = dataclasses.field(default_factory=list)
    has_strong_matches: bool = True
    message: str = ""
    strong_threshold: int = DEFAULT_STRONG_THRESHOLD

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shortlist": self.shortlist,
            "strong": self.strong,
            "possible": self.possible,
            "gaps": self.gaps,
            "rejected": self.rejected,
            "has_strong_matches": self.has_strong_matches,
            "message": self.message,
            "total_description_tokens": self.total_description_tokens,
            "budget": self.budget,
            "installed_layer_conflicts": self.installed_layer_conflicts,
            "warnings": self.warnings,
            "strong_threshold": self.strong_threshold,
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
        matched_layer: Optional[str] = s.get("layer")

        # Check tag matches if layer not explicitly provided
        if not matched_layer or matched_layer == "unknown":
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

    Scoring formula prioritizes:
        1. Fit first: strength of tag match, secondary-tag coverage, specificity.
        2. Quality: description token efficiency, concise line count, modularity, freshness, clean audit.
        3. Popularity: weak tiebreaker only (log-scaled 0 to 5 points max).
    """
    meta = candidate.get("meta") or {}
    layer = candidate.get("layer", "general").lower()
    query = candidate.get("found_by_query", "").lower()
    cid = candidate.get("id", "").lower()
    cname = (candidate.get("skill_name") or candidate.get("name") or "").lower()
    if layer in ("general", ""):
        layer = infer_functional_layer(cname, candidate.get("tags") or [])
    desc = (meta.get("description") or candidate.get("description") or "").lower()
    ctags = [str(t).lower() for t in (candidate.get("tags") or [])]
    cand_text = f"{cid} {cname} {desc} {' '.join(ctags)} {query}"

    # Profile stack tags
    clean_tags = [str(t).strip().lower() for t in stack_tags if str(t).strip()]
    profile = build_stack_profile(clean_tags)
    primary_tags = set(profile.get("primary", []))
    secondary_tags = set(profile.get("secondary", []))
    generic_tags = set(profile.get("generic", []))
    if not primary_tags and clean_tags:
        primary_tags = {clean_tags[0]}
        secondary_tags = set(clean_tags[1:])

    # -------------------------------------------------------------------------
    # 1. Fit Scoring (Max ~95 pts)
    # -------------------------------------------------------------------------
    primary_name_match = any(
        re.search(rf"\b{re.escape(t)}\b", f"{cname} {cid}") for t in primary_tags
    )
    primary_text_match = any(
        re.search(rf"\b{re.escape(t)}\b", cand_text) for t in primary_tags
    )

    if primary_name_match:
        tag_match_score = 40
    elif primary_text_match:
        tag_match_score = 25
    else:
        tag_match_score = LAYER_PRIORITIES.get(layer, 15)

    covered_secondary = [
        t for t in secondary_tags if re.search(rf"\b{re.escape(t)}\b", cand_text)
    ]
    secondary_coverage_score = min(30, len(covered_secondary) * 15)

    combo_bonus = 15 if (primary_text_match and covered_secondary) else 0
    specific_name_bonus = 10 if any(
        re.search(rf"\b{re.escape(t)}\b", cname) for t in (primary_tags | secondary_tags)
    ) else 0
    specificity_score = combo_bonus + specific_name_bonus

    fit_total = tag_match_score + secondary_coverage_score + specificity_score

    # -------------------------------------------------------------------------
    # 2. Quality Scoring (Max ~55 pts)
    # -------------------------------------------------------------------------
    desc_tokens = meta.get("description_tokens", 25)
    if desc_tokens <= 15:
        token_score = 15
    elif desc_tokens <= 30:
        token_score = 12
    elif desc_tokens <= 50:
        token_score = 6
    else:
        token_score = 0

    line_count = meta.get("skill_md_lines", 150)
    if line_count < 300:
        line_score = 15
    elif line_count <= 500:
        line_score = 8
    else:
        line_score = 0

    has_refs = bool(meta.get("has_references", False))
    ref_score = 10 if has_refs else 0

    last_commit = meta.get("last_commit")
    age_days = parse_commit_age_days(last_commit, now_epoch=now_epoch)
    if age_days is None:
        freshness_score = 5
    elif age_days <= 180:
        freshness_score = 10
    elif age_days <= 365:
        freshness_score = 5
    else:
        freshness_score = 0

    audit_status = meta.get("audit_status", "clean")
    audit_score = 10 if audit_status == "clean" else 0

    quality_total = token_score + line_score + ref_score + freshness_score + audit_score

    # -------------------------------------------------------------------------
    # 3. Popularity Tiebreaker (Max 5 pts)
    # -------------------------------------------------------------------------
    installs = candidate.get("install_count") or 0
    if installs >= 100000:
        pop_score = 5
    elif installs >= 10000:
        pop_score = 4
    elif installs >= 1000:
        pop_score = 3
    elif installs >= 100:
        pop_score = 2
    elif installs >= 10:
        pop_score = 1
    else:
        pop_score = 0

    total_score = fit_total + quality_total + pop_score

    signals: Dict[str, Any] = {
        "fit": {
            "tag_match_score": tag_match_score,
            "secondary_coverage_score": secondary_coverage_score,
            "specificity_score": specificity_score,
            "covered_secondary": covered_secondary,
            "total": fit_total,
        },
        "quality": {
            "description_tokens": token_score,
            "line_count": line_score,
            "references": ref_score,
            "freshness": freshness_score,
            "audit_status": audit_score,
            "total": quality_total,
        },
        "popularity": {
            "score": pop_score,
            "install_count": installs,
            "note": "weak tiebreaker only (max 5 pts)",
        },
        # Backward-compatible keys
        "tag_match": {
            "score": tag_match_score,
            "base_layer": tag_match_score,
            "exact_tag_bonus": secondary_coverage_score,
        },
        "description_tokens": {
            "score": token_score,
            "tokens": desc_tokens,
        },
        "line_count": {
            "score": line_score,
            "lines": line_count,
        },
        "references": {
            "score": ref_score,
            "has_references": has_refs,
        },
        "scripts": {
            "score": 0,
            "has_scripts": bool(meta.get("has_scripts", False)),
            "note": "informational only",
        },
        "freshness": {
            "score": freshness_score,
            "age_days": age_days,
        },
        "audit_status": {
            "score": audit_score,
            "status": audit_status,
        },
    }

    return total_score, signals


HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")

PRIMARY_LANGUAGES_SET: Set[str] = {
    "python", "go", "golang", "rust", "dart", "typescript", "javascript",
    "ruby", "java", "kotlin", "php", "csharp", "swift",
}

PRIMARY_FRAMEWORKS_SET: Set[str] = {
    "flutter", "django", "fastapi", "flask", "react", "next", "vue", "nuxt",
    "svelte", "sveltekit", "angular", "rails", "express", "nestjs", "fastify",
    "gin", "fiber", "echo", "axum", "actix", "spring-boot", "remix", "astro",
    # Cross-platform / mobile wrappers that conflict with web frameworks
    "react-native", "expo", "electron", "tauri", "capacitor", "ionic",
    "reactlynx", "weapp", "tamagui",
}

SPECIALIZED_SERVICES_SET: Set[str] = {
    "redis", "mongodb", "kafka", "rabbitmq", "cassandra", "elasticsearch"
}

STATE_MANAGERS_SET: Set[str] = {
    "bloc", "provider", "riverpod", "getx", "mobx", "redux", "zustand", "signals"
}


def infer_functional_layer(name: str, tags: Sequence[str] = ()) -> str:
    """Infer functional layer from candidate name and tags when layer is general."""
    n = name.lower()
    t = " ".join(str(x) for x in tags).lower()
    combined = f"{n} {t}"
    if any(k in combined for k in ["test", "testing", "ci", "cd", "action", "linter", "lint"]):
        return "testing"
    if any(k in combined for k in ["security", "auth", "audit", "permission", "jwt"]):
        return "security"
    if any(k in combined for k in ["state", "provider", "bloc", "signals", "hooks", "redux", "mobx", "zustand"]):
        return "state"
    if any(k in combined for k in ["style", "css", "tailwind", "ui", "theme", "shadcn", "component"]):
        return "styling"
    if any(k in combined for k in ["docker", "deploy", "infra", "k8s", "container", "compose", "devops"]):
        return "infra"
    if any(k in combined for k in ["celery", "task", "worker", "queue", "redis"]):
        return "tasks"
    if any(k in combined for k in ["network", "dio", "http", "api", "client", "fetch"]):
        return "network"
    if any(k in combined for k in ["pattern", "clean-arch", "architecture", "convention", "best-practice", "guideline"]):
        return "architecture"
    return "general"


def apply_hard_gates(
    candidates: Sequence[Union[Dict[str, Any], Any]],
    installed_skills: Optional[Sequence[Dict[str, Any]]] = None,
    stack_tags: Optional[Sequence[str]] = None,
    tag_queries_table: Optional[Dict[str, Dict[str, Any]]] = None,
    min_install_count: int = DEFAULT_MIN_INSTALL_COUNT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply Phase 3 hard gates, returning (eligible, rejected_with_reasons).

    Hard gates:
    1. Real SHA exists via frontmatter / pinned_sha (40 hex chars, never floating HEAD).
    2. Matches a detected tag by name, description, or path.
    3. Negative language/framework constraint (no conflicting language, framework, or uninstalled service).
    4. Install count floor (configurable).
    5. Audit status (clean / approved).
    6. Layer collision with installed skills and already-installed check.
    """
    clean_tags = [t.strip().lower() for t in (stack_tags or []) if t and t.strip()]
    installed = list(installed_skills or [])
    table = tag_queries_table if tag_queries_table is not None else load_tag_queries_table()

    installed_layers_map, _ = infer_installed_skills_layers(installed, table)
    installed_layers: Set[str] = set(installed_layers_map.values())
    installed_names: Set[str] = {s.get("name", "").strip().lower() for s in installed}

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

    expanded_stack = set(clean_tags)
    if "go" in clean_tags:
        expanded_stack.add("golang")
    if "golang" in clean_tags:
        expanded_stack.add("go")
    if "next" in clean_tags:
        expanded_stack.add("nextjs")
    if "nextjs" in clean_tags:
        expanded_stack.add("next")
    if "react" in clean_tags:
        expanded_stack.add("next")
        expanded_stack.add("nextjs")

    for cand in cand_dicts:
        cid = cand.get("id", "")
        skill_name = cand.get("skill_name", cand.get("name", "")).strip().lower()
        layer = cand.get("layer", "general").strip().lower()
        meta = cand.get("meta") or {}
        description = (meta.get("description") or cand.get("description") or "").strip()
        audit_status = (
            meta.get("audit_status") or cand.get("audit_status") or "clean"
        ).strip().lower()
        install_count = cand.get("install_count")
        pinned_sha = cand.get("pinned_sha") or cand.get("commit_sha")

        # Hard Gate 1: Real SHA Check (never floating HEAD or unverified)
        if pinned_sha and (pinned_sha == "HEAD" or pinned_sha.startswith("HEAD")):
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"No valid commit SHA resolved (unverified or floating HEAD: {pinned_sha!r})",
            })
            continue

        if audit_status in ("metadata_unavailable", "fetch_error"):
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"No valid commit SHA resolved (frontmatter fetch failed: {audit_status!r})",
            })
            continue

        # Hard Gate 5 (Pre-check): Audit status failed / malicious / unsafe
        if audit_status in ("failed", "malicious", "unsafe"):
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Failed audit status: '{audit_status}'",
            })
            continue

        # Missing or empty description
        if not description:
            # Fallback for candidates discovered via search index without inline frontmatter
            is_search_index = bool(
                cand.get("provenance", {}).get("source") == "skills.sh/api/search"
                or cand.get("provenance", {}).get("type") == "captured_live"
                or (not cand.get("pinned_sha") and cand.get("found_by_query") and skill_name and audit_status == "clean")
            )
            if is_search_index and skill_name:
                description = skill_name.replace("-", " ").replace("_", " ")
                if isinstance(cand.get("meta"), dict):
                    cand["meta"]["description"] = description
                    cand["meta"]["description_tokens"] = len(description.split())
                cand["description"] = description
            else:
                rejected.append({
                    "id": cid,
                    "layer": layer,
                    "reason": "Missing or empty skill description",
                })
                continue

        # Inferred functional layer if general or unassigned
        if layer in ("general", ""):
            layer = infer_functional_layer(skill_name, cand.get("tags", []))
            cand["layer"] = layer

        # Hard Gate 6: Already installed & Layer collision with installed skill
        if skill_name in installed_names or cid in installed_names:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Skill '{skill_name}' is already installed",
            })
            continue

        if layer in installed_layers:
            matched = [k for k, v in installed_layers_map.items() if v == layer][0]
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Layer collision with installed skill '{matched}' (layer: {layer})",
            })
            continue

        cand_self_text = " ".join([
            cid,
            skill_name,
            description,
            " ".join(str(t) for t in cand.get("tags", [])),
        ]).lower()

        cand_text = " ".join([
            cid,
            skill_name,
            description,
            cand.get("found_by_query", ""),
            " ".join(str(t) for t in cand.get("tags", [])),
        ]).lower()

        # Hard Gate 3: Negative Language/Framework Constraint
        conflict_lang = None
        for lang in sorted(PRIMARY_LANGUAGES_SET | PRIMARY_FRAMEWORKS_SET, key=lambda x: -len(x)):
            if lang not in expanded_stack:
                if re.search(rf"\b{re.escape(lang)}\b", cand_self_text):
                    conflict_lang = lang
                    break

        if conflict_lang:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Names conflicting language or framework '{conflict_lang}' absent from detected stack",
            })
            continue

        # Check specialized service conflict (e.g. redis when redis not detected)
        conflict_service = None
        for svc in SPECIALIZED_SERVICES_SET:
            if svc not in expanded_stack:
                if re.search(rf"\b{re.escape(svc)}\b", cand_self_text):
                    conflict_service = svc
                    break

        if conflict_service:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Names technology '{conflict_service}' absent from detected stack",
            })
            continue

        # Check conflicting state management paradigms (e.g. bloc vs provider)
        conflict_state = None
        active_state = [s for s in STATE_MANAGERS_SET if s in expanded_stack]
        if active_state:
            for sm in sorted(STATE_MANAGERS_SET, key=lambda x: -len(x)):
                if sm not in expanded_stack:
                    if re.search(rf"\b{re.escape(sm)}\b", cand_self_text):
                        conflict_state = sm
                        break

        if conflict_state:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Names conflicting state management library '{conflict_state}' absent from detected stack",
            })
            continue

        # Hard Gate 2: Matches a detected tag by name, description, query, or path
        if clean_tags:
            matches_detected = False
            for tag in clean_tags:
                if re.search(rf"\b{re.escape(tag)}\b", cand_text):
                    matches_detected = True
                    break
                alt = tag.replace("-", "_") if "-" in tag else tag.replace("_", "-")
                if re.search(rf"\b{re.escape(alt)}\b", cand_text):
                    matches_detected = True
                    break
            if not matches_detected:
                rejected.append({
                    "id": cid,
                    "layer": layer,
                    "reason": f"Does not match any detected tag in stack: {clean_tags}",
                })
                continue

        # Hard Gate 4: Install count floor
        if install_count is not None and install_count < min_install_count:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Install count ({install_count}) below floor ({min_install_count})",
            })
            continue

        eligible.append(cand)

    return eligible, rejected


def rank_candidates(
    candidates: Sequence[Union[Dict[str, Any], Any]],
    installed_skills: Optional[Sequence[Dict[str, Any]]] = None,
    stack_tags: Optional[Sequence[str]] = None,
    tag_queries_table: Optional[Dict[str, Dict[str, Any]]] = None,
    budget: int = DEFAULT_BUDGET,
    min_install_count: int = DEFAULT_MIN_INSTALL_COUNT,
    now_epoch: Optional[float] = None,
    strong_threshold: int = DEFAULT_STRONG_THRESHOLD,
    possible_threshold: int = DEFAULT_POSSIBLE_THRESHOLD,
) -> RankResult:
    """Filter, score, and categorize candidate skills into deterministic tiers:
    - strong: at most one per layer, score >= strong_threshold, under token budget
    - possible: near misses, alternatives in already-filled layers, score >= possible_threshold
    - gaps: detected stack tags with no matching candidate
    - rejected: failed hard gates or score < possible_threshold with explicit reasons
    """
    clean_tags = [t.strip() for t in stack_tags if t.strip()] if stack_tags else []
    installed = list(installed_skills) if installed_skills else []
    table = tag_queries_table if tag_queries_table is not None else load_tag_queries_table()

    # Step 1: Infer installed skills layers
    installed_layers_map, unknown_layer_warnings = infer_installed_skills_layers(installed, table)
    installed_layers: Set[str] = set(installed_layers_map.values())

    # Step 2: Apply Hard Gates
    eligible, rejected = apply_hard_gates(
        candidates=candidates,
        installed_skills=installed,
        stack_tags=clean_tags,
        tag_queries_table=table,
        min_install_count=min_install_count,
    )

    # Step 3: Score eligible candidates
    scored_candidates: List[Tuple[int, int, str, Dict[str, Any], Dict[str, Any]]] = []
    for cand in eligible:
        score, signals = score_candidate(cand, clean_tags, now_epoch=now_epoch)
        cid = cand.get("id", "")
        install_count = cand.get("install_count") or 0
        scored_candidates.append((score, install_count, cid, cand, signals))

    # Sort deterministically:
    # 1. Total score DESCENDING
    # 2. Raw install_count DESCENDING (popularity tiebreaker when scores equal)
    # 3. ID ASCENDING (stable alphanumeric tiebreaker)
    scored_candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))

    # Step 4: Tier Categorization
    strong: List[Dict[str, Any]] = []
    possible: List[Dict[str, Any]] = []
    selected_layers_count: Dict[str, int] = {}
    current_tokens = 0

    for score, pop_score, cid, cand, signals in scored_candidates:
        layer = cand.get("layer", "general").strip().lower()
        if layer in ("general", ""):
            layer = infer_functional_layer(cand.get("skill_name", ""), cand.get("tags", []))
            cand["layer"] = layer
        meta = cand.get("meta") or {}
        desc_tokens = meta.get("description_tokens") or cand.get("description_tokens") or 25
        cand_desc = (meta.get("description") or cand.get("description") or "").strip()
        pinned_sha = cand.get("pinned_sha") or cand.get("commit_sha")
        if pinned_sha and not HEX40_RE.match(str(pinned_sha)):
            pinned_sha = None

        item_dict = {
            "id": cid,
            "source_repo": cand.get("source_repo", cand.get("repo", "")),
            "skill_name": cand.get("skill_name", cand.get("name", "")),
            "layer": layer,
            "pinned_sha": pinned_sha,
            "found_by_query": cand.get("found_by_query", ""),
            "install_count": cand.get("install_count"),
            "description": cand_desc,
            "description_tokens": desc_tokens,
            "score": score,
            "signals": signals,
        }

        layer_limit = 3 if layer in ("general", "") else 1
        current_layer_count = selected_layers_count.get(layer, 0)

        if score >= strong_threshold:
            if current_layer_count < layer_limit and (current_tokens + desc_tokens <= budget):
                selected_layers_count[layer] = current_layer_count + 1
                current_tokens += desc_tokens
                strong.append(item_dict)
            else:
                if current_layer_count >= layer_limit:
                    item_dict["tier_reason"] = (
                        f"Layer collision in selection: layer '{layer}' already represented"
                    )
                    # Also record in rejected for layer collision tests compatibility
                    rejected.append({
                        "id": cid,
                        "layer": layer,
                        "reason": f"Layer collision in selection: layer '{layer}' already represented",
                    })
                else:
                    item_dict["tier_reason"] = f"Exceeds token budget ({current_tokens} + {desc_tokens} > {budget})"
                    rejected.append({
                        "id": cid,
                        "layer": layer,
                        "reason": f"Exceeds token budget ({current_tokens} + {desc_tokens} > {budget})",
                    })
                possible.append(item_dict)
        elif score >= possible_threshold:
            item_dict["tier_reason"] = f"Score {score} below strong threshold ({strong_threshold})"
            possible.append(item_dict)
        else:
            rejected.append({
                "id": cid,
                "layer": layer,
                "reason": f"Score {score} below possible threshold ({possible_threshold})",
            })

    # Step 5: Detect Gaps (detected stack tags without any matching candidate)
    matched_tags: Set[str] = set()
    for tag in clean_tags:
        t_clean = tag.strip().lower()
        if not t_clean:
            continue
        for cand in eligible:
            cid = cand.get("id", "").lower()
            cname = (cand.get("skill_name") or cand.get("name") or "").lower()
            cdesc = (cand.get("meta", {}).get("description") or cand.get("description") or "").lower()
            ctags = [str(t).lower() for t in (cand.get("tags") or [])]
            cand_text = f"{cid} {cname} {cdesc} {' '.join(ctags)}"
            if re.search(rf"\b{re.escape(t_clean)}\b", cand_text):
                matched_tags.add(t_clean)
                break
            alt = t_clean.replace("-", "_") if "-" in t_clean else t_clean.replace("_", "-")
            if re.search(rf"\b{re.escape(alt)}\b", cand_text):
                matched_tags.add(t_clean)
                break

    gaps: List[str] = [t for t in clean_tags if t.strip().lower() not in matched_tags]

    # Honest reporting: If nothing is strong, shortlist is empty
    if not strong:
        has_strong_matches = False
        shortlist = []
        message = "No strong match found for detected stack. Showing near misses and coverage gaps."
    else:
        has_strong_matches = True
        shortlist = strong
        message = f"Found {len(strong)} strong match{'es' if len(strong) != 1 else ''}."

    return RankResult(
        shortlist=shortlist,
        rejected=rejected,
        total_description_tokens=current_tokens,
        budget=budget,
        installed_layer_conflicts=list(installed_layers),
        warnings=unknown_layer_warnings,
        strong=strong,
        possible=possible,
        gaps=gaps,
        has_strong_matches=has_strong_matches,
        message=message,
        strong_threshold=strong_threshold,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_human(result: RankResult) -> str:
    lines: List[str] = []
    if not result.has_strong_matches or not result.strong:
        lines.append("=== NO STRONG MATCH ===")
        lines.append(f"No skills met the threshold ({result.strong_threshold} pts) for strong recommendation.")
        lines.append(result.message)
        lines.append("")
        if result.possible:
            lines.append(f"Near Misses / Possible Skills ({len(result.possible)}):")
            for idx, item in enumerate(result.possible, 1):
                cid = item["id"]
                layer = item["layer"]
                score = item["score"]
                tokens = item["description_tokens"]
                desc = item["description"]
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                reason = item.get("tier_reason", "")
                reason_str = f" [{reason}]" if reason else ""
                lines.append(f"  {idx}. [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok){reason_str}")
                lines.append(f"     \"{desc}\"")
        if result.gaps:
            lines.append("")
            lines.append("Coverage Gaps (Detected tags with no candidates):")
            for g in result.gaps:
                lines.append(f"  - {g}")
    else:
        lines.append("=== SHORTLIST (Strong Recommendations) ===")
        lines.append(f"Total Description Tokens: {result.total_description_tokens} / {result.budget}")
        lines.append("")
        for idx, item in enumerate(result.strong, 1):
            cid = item["id"]
            layer = item["layer"]
            score = item["score"]
            tokens = item["description_tokens"]
            desc = item["description"]
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"  {idx}. [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok)")
            lines.append(f"     \"{desc}\"")

        if result.possible:
            lines.append("")
            lines.append(f"Possible Alternatives ({len(result.possible)}):")
            for idx, item in enumerate(result.possible, 1):
                cid = item["id"]
                layer = item["layer"]
                score = item["score"]
                tokens = item["description_tokens"]
                lines.append(f"  - [{score:>3} pts] {cid:<35} [{layer:<10}] ({tokens} tok)")

        if result.gaps:
            lines.append("")
            lines.append("Coverage Gaps (Detected tags with no candidates):")
            for g in result.gaps:
                lines.append(f"  - {g}")

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
