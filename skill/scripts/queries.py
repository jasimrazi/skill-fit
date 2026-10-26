#!/usr/bin/env python3
"""Build query list from codebase scan output.

Usage:
    queries.py <scan-json-or-repo-path> [--table tag-queries.json] [--human]

Output fields (JSON):
    queries: list[str] (capped at 10)
    query_details: list[dict(query, tag, layer)]
    tags_matched: list[str]
    no_coverage: list[str]
"""
from __future__ import annotations

import argparse
import json
import os
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


def build_queries(
    scan_data: Dict[str, Any],
    table_path: Optional[str] = None,
    max_queries: int = MAX_QUERIES,
) -> Dict[str, Any]:
    table = load_tag_queries_table(table_path)
    tags = extract_tags_from_scan(scan_data)

    queries: List[str] = []
    query_details: List[Dict[str, str]] = []
    tags_matched: List[str] = []
    no_coverage: List[str] = []
    seen_queries: Set[str] = set()

    for tag in tags:
        # Match directly or with hyphen/underscore alias
        match = (
            table.get(tag)
            or table.get(tag.replace("_", "-"))
            or table.get(tag.replace("-", "_"))
        )
        if match and isinstance(match, dict):
            tags_matched.append(tag)
            layer = match.get("layer", "general")
            for q in match.get("queries", []):
                q_clean = str(q).strip()
                if q_clean and q_clean not in seen_queries:
                    if len(queries) < max_queries:
                        seen_queries.add(q_clean)
                        queries.append(q_clean)
                        query_details.append({
                            "query": q_clean,
                            "tag": tag,
                            "layer": layer,
                        })
        else:
            no_coverage.append(tag)

    return {
        "queries": queries,
        "query_details": query_details,
        "tags_matched": tags_matched,
        "no_coverage": sorted(set(no_coverage)),
    }


def _human_output(data: Dict[str, Any]) -> str:
    lines = [
        "=" * 60,
        f"Skill Queries ({len(data.get('queries', []))} generated, max {MAX_QUERIES})",
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
