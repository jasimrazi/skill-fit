#!/usr/bin/env python3
"""Deterministic lockfile manager (skill-fit.lock) and verification engine.

Resolution order:
    1. Lockfile exists AND manifest_hash == scanner's hash -> use lockfile (no network).
    Stack changed (hash mismatch) -> show proposed diff, change nothing without approval.

Lockfile schema:
    {
      "schema": 1,
      "manifest_hash": "...",
      "resolved_at": "ISO-8601",
      "skills": [
        {
          "id": "owner/repo@skill-name",
          "layer": "framework",
          "pinned_sha": "...",
          "content_hash": "sha256:...",
          "description_tokens": 15,
          "approved": true
        }
      ]
    }

Usage:
    lock.py verify [--lockfile PATH] [--skills-dir DIR] [--human]
    lock.py create --proposed FILE --manifest-hash HASH [--lockfile PATH] [--approve] [--human]
    lock.py diff --proposed FILE --manifest-hash HASH [--lockfile PATH] [--human]

Runtime: Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

DEFAULT_LOCKFILE_NAME = "skill-fit.lock"
LOCKFILE_SCHEMA_VERSION = 1


def compute_content_hash(skill_dir: Union[str, Path]) -> str:
    """Compute SHA-256 over sorted (relative_path, file_bytes) pairs of skill directory."""
    base = Path(skill_dir).resolve()
    if not base.exists() or not base.is_dir():
        return "sha256:0000000000000000000000000000000000000000000000000000000000000000"

    hasher = hashlib.sha256()
    file_entries: List[Tuple[str, Path]] = []

    for root, _, filenames in os.walk(str(base)):
        rel_root = os.path.relpath(root, str(base)).replace("\\", "/")
        parts = rel_root.split("/")
        if ".git" in parts or "__pycache__" in parts:
            continue

        for fname in filenames:
            if fname in {".DS_Store", "Thumbs.db"}:
                continue
            full_path = Path(root) / fname
            rel_path = full_path.relative_to(base).as_posix()
            file_entries.append((rel_path, full_path))

    # Sort relative paths deterministically
    file_entries.sort(key=lambda x: x[0])

    for rel_path, full_path in file_entries:
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(b"\x00")
        try:
            with open(full_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            pass
        hasher.update(b"\x00")

    return f"sha256:{hasher.hexdigest()}"


def serialize_lockfile(data: Dict[str, Any]) -> bytes:
    """Serialize lockfile dictionary with sorted keys and stable indent."""
    # Ensure skills list is sorted by skill id for stable diffs
    if "skills" in data and isinstance(data["skills"], list):
        data["skills"] = sorted(data["skills"], key=lambda s: s.get("id", ""))
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def read_lockfile(lockfile_path: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """Read and validate a skill-fit.lock file."""
    path = Path(lockfile_path)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("schema") == LOCKFILE_SCHEMA_VERSION:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def write_lockfile_atomic(lockfile_path: Union[str, Path], data: Dict[str, Any]) -> None:
    """Atomically write lockfile via temporary file + rename."""
    dest = Path(lockfile_path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    serialized = serialize_lockfile(data)

    temp_fd, temp_path = tempfile.mkstemp(
        dir=dest.parent, prefix=".skill-fit-lock-", suffix=".tmp"
    )
    try:
        with os.fdopen(temp_fd, "wb") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, dest)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def compute_lockfile_diff(
    current: Optional[Dict[str, Any]],
    proposed_manifest_hash: str,
    proposed_skills: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute difference between current lockfile and proposed state."""
    current_hash = current.get("manifest_hash") if current else None
    hash_changed = current_hash != proposed_manifest_hash

    current_skills = {s.get("id"): s for s in (current.get("skills", []) if current else [])}
    prop_skills = {s.get("id"): s for s in proposed_skills}

    added = [s for sid, s in prop_skills.items() if sid not in current_skills]
    removed = [s for sid, s in current_skills.items() if sid not in prop_skills]
    modified = []

    for sid, s_new in prop_skills.items():
        if sid in current_skills:
            s_old = current_skills[sid]
            if (
                s_old.get("pinned_sha") != s_new.get("pinned_sha")
                or s_old.get("content_hash") != s_new.get("content_hash")
            ):
                modified.append({
                    "id": sid,
                    "old_sha": s_old.get("pinned_sha"),
                    "new_sha": s_new.get("pinned_sha"),
                    "old_content_hash": s_old.get("content_hash"),
                    "new_content_hash": s_new.get("content_hash"),
                })

    return {
        "manifest_hash_changed": hash_changed,
        "old_manifest_hash": current_hash,
        "new_manifest_hash": proposed_manifest_hash,
        "added_skills": added,
        "removed_skills": removed,
        "modified_skills": modified,
    }


