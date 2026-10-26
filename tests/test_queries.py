"""Tests for skill/scripts/queries.py."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
QUERIES_PY = ROOT / "skill" / "scripts" / "queries.py"
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden"


def run_queries(input_arg: str, *extra_args: str) -> dict:
    cmd = [sys.executable, str(QUERIES_PY), str(input_arg)] + list(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"queries.py failed: {result.stderr}\nstdout: {result.stdout}"
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Invalid JSON: {exc}\nstdout: {result.stdout}") from exc


def test_queries_shape_and_caps():
    """Output has required fields and queries are capped at 10."""
    res = run_queries(str(GOLDEN / "react-tailwind.json"))
    assert "queries" in res
    assert "no_coverage" in res
    assert isinstance(res["queries"], list)
    assert isinstance(res["no_coverage"], list)
    assert len(res["queries"]) <= 10


def test_queries_react_tailwind():
    """react-tailwind maps to React and Tailwind queries, plus GitHub Actions."""
    res = run_queries(str(GOLDEN / "react-tailwind.json"))
    queries = res["queries"]
    assert "react best practices" in queries
    assert "tailwind css patterns" in queries
    assert "github actions best practices" in queries
    # vite, eslint, vitest are in no_coverage
    no_cov = res["no_coverage"]
    assert "vite" in no_cov
    assert "eslint" in no_cov


def test_queries_fastapi_docker():
    """fastapi-docker maps to FastAPI, Pytest, and Docker queries."""
    res = run_queries(str(GOLDEN / "fastapi-docker.json"))
    queries = res["queries"]
    assert "fastapi best practices" in queries
    assert "pytest best practices" in queries
    assert "docker best practices" in queries
    # pydantic, sqlalchemy in no_coverage
    no_cov = res["no_coverage"]
    assert "pydantic" in no_cov
    assert "sqlalchemy" in no_cov


def test_queries_flutter():
    """flutter maps to flutter queries."""
    res = run_queries(str(GOLDEN / "flutter.json"))
    queries = res["queries"]
    assert "flutter best practices" in queries
    assert "provider" in res["no_coverage"]


def test_queries_empty():
    """empty fixture produces empty queries and empty no_coverage."""
    res = run_queries(str(GOLDEN / "empty.json"))
    assert res["queries"] == []
    assert res["no_coverage"] == []


def test_queries_deterministic_order():
    """Running queries twice on same fixture produces identical output."""
    res1 = run_queries(str(GOLDEN / "fastapi-docker.json"))
    res2 = run_queries(str(GOLDEN / "fastapi-docker.json"))
    assert res1 == res2


def test_queries_cap_enforced():
    """When a scan produces many tags, queries are strictly capped at 10."""
    # Synthetic scan data with 12 tags
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump({
            "stack": [
                {"name": "react"}, {"name": "next"}, {"name": "vue"},
                {"name": "tailwindcss"}, {"name": "fastapi"}, {"name": "django"},
                {"name": "flask"}, {"name": "express"}, {"name": "prisma"},
                {"name": "pytest"}, {"name": "typescript"}, {"name": "postgres"}
            ],
            "infra": ["docker", "github_actions"]
        }, tf)
        tf_name = tf.name

    try:
        res = run_queries(tf_name)
        assert len(res["queries"]) == 10, f"Expected 10 queries max, got {len(res['queries'])}"
    finally:
        import os
        os.remove(tf_name)


def test_queries_matches_golden():
    """Each existing scan fixture maps to an exact expected query list."""
    golden_q_dir = GOLDEN / "queries"
    fixtures = [
        "react-tailwind",
        "fastapi-docker",
        "flutter",
        "monorepo",
        "malformed-manifest",
        "empty",
    ]
    for fixture in fixtures:
        actual = run_queries(str(GOLDEN / f"{fixture}.json"))
        expected_file = golden_q_dir / f"{fixture}.json"
        with open(expected_file, "r", encoding="utf-8") as f:
            expected = json.load(f)
        assert actual == expected, f"Queries for {fixture!r} did not match golden file"

