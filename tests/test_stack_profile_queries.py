"""Tests for Phase 1: Stack Profile, Alias Table, and Primary-Gated Query Generation."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
QUERIES_PY = ROOT / "skill" / "scripts" / "queries.py"
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden"

# Ensure skill scripts can be imported directly
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import queries  # noqa: E402


def run_queries_cli(input_arg: str, *extra_args: str) -> dict:
    cmd = [sys.executable, str(QUERIES_PY), str(input_arg)] + list(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"queries.py failed: {result.stderr}\nstdout: {result.stdout}"
    return json.loads(result.stdout)


def test_alias_table_normalization():
    """Verify alias normalization across ecosystem variations."""
    assert queries.normalize_tag_alias("golang") == "go"
    assert queries.normalize_tag_alias("nextjs") == "next"
    assert queries.normalize_tag_alias("next.js") == "next"
    assert queries.normalize_tag_alias("tailwindcss") == "tailwind"
    assert queries.normalize_tag_alias("docker-compose") == "docker"
    assert queries.normalize_tag_alias("postgresql") == "postgres"
    assert queries.normalize_tag_alias("pytest-django") == "pytest"
    assert queries.normalize_tag_alias("github.com/gin-gonic/gin") == "gin"
    assert queries.normalize_tag_alias("github.com/google/uuid") == "uuid"


def test_tag_splitting_categories():
    """Verify detected tags are classified into primary, secondary, and generic."""
    raw_tags = [
        "fastapi", "docker", "docker-compose", "pytest", "pydantic", "sqlalchemy", "uvicorn"
    ]
    profile = queries.build_stack_profile(raw_tags)
    assert "fastapi" in profile["primary"]
    assert "docker" in profile["generic"]
    assert "pytest" in profile["generic"]
    assert "pydantic" in profile["secondary"]
    assert "sqlalchemy" in profile["secondary"]
    assert "uvicorn" in profile["secondary"]


def test_rule_no_query_without_primary_tag():
    """Rule: No query is built without a primary tag. Generic or secondary alone yield 0 queries."""
    # Only generic tags
    generic_only_scan = {
        "stack": [{"name": "pytest"}, {"name": "flake8"}],
        "infra": ["docker", "github_actions"],
    }
    res = queries.build_queries(generic_only_scan)
    assert res["queries"] == [], "Must produce zero queries when no primary tag is detected"

    # Only secondary tags
    secondary_only_scan = {
        "stack": [{"name": "celery"}, {"name": "redis"}],
        "infra": [],
    }
    res2 = queries.build_queries(secondary_only_scan)
    assert res2["queries"] == [], "Must produce zero queries when no primary tag is detected"


def test_flutter_fixture_queries():
    """Flutter fixture produces exact expected queries containing primary tag, no Go linting."""
    res = run_queries_cli(str(FIXTURES / "flutter"))
    q_list = res["queries"]
    assert len(q_list) > 0

    # Must contain flutter in every query
    for q in q_list:
        assert "flutter" in q.lower(), f"Flutter fixture produced non-flutter query: {q}"

    # Specifically check expected queries
    assert "flutter best practices" in q_list
    assert "flutter state management" in q_list
    assert "flutter provider" in q_list

    # Regression test (a): Ensure no Go linting or generic linter skill query
    assert not any("go" in q.lower() or "golang" in q.lower() for q in q_list)
    assert not any("lint" in q.lower() and "flutter" not in q.lower() for q in q_list)


def test_django_fixture_queries():
    """Django fixture produces exact expected queries, and specifically NO redis query."""
    res = run_queries_cli(str(FIXTURES / "django"))
    q_list = res["queries"]
    assert len(q_list) > 0

    # Must contain django in every query
    for q in q_list:
        assert "django" in q.lower(), f"Django fixture produced non-django query: {q}"

    # Expected queries
    assert "django best practices" in q_list
    assert "django celery" in q_list

    # Regression test (b): No redis-best-practices query when redis is not detected
    assert not any("redis" in q.lower() for q in q_list)


def test_nextjs_fixture_queries():
    """Next.js fixture produces exact expected queries and no generic eslint query."""
    res = run_queries_cli(str(FIXTURES / "nextjs"))
    q_list = res["queries"]
    assert len(q_list) > 0

    # Every query must contain next or react
    for q in q_list:
        assert ("next" in q.lower() or "react" in q.lower()), f"Unexpected query: {q}"

    assert "nextjs best practices" in q_list or "next best practices" in q_list
    assert "nextjs app router" in q_list or "next app router" in q_list
    assert "react best practices" in q_list

    # No generic eslint query
    assert "eslint" not in q_list
    assert not any(q == "lint" or q == "eslint best practices" for q in q_list)


def test_fastapi_docker_fixture_queries():
    """FastAPI+Docker fixture produces queries scoped to FastAPI, no generic-only docker/pytest queries."""
    res = run_queries_cli(str(FIXTURES / "fastapi-docker"))
    q_list = res["queries"]
    assert len(q_list) > 0

    # Every query must contain fastapi
    for q in q_list:
        assert "fastapi" in q.lower(), f"FastAPI fixture produced non-fastapi query: {q}"

    assert "fastapi best practices" in q_list
    assert "fastapi patterns" in q_list
    assert "fastapi docker" in q_list

    # No generic-only queries allowed!
    assert "docker best practices" not in q_list
    assert "docker-compose patterns" not in q_list
    assert "pytest best practices" not in q_list
    assert "python testing patterns" not in q_list


def test_go_fixture_queries():
    """Go fixture produces exact expected queries scoped to Go/Gin."""
    res = run_queries_cli(str(FIXTURES / "go-gin"))
    q_list = res["queries"]
    assert len(q_list) > 0

    for q in q_list:
        assert ("go" in q.lower() or "gin" in q.lower()), f"Go fixture produced non-go query: {q}"

    assert any("go" in q.lower() and "best practices" in q.lower() for q in q_list)
    assert any("gin" in q.lower() for q in q_list)

    # Ensure no flutter queries
    assert not any("flutter" in q.lower() for q in q_list)


def test_no_generic_only_query_exists_invariant():
    """Invariant: across any input scan, no query is generic-only."""
    for fixture_dir in [
        FIXTURES / "flutter",
        FIXTURES / "django",
        FIXTURES / "nextjs",
        FIXTURES / "fastapi-docker",
        FIXTURES / "go-gin",
    ]:
        res = run_queries_cli(str(fixture_dir))
        for q in res["queries"]:
            words = q.lower().split()
            # Generic-only checks: queries like "pytest best practices" or "docker best practices"
            is_generic_only = all(
                w in {
                    "best", "practices", "patterns", "testing", "test", "lint", "linter",
                    "ci", "cd", "docker", "container", "optimization", "format", "formatter"
                }
                for w in words
            )
            assert not is_generic_only, f"Generic-only query generated: {q!r} for {fixture_dir.name}"