def propose_lockfile(
    lockfile_path: Union[str, Path],
    new_manifest_hash: str,
    proposed_skills: Sequence[Dict[str, Any]],
    approved: bool = False,
    now_iso: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Check stack changes, return diff, and write only if approved."""
    current = read_lockfile(lockfile_path)
    diff = compute_lockfile_diff(current, new_manifest_hash, proposed_skills)

    has_diff = (
        current is None
        or diff["manifest_hash_changed"]
        or diff["added_skills"]
        or diff["removed_skills"]
        or diff["modified_skills"]
    )

    if not has_diff:
        return "unchanged", diff

    if not approved:
        # Per resolution order: show diff, change nothing without approval
        status = "stack_changed" if (current and diff["manifest_hash_changed"]) else "unapproved"
        return status, diff

    # Approved: generate lockfile payload and write atomically
    resolved_time = now_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_data: Dict[str, Any] = {
        "schema": LOCKFILE_SCHEMA_VERSION,
        "manifest_hash": new_manifest_hash,
        "resolved_at": resolved_time,
        "skills": list(proposed_skills),
    }

    write_lockfile_atomic(lockfile_path, new_data)
    return "written", diff


def find_skill_dir(skill_id: str, skills_dirs: Sequence[Union[str, Path]]) -> Optional[Path]:
    """Find directory corresponding to skill id in provided skill search dirs."""
    skill_name = skill_id.split("@")[-1] if "@" in skill_id else skill_id
    for s_dir in skills_dirs:
        dir_path = Path(s_dir)
        if not dir_path.exists():
            continue
        # Direct name match
        direct = dir_path / skill_name
        if direct.exists() and direct.is_dir():
            return direct
        # Subdirectories (e.g. owner/repo nesting)
        for child in dir_path.iterdir():
            if child.is_dir() and child.name == skill_name:
                return child
    return None


def verify_lockfile(
    lockfile_path: Union[str, Path],
    skills_dirs: Optional[Sequence[Union[str, Path]]] = None,
) -> Dict[str, Any]:
    """Recompute content hashes of installed skills and detect drift against lockfile."""
    current = read_lockfile(lockfile_path)
    if not current:
        return {
            "verified": False,
            "error": f"Lockfile not found or unreadable: {lockfile_path}",
            "drifts": [],
            "skills_checked": [],
        }

    search_dirs = (
        list(skills_dirs)
        if skills_dirs
        else [Path(".agents/skills"), Path(".claude/skills")]
    )
    locked_skills = current.get("skills", [])
    drifts: List[Dict[str, Any]] = []
    checked: List[Dict[str, Any]] = []

    for item in locked_skills:
        sid = item.get("id", "")
        expected_hash = item.get("content_hash", "")
        installed_path = find_skill_dir(sid, search_dirs)

        if not installed_path or not installed_path.exists():
            drifts.append({
                "id": sid,
                "status": "missing",
                "expected_hash": expected_hash,
                "actual_hash": None,
                "path": None,
            })
            continue

        actual_hash = compute_content_hash(installed_path)
        if actual_hash != expected_hash:
            drifts.append({
                "id": sid,
                "status": "modified",
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "path": str(installed_path),
            })
        else:
            checked.append({
                "id": sid,
                "status": "clean",
                "content_hash": actual_hash,
                "path": str(installed_path),
            })

    is_verified = (len(drifts) == 0) and (len(locked_skills) > 0)
    return {
        "verified": is_verified,
        "manifest_hash": current.get("manifest_hash"),
        "drifts": drifts,
        "skills_checked": checked,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_human_verify(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    status = "VERIFIED: PASS" if result.get("verified") else "VERIFIED: FAIL"
    lines.append(f"=== LOCKFILE VERIFICATION: {status} ===")
    lines.append(f"Manifest Hash: {result.get('manifest_hash', 'unknown')}")
    lines.append("")

    checked = result.get("skills_checked", [])
    if checked:
        lines.append(f"Clean Skills ({len(checked)}):")
        for s in checked:
            lines.append(f"  [OK] {s['id']:<35} ({s['content_hash'][:16]}...)")

    drifts = result.get("drifts", [])
    if drifts:
        lines.append("")
        lines.append(f"Drifts / Failures ({len(drifts)}):")
        for d in drifts:
            status_tag = d["status"].upper()
            lines.append(f"  [{status_tag}] {d['id']:<35}")
            if d.get("expected_hash"):
                lines.append(f"        Expected: {d['expected_hash']}")
            if d.get("actual_hash"):
                lines.append(f"        Actual:   {d['actual_hash']}")

    return "\n".join(lines)


def render_human_diff(status: str, diff: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"=== LOCKFILE DIFF (Status: {status.upper()}) ===")
    if diff.get("manifest_hash_changed"):
        lines.append("Manifest Hash Changed:")
        lines.append(f"  - Old: {diff.get('old_manifest_hash')}")
        lines.append(f"  + New: {diff.get('new_manifest_hash')}")
        lines.append("")

    added = diff.get("added_skills", [])
    if added:
        lines.append(f"Added Skills ({len(added)}):")
        for s in added:
            lines.append(f"  + {s.get('id')} [{s.get('layer')}]")

    removed = diff.get("removed_skills", [])
    if removed:
        lines.append(f"Removed Skills ({len(removed)}):")
        for s in removed:
            lines.append(f"  - {s.get('id')} [{s.get('layer')}]")

    modified = diff.get("modified_skills", [])
    if modified:
        lines.append(f"Modified Skills ({len(modified)}):")
        for s in modified:
            lines.append(f"  * {s.get('id')}")

    if not added and not removed and not modified and not diff.get("manifest_hash_changed"):
        lines.append("  (No changes proposed)")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-fit lockfile manager")
    subparsers = parser.add_subparsers(dest="command")

    # verify
    verify_p = subparsers.add_parser("verify", help="Verify installed skills vs lockfile")
    verify_p.add_argument("--lockfile", default=DEFAULT_LOCKFILE_NAME, help="Path to lockfile")
    verify_p.add_argument(
        "--skills-dir", action="append", help="Directory where skills are installed"
    )
    verify_p.add_argument("--human", action="store_true", help="Print human-readable output")

    # create
    create_p = subparsers.add_parser("create", help="Create or update lockfile")
    create_p.add_argument("--proposed", required=True, help="Path to proposed skills JSON")
    create_p.add_argument("--manifest-hash", required=True, help="Current repo manifest hash")
    create_p.add_argument("--lockfile", default=DEFAULT_LOCKFILE_NAME, help="Path to lockfile")
    create_p.add_argument(
        "--approve", action="store_true", help="Explicit approval to write lockfile"
    )
    create_p.add_argument("--human", action="store_true", help="Print human-readable output")

    # diff
    diff_p = subparsers.add_parser("diff", help="Diff proposed skills against lockfile")
    diff_p.add_argument("--proposed", required=True, help="Path to proposed skills JSON")
    diff_p.add_argument("--manifest-hash", required=True, help="Current repo manifest hash")
    diff_p.add_argument("--lockfile", default=DEFAULT_LOCKFILE_NAME, help="Path to lockfile")
    diff_p.add_argument("--human", action="store_true", help="Print human-readable output")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    if args.command == "verify":
        result = verify_lockfile(args.lockfile, skills_dirs=args.skills_dir)
        if args.human:
            print(render_human_verify(result))
        else:
            print(json.dumps(result, indent=2))
        return 0 if result.get("verified") else 1

    if args.command in {"create", "diff"}:
        try:
            with open(args.proposed, "r", encoding="utf-8") as f:
                proposed_data = json.load(f)
        except Exception as exc:
            err_msg = {"error": f"Failed to load proposed skills file: {exc}"}
            print(json.dumps(err_msg), file=sys.stderr)
            return 1

        is_approve = getattr(args, "approve", False) and args.command == "create"
        status, diff = propose_lockfile(
            lockfile_path=args.lockfile,
            new_manifest_hash=args.manifest_hash,
            proposed_skills=proposed_data,
            approved=is_approve,
        )

        if args.human:
            print(render_human_diff(status, diff))
        else:
            print(json.dumps({"status": status, "diff": diff}, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
