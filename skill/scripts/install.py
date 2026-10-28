#!/usr/bin/env python3
"""Trust gate and installation engine for shortlisted agent skills.

Hard constraints:
    - Dry-run by default: no skill is installed without explicit approval.
    - Trust gate shows: source repo, pinned SHA, audit status, scripts flag, token cost,
      and sanitized, quoted description. Labeled "unvetted" unless in current lockfile.
    - Post-install verification: recompute content hash; on mismatch, quarantine the skill
      and abort lockfile writing.
    - Write lockfile only after all approved skills verify cleanly.

Usage:
    install.py <shortlist.json> [--manifest-hash HASH] [--skills-dir DIR] [--lockfile PATH]
               [--approve ID ...] [--approve-all] [--dry-run] [--human]

Runtime: Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

# Bootstrap _common, lock, and registry
sys.path.insert(0, os.path.dirname(__file__))
import lock  # noqa: E402
from registry import sanitize_description  # noqa: E402

DEFAULT_SKILLS_DIR = ".agents/skills"
DEFAULT_LOCKFILE_PATH = "skill-fit.lock"


def resolve_remote_sha(source_repo: str, timeout_s: float = 5.0) -> str:
    """Resolve latest commit SHA for a GitHub repo (owner/repo).

    Queries GitHub commit API or git ls-remote. Returns 40-char SHA or 'HEAD' on failure.
    """
    if not source_repo or "/" not in source_repo:
        return "HEAD"
    owner, repo = source_repo.split("/", 1)

    # 1. Try GitHub commit API
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "skill-fit/0.1",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                sha = data.get("sha")
                if sha and len(sha) == 40:
                    return sha
    except Exception:
        pass

    # 2. Try git ls-remote if git is available locally
    try:
        cmd = ["git", "ls-remote", f"https://github.com/{owner}/{repo}.git", "HEAD"]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode == 0 and proc.stdout:
            parts = proc.stdout.split()
            if parts and len(parts[0]) == 40:
                return parts[0]
    except Exception:
        pass

    return "HEAD"


def format_trust_gate_item(
    item: Dict[str, Any], locked_ids: Optional[Set[str]] = None
) -> Dict[str, Any]:
    """Format single candidate item for the trust gate."""
    locked = locked_ids or set()
    cid = item.get("id", "")
    source_repo = item.get("source_repo", "")
    skill_name = item.get("skill_name") or (cid.split("@")[-1] if "@" in cid else cid)
    pinned_sha = item.get("pinned_sha", "HEAD")
    raw_desc = item.get("description", "")
    sanitized_desc = sanitize_description(raw_desc, max_chars=300)
    tokens = item.get("description_tokens", 0)

    signals = item.get("signals") or {}
    audit_signal = signals.get("audit_status") or {}
    audit_status = audit_signal.get("status") or item.get("audit_status", "clean")

    scripts_signal = signals.get("scripts") or {}
    has_scripts = scripts_signal.get("has_scripts") or item.get("has_scripts", False)

    status_label = "locked" if cid in locked else "unvetted"

    return {
        "id": cid,
        "source_repo": source_repo,
        "skill_name": skill_name,
        "layer": item.get("layer", "general"),
        "pinned_sha": pinned_sha,
        "audit_status": audit_status,
        "has_scripts": bool(has_scripts),
        "description_tokens": tokens,
        "quoted_description": f'"{sanitized_desc}"',
        "status": status_label,
    }


def default_installer_runner(
    source_repo: str,
    pinned_sha: str,
    target_dir: Path,
) -> Tuple[bool, str]:
    """Execute `npx skills add <source_repo>#<pinned_sha>` to install skill."""
    npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
    if not pinned_sha or pinned_sha == "HEAD":
        resolved = resolve_remote_sha(source_repo)
        if resolved and resolved != "HEAD":
            pinned_sha = resolved

    if pinned_sha and pinned_sha != "HEAD":
        specifier = f"{source_repo}#{pinned_sha}"
    else:
        specifier = source_repo

    target_dir.mkdir(parents=True, exist_ok=True)
    cmd = [npx_cmd, "skills", "add", specifier, "--yes"]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_dir.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30.0,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, f"Command failed (exit {proc.returncode}): {proc.stderr.strip()}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


def quarantine_dir(skill_dir: Path) -> Path:
    """Safely move a tampered skill directory to quarantine."""
    ts = int(time.time())
    quarantine_path = skill_dir.parent / f"{skill_dir.name}.quarantine.{ts}"
    try:
        shutil.move(str(skill_dir), str(quarantine_path))
    except Exception:
        # Fallback: rename
        os.rename(str(skill_dir), str(quarantine_path))
    return quarantine_path


def install_skills(
    shortlist: Sequence[Dict[str, Any]],
    manifest_hash: str,
    skills_dir: Union[str, Path] = DEFAULT_SKILLS_DIR,
    lockfile_path: Union[str, Path] = DEFAULT_LOCKFILE_PATH,
    approved_ids: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    installer_runner: Optional[Callable[[str, str, Path], Tuple[bool, str]]] = None,
) -> Dict[str, Any]:
    """Execute trust gate, approval check, post-install hash verification, and lockfile write."""
    target_skills_dir = Path(skills_dir)
    lock_path = Path(lockfile_path)

    # Read existing lockfile to identify currently locked skills
    current_lock = lock.read_lockfile(lock_path)
    locked_ids = {
        s.get("id")
        for s in (current_lock.get("skills", []) if current_lock else [])
        if s.get("approved")
    }

    # 1. Format trust gate for all items
    trust_gate_items = [format_trust_gate_item(item, locked_ids) for item in shortlist]

    approved_set = set(approved_ids or [])
    is_dry_run = dry_run or (len(approved_set) == 0)

    if is_dry_run:
        return {
            "dry_run": True,
            "manifest_hash": manifest_hash,
            "trust_gate": trust_gate_items,
            "installed": [],
            "failures": [],
            "warnings": ["Dry-run active: no skills installed, no lockfile written."],
        }

    runner = installer_runner or default_installer_runner
    installed_entries: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    # 2. Process approved skills
    for item in shortlist:
        cid = item.get("id", "")
        if cid not in approved_set:
            continue

        source_repo = item.get("source_repo", "")
        pinned_sha = item.get("pinned_sha") or "HEAD"
        if pinned_sha == "HEAD":
            resolved = resolve_remote_sha(source_repo)
            if resolved and resolved != "HEAD":
                pinned_sha = resolved
        expected_hash = item.get("expected_content_hash") or item.get("content_hash")
        tokens = item.get("description_tokens", 0)
        layer = item.get("layer", "general")

        # Run installer
        success, msg = runner(source_repo, pinned_sha, target_skills_dir)
        if not success:
            failures.append({
                "id": cid,
                "status": "install_failed",
                "reason": msg,
            })
            continue

        # Locate installed directory
        installed_path = lock.find_skill_dir(cid, [target_skills_dir])
        if not installed_path or not installed_path.exists():
            failures.append({
                "id": cid,
                "status": "missing_after_install",
                "reason": f"Skill directory not found in {target_skills_dir} after install",
            })
            continue

        # Recompute content hash
        actual_hash = lock.compute_content_hash(installed_path)

        # Content hash verification
        if expected_hash and actual_hash != expected_hash:
            q_path = quarantine_dir(installed_path)
            failures.append({
                "id": cid,
                "status": "tampered",
                "reason": f"Content hash mismatch (expected {expected_hash}, got {actual_hash})",
                "quarantine_path": str(q_path),
            })
            continue

        # Verification succeeded!
        installed_entries.append({
            "id": cid,
            "layer": layer,
            "pinned_sha": pinned_sha,
            "content_hash": actual_hash,
            "description_tokens": tokens,
            "approved": True,
        })

    # 3. Write lockfile only if no failures occurred
    lockfile_written = False
    if len(failures) == 0 and len(installed_entries) > 0:
        # Merge with existing lockfile entries if any
        existing_skills = {
            s["id"]: s for s in (current_lock.get("skills", []) if current_lock else [])
        }
        for entry in installed_entries:
            existing_skills[entry["id"]] = entry

        all_skills = sorted(list(existing_skills.values()), key=lambda s: s.get("id", ""))
        lock.propose_lockfile(
            lockfile_path=lock_path,
            new_manifest_hash=manifest_hash,
            proposed_skills=all_skills,
            approved=True,
        )
        lockfile_written = True

    return {
        "dry_run": False,
        "manifest_hash": manifest_hash,
        "trust_gate": trust_gate_items,
        "installed": installed_entries,
        "failures": failures,
        "lockfile_written": lockfile_written,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_human(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    mode = "DRY RUN (NO CHANGES)" if result.get("dry_run") else "INSTALL REPORT"
    lines.append(f"=== TRUST GATE & INSTALLATION: {mode} ===")
    lines.append(f"Manifest Hash: {result.get('manifest_hash', 'unknown')}")
    lines.append("")

    trust_gate = result.get("trust_gate", [])
    if trust_gate:
        lines.append("Shortlisted Skills (Trust Gate):")
        for idx, item in enumerate(trust_gate, 1):
            status = item["status"].upper()
            cid = item["id"]
            repo = item["source_repo"]
            sha = item["pinned_sha"][:8] if item["pinned_sha"] else "HEAD"
            audit = item["audit_status"]
            scripts = "yes" if item["has_scripts"] else "no"
            tok = item["description_tokens"]
            desc = item["quoted_description"]
            lines.append(
                f"  {idx}. [{status:<8}] {cid:<35} (repo: {repo}, sha: {sha})"
            )
            lines.append(
                f"      audit: {audit:<6} | scripts: {scripts:<3} | tokens: {tok:<3} | desc: {desc}"
            )

    installed = result.get("installed", [])
    if installed:
        lines.append("")
        lines.append(f"Installed Successfully ({len(installed)}):")
        for item in installed:
            lines.append(
                f"  [OK] {item['id']:<35} (hash: {item['content_hash'][:16]}...)"
            )

    failures = result.get("failures", [])
    if failures:
        lines.append("")
        lines.append(f"Failures & Tamper Alerts ({len(failures)}):")
        for f in failures:
            lines.append(f"  [FAIL] {f['id']:<35} -> {f['status']}: {f['reason']}")

    lines.append("")
    lock_status = "Written" if result.get("lockfile_written") else "Untouched"
    lines.append(f"Lockfile Status: {lock_status}")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-fit trust gate and installer")
    parser.add_argument("shortlist", help="Path to shortlisted skills JSON file")
    parser.add_argument("--manifest-hash", required=True, help="Current repo manifest hash")
    parser.add_argument("--skills-dir", default=DEFAULT_SKILLS_DIR, help="Skills install directory")
    parser.add_argument("--lockfile", default=DEFAULT_LOCKFILE_PATH, help="Path to lockfile")
    parser.add_argument("--approve", action="append", help="Approve skill ID for installation")
    parser.add_argument("--approve-all", action="store_true", help="Approve all shortlisted skills")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without installing")
    parser.add_argument("--human", action="store_true", help="Print human-readable table")

    args = parser.parse_args(argv)

    try:
        with open(args.shortlist, "r", encoding="utf-8") as f:
            shortlist_data = json.load(f)
            if isinstance(shortlist_data, dict):
                shortlist_data = shortlist_data.get("shortlist", [])
    except Exception as exc:
        print(json.dumps({"error": f"Failed to load shortlist file: {exc}"}), file=sys.stderr)
        return 1

    if args.approve_all:
        approved_ids = [item.get("id") for item in shortlist_data if item.get("id")]
    else:
        approved_ids = args.approve or []

    result = install_skills(
        shortlist=shortlist_data,
        manifest_hash=args.manifest_hash,
        skills_dir=args.skills_dir,
        lockfile_path=args.lockfile,
        approved_ids=approved_ids,
        dry_run=args.dry_run,
    )

    if args.human:
        print(render_human(result))
    else:
        print(json.dumps(result, indent=2))

    # Exit code 1 if any failures occurred during approved installs
    return 1 if result.get("failures") else 0


if __name__ == "__main__":
    sys.exit(main())
