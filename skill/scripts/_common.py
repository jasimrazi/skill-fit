"""Common utilities for context-audit scripts.

Runtime: zero external dependencies, Python 3.8+.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Token estimate
# ---------------------------------------------------------------------------

def token_estimate(text: str) -> int:
    """Estimated token count: len(text) // 4.

    Always call this 'estimated' in output — it is not exact.
    """
    return len(text) // 4


# ---------------------------------------------------------------------------
# Safe file read
# ---------------------------------------------------------------------------

def safe_read(path: str) -> Tuple[str, Optional[str]]:
    """Read *path* safely.

    Returns ``(content, None)`` on success or ``("", error_string)`` on any
    OS / decode error.  Never raises.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(), None
    except OSError as exc:
        return "", str(exc)


# ---------------------------------------------------------------------------
# Frontmatter parser (hand-rolled, no PyYAML)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse YAML-style ``---`` frontmatter from a SKILL.md.

    Supports:
    - ``key: value`` (string, optionally quoted)
    - Block scalars  ``>-``  ``|``  etc. (collected as a single line)
    - Ignores comment lines starting with ``#``

    Returns ``{}`` when no valid frontmatter block is found.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    result: Dict[str, str] = {}
    i = 1

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == "---":
            break

        if stripped.startswith("#") or ":" not in stripped:
            i += 1
            continue

        key, _, rest = stripped.partition(":")
        key = key.strip()
        val = rest.strip()

        # Block scalar indicators
        if val in (">", ">-", ">+", "|", "|-", "|+"):
            chunks = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                # Block ends at the next non-indented, non-empty line
                if next_line and not next_line[0].isspace():
                    break
                chunks.append(next_line.strip())
                i += 1
            result[key] = " ".join(c for c in chunks if c)
            continue

        # Strip surrounding quotes
        if (
            len(val) >= 2
            and val[0] in ('"', "'")
            and val[-1] == val[0]
        ):
            val = val[1:-1]

        if key:
            result[key] = val

        i += 1

    return result
