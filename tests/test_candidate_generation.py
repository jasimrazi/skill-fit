"""Tests for Phase 2: Candidate Generation under Time Budget, Trusted Repos, and Pool Caching."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

import cache  # noqa: E402
from registry import Candidate, Meta, Registry, TRUSTED_REPOS_MAP  # noqa: E402


def test_trusted_repos_map_verified_entries():
    """Verify TRUSTED_REPOS_MAP exists, has verified repos with commit SHAs and relative SKILL.md paths."""
    assert isinstance(TRUSTED_REPOS_MAP, dict)
    assert "react" in TRUSTED_REPOS_MAP
    assert "django" in TRUSTED_REPOS_MAP

    for tag, repos in TRUSTED_REPOS_MAP.items():
        assert len(repos) > 0, f"No trusted repos defined for tag {tag}"
        for entry in repos:
            assert "repo" in entry
            assert "skill_name" in entry
            assert "commit_sha" in entry
            assert "path" in entry
            assert len(entry["commit_sha"]) == 40, f"Invalid commit SHA for {entry['repo']}"
            assert entry["path"].endswith("SKILL.md")


def test_parallel_candidate_generation_completed(monkeypatch):
    """Parallel queries complete within budget and record statuses."""
    reg = Registry(timeout_s=2.0)

    # Mock primary adapter
    def mock_search(query, layer=""):
        time.sleep(0.01)
        return [
            Candidate(
                id=f"test/repo@{query.replace(' ', '-')}",
                source_repo="test/repo",
                skill_name=query.replace(" ", "-"),
                layer=layer,
                found_by_query=query,
                install_count=500,
            )
        ]

    monkeypatch.setattr(reg.primary_adapter, "search", mock_search)
    monkeypatch.setattr(
        reg.meta_adapter,
        "fetch_meta",
        lambda c: Meta(description="Test description", description_tokens=4, skill_md_lines=10),
    )

    queries_with_layers = [
        ("flutter best practices", "framework"),
        ("flutter provider", "state"),
    ]

    result = reg.generate_candidates(
        queries_with_layers=queries_with_layers,
        tags=["flutter", "provider"],
        time_budget_s=5.0,
    )

    assert result["queries_total"] == 2
    assert result["queries_completed"] == 2
    assert result["queries_timed_out"] == 0
    assert result["queries_failed"] == 0
    assert len(result["candidates"]) >= 2

    # Check per-query status
    statuses = {s["query"]: s["status"] for s in result["query_statuses"]}
    assert statuses["flutter best practices"] == "completed"
    assert statuses["flutter provider"] == "completed"


def test_time_budget_timeout_marking(monkeypatch):
    """When time budget is exceeded, pending queries are marked timed_out."""
    reg = Registry(timeout_s=5.0)

    def slow_search(query, layer=""):
        time.sleep(0.25)
        return []

    monkeypatch.setattr(reg.primary_adapter, "search", slow_search)
    monkeypatch.setattr(reg.cli_adapter, "search", lambda q, layer="": [])

    queries_with_layers = [
        ("q1", "layer1"),
        ("q2", "layer2"),
        ("q3", "layer3"),
    ]

    # Ultra tight budget of 0.05 seconds
    result = reg.generate_candidates(
        queries_with_layers=queries_with_layers,
        tags=["q1"],
        time_budget_s=0.05,
    )

    assert result["queries_total"] == 3
    # At least some or all must be marked timed_out
    assert result["queries_timed_out"] > 0
    statuses = [s["status"] for s in result["query_statuses"]]
    assert "timed_out" in statuses


def test_query_failure_status_marking(monkeypatch):
    """Query failure is isolated and marked as failed without crashing batch."""
    reg = Registry(timeout_s=2.0)

    def failing_search(query, layer=""):
        if query == "bad_query":
            raise RuntimeError("Network boom!")
        return [
            Candidate(
                id="good/repo@good-skill",
                source_repo="good/repo",
                skill_name="good-skill",
                layer=layer,
                found_by_query=query,
            )
        ]

    monkeypatch.setattr(reg.primary_adapter, "search", failing_search)
    monkeypatch.setattr(
        reg.meta_adapter,
        "fetch_meta",
        lambda c: Meta(description="Desc", description_tokens=2, skill_md_lines=5),
    )

    queries_with_layers = [
        ("good_query", "framework"),
        ("bad_query", "generic"),
    ]

    result = reg.generate_candidates(
        queries_with_layers=queries_with_layers,
        tags=["good_query"],
        time_budget_s=5.0,
    )

    assert result["queries_total"] == 2
    statuses = {s["query"]: s["status"] for s in result["query_statuses"]}
    assert statuses["good_query"] == "completed"
    assert statuses["bad_query"] == "failed"
    assert result["queries_failed"] == 1
    assert result["queries_completed"] == 1


def test_trusted_repos_candidate_injection(monkeypatch):
    """Detected tags matching trusted-repos map inject candidates with real commit SHAs."""
    reg = Registry(offline=True)

    result = reg.generate_candidates(
        queries_with_layers=[],
        tags=["react"],
        time_budget_s=5.0,
    )

    candidate_ids = [c["id"] for c in result["candidates"]]
    assert "vercel-labs/agent-skills@vercel-react-best-practices" in candidate_ids
    injected = next(
        c for c in result["candidates"]
        if c["id"] == "vercel-labs/agent-skills@vercel-react-best-practices"
    )
    assert injected.get("pinned_sha") == "1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62"


def test_candidate_pool_caching(tmp_path):
    """Candidate pool is cached by canonical tag set and TTL, preserving query stats."""
    cache_dir = tmp_path / "cache"
    tags = ["fastapi", "docker"]

    pool_data = {
        "candidates": [
            {
                "id": "tiangolo/fastapi@fastapi",
                "source_repo": "tiangolo/fastapi",
                "skill_name": "fastapi",
                "layer": "backend",
                "found_by_query": "fastapi best practices",
            }
        ],
        "queries_completed": 3,
        "queries_timed_out": 0,
        "queries_failed": 0,
        "queries_total": 3,
        "query_statuses": [
            {"query": "fastapi best practices", "status": "completed", "candidates_count": 1}
        ],
    }

    # Store in cache
    cache.set_candidate_pool_cache(
        tags=tags,
        pool_data=pool_data,
        cache_dir=cache_dir,
        ttl_s=3600,
    )

    # Retrieve from cache
    entry, warnings = cache.get_candidate_pool_cache(
        tags=["docker", "fastapi"],  # Order permutation to test canonical tag set
        cache_dir=cache_dir,
    )

    assert entry is not None
    assert entry["queries_completed"] == 3
    assert len(entry["candidates"]) == 1
    assert entry["candidates"][0]["id"] == "tiangolo/fastapi@fastapi"
