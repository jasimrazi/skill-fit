#!/usr/bin/env python3
"""Cache manager for skill search results and candidate metadata.

Resolution order:
    1. Lockfile (handled by orchestrator)
    2. Cache hit (within TTL, default 7 days) -> use cache. No network.
    3. Live search -> score -> write cache.

Usage:
    cache.py get --tags react,next [--cache-dir DIR] [--refresh] [--offline] [--human]
    cache.py set --tags react,next --input candidates.json [--ttl 604800] [--cache-dir DIR]

Runtime: Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Bootstrap _common and queries
sys.path.insert(0, os.path.dirname(__file__))
from _common import safe_read  # noqa: E402
from queries import DEFAULT_TABLE_PATH  # noqa: E402

DEFAULT_TTL_S = 7 * 86400  # 7 days in seconds
CACHE_SCHEMA_VERSION = 1


def get_default_cache_dir() -> Path:
    """Return OS-appropriate default cache directory."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "skill-fit" / "cache"
        return Path.home() / "AppData" / "Local" / "skill-fit" / "cache"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "skill-fit"
    return Path.home() / ".cache" / "skill-fit"


def get_tag_queries_version(table_path: Optional[str] = None) -> str:
    """Read the version string from tag-queries.json."""
    path = table_path or DEFAULT_TABLE_PATH
    content, err = safe_read(path)
    if err or not content:
        return "1.0.0"
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return str(data.get("version", "1.0.0"))
    except json.JSONDecodeError:
        pass
    return "1.0.0"


def compute_cache_key(tags: Sequence[str], tag_queries_version: str) -> str:
    """Compute deterministic SHA-1 key from sorted lowercase unique tags and queries version."""
    cleaned_tags = sorted(list({t.strip().lower() for t in tags if t and t.strip()}))
    payload = f"{','.join(cleaned_tags)}:{tag_queries_version}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


@dataclasses.dataclass
class CacheEntry:
    schema: int
    fetched_at: str
    ttl_s: int
    candidates: List[Dict[str, Any]]
    key: str
    file_path: Optional[Path] = None
    is_stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "fetched_at": self.fetched_at,
            "ttl_s": self.ttl_s,
            "candidates": self.candidates,
            "key": self.key,
            "is_stale": self.is_stale,
        }


def parse_iso_timestamp(ts_str: str) -> Optional[float]:
    """Parse ISO-8601 string to unix timestamp (seconds)."""
    try:
        # Standardize UTC indicator for Python 3.8-3.10 compatibility
        normalized = ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(normalized)
        return dt.timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def get_cache(
    tags: Sequence[str],
    cache_dir: Optional[Union[str, Path]] = None,
    tag_queries_version: Optional[str] = None,
    refresh: bool = False,
    offline: bool = False,
    allow_stale: bool = False,
    now_s: Optional[float] = None,
) -> Tuple[Optional[CacheEntry], List[str]]:
    """Retrieve cached candidates if present and valid.

    Returns:
        (entry, warnings)
        If refresh=True, returns (None, []) without reading cache.
        If offline=True or allow_stale=True, returns expired entry marked with is_stale=True.
    """
    warnings: List[str] = []
    if refresh:
        return None, []

    version = tag_queries_version or get_tag_queries_version()
    key = compute_cache_key(tags, version)
    target_dir = Path(cache_dir) if cache_dir else get_default_cache_dir()
    cache_file = target_dir / f"{key}.json"

    if not cache_file.exists():
        return None, []

    content, err = safe_read(str(cache_file))
    if err or not content:
        warnings.append(f"Cache file unreadable ({err or 'empty'}): {cache_file}")
        return None, warnings

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        warnings.append(f"Corrupt cache JSON at {cache_file}: {exc}")
        return None, warnings

    if not isinstance(data, dict):
        warnings.append(f"Invalid cache schema at {cache_file}: root is not an object")
        return None, warnings

    schema = data.get("schema")
    if schema != CACHE_SCHEMA_VERSION:
        warnings.append(f"Unsupported cache schema version {schema} at {cache_file}")
        return None, warnings

    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        warnings.append(f"Invalid cache format at {cache_file}: missing 'candidates' list")
        return None, warnings

    fetched_at = data.get("fetched_at", "")
    ttl_s = data.get("ttl_s", DEFAULT_TTL_S)

    fetched_epoch = parse_iso_timestamp(fetched_at)
    if now_s is not None:
        current_epoch = now_s
    else:
        current_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()

    is_expired = False
    if fetched_epoch is None:
        warnings.append(f"Malformed fetched_at timestamp '{fetched_at}' in cache file")
        is_expired = True
    else:
        age_s = current_epoch - fetched_epoch
        if age_s > ttl_s:
            is_expired = True

    if is_expired:
        if offline or allow_stale:
            warnings.append(f"Cache entry at {cache_file} is expired (stale fallback active)")
            return CacheEntry(
                schema=schema,
                fetched_at=fetched_at,
                ttl_s=ttl_s,
                candidates=candidates,
                key=key,
                file_path=cache_file,
                is_stale=True,
            ), warnings
        return None, warnings

    return CacheEntry(
        schema=schema,
        fetched_at=fetched_at,
        ttl_s=ttl_s,
        candidates=candidates,
        key=key,
        file_path=cache_file,
        is_stale=False,
    ), warnings


