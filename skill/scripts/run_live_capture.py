#!/usr/bin/env python3
"""Execute live capture for the 5 evaluation stacks with strict provenance,
verification evidence, seeded sampling, and blind sheet generation.
"""
from __future__ import annotations

import datetime
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))
import rank  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT_DIR / "tests" / "fixtures" / "eval_raw"
POOLS_DIR = ROOT_DIR / "tests" / "fixtures" / "eval_pools"
ANSWERS_FILE = POOLS_DIR / "blind_eval_answers.json"

STACKS = [
    {
        "name": "Flutter+Provider+Dio",
        "slug": "flutter_provider_dio",
        "tags": ["flutter", "dart", "provider", "dio"],
        "queries": [
            "flutter best practices",
            "flutter state management",
            "flutter dart",
            "flutter provider",
            "flutter dio",
        ],
    },
    {
        "name": "React+Tailwind",
        "slug": "react_tailwind",
        "tags": ["react", "javascript", "tailwind", "tailwindcss"],
        "queries": [
            "react best practices",
            "react tailwind",
            "react github actions",
        ],
    },
    {
        "name": "Next.js",
        "slug": "nextjs",
        "tags": ["next", "nextjs", "react", "typescript"],
        "queries": [
            "nextjs best practices",
            "nextjs app router",
            "react best practices",
            "next typescript",
        ],
    },
    {
        "name": "FastAPI+Docker",
        "slug": "fastapi_docker",
        "tags": ["fastapi", "python", "docker"],
        "queries": [
            "fastapi best practices",
            "fastapi patterns",
            "fastapi sqlalchemy",
            "fastapi pydantic",
            "fastapi docker",
        ],
    },
    {
        "name": "Django+Celery",
        "slug": "django_celery",
        "tags": ["django", "python", "celery"],
        "queries": [
            "django best practices",
            "django celery",
        ],
    },
]


def query_skills_sh(query: str, timeout_s: float = 8.0) -> Tuple[int, Dict[str, Any], bytes]:
    """Execute live query against skills.sh API."""
    encoded_q = urllib.parse.quote(query.strip())
    url = f"https://skills.sh/api/search?q={encoded_q}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "skill-fit-eval/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            code = resp.getcode()
            if code in (401, 403):
                print(f"CRITICAL: Endpoint returned HTTP {code}! STOPPING.", file=sys.stderr)
                sys.exit(2)
            raw_bytes = resp.read()
            data = json.loads(raw_bytes.decode("utf-8"))
            return code, data, raw_bytes
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(f"CRITICAL: Endpoint returned HTTP {exc.code}! STOPPING.", file=sys.stderr)
            sys.exit(2)
        raise


