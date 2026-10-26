"""Tests for skill/scripts/write_map.py.

Phase 0: Tests are written first and EXPECTED TO FAIL because write_map.py is a
placeholder that outputs `{}`. Tests assert real behaviour write_map.py must provide.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
WRITE_MAP_PY = ROOT / "skill" / "scripts" / "write_map.py"
SCAN_PY = ROOT / "skill" / "scripts" / "scan.py"
FIXTURES = ROOT / "tests" / "fixtures"

START_MARKER = "<!-- context-audit:start"
END_MARKER = "<!-- context-audit:end -->"


def run_write_map(path: str, *extra_args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(WRITE_MAP_PY), path] + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Marker isolation: content outside markers must never be touched
# ---------------------------------------------------------------------------

def test_marker_isolation():
    """Content outside context-audit markers must be byte-identical after a write."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "AGENTS.md"
        before_content = (
            "# My Project\n\nSome existing content above.\n\n"
            f"{START_MARKER} hash=0000 -->\n"
            "OLD MAP CONTENT\n"
            f"{END_MARKER}\n\n"
            "Content below the markers.\n"
        )
        target.write_text(before_content, encoding="utf-8")

        fixture = str(FIXTURES / "react-tailwind")
        result = run_write_map(fixture, "--target", str(target))
        assert result.returncode == 0, f"write_map.py failed: {result.stderr}"

        after = target.read_text(encoding="utf-8")
        # Lines outside markers must be preserved exactly
        assert after.startswith("# My Project\n\nSome existing content above.\n\n"), (
            "Content before the start marker was modified!"
        )
        assert after.endswith("Content below the markers.\n"), (
            "Content after the end marker was modified!"
        )


def test_idempotence():
    """Running write_map.py twice must produce byte-identical output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "AGENTS.md"
        target.write_text(
            f"{START_MARKER} hash=0000 -->\nOLD\n{END_MARKER}\n",
            encoding="utf-8",
        )
        fixture = str(FIXTURES / "react-tailwind")

        run_write_map(fixture, "--target", str(target))
        first = target.read_bytes()

        run_write_map(fixture, "--target", str(target))
        second = target.read_bytes()

        assert first == second, (
            "write_map.py is not idempotent: second run produced different output"
        )


def test_dry_run_does_not_write():
    """--dry-run must not modify the target file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "AGENTS.md"
        original = f"{START_MARKER} hash=0000 -->\nORIGINAL\n{END_MARKER}\n"
        target.write_text(original, encoding="utf-8")

        fixture = str(FIXTURES / "react-tailwind")
        result = run_write_map(fixture, "--target", str(target), "--dry-run")
        assert result.returncode == 0, f"--dry-run failed: {result.stderr}"

        after = target.read_text(encoding="utf-8")
        assert after == original, "--dry-run modified the file!"


def test_check_exits_0_when_fresh():
    """--check must exit 0 when the stored hash matches the current scan hash."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "AGENTS.md"
        # First, write a fresh map
        target.write_text(
            f"{START_MARKER} hash=0000 -->\nPLACEHOLDER\n{END_MARKER}\n",
            encoding="utf-8",
        )
        fixture = str(FIXTURES / "react-tailwind")
        run_write_map(fixture, "--target", str(target))  # write real map

        # Now check — hash should match
        result = run_write_map(fixture, "--target", str(target), "--check")
        assert result.returncode == 0, (
            f"--check exited {result.returncode} on a fresh map; expected 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_check_exits_1_when_stale():
    """--check must exit 1 when the stored hash does not match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "AGENTS.md"
        # Deliberately write a stale hash
        target.write_text(
            f"{START_MARKER} hash=deadbeef -->\nSTALE CONTENT\n{END_MARKER}\n",
            encoding="utf-8",
        )
        fixture = str(FIXTURES / "react-tailwind")
        result = run_write_map(fixture, "--target", str(target), "--check")
        assert result.returncode == 1, (
            f"--check exited {result.returncode} on a stale map; expected 1"
        )


def test_no_target_requires_create_flag():
    """write_map.py on a non-existent target must fail without --create."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "NONEXISTENT.md"
        fixture = str(FIXTURES / "react-tailwind")
        result = run_write_map(fixture, "--target", str(target))
        assert result.returncode != 0, (
            "write_map.py should fail when target does not exist and --create is not given"
        )


def test_token_ceiling_enforced():
    """Map output between markers must be <= 600 estimated tokens (2,400 chars)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "AGENTS.md"
        target.write_text(
            f"{START_MARKER} hash=0000 -->\nPLACEHOLDER\n{END_MARKER}\n",
            encoding="utf-8",
        )
        fixture = str(FIXTURES / "react-tailwind")
        run_write_map(fixture, "--target", str(target))

        content = target.read_text(encoding="utf-8")
        # Extract content between markers
        start_idx = content.find(START_MARKER)
        end_idx = content.find(END_MARKER)
        assert start_idx != -1 and end_idx != -1, "Markers not found after write"
        map_content = content[start_idx:end_idx + len(END_MARKER)]

        estimated_tokens = len(map_content) / 4
        assert estimated_tokens <= 600, (
            f"Map content is {estimated_tokens:.0f} estimated tokens (limit 600); "
            f"len={len(map_content)}"
        )
