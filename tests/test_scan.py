"""Tests for skill/scripts/scan.py.

Phase 0: Tests are written first and EXPECTED TO FAIL because scan.py is a placeholder
that outputs `{}`. Each test asserts a key that a real scan must produce.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent  # context-audit/
SCAN_PY = ROOT / "skill" / "scripts" / "scan.py"
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden"


def run_scan(fixture_name: str) -> dict:
    """Run scan.py against a fixture directory and return parsed JSON."""
    fixture_path = FIXTURES / fixture_name
    result = subprocess.run(
        [sys.executable, str(SCAN_PY), str(fixture_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scan.py exited {result.returncode} for {fixture_name!r}\n"
        f"stderr: {result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"scan.py output is not valid JSON for {fixture_name!r}: {exc}\n"
            f"stdout: {result.stdout!r}"
        ) from exc


def load_golden(fixture_name: str) -> dict:
    path = GOLDEN / f"{fixture_name}.json"
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers for common assertions (all will fail against the placeholder `{}`)
# ---------------------------------------------------------------------------

def assert_scan_shape(result: dict, fixture_name: str) -> None:
    """Assert that the result has the required top-level fields."""
    required = [
        "project", "manifests", "stack", "commands",
        "infra", "architecture", "entry_points",
        "dir_purposes", "avoid", "hash", "warnings",
    ]
    missing = [k for k in required if k not in result]
    assert not missing, (
        f"scan({fixture_name!r}) is missing required fields: {missing}\n"
        f"Got keys: {list(result.keys())}"
    )


# ---------------------------------------------------------------------------
# Per-fixture tests
# ---------------------------------------------------------------------------

def test_scan_react_tailwind():
    result = run_scan("react-tailwind")
    assert_scan_shape(result, "react-tailwind")
    # Must detect JS/React stack
    stack_names = [s["name"].lower() for s in result.get("stack", [])]
    assert any("react" in n for n in stack_names), (
        f"Expected 'react' in stack, got: {stack_names}"
    )
    # Must find npm scripts as commands
    assert result.get("commands"), "Expected commands from package.json scripts"
    assert "build" in result["commands"], "Expected 'build' command from npm scripts"


def test_scan_fastapi_docker():
    result = run_scan("fastapi-docker")
    assert_scan_shape(result, "fastapi-docker")
    # Must detect Python/FastAPI
    stack_names = [s["name"].lower() for s in result.get("stack", [])]
    assert any("fastapi" in n for n in stack_names), (
        f"Expected 'fastapi' in stack, got: {stack_names}"
    )
    # Must detect Docker infra
    assert any("docker" in i.lower() for i in result.get("infra", [])), (
        f"Expected docker in infra, got: {result.get('infra')}"
    )
    # Must find Makefile targets
    assert "test" in result.get("commands", {}), "Expected 'test' from Makefile"


def test_scan_flutter():
    result = run_scan("flutter")
    assert_scan_shape(result, "flutter")
    stack_names = [s["name"].lower() for s in result.get("stack", [])]
    assert any("flutter" in n for n in stack_names), (
        f"Expected 'flutter' in stack, got: {stack_names}"
    )


def test_scan_monorepo():
    result = run_scan("monorepo")
    assert_scan_shape(result, "monorepo")
    # Must detect workspaces / monorepo architecture hint
    arch_hints = [a.get("hint", "").lower() for a in result.get("architecture", [])]
    assert any("monorepo" in h or "workspace" in h for h in arch_hints), (
        f"Expected monorepo/workspace architecture hint, got: {arch_hints}"
    )


def test_scan_malformed_manifest():
    """Malformed package.json must produce a warning, NOT a crash."""
    result = run_scan("malformed-manifest")
    assert_scan_shape(result, "malformed-manifest")
    warnings = result.get("warnings", [])
    assert warnings, (
        "Expected at least one warning for the malformed package.json, got none"
    )
    # The warning should mention the broken file
    assert any("package.json" in w for w in warnings), (
        f"Expected warning mentioning package.json, got: {warnings}"
    )
    # Valid requirements.txt must still be parsed despite the broken manifest
    stack_names = [s["name"].lower() for s in result.get("stack", [])]
    assert any("requests" in n for n in stack_names), (
        f"Expected 'requests' from valid requirements.txt, got: {stack_names}"
    )


def test_scan_empty():
    """Empty directory must produce a valid (mostly-empty) scan, no crash."""
    result = run_scan("empty")
    assert_scan_shape(result, "empty")
    assert result.get("stack") == [], f"Expected empty stack, got: {result.get('stack')}"
    assert result.get("warnings") == [] or isinstance(result.get("warnings"), list)


def test_scan_output_is_under_token_limit():
    """Total scan output must be under ~1,500 estimated tokens (6,000 chars)."""
    for fixture in ["react-tailwind", "fastapi-docker", "flutter", "monorepo"]:
        fixture_path = FIXTURES / fixture
        result = subprocess.run(
            [sys.executable, str(SCAN_PY), str(fixture_path)],
            capture_output=True,
            text=True,
        )
        output = result.stdout
        estimated_tokens = len(output) / 4
        assert estimated_tokens <= 1500, (
            f"scan({fixture!r}) output is {estimated_tokens:.0f} estimated tokens "
            f"(limit 1500); len={len(output)}"
        )


def test_scan_hash_is_deterministic():
    """Running scan twice on the same fixture must produce the same hash."""
    for fixture in ["react-tailwind", "fastapi-docker"]:
        r1 = run_scan(fixture)
        r2 = run_scan(fixture)
        assert r1.get("hash") == r2.get("hash"), (
            f"Hash not deterministic for {fixture!r}: {r1.get('hash')!r} != {r2.get('hash')!r}"
        )


def test_scan_matches_golden():
    """All fixtures must match their golden JSON output."""
    fixtures = [
        "react-tailwind",
        "fastapi-docker",
        "flutter",
        "monorepo",
        "malformed-manifest",
        "empty",
    ]
    for fixture in fixtures:
        actual = run_scan(fixture)
        expected = load_golden(fixture)
        assert actual == expected, f"scan({fixture!r}) did not match golden output"