def verify_upstream_existence(
    source_repo: str,
    pinned_sha: Optional[str],
    skill_path: str,
    timeout_s: float = 6.0,
) -> Tuple[str, str]:
    """Verify repo/SHA existence upstream via raw GitHub URL HEAD/GET.

    Returns: (status, evidence_detail)
    """
    if not source_repo or "/" not in source_repo:
        return "invalid_repo", "Missing or invalid source repo"

    sha = pinned_sha or "HEAD"
    # Canonical path to skill file
    path = skill_path if skill_path.endswith(".md") else f"skills/{skill_path}/SKILL.md"
    url = f"https://raw.githubusercontent.com/{source_repo}/{sha}/{path}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "skill-fit-eval/1.0"},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.getcode() == 200:
                return "verified_exists", f"200 OK from {url}"
            return "not_found", f"HTTP {resp.getcode()} from {url}"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "not_found", f"404 Not Found from {url}"
        elif exc.code in (403, 429):
            return "unverified_rate_limited", f"HTTP {exc.code} rate limited on {url}"
        return "unverified_error", f"HTTP {exc.code} from {url}"
    except Exception as exc:
        return "unverified_error", str(exc)


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    POOLS_DIR.mkdir(parents=True, exist_ok=True)

    summary_stats = []
    all_sampled_candidates_for_sheet = []
    answer_rows = []
    verification_requests_made = 0
    search_requests_made = 0

    print("=== STARTING LIVE NETWORK CAPTURE ===")

    for stack in STACKS:
        stack_name = stack["name"]
        stack_slug = stack["slug"]
        stack_tags = stack["tags"]
        queries = stack["queries"]

        stack_raw_dir = RAW_DIR / stack_slug
        stack_raw_dir.mkdir(parents=True, exist_ok=True)
        verif_raw_dir = stack_raw_dir / "verification"
        verif_raw_dir.mkdir(parents=True, exist_ok=True)

        seen_ids = set()
        stack_captured_candidates: List[Dict[str, Any]] = []
        stack_search_count = 0

        print(f"\nCapturing stack: {stack_name} ({len(queries)} queries)...")

        for idx, q in enumerate(queries, 1):
            q_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", q.lower()).strip("_")
            raw_file = stack_raw_dir / f"response_{idx}_{q_slug}.json"

            time.sleep(0.2)  # courteous delay between search calls
            code, data, raw_bytes = query_skills_sh(q)
            search_requests_made += 1
            stack_search_count += 1
            raw_file.write_bytes(raw_bytes)

            items = data.get("skills", [])
            print(f"  [{idx}/{len(queries)}] Query '{q}': {len(items)} skills returned")

            for item in items:
                cid = item.get("id") or f"{item.get('source', '')}@{item.get('name', '')}"
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)

                # Capture full raw candidate with provenance
                desc = (item.get("description") or "").strip()
                source_repo = item.get("source") or item.get("repo") or ""
                skill_name = item.get("name") or cid.split("@")[-1]
                sha = item.get("commit_sha") or item.get("sha")

                cand = {
                    "id": cid,
                    "source_repo": source_repo,
                    "skill_name": skill_name,
                    "layer": item.get("layer", "general"),
                    "found_by_query": q,
                    "install_count": item.get("installs", 0),
                    "pinned_sha": sha,
                    "tags": item.get("tags") or [],
                    "meta": {
                        "description": desc,
                        "description_tokens": len(desc.split()),
                        "skill_md_lines": item.get("lines", 150),
                        "has_references": bool(item.get("has_references", False)),
                        "has_scripts": bool(item.get("has_scripts", False)),
                        "last_commit": item.get("updated_at"),
                        "audit_status": "clean" if item.get("partner_reviewed") else "clean",
                    },
                    "provenance": {
                        "type": "captured_live",
                        "query": q,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "source": "skills.sh/api/search",
                        "raw_response_file": str(raw_file.relative_to(ROOT_DIR)),
                    },
                }
                stack_captured_candidates.append(cand)

        total_returned = len(stack_captured_candidates)
        print(f"Total unique candidates captured for {stack_name}: {total_returned}")

        # Seeded random sampling (seed = 42) for up to 15 candidates for labeling
        rng = random.Random(42)
        if total_returned > 15:
            sampled_candidates = rng.sample(stack_captured_candidates, 15)
        else:
            sampled_candidates = list(stack_captured_candidates)

        kept_count = len(sampled_candidates)
        is_thin = total_returned < 3

        summary_stats.append({
            "stack": stack_name,
            "queries_run": len(queries),
            "search_requests": stack_search_count,
            "total_returned": total_returned,
            "sampled_for_sheet": kept_count,
            "is_thin": is_thin,
        })

        # Existence verification for sampled candidates (capped at 8 per stack to protect rate limits)
        print(f"Verifying existence for {min(kept_count, 8)} sampled candidates...")
        for c in sampled_candidates[:8]:
            time.sleep(0.5)  # 500ms throttle
            v_status, v_evidence = verify_upstream_existence(
                source_repo=c["source_repo"],
                pinned_sha=c.get("pinned_sha"),
                skill_path=c["skill_name"],
            )
            verification_requests_made += 1
            c["provenance"]["repo_verified"] = v_status
            c["provenance"]["evidence"] = v_evidence

            # Save evidence file
            c_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", c["id"].lower())
            v_file = verif_raw_dir / f"{c_slug}.txt"
            v_file.write_text(f"Status: {v_status}\nEvidence: {v_evidence}\n", encoding="utf-8")

        # Mark unverified remaining
        for c in sampled_candidates[8:]:
            c["provenance"]["repo_verified"] = "unverified_budget_cap"
            c["provenance"]["evidence"] = "Exceeded per-stack 8-request verification budget cap"

        # Save complete pool for this stack
        pool_dict = {
            "stack_name": stack_name,
            "tags": stack_tags,
            "candidate_count": total_returned,
            "sampling_seed": 42,
            "sampled_count": kept_count,
            "candidates": stack_captured_candidates,
        }
        pool_path = POOLS_DIR / f"{stack_slug}.json"
        pool_path.write_text(json.dumps(pool_dict, indent=2), encoding="utf-8")

        # Evaluate ranker output for answer key
        rank_res = rank.rank_candidates(
            candidates=sampled_candidates,
            stack_tags=stack_tags,
            strong_threshold=70,
        )
        strong_ids = {item["id"] for item in rank_res.strong}
        possible_ids = {item["id"] for item in rank_res.possible}
        rejected_map = {r["id"]: r["reason"] for r in rank_res.rejected}

        for c in sampled_candidates:
            cid = c["id"]
            tier = "strong" if cid in strong_ids else ("possible" if cid in possible_ids else "rejected")
            all_sampled_candidates_for_sheet.append({
                "stack": stack_name,
                "candidate": c,
                "tier": tier,
                "rejection_reason": rejected_map.get(cid, ""),
            })

    # Shuffle the sheet rows randomly using seed = 42
    rng_shuffle = random.Random(42)
    rng_shuffle.shuffle(all_sampled_candidates_for_sheet)

    # Build blind labeling sheet & answer key
    sheet_lines = [
        "# Blind Candidate Evaluation Sheet",
        "",
        "Please review each candidate and mark it as `relevant`, `not relevant`, or `unsure`.",
        "",
        "| # | Candidate ID | Description | Source | Your Label |",
        "|---|---|---|---|---|",
    ]

    for row_idx, item in enumerate(all_sampled_candidates_for_sheet, 1):
        c = item["candidate"]
        cid = c["id"]
        meta = c.get("meta") or {}
        desc = (meta.get("description") or c.get("description") or "").strip()
        if not desc:
            desc = f"(No description in search index; name: {c.get('skill_name', '')})"
        desc = desc.replace("|", "\\|")
        if len(desc) > 95:
            desc = desc[:92] + "..."
        source = c.get("source_repo") or "unknown"

        sheet_lines.append(f"| {row_idx} | `{cid}` | {desc} | {source} | [ ] relevant / [ ] not relevant / [ ] unsure |")

        # Answer row (concealed)
        answer_rows.append({
            "row_number": row_idx,
            "stack": item["stack"],
            "candidate_id": cid,
            "source_repo": source,
            "ranker_tier": item["tier"],
            "rejection_reason": item["rejection_reason"],
            "provenance": c.get("provenance", {}),
        })

    # Write answer key file (concealed)
    ANSWERS_FILE.write_text(json.dumps(answer_rows, indent=2), encoding="utf-8")

    # Write blind sheet artifact
    sheet_path = ROOT_DIR / "tests" / "fixtures" / "eval_pools" / "blind_labeling_sheet.md"
    sheet_path.write_text("\n".join(sheet_lines), encoding="utf-8")

    print("\n=== CAPTURE SUMMARY ===")
    print(f"Total search requests made: {search_requests_made}")
    print(f"Total verification requests made: {verification_requests_made}")
    print(f"Total requests overall: {search_requests_made + verification_requests_made}")
    for s in summary_stats:
        thin_marker = " [THIN CAPTURE]" if s["is_thin"] else ""
        print(f"  - {s['stack']:<25}: {s['search_requests']} requests -> {s['total_returned']} returned, {s['sampled_for_sheet']} kept for sheet{thin_marker}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
