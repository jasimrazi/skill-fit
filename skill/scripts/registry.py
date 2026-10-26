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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

# Bootstrap _common
sys.path.insert(0, os.path.dirname(__file__))
from _common import parse_frontmatter, token_estimate  # noqa: E402

DEFAULT_TIMEOUT_S = 5.0
MAX_FETCH_BYTES = 8192  # 8 KB cap: frontmatter only
MAX_DESCRIPTION_CHARS = 1024
MAX_WORKERS = 4
SKILLS_SH_SEARCH_URL = "https://skills.sh/api/search"
RAW_GITHUB_URL = "https://raw.githubusercontent.com"


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_repo": self.source_repo,
            "skill_name": self.skill_name,
            "layer": self.layer,
            "found_by_query": self.found_by_query,
            "install_count": self.install_count,
        }


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

    def search(self, query: str, layer: str = "") -> List[Candidate]:
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
            return []
        except Exception as e:
            self.warnings.append(f"Search error for {query!r}: {e}")
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
        """Search multiple queries with bounded concurrency (max 4 workers)."""
        if self.offline or not queries_with_layers:
            return []

        results: List[Candidate] = []
        seen_ids = set()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_query = {
                executor.submit(self.search, q, layer): (q, layer)
                for q, layer in queries_with_layers
            }
            for future in as_completed(future_to_query):
                try:
                    candidates = future.result()
                    for c in candidates:
                        if c.id not in seen_ids:
                            seen_ids.add(c.id)
                            results.append(c)
                except Exception as e:
                    self.warnings.append(f"Batch worker error: {e}")

        return results


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
