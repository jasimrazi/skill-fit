#!/usr/bin/env python3
"""Build query list from codebase scan output with primary-tag gating.

Usage:
    queries.py <scan-json-or-repo-path> [--table tag-queries.json] [--human]

Output fields (JSON):
    queries: list[str] (capped at 10)
    query_details: list[dict(query, tag, layer)]
    tags_matched: list[str]
    no_coverage: list[str]
    profile: dict(primary, secondary, generic, all_detected)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set

# Bootstrap _common and scan
sys.path.insert(0, os.path.dirname(__file__))
from _common import safe_read  # noqa: E402
from scan import scan  # noqa: E402

DEFAULT_TABLE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "references", "tag-queries.json")
)
MAX_QUERIES = 10

# ---------------------------------------------------------------------------
# Tag Taxonomy & Alias Normalization
# ---------------------------------------------------------------------------

ALIAS_TABLE: Dict[str, str] = {
    "golang": "go",
    "nextjs": "next",
    "next.js": "next",
    "tailwindcss": "tailwind",
    "docker-compose": "docker",
    "compose": "docker",
    "postgresql": "postgres",
    "psycopg": "postgres",
    "psycopg2": "postgres",
    "github_actions": "github-actions",
    "pytest-django": "pytest",
    "pytest-asyncio": "pytest",
    "flutter_lints": "lint",
    "flutter_test": "test",
    "react-dom": "react",
}

PRIMARY_LANGUAGES: Set[str] = {
    "python", "go", "rust", "dart", "typescript", "javascript",
    "ruby", "java", "kotlin", "php", "csharp", "swift",
}

PRIMARY_FRAMEWORKS: Set[str] = {
    "flutter", "django", "fastapi", "flask", "react", "next", "vue", "nuxt",
    "svelte", "sveltekit", "angular", "rails", "express", "nestjs", "fastify",
    "gin", "fiber", "echo", "axum", "actix", "spring-boot", "remix", "astro",
}

GENERIC_TAGS: Set[str] = {
    "lint", "linter", "eslint", "ruff", "flake8", "golangci-lint", "flutter_lints",
    "test", "testing", "pytest", "pytest-django", "pytest-asyncio", "vitest", "jest",
    "flutter_test", "playwright", "cypress",
    "ci", "github-actions", "github_actions",
    "format", "formatter", "prettier", "black",
    "docker", "docker-compose", "containers",
}


def normalize_tag_alias(raw: str) -> str:
    """Normalize raw tag string to canonical ecosystem alias."""
    cleaned = str(raw).strip().lower()
    # Strip git url or package path prefix: e.g. github.com/gin-gonic/gin -> gin
    if "/" in cleaned:
        cleaned = cleaned.split("/")[-1]
    # Strip version constraints
    cleaned = re.split(r"[\s^~>=<@!]+", cleaned)[0]
    return ALIAS_TABLE.get(cleaned, cleaned)


def load_tag_queries_table(table_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    path = table_path or DEFAULT_TABLE_PATH
    content, err = safe_read(path)
    if err or not content:
        return {}
    try:
        data = json.loads(content)
        return data.get("tags", {}) if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_tags_from_scan(scan_data: Dict[str, Any]) -> List[str]:
    """Extract ordered unique tags from scan stack and infra."""
    tags: List[str] = []
    seen: Set[str] = set()

    def add_tag(raw: str) -> None:
        cleaned = raw.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            tags.append(cleaned)

    # 1. Stack packages
    for item in scan_data.get("stack", []):
        if isinstance(item, dict) and "name" in item:
            add_tag(str(item["name"]))
        elif isinstance(item, str):
            add_tag(item)

    # 2. Infra components
    for inf in scan_data.get("infra", []):
        add_tag(str(inf))

    return tags


def build_stack_profile(
    tags_or_scan: Any, manifests: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Categorize detected tags into primary, secondary, and generic buckets."""
    if isinstance(tags_or_scan, dict):
        raw_tags = extract_tags_from_scan(tags_or_scan)
        scan_manifests = [str(m).lower() for m in tags_or_scan.get("manifests", [])]
    elif isinstance(tags_or_scan, (list, tuple, set)):
        raw_tags = [normalize_tag_alias(t) for t in tags_or_scan]
        scan_manifests = [str(m).lower() for m in (manifests or [])]
    else:
        raw_tags = []
        scan_manifests = [str(m).lower() for m in (manifests or [])]

    # Infer primary languages from manifests if not explicitly in dependencies
    inferred_primaries: List[str] = []
    for m in scan_manifests:
        m_base = os.path.basename(m).lower()
        if m_base == "go.mod":
            inferred_primaries.append("go")
        elif m_base == "cargo.toml":
            inferred_primaries.append("rust")
        elif m_base == "pubspec.yaml":
            inferred_primaries.append("dart")
            inferred_primaries.append("flutter")
        elif m_base in ("requirements.txt", "pyproject.toml"):
            inferred_primaries.append("python")
        elif m_base == "package.json":
            inferred_primaries.append("javascript")

    normalized_tags: List[str] = []
    seen = set()
    for t in raw_tags:
        norm = normalize_tag_alias(t)
        if norm and norm not in seen:
            seen.add(norm)
            normalized_tags.append(norm)

    primary: List[str] = []
    secondary: List[str] = []
    generic: List[str] = []

    for t in normalized_tags:
        if t in PRIMARY_FRAMEWORKS or t in PRIMARY_LANGUAGES:
            if t not in primary:
                primary.append(t)
        elif t in GENERIC_TAGS:
            if t not in generic:
                generic.append(t)
        else:
            if t not in secondary:
                secondary.append(t)

    # Append inferred primaries if not already covered
    for ip in inferred_primaries:
        if ip not in primary and ip not in secondary and ip not in generic:
            if not primary or (ip == "go" and "gin" in primary) or (ip == "dart" and "flutter" in primary):
                primary.append(ip)

    return {
        "primary": primary,
        "secondary": secondary,
        "generic": generic,
        "all_detected": normalized_tags,
    }


