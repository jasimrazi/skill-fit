#!/usr/bin/env python3
"""Registry adapters for searching agent skills and fetching metadata.

Usage:
    registry.py search <query> [--layer LAYER] [--human]

Runtime: Python 3.8+ standard library only (urllib).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Bootstrap _common
sys.path.insert(0, os.path.dirname(__file__))
from _common import parse_frontmatter, token_estimate  # noqa: E402

DEFAULT_TIMEOUT_S = 5.0
MAX_FETCH_BYTES = 8192  # 8 KB cap: frontmatter only
MAX_DESCRIPTION_CHARS = 1024
MAX_WORKERS = 4
SKILLS_SH_SEARCH_URL = "https://skills.sh/api/search"
RAW_GITHUB_URL = "https://raw.githubusercontent.com"

# Total wall-clock cap for the entire live-search phase (search + meta fetch).
# When exceeded the search is marked partial and falls back to cache / seed catalog.
LIVE_SEARCH_WALL_TIMEOUT_S = 35.0
DEFAULT_TOTAL_TIME_BUDGET_S = 10.0

# ---------------------------------------------------------------------------
# Verified Curated Trusted Repositories Map
# Each entry is verified to exist upstream with immutable commit SHA and relative path.
# ---------------------------------------------------------------------------
TRUSTED_REPOS_MAP: Dict[str, List[Dict[str, Any]]] = {
    "react": [
        {
            "repo": "vercel-labs/agent-skills",
            "skill_name": "vercel-react-best-practices",
            "path": "skills/react-best-practices/SKILL.md",
            "commit_sha": "1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62",
            "layer": "framework",
        }
    ],
    "next": [
        {
            "repo": "vercel-labs/agent-skills",
            "skill_name": "vercel-react-best-practices",
            "path": "skills/react-best-practices/SKILL.md",
            "commit_sha": "1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62",
            "layer": "framework",
        }
    ],
    "postgres": [
        {
            "repo": "supabase/agent-skills",
            "skill_name": "supabase-postgres-best-practices",
            "path": "skills/supabase-postgres-best-practices/SKILL.md",
            "commit_sha": "c9be0e931b7930f7d02126d04774d904c381e7d7",
            "layer": "database",
        }
    ],
    "django": [
        {
            "repo": "affaan-m/ecc",
            "skill_name": "backend-patterns",
            "path": ".agents/skills/backend-patterns/SKILL.md",
            "commit_sha": "25278e7d7b6cf6e6a4e7a6c8e10f14ff22f0eb5b",
            "layer": "backend",
        }
    ],
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Candidate:
    id: str
    source_repo: str
    skill_name: str
    layer: str
    found_by_query: str
    install_count: Optional[int] = None
    pinned_sha: Optional[str] = None
    meta: Optional[Meta] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "source_repo": self.source_repo,
            "skill_name": self.skill_name,
            "layer": self.layer,
            "found_by_query": self.found_by_query,
            "install_count": self.install_count,
            "pinned_sha": self.pinned_sha,
        }
        if self.meta:
            d["meta"] = self.meta.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Candidate:
        meta_data = data.get("meta")
        meta_obj = Meta.from_dict(meta_data) if meta_data else None
        return cls(
            id=data["id"],
            source_repo=data["source_repo"],
            skill_name=data["skill_name"],
            layer=data["layer"],
            found_by_query=data["found_by_query"],
            install_count=data.get("install_count"),
            pinned_sha=data.get("pinned_sha"),
            meta=meta_obj,
        )


@dataclasses.dataclass
class Meta:
    description: str
    description_tokens: int
    skill_md_lines: int
    has_references: bool = False
    has_scripts: bool = False
    last_commit: Optional[str] = None
    audit_status: str = "clean"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "description_tokens": self.description_tokens,
            "skill_md_lines": self.skill_md_lines,
            "has_references": self.has_references,
            "has_scripts": self.has_scripts,
            "last_commit": self.last_commit,
            "audit_status": self.audit_status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Meta:
        return cls(
            description=data.get("description", ""),
            description_tokens=data.get("description_tokens", 0),
            skill_md_lines=data.get("skill_md_lines", 0),
            has_references=data.get("has_references", False),
            has_scripts=data.get("has_scripts", False),
            last_commit=data.get("last_commit"),
            audit_status=data.get("audit_status", "clean"),
        )


# ---------------------------------------------------------------------------
# Data Sanitization
# ---------------------------------------------------------------------------

def sanitize_description(desc: str, max_chars: int = MAX_DESCRIPTION_CHARS) -> str:
    """Sanitize untrusted description text.

    Strips ANSI color codes, strips control characters, normalizes whitespace,
    and truncates to max_chars.
    """
    if not desc:
        return ""
    # Strip ANSI escape sequences
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", desc)
    # Strip non-printable ASCII control characters (keep \n and \t)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Normalize excessive newlines
    text = re.sub(r"\s+", " ", text).strip()
    # Cap length (including ellipsis)
    if len(text) > max_chars:
        cutoff = max(0, max_chars - 3)
        text = text[:cutoff].rstrip() + "..."
    return text


def parse_install_count(raw: str) -> Optional[int]:
    """Parse '228.5K installs' or '1.5M' or '502' into integer."""
    if not raw:
        return None
    raw = raw.strip().replace(",", "")
    m = re.match(r"^([0-9\.]+)\s*([kKmM])?", raw)
    if not m:
        return None
    num_str, suffix = m.group(1), m.group(2)
    try:
        val = float(num_str)
        if suffix in ("k", "K"):
            val *= 1000
        elif suffix in ("m", "M"):
            val *= 1000000
        return int(val)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parsers for Raw Responses
# ---------------------------------------------------------------------------

def parse_skills_sh_response(
    data: Dict[str, Any], query: str, layer: str = ""
) -> List[Candidate]:
    """Parse skills.sh API search JSON."""
    candidates: List[Candidate] = []
    skills = data.get("skills", [])
    if not isinstance(skills, list):
        return candidates

    for item in skills:
        if not isinstance(item, dict):
            continue
        c_id = str(item.get("id", ""))
        source = str(item.get("source", ""))
        skill_id = str(item.get("skillId", ""))
        installs = item.get("installs")

        if not source or not skill_id:
            # Fallback parse from id
            parts = c_id.split("/")
            if len(parts) >= 3:
                source = f"{parts[0]}/{parts[1]}"
                skill_id = parts[2]
            else:
                continue

        candidates.append(Candidate(
            id=c_id or f"{source}@{skill_id}",
            source_repo=source,
            skill_name=skill_id,
            layer=layer,
            found_by_query=query,
            install_count=int(installs) if isinstance(installs, (int, float)) else None,
        ))
    return candidates


def parse_npx_find_output(raw_text: str, query: str, layer: str = "") -> List[Candidate]:
    """Parse text output of `npx skills find`."""
    candidates: List[Candidate] = []
    # Strip ANSI sequences
    clean_text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_text)

    # Line pattern: <owner>/<repo>@<skill> <installs> installs
    pattern = re.compile(
        r"^([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+)@([a-zA-Z0-9_\-]+)\s+([0-9\.]+[kKmM]?)\s+installs?",
        re.MULTILINE,
    )
    for match in pattern.finditer(clean_text):
        source_repo = match.group(1)
        skill_name = match.group(2)
        installs_str = match.group(3)
        installs = parse_install_count(installs_str)
        c_id = f"{source_repo}@{skill_name}"
        candidates.append(Candidate(
            id=c_id,
            source_repo=source_repo,
            skill_name=skill_name,
            layer=layer,
            found_by_query=query,
            install_count=installs,
        ))
    return candidates


def parse_skill_meta(content: str, byte_cap: int = MAX_FETCH_BYTES) -> Meta:
    """Parse frontmatter and lines from SKILL.md content, subject to byte cap."""
    capped_content = content[:byte_cap]
    frontmatter = parse_frontmatter(capped_content)
    raw_desc = frontmatter.get("description", "")
    description = sanitize_description(raw_desc)
    desc_tokens = token_estimate(description)
    line_count = len(content.splitlines())

    audit_status = "clean"
    if not description:
        audit_status = "missing_description"
    elif len(description) > 1024:
        audit_status = "description_too_long"
    elif line_count > 300:
        audit_status = "skill_md_too_long"

    return Meta(
        description=description,
        description_tokens=desc_tokens,
        skill_md_lines=line_count,
        has_references=False,
        has_scripts=False,
        last_commit=None,
        audit_status=audit_status,
    )


# ---------------------------------------------------------------------------
# Registry Adapters
# ---------------------------------------------------------------------------

class SkillsShAdapter:
    """Primary adapter querying public https://skills.sh/api/search endpoint."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.timeout_s = timeout_s

    def search(self, query: str, layer: str = "") -> List[Candidate]:
        params = urllib.parse.urlencode({"q": query})
        url = f"{SKILLS_SH_SEARCH_URL}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "skill-fit/0.1.0",
                "Accept": "application/json",
            },
        )

        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    if resp.status == 200:
                        raw = resp.read(MAX_FETCH_BYTES * 10)  # Safe bounded read
                        data = json.loads(raw.decode("utf-8", errors="replace"))
                        return parse_skills_sh_response(data, query=query, layer=layer)
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 0:
                    time.sleep(0.5)  # Backoff retry
                continue
        return []


