#!/usr/bin/env python3
"""Writes/refreshes the marked project map block in CLAUDE.md / AGENTS.md.

Usage:
    write_map.py <path> [--target CLAUDE.md|AGENTS.md] [--create] [--dry-run] [--check]
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Bootstrap _common and scan
sys.path.insert(0, os.path.dirname(__file__))
from _common import safe_read, token_estimate  # noqa: E402
from scan import scan  # noqa: E402

START_MARKER_PREFIX = "<!-- context-audit:start"
END_MARKER = "<!-- context-audit:end -->"
TOKEN_CEILING = 600
TEMPLATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "map.template.md")
)


# ---------------------------------------------------------------------------
# Section formatters
# ---------------------------------------------------------------------------

def _fmt_commands(commands: Dict[str, str]) -> str:
    if not commands:
        return ""
    lines = ["**Commands**"]
    for k in sorted(commands.keys()):
        lines.append(f"- `{k}`: `{commands[k]}`")
    return "\n".join(lines)


def _fmt_entry_points(entry_points: List[str]) -> str:
    if not entry_points:
        return ""
    lines = ["**Entry Points**"]
    for ep in entry_points:
        lines.append(f"- `{ep}`")
    return "\n".join(lines)


def _fmt_dir_purposes(dir_purposes: Dict[str, str]) -> str:
    if not dir_purposes:
        return ""
    lines = ["**Directory Purposes**"]
    for d in sorted(dir_purposes.keys()):
        lines.append(f"- `{d}/`: {dir_purposes[d]}")
    return "\n".join(lines)


def _fmt_avoid(avoid: List[str]) -> str:
    if not avoid:
        return ""
    lines = ["**Avoid / Do Not Edit**"]
    for a in avoid:
        lines.append(f"- `{a}`")
    return "\n".join(lines)


def _fmt_architecture(architecture: List[Dict[str, Any]]) -> str:
    if not architecture:
        return ""
    lines = ["**Architecture Hints**"]
    for arch in architecture:
        hint = arch.get("hint", "")
        conf = arch.get("confidence", 0.0)
        ev = ", ".join(arch.get("evidence", [])[:2])
        ev_str = f" ({ev})" if ev else ""
        lines.append(f"- {hint} [confidence {conf:.1f}]{ev_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render and Trim
# ---------------------------------------------------------------------------

def render_map(data: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Render map block within 600 estimated token ceiling.

    Trimming priority (trim first to last):
    1. architecture hints (trim first)
    2. avoid list
    3. directory purposes
    4. entry points
    5. commands (trim last)
    """
    repo_hash = data.get("hash", "")
    project = data.get("project", "")

    commands = dict(data.get("commands") or {})
    entry_points = list(data.get("entry_points") or [])
    dir_purposes = dict(data.get("dir_purposes") or {})
    avoid = list(data.get("avoid") or [])
    architecture = list(data.get("architecture") or [])

    trimmed: List[str] = []

    def build_block(
        cmds: Dict[str, str],
        eps: List[str],
        dp: Dict[str, str],
        av: List[str],
        arch: List[Dict[str, Any]],
    ) -> str:
        sections = [
            f"<!-- context-audit:start hash={repo_hash} -->",
            f"### Project Map: {project}",
            "",
        ]
        s_cmd = _fmt_commands(cmds)
        if s_cmd:
            sections.extend([s_cmd, ""])
        s_ep = _fmt_entry_points(eps)
        if s_ep:
            sections.extend([s_ep, ""])
        s_dp = _fmt_dir_purposes(dp)
        if s_dp:
            sections.extend([s_dp, ""])
        s_av = _fmt_avoid(av)
        if s_av:
            sections.extend([s_av, ""])
        s_arch = _fmt_architecture(arch)
        if s_arch:
            sections.extend([s_arch, ""])
        sections.append("<!-- context-audit:end -->")
        return "\n".join(sections)

    # Iterative trimming until block <= TOKEN_CEILING
    # Step 1: trim architecture
    block = build_block(commands, entry_points, dir_purposes, avoid, architecture)
    if token_estimate(block) > TOKEN_CEILING and architecture:
        trimmed.append("architecture hints")
        architecture = []
        block = build_block(commands, entry_points, dir_purposes, avoid, architecture)

    # Step 2: trim avoid
    if token_estimate(block) > TOKEN_CEILING and avoid:
        trimmed.append("avoid list")
        avoid = []
        block = build_block(commands, entry_points, dir_purposes, avoid, architecture)

    # Step 3: trim directory purposes
    if token_estimate(block) > TOKEN_CEILING and dir_purposes:
        trimmed.append("directory purposes")
        dir_purposes = {}
        block = build_block(commands, entry_points, dir_purposes, avoid, architecture)

    # Step 4: trim entry points
    if token_estimate(block) > TOKEN_CEILING and entry_points:
        trimmed.append("entry points")
        entry_points = []
        block = build_block(commands, entry_points, dir_purposes, avoid, architecture)

    # Step 5: trim commands (partial)
    if token_estimate(block) > TOKEN_CEILING and len(commands) > 4:
        trimmed.append("commands (partial)")
        keys = sorted(commands.keys())[:4]
        commands = {k: commands[k] for k in keys}
        block = build_block(commands, entry_points, dir_purposes, avoid, architecture)

    return block, trimmed