def build_queries(
    scan_data: Dict[str, Any],
    table_path: Optional[str] = None,
    max_queries: int = MAX_QUERIES,
) -> Dict[str, Any]:
    """Build queries strictly requiring a primary tag and only from detected tags."""
    table = load_tag_queries_table(table_path)
    profile = build_stack_profile(scan_data)

    primary_tags = profile["primary"]

    # RULE: No query is built without a primary tag
    if not primary_tags:
        return {
            "queries": [],
            "query_details": [],
            "tags_matched": [],
            "no_coverage": profile["all_detected"],
            "profile": profile,
        }

    # Driver primary for contextualizing secondary and generic tags
    # Prefer framework (e.g. flutter, django, fastapi, next, gin) over broad language
    framework_primaries = [p for p in primary_tags if p in PRIMARY_FRAMEWORKS]
    driver_primary = framework_primaries[0] if framework_primaries else primary_tags[0]

    queries: List[str] = []
    query_details: List[Dict[str, str]] = []
    tags_matched: List[str] = []
    no_coverage: List[str] = []
    seen_queries: Set[str] = set()

    def add_query(q_str: str, source_tag: str, layer: str) -> None:
        q_clean = q_str.strip()
        if q_clean and q_clean not in seen_queries and len(queries) < max_queries:
            seen_queries.add(q_clean)
            queries.append(q_clean)
            query_details.append({
                "query": q_clean,
                "tag": source_tag,
                "layer": layer,
            })
            if source_tag not in tags_matched:
                tags_matched.append(source_tag)

    # 1. Primary tags queries
    # If frameworks are detected, prioritize framework queries and contextualize languages
    target_primaries = framework_primaries if framework_primaries else primary_tags

    for p_tag in target_primaries:
        entry = table.get(p_tag)
        if entry and isinstance(entry, dict):
            layer = entry.get("layer", "framework")
            for q_tmpl in entry.get("queries", []):
                rendered = q_tmpl.format(primary=driver_primary)
                add_query(rendered, p_tag, layer)
        else:
            add_query(f"{p_tag} best practices", p_tag, "framework")

    # If companion language is present with framework (e.g. typescript with next), contextualize:
    if framework_primaries:
        for p_tag in primary_tags:
            if p_tag not in framework_primaries and p_tag in ("typescript", "dart"):
                add_query(f"{driver_primary} {p_tag}", p_tag, "language")

    # 2. Secondary tags queries (strictly detected secondary tags in table, paired with primary)
    for s_tag in profile["secondary"]:
        entry = table.get(s_tag)
        if entry and isinstance(entry, dict):
            layer = entry.get("layer", "general")
            for q_tmpl in entry.get("queries", []):
                rendered = q_tmpl.format(primary=driver_primary)
                add_query(rendered, s_tag, layer)

    # 3. Generic tags queries (only paired with primary, never generic-only)
    for g_tag in profile["generic"]:
        entry = table.get(g_tag)
        if entry and isinstance(entry, dict):
            layer = entry.get("layer", "infra")
            for q_tmpl in entry.get("queries", []):
                rendered = q_tmpl.format(primary=driver_primary)
                # Enforce: must contain a primary tag (no generic-only)
                if any(p in rendered.lower() for p in primary_tags):
                    add_query(rendered, g_tag, layer)

    # Compute unrepresented detected tags
    all_matched = set(tags_matched)
    for t in profile["all_detected"]:
        if t not in all_matched:
            no_coverage.append(t)

    return {
        "queries": queries,
        "query_details": query_details,
        "tags_matched": tags_matched,
        "no_coverage": sorted(list(set(no_coverage))),
        "profile": profile,
    }


def _human_output(data: Dict[str, Any]) -> str:
    queries_count = len(data.get("queries", []))
    lines = [
        "=" * 60,
        f"Skill Queries ({queries_count} generated, max {MAX_QUERIES})",
        "=" * 60,
        f"{'Query':<35} {'Layer':<12} {'Source Tag'}",
        "-" * 60,
    ]
    for qd in data.get("query_details", []):
        lines.append(f"{qd['query']:<35} {qd['layer']:<12} {qd['tag']}")

    if data.get("no_coverage"):
        lines.append("")
        lines.append("-- Tags Without Registry Coverage " + "-" * 26)
        for tag in data["no_coverage"]:
            lines.append(f"  - {tag}")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate skill search queries from repo scan."
    )
    parser.add_argument(
        "target", help="Path to repo directory or scan JSON file (or '-' for stdin)"
    )
    parser.add_argument(
        "--table", dest="table", default=None, help="Custom tag-queries.json table"
    )
    parser.add_argument(
        "--human", dest="human", action="store_true", help="Print formatted table"
    )

    args = parser.parse_args()

    # Load scan data
    if args.target == "-":
        scan_data = json.load(sys.stdin)
    elif os.path.isdir(args.target):
        scan_data = scan(args.target)
    elif os.path.isfile(args.target):
        content, err = safe_read(args.target)
        if err:
            print(f"Error reading {args.target!r}: {err}", file=sys.stderr)
            sys.exit(1)
        scan_data = json.loads(content)
    else:
        print(f"Error: {args.target!r} is neither a file nor a directory", file=sys.stderr)
        sys.exit(1)

    result = build_queries(scan_data, table_path=args.table)

    if args.human:
        print(_human_output(result))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