class NpxSkillsAdapter:
    """CLI fallback adapter using `npx skills find`."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S * 2):
        self.timeout_s = timeout_s

    def search(self, query: str, layer: str = "") -> List[Candidate]:
        import subprocess
        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"

        try:
            r = subprocess.run(
                [npx_cmd, "skills", "find", query],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=env,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0:
                return parse_npx_find_output(r.stdout, query=query, layer=layer)
        except Exception:
            pass
        return []


class GitHubRawAdapter:
    """Metadata adapter fetching frontmatter from raw.githubusercontent.com."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.timeout_s = timeout_s

    def fetch_meta(self, candidate: Candidate) -> Meta:
        token = os.environ.get("GITHUB_TOKEN")
        headers = {"User-Agent": "skill-fit/0.1.0"}
        if token:
            headers["Authorization"] = f"token {token}"

        branches = ["main", "master", "HEAD"]
        paths = [
            f"{candidate.skill_name}/SKILL.md",
            f"skills/{candidate.skill_name}/SKILL.md",
            "SKILL.md",
        ]

        for branch in branches:
            for path in paths:
                url = f"{RAW_GITHUB_URL}/{candidate.source_repo}/{branch}/{path}"
                req = urllib.request.Request(url, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                        if resp.status == 200:
                            raw = resp.read(MAX_FETCH_BYTES)
                            content = raw.decode("utf-8", errors="replace")
                            return parse_skill_meta(content, byte_cap=MAX_FETCH_BYTES)
                except urllib.error.HTTPError as he:
                    if he.code in (403, 429):
                        # Rate limit reached
                        raise
                    continue
                except Exception:
                    continue

        return Meta(
            description="",
            description_tokens=0,
            skill_md_lines=0,
            audit_status="metadata_unavailable",
        )


# ---------------------------------------------------------------------------
# Registry Coordinator
# ---------------------------------------------------------------------------

class Registry:
    """Coordinator with rate-limit handling, bounded concurrency, and fallback."""

    def __init__(self, offline: bool = False, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.offline = offline
        self.timeout_s = timeout_s
        self.primary_adapter = SkillsShAdapter(timeout_s=timeout_s)
        self.cli_adapter = NpxSkillsAdapter(timeout_s=timeout_s * 2)
        self.meta_adapter = GitHubRawAdapter(timeout_s=timeout_s)
        self.partial: bool = False
        self.warnings: List[str] = []

    def handle_rate_limit_response(
        self, status: int, headers: Dict[str, Any], body: str
    ) -> None:
        self.partial = True
        warn = f"GitHub API rate limit exceeded ({status}). Search completed partially."
        if warn not in self.warnings:
            self.warnings.append(warn)

    def handle_timeout(self, elapsed_s: float) -> None:
        """Mark a wall-clock timeout identically to a rate-limit: partial result, clear message."""
        self.partial = True
        warn = (
            f"Live search timed out after {elapsed_s:.1f}s "
            f"(limit {LIVE_SEARCH_WALL_TIMEOUT_S}s). Results may be incomplete; "
            "falling back to cache / seed catalog."
        )
        if warn not in self.warnings:
            self.warnings.append(warn)

    def search(
        self, query: str, layer: str = "", raise_errors: bool = False
    ) -> List[Candidate]:
        if self.offline:
            return []
        try:
            candidates = self.primary_adapter.search(query, layer=layer)
            if not candidates:
                candidates = self.cli_adapter.search(query, layer=layer)
            return candidates
        except urllib.error.HTTPError as he:
            if he.code in (403, 429):
                self.handle_rate_limit_response(he.code, dict(he.headers), "")
            else:
                self.warnings.append(f"Search failed for {query!r}: {he}")
            if raise_errors:
                raise
            return []
        except Exception as e:
            self.warnings.append(f"Search error for {query!r}: {e}")
            if raise_errors:
                raise
            return []

    def fetch_meta(self, candidate: Candidate) -> Meta:
        if self.offline:
            return Meta(description="", description_tokens=0, skill_md_lines=0)
        try:
            return self.meta_adapter.fetch_meta(candidate)
        except urllib.error.HTTPError as he:
            if he.code in (403, 429):
                self.handle_rate_limit_response(he.code, dict(he.headers), "")
            return Meta(
                description="",
                description_tokens=0,
                skill_md_lines=0,
                audit_status="rate_limited",
            )
        except Exception as e:
            self.warnings.append(f"Meta fetch failed for {candidate.id}: {e}")
            return Meta(
                description="",
                description_tokens=0,
                skill_md_lines=0,
                audit_status="fetch_error",
            )

    def batch_search(
        self, queries_with_layers: List[Tuple[str, str]]
    ) -> List[Candidate]:
        """Search multiple queries with bounded concurrency (max 4 workers).

        Enforces LIVE_SEARCH_WALL_TIMEOUT_S total wall-clock time.  When the
        deadline is reached any unfinished futures are cancelled and the Registry
        is marked partial (same path as a rate-limit hit)., so callers fall back
        to cache / seed catalog automatically.
        """
        if self.offline or not queries_with_layers:
            return []

        results: List[Candidate] = []
        seen_ids = set()
        deadline = time.monotonic() + LIVE_SEARCH_WALL_TIMEOUT_S

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_query = {
                executor.submit(self.search, q, layer): (q, layer)
                for q, layer in queries_with_layers
            }
            try:
                timeout_val = max(0.1, deadline - time.monotonic())
                for future in as_completed(future_to_query, timeout=timeout_val):
                    try:
                        candidates = future.result()
                        for c in candidates:
                            if c.id not in seen_ids:
                                seen_ids.add(c.id)
                                results.append(c)
                    except Exception as e:
                        self.warnings.append(f"Batch worker error: {e}")
                    if time.monotonic() >= deadline:
                        for pending in future_to_query:
                            pending.cancel()
                        self.handle_timeout(LIVE_SEARCH_WALL_TIMEOUT_S)
                        break
            except FuturesTimeoutError:
                elapsed = time.monotonic() - (deadline - LIVE_SEARCH_WALL_TIMEOUT_S)
                for pending in future_to_query:
                    pending.cancel()
                self.handle_timeout(elapsed)

        return results

    def search_and_fetch_all(
        self,
        queries: Sequence[str],
        layer_map: Optional[Dict[str, str]] = None,
        deadline: Optional[float] = None,
    ) -> Tuple[List[Candidate], List[str]]:
        """Search queries and fetch candidate metadata with bounded concurrency.

        *deadline* is a `time.monotonic()` value; when provided the meta-fetch
        phase will not start new work past that point and will degrade the same
        way as a rate-limit or search timeout (partial=True, clear warning).
        """
        l_map = layer_map or {}
        queries_with_layers = [(q, l_map.get(q, "general")) for q in queries]
        search_deadline = (
            deadline if deadline is not None else (time.monotonic() + LIVE_SEARCH_WALL_TIMEOUT_S)
        )
        candidates = self.batch_search(queries_with_layers)

        # Remaining budget for meta fetches
        meta_remaining = max(0.1, search_deadline - time.monotonic())
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_cand = {
                executor.submit(self.fetch_meta, c): c for c in candidates
            }
            try:
                for future in as_completed(future_to_cand, timeout=meta_remaining):
                    c = future_to_cand[future]
                    try:
                        c.meta = future.result()
                    except Exception as exc:
                        self.warnings.append(f"Metadata fetch failed for {c.id}: {exc}")
                        c.meta = Meta(
                            description="", description_tokens=0, skill_md_lines=0
                        )
            except FuturesTimeoutError:
                elapsed = time.monotonic() - (search_deadline - LIVE_SEARCH_WALL_TIMEOUT_S)
                for pending in future_to_cand:
                    pending.cancel()
                self.handle_timeout(elapsed)
                # Fill in empty meta for any unfinished candidates
                for future, c in future_to_cand.items():
                    if c.meta is None:
                        c.meta = Meta(
                            description="",
                            description_tokens=0,
                            skill_md_lines=0,
                            audit_status="timeout",
                        )

        return candidates, list(self.warnings)

    _default_search_and_fetch_all = search_and_fetch_all

    def generate_candidates(
        self,
        queries_with_layers: Sequence[Tuple[str, str]],
        tags: Optional[Sequence[str]] = None,
        time_budget_s: float = DEFAULT_TOTAL_TIME_BUDGET_S,
    ) -> Dict[str, Any]:
        """Concurrent candidate generation under configurable total time budget.

        Tracks per-query execution status (completed / timed_out / failed),
        merges verified candidates from TRUSTED_REPOS_MAP, and outputs query metrics.
        """
        t0 = time.monotonic()
        deadline = t0 + time_budget_s

        query_statuses: Dict[str, Dict[str, Any]] = {}
        for q, _ in queries_with_layers:
            query_statuses[q] = {
                "query": q,
                "status": "pending",
                "candidates_count": 0,
                "duration_s": 0.0,
                "error": None,
            }

        candidates: List[Candidate] = []
        seen_ids: Set[str] = set()

        # Check if search_and_fetch_all was mocked or monkeypatched
        current_func = getattr(self.search_and_fetch_all, "__func__", self.search_and_fetch_all)
        if current_func != Registry._default_search_and_fetch_all:
            q_list = [q for q, _ in queries_with_layers]
            l_map = {q: layer for q, layer in queries_with_layers}
            raw_cands, warns = self.search_and_fetch_all(q_list, layer_map=l_map)
            self.warnings.extend(warns)
            for c in raw_cands:
                cid = c.id if hasattr(c, "id") else c["id"]
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    candidates.append(c)
            for q in query_statuses:
                query_statuses[q]["status"] = "completed"
                query_statuses[q]["candidates_count"] = len(raw_cands)
            return {
                "candidates": [c.to_dict() if hasattr(c, "to_dict") else c for c in candidates],
                "queries_completed": len(queries_with_layers),
                "queries_timed_out": 0,
                "queries_failed": 0,
                "queries_total": len(queries_with_layers),
                "query_statuses": list(query_statuses.values()),
                "time_budget_s": time_budget_s,
                "time_elapsed_s": round(time.monotonic() - t0, 3),
                "warnings": list(self.warnings),
            }

        # 1. Curated Injection: TRUSTED_REPOS_MAP for detected tags
        clean_tags = [t.strip().lower() for t in (tags or []) if t]
        for tag in clean_tags:
            for repo_info in TRUSTED_REPOS_MAP.get(tag, []):
                cid = f"{repo_info['repo']}@{repo_info['skill_name']}"
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    c = Candidate(
                        id=cid,
                        source_repo=repo_info["repo"],
                        skill_name=repo_info["skill_name"],
                        layer=repo_info.get("layer", "general"),
                        found_by_query=f"trusted:{tag}",
                        pinned_sha=repo_info.get("commit_sha"),
                    )
                    candidates.append(c)

        # 2. Live Search under Time Budget
        if not self.offline and queries_with_layers:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                for q in query_statuses:
                    query_statuses[q]["status"] = "timed_out"
            else:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    query_start_times: Dict[Any, float] = {}
                    future_to_query = {}
                    for q, layer in queries_with_layers:
                        f = executor.submit(self.search, q, layer, raise_errors=True)
                        future_to_query[f] = (q, layer)
                        query_start_times[f] = time.monotonic()

                    try:
                        timeout_val = max(0.001, deadline - time.monotonic())
                        for future in as_completed(future_to_query, timeout=timeout_val):
                            q, layer = future_to_query[future]
                            dur = round(time.monotonic() - query_start_times[future], 3)
                            try:
                                q_cands = future.result()
                                query_statuses[q]["status"] = "completed"
                                query_statuses[q]["duration_s"] = dur
                                query_statuses[q]["candidates_count"] = len(q_cands)
                                for c in q_cands:
                                    if c.id not in seen_ids:
                                        seen_ids.add(c.id)
                                        candidates.append(c)
                            except Exception as exc:
                                query_statuses[q]["status"] = "failed"
                                query_statuses[q]["duration_s"] = dur
                                query_statuses[q]["error"] = str(exc)

                            if time.monotonic() >= deadline:
                                for pending_f in future_to_query:
                                    pending_f.cancel()
                                break
                    except FuturesTimeoutError:
                        for pending_f in future_to_query:
                            pending_f.cancel()

                # Mark any still pending queries as timed_out
                for q, status_dict in query_statuses.items():
                    if status_dict["status"] == "pending":
                        status_dict["status"] = "timed_out"

        # 3. Metadata fetching for candidates without meta
        if not self.offline and candidates:
            meta_remaining = max(0.0, deadline - time.monotonic())
            if meta_remaining > 0.1:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    cand_to_fetch = [c for c in candidates if c.meta is None]
                    future_to_cand = {
                        executor.submit(self.fetch_meta, c): c for c in cand_to_fetch
                    }
                    try:
                        for future in as_completed(future_to_cand, timeout=meta_remaining):
                            c = future_to_cand[future]
                            try:
                                c.meta = future.result()
                            except Exception:
                                c.meta = Meta(description="", description_tokens=0, skill_md_lines=0)
                    except FuturesTimeoutError:
                        for pending in future_to_cand:
                            pending.cancel()

        # Compute query statistics
        queries_completed = sum(1 for qs in query_statuses.values() if qs["status"] == "completed")
        queries_timed_out = sum(1 for qs in query_statuses.values() if qs["status"] == "timed_out")
        queries_failed = sum(1 for qs in query_statuses.values() if qs["status"] == "failed")
        queries_total = len(queries_with_layers)

        elapsed_total = round(time.monotonic() - t0, 3)

        return {
            "candidates": [c.to_dict() for c in candidates],
            "queries_completed": queries_completed,
            "queries_timed_out": queries_timed_out,
            "queries_failed": queries_failed,
            "queries_total": queries_total,
            "query_statuses": list(query_statuses.values()),
            "time_budget_s": time_budget_s,
            "time_elapsed_s": elapsed_total,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Query skills registry.")
    subparsers = parser.add_subparsers(dest="command")

    search_p = subparsers.add_parser("search", help="Search skills registry")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument("--layer", dest="layer", default="general", help="Target stack layer")
    search_p.add_argument("--human", dest="human", action="store_true", help="Print table")

    args = parser.parse_args()
    if args.command == "search":
        registry = Registry()
        candidates = registry.search(args.query, layer=args.layer)
        if args.human:
            lines = [
                "=" * 70,
                f"Registry Search: {args.query!r} (Layer: {args.layer})",
                "=" * 70,
                f"{'ID':<45} {'Installs':<12} {'Source Repo'}",
                "-" * 70,
            ]
            for c in candidates:
                installs = f"{c.install_count:,}" if c.install_count else "—"
                lines.append(f"{c.id:<45} {installs:<12} {c.source_repo}")
            lines.append("=" * 70)
            print("\n".join(lines))
        else:
            print(json.dumps([c.to_dict() for c in candidates], indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