# ---------------------------------------------------------------------------
# File updating
# ---------------------------------------------------------------------------

def update_file_content(original: str, map_block: str) -> str:
    """Replace content between markers, or append if markers not present."""
    start_idx = original.find(START_MARKER_PREFIX)
    end_idx = original.find(END_MARKER)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        before = original[:start_idx]
        after = original[end_idx + len(END_MARKER):]
        return before + map_block + after

    # Markers not present: append
    if not original:
        return map_block + "\n"
    elif original.endswith("\n\n"):
        return original + map_block + "\n"
    elif original.endswith("\n"):
        return original + "\n" + map_block + "\n"
    else:
        return original + "\n\n" + map_block + "\n"


def extract_stored_hash(content: str) -> Optional[str]:
    m = re.search(r"<!-- context-audit:start\s+hash=([^\s>]+)\s*-->", content)
    return m.group(1) if m else None


def pick_target_file(project_root: str, explicit_target: Optional[str]) -> str:
    if explicit_target:
        return explicit_target
    for name in ("CLAUDE.md", "AGENTS.md"):
        candidate = os.path.join(project_root, name)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(project_root, "CLAUDE.md")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write or check project map in CLAUDE.md / AGENTS.md."
    )
    parser.add_argument("path", help="Path to project directory to scan")
    parser.add_argument(
        "--target", dest="target", default=None, help="Target markdown file (CLAUDE.md/AGENTS.md)"
    )
    parser.add_argument(
        "--create", dest="create", action="store_true",
        help="Create target file if it does not exist",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Print diff without writing changes",
    )
    parser.add_argument(
        "--check", dest="check", action="store_true",
        help="Exit 0 if stored hash matches current scan, 1 if stale",
    )

    args = parser.parse_args()

    project_root = os.path.abspath(args.path)
    if not os.path.isdir(project_root):
        print(f"Error: {project_root!r} is not a directory", file=sys.stderr)
        sys.exit(1)

    target_path = pick_target_file(project_root, args.target)

    # Perform scan
    scan_data = scan(project_root)
    current_hash = scan_data.get("hash", "")

    # --check mode
    if args.check:
        if not os.path.isfile(target_path):
            sys.exit(1)
        content, err = safe_read(target_path)
        if err:
            sys.exit(1)
        stored_hash = extract_stored_hash(content)
        if stored_hash == current_hash and stored_hash is not None:
            sys.exit(0)
        else:
            sys.exit(1)

    # Check existence
    if not os.path.isfile(target_path):
        if not args.create:
            print(
                f"Error: target file {target_path!r} does not exist. Use --create to create it.",
                file=sys.stderr,
            )
            sys.exit(1)
        original_content = ""
    else:
        original_content, err = safe_read(target_path)
        if err:
            print(f"Error reading target {target_path!r}: {err}", file=sys.stderr)
            sys.exit(1)

    map_block, trimmed = render_map(scan_data)
    new_content = update_file_content(original_content, map_block)

    if trimmed:
        print(f"[context-audit] Trimmed sections to enforce token ceiling: {', '.join(trimmed)}")

    if args.dry_run:
        diff = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{os.path.basename(target_path)}",
            tofile=f"b/{os.path.basename(target_path)}",
        )
        sys.stdout.writelines(diff)
        return

    # Write changes
    with open(target_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)


if __name__ == "__main__":
    main()