def set_cache(
    tags: Sequence[str],
    candidates: Sequence[Union[Dict[str, Any], Any]],
    cache_dir: Optional[Union[str, Path]] = None,
    tag_queries_version: Optional[str] = None,
    ttl_s: int = DEFAULT_TTL_S,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Write candidates atomically to cache. Returns cache key."""
    version = tag_queries_version or get_tag_queries_version()
    key = compute_cache_key(tags, version)
    target_dir = Path(cache_dir) if cache_dir else get_default_cache_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_dir / f"{key}.json"

    # Serialize candidate objects if needed
    serializable_candidates: List[Dict[str, Any]] = []
    for c in candidates:
        if hasattr(c, "to_dict") and callable(c.to_dict):
            serializable_candidates.append(c.to_dict())
        elif isinstance(c, dict):
            serializable_candidates.append(c)
        else:
            serializable_candidates.append(dataclasses.asdict(c))

    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    entry_payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "fetched_at": now_dt.isoformat(),
        "ttl_s": ttl_s,
        "candidates": serializable_candidates,
    }

    serialized = json.dumps(entry_payload, indent=2, sort_keys=True)

    # Atomic write via temp file + os.replace in target directory
    temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=f".{key}-", suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, dest_file)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise

    return key


def set_candidate_pool_cache(
    tags: Sequence[str],
    pool_data: Dict[str, Any],
    cache_dir: Optional[Union[str, Path]] = None,
    tag_queries_version: Optional[str] = None,
    ttl_s: int = DEFAULT_TTL_S,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Cache candidate pool including per-query execution stats and completion counts."""
    version = tag_queries_version or get_tag_queries_version()
    key = compute_cache_key(tags, version)
    target_dir = Path(cache_dir) if cache_dir else get_default_cache_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_dir / f"pool_{key}.json"
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    entry_payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "fetched_at": now_dt.isoformat(),
        "ttl_s": ttl_s,
        "key": key,
        "pool": pool_data,
    }
    serialized = json.dumps(entry_payload, indent=2, sort_keys=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=f".pool_{key}-", suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, dest_file)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise
    return key


