import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import cache  # noqa: E402


@pytest.fixture
def temp_cache_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_compute_cache_key():
    tags_a = ["react", "next", "tailwindcss"]
    tags_b = ["tailwindcss", "react", "next"]
    version = "1.0.0"

    key_a = cache.compute_cache_key(tags_a, version)
    key_b = cache.compute_cache_key(tags_b, version)

    assert key_a == key_b
    assert len(key_a) == 40  # SHA-1 hex digest

    # Different version produces different key
    key_v2 = cache.compute_cache_key(tags_a, "2.0.0")
    assert key_a != key_v2

    # Different tags produce different key
    key_diff = cache.compute_cache_key(["vue", "django"], version)
    assert key_a != key_diff


def test_cache_miss_on_empty(temp_cache_dir):
    entry, warnings = cache.get_cache(
        tags=["react"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )
    assert entry is None
    assert warnings == []


def test_cache_set_and_get_hit(temp_cache_dir):
    candidates = [
        {
            "id": "vercel/next.js@next-best-practices",
            "source_repo": "vercel/next.js",
            "skill_name": "next-best-practices",
            "layer": "framework",
            "found_by_query": "nextjs best practices",
            "install_count": 15000,
            "meta": {
                "description": "Best practices for Next.js app router.",
                "description_tokens": 10,
                "skill_md_lines": 120,
                "has_references": True,
                "has_scripts": False,
                "last_commit": "2026-03-01T00:00:00Z",
                "audit_status": "clean",
            },
        }
    ]

    key = cache.set_cache(
        tags=["next", "react"],
        candidates=candidates,
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
        ttl_s=3600,
    )
    assert len(key) == 40

    cache_file = temp_cache_dir / f"{key}.json"
    assert cache_file.exists()

    entry, warnings = cache.get_cache(
        tags=["react", "next"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    assert warnings == []
    assert entry is not None
    assert entry.schema == 1
    assert entry.is_stale is False
    assert len(entry.candidates) == 1
    assert entry.candidates[0]["id"] == "vercel/next.js@next-best-practices"
    assert entry.candidates[0]["meta"]["skill_md_lines"] == 120


def test_cache_refresh_flag(temp_cache_dir):
    candidates = [
        {
            "id": "test/repo@skill",
            "source_repo": "test/repo",
            "skill_name": "skill",
            "layer": "framework",
            "found_by_query": "react",
        }
    ]
    cache.set_cache(
        tags=["react"],
        candidates=candidates,
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    # Normal get succeeds
    entry, _ = cache.get_cache(
        tags=["react"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )
    assert entry is not None

    # Refresh flag bypasses reads
    entry_refreshed, _ = cache.get_cache(
        tags=["react"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
        refresh=True,
    )
    assert entry_refreshed is None


def test_cache_expiry_and_offline(temp_cache_dir):
    candidates = [
        {
            "id": "test/repo@skill",
            "source_repo": "test/repo",
            "skill_name": "skill",
            "layer": "backend",
            "found_by_query": "fastapi",
        }
    ]
    cache.set_cache(
        tags=["fastapi"],
        candidates=candidates,
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
        ttl_s=10,
    )

    # Check with simulated time past TTL
    future_time = time.time() + 100

    # Normal get: expired -> None
    entry, warnings = cache.get_cache(
        tags=["fastapi"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
        now_s=future_time,
    )
    assert entry is None

    # Offline get: expired -> returns stale entry with is_stale=True
    entry_offline, warnings_offline = cache.get_cache(
        tags=["fastapi"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
        offline=True,
        now_s=future_time,
    )
    assert entry_offline is not None
    assert entry_offline.is_stale is True
    assert any("expired" in w.lower() or "stale" in w.lower() for w in warnings_offline)


def test_cache_corruption_graceful_degradation(temp_cache_dir):
    key = cache.compute_cache_key(["corrupt_tag"], "1.0.0")
    cache_file = temp_cache_dir / f"{key}.json"

    # Write truncated / invalid JSON
    cache_file.write_text("{ incomplete json ...", encoding="utf-8")

    entry, warnings = cache.get_cache(
        tags=["corrupt_tag"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    assert entry is None
    assert len(warnings) > 0
    assert any("corrupt" in w.lower() or "json" in w.lower() for w in warnings)


def test_cache_missing_schema_or_fields(temp_cache_dir):
    key = cache.compute_cache_key(["bad_schema"], "1.0.0")
    cache_file = temp_cache_dir / f"{key}.json"

    # Write JSON missing candidates
    cache_file.write_text(json.dumps({"wrong": "data"}), encoding="utf-8")

    entry, warnings = cache.get_cache(
        tags=["bad_schema"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    assert entry is None
    assert len(warnings) > 0


def test_cache_atomic_write(temp_cache_dir):
    key = cache.compute_cache_key(["atomic"], "1.0.0")
    cache_file = temp_cache_dir / f"{key}.json"

    candidates = [
        {
            "id": "a/b@c",
            "source_repo": "a/b",
            "skill_name": "c",
            "layer": "ci",
            "found_by_query": "ci",
        }
    ]
    cache.set_cache(
        tags=["atomic"],
        candidates=candidates,
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    # Valid readable JSON
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["candidates"][0]["id"] == "a/b@c"

    # No leftover temp files in cache dir
    files = list(temp_cache_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == f"{key}.json"


def test_second_repo_identical_tags_zero_network(temp_cache_dir, monkeypatch):
    # Repo 1 has tags ['django', 'docker']
    # Repo 2 has tags ['docker', 'django']
    # If cache is set once, second repo reads from cache without any network or registry call
    mock_network = MagicMock(side_effect=RuntimeError("Network called!"))
    monkeypatch.setattr("urllib.request.urlopen", mock_network)

    candidates = [
        {
            "id": "test/django-best-practices@skill",
            "source_repo": "test/django-best-practices",
            "skill_name": "skill",
            "layer": "backend",
            "found_by_query": "django best practices",
        }
    ]

    # Populate cache for repo 1
    cache.set_cache(
        tags=["django", "docker"],
        candidates=candidates,
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    # Repo 2 queries cache with reversed tags
    entry, warnings = cache.get_cache(
        tags=["docker", "django"],
        cache_dir=temp_cache_dir,
        tag_queries_version="1.0.0",
    )

    assert entry is not None
    assert len(entry.candidates) == 1
    assert entry.candidates[0]["id"] == "test/django-best-practices@skill"
    mock_network.assert_not_called()


def test_cache_cli(temp_cache_dir, capsys):
    input_file = temp_cache_dir / "input.json"
    candidate_data = [
        {
            "id": "cli/skill@test",
            "source_repo": "cli/skill",
            "skill_name": "test",
            "layer": "orm",
            "found_by_query": "prisma",
        }
    ]
    input_file.write_text(json.dumps(candidate_data), encoding="utf-8")

    # CLI set
    ret_set = cache.main([
        "set",
        "--tags",
        "prisma,postgres",
        "--input",
        str(input_file),
        "--cache-dir",
        str(temp_cache_dir),
    ])
    assert ret_set == 0
    captured_set = capsys.readouterr()
    set_json = json.loads(captured_set.out)
    assert set_json["success"] is True

    # CLI get json
    ret_get = cache.main([
        "get",
        "--tags",
        "postgres,prisma",
        "--cache-dir",
        str(temp_cache_dir),
    ])
    assert ret_get == 0
    captured_get = capsys.readouterr()
    get_json = json.loads(captured_get.out)
    assert get_json["hit"] is True
    assert len(get_json["entry"]["candidates"]) == 1

    # CLI get human
    ret_human = cache.main([
        "get",
        "--tags",
        "postgres,prisma",
        "--cache-dir",
        str(temp_cache_dir),
        "--human",
    ])
    assert ret_human == 0
    captured_human = capsys.readouterr()
    assert "Cache Key:" in captured_human.out
    assert "VALID" in captured_human.out
    assert "cli/skill" in captured_human.out