def get_candidate_pool_cache(
    tags: Sequence[str],
    cache_dir: Optional[Union[str, Path]] = None,
    tag_queries_version: Optional[str] = None,
    refresh: bool = False,
    offline: bool = False,
    allow_stale: bool = False,
    now_s: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Retrieve cached candidate pool by canonical tag set within TTL."""
    if refresh:
        return None, ["Cache refresh requested"]

    version = tag_queries_version or get_tag_queries_version()
    key = compute_cache_key(tags, version)
    target_dir = Path(cache_dir) if cache_dir else get_default_cache_dir()
    pool_file = target_dir / f"pool_{key}.json"

    if not pool_file.exists():
        return None, []

    content, err = safe_read(pool_file)
    if err or not content:
        return None, [f"Failed to read pool cache {pool_file}: {err}"]

    try:
        data = json.loads(content)
        pool = data.get("pool")
        if not pool or not isinstance(pool, dict):
            return None, ["Malformed pool cache entry"]

        fetched_at = data.get("fetched_at")
        ttl_s = data.get("ttl_s", DEFAULT_TTL_S)
        ref_time = now_s if now_s is not None else time.time()
        ts = parse_iso_timestamp(fetched_at)
        is_stale = (ts is None) or (ref_time - ts > ttl_s)

        if is_stale and not allow_stale and not offline:
            return None, ["Pool cache entry is stale"]

        return pool, []
    except Exception as exc:
        return None, [f"Pool cache load error: {exc}"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_human_entry(entry: Optional[CacheEntry], key: str, warnings: List[str]) -> str:
    lines: List[str] = []
    lines.append(f"Cache Key: {key}")
    if entry:
        status = "STALE" if entry.is_stale else "VALID"
        lines.append(f"Status:    {status} (Fetched: {entry.fetched_at}, TTL: {entry.ttl_s}s)")
        lines.append(f"Count:     {len(entry.candidates)} candidates")
        lines.append("")
        for idx, c in enumerate(entry.candidates, 1):
            name = c.get("skill_name") or c.get("id", "unknown")
            repo = c.get("source_repo", "")
            layer = c.get("layer", "")
            lines.append(f"  {idx}. {name:<25} [{layer:<10}] {repo}")
    else:
        lines.append("Status:    MISS")

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-fit search cache manager")
    subparsers = parser.add_subparsers(dest="command")

    get_parser = subparsers.add_parser("get", help="Get cached candidates for tags")
    get_parser.add_argument("--tags", required=True, help="Comma-separated list of stack tags")
    get_parser.add_argument("--cache-dir", default=None, help="Override cache directory")
    get_parser.add_argument("--refresh", action="store_true", help="Bypass cache reads")
    get_parser.add_argument(
        "--offline", action="store_true", help="Offline mode; allows stale fallback"
    )
    get_parser.add_argument("--allow-stale", action="store_true", help="Allow stale entries")
    get_parser.add_argument("--human", action="store_true", help="Print human-readable table")

    set_parser = subparsers.add_parser("set", help="Set cached candidates for tags")
    set_parser.add_argument("--tags", required=True, help="Comma-separated list of stack tags")
    set_parser.add_argument(
        "--input", required=True, help="Path to JSON file containing candidates array"
    )
    set_parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help="TTL in seconds")
    set_parser.add_argument("--cache-dir", default=None, help="Override cache directory")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    if args.command == "get":
        version = get_tag_queries_version()
        key = compute_cache_key(tags, version)
        entry, warnings = get_cache(
            tags=tags,
            cache_dir=args.cache_dir,
            refresh=args.refresh,
            offline=args.offline,
            allow_stale=args.allow_stale,
        )

        if args.human:
            print(render_human_entry(entry, key, warnings))
        else:
            output = {
                "hit": entry is not None,
                "key": key,
                "is_stale": entry.is_stale if entry else False,
                "entry": entry.to_dict() if entry else None,
                "warnings": warnings,
            }
            print(json.dumps(output, indent=2))
        return 0

    if args.command == "set":
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                candidates_data = json.load(f)
        except Exception as exc:
            err_msg = {"error": f"Failed to load input file: {exc}"}
            print(json.dumps(err_msg), file=sys.stderr)
            return 1

        if not isinstance(candidates_data, list):
            err_msg = {"error": "Input JSON must be an array of candidates"}
            print(json.dumps(err_msg), file=sys.stderr)
            return 1

        key = set_cache(
            tags=tags,
            candidates=candidates_data,
            cache_dir=args.cache_dir,
            ttl_s=args.ttl,
        )
        print(json.dumps({"success": True, "key": key}))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
