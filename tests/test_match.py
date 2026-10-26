import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import cache  # noqa: E402
import lock  # noqa: E402
import match  # noqa: E402
from registry import Candidate, Meta  # noqa: E402


def test_resolution_order_step1_lockfile_hit(tmp_path, monkeypatch):
    # Lockfile exists with matching manifest hash
    mock_network = MagicMock(side_effect=RuntimeError("Network called!"))
    monkeypatch.setattr("urllib.request.urlopen", mock_network)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8"
    )

    # Perform a quick scan to get manifest hash
    from scan import scan
    scan_res = scan(str(repo_dir))
    m_hash = scan_res["hash"]

    # Write matching lockfile
    lock_file = repo_dir / "skill-fit.lock"
    lock_data = {
        "schema": 1,
        "manifest_hash": m_hash,
        "resolved_at": "2026-10-26T14:00:00Z",
        "skills": [
            {
                "id": "vercel/next.js@next-practices",
                "layer": "framework",
                "pinned_sha": "sha123",
                "content_hash": "sha256:abcd",
                "description_tokens": 12,
                "approved": True,
            }
        ],
    }
    lock_file.write_bytes(lock.serialize_lockfile(lock_data))

    result = match.match_skills(
        repo_path=repo_dir,
        lockfile_path=lock_file,
    )

    assert result["resolution_source"] == "lockfile"
    assert result["manifest_hash"] == m_hash
    assert len(result["shortlist"]) == 1
    assert result["shortlist"][0]["id"] == "vercel/next.js@next-practices"
    mock_network.assert_not_called()


def test_resolution_order_step2_cache_hit(tmp_path, monkeypatch):
    mock_network = MagicMock(side_effect=RuntimeError("Network called!"))
    monkeypatch.setattr("urllib.request.urlopen", mock_network)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "requirements.txt").write_text("fastapi==0.100.0\n", encoding="utf-8")

    from scan import scan
    scan(str(repo_dir))
    tags = ["fastapi"]

    cache_dir = tmp_path / "cache"
    cache.set_cache(
        tags=tags,
        candidates=[
            {
                "id": "tiangolo/fastapi@fastapi-patterns",
                "source_repo": "tiangolo/fastapi",
                "skill_name": "fastapi-patterns",
                "layer": "backend",
                "found_by_query": "fastapi best practices",
                "install_count": 8000,
                "meta": {
                    "description": "FastAPI production patterns.",
                    "description_tokens": 8,
                    "skill_md_lines": 100,
                    "has_references": True,
                    "has_scripts": False,
                    "last_commit": "2026-03-01T00:00:00Z",
                    "audit_status": "clean",
                },
            }
        ],
        cache_dir=cache_dir,
    )

    result = match.match_skills(
        repo_path=repo_dir,
        cache_dir=cache_dir,
        lockfile_path=repo_dir / "skill-fit.lock",  # No lockfile present
    )

    assert result["resolution_source"] == "cache"
    assert len(result["shortlist"]) == 1
    assert result["shortlist"][0]["id"] == "tiangolo/fastapi@fastapi-patterns"
    mock_network.assert_not_called()


def test_resolution_order_step3_live_search(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8"
    )

    cache_dir = tmp_path / "cache"

    # Mock registry search
    mock_candidate = Candidate(
        id="owner/react-live@react-skills",
        source_repo="owner/react-live",
        skill_name="react-skills",
        layer="framework",
        found_by_query="react best practices",
        install_count=5000,
        meta=Meta(
            description="Live react patterns.",
            description_tokens=9,
            skill_md_lines=95,
            has_references=True,
            has_scripts=False,
            last_commit="2026-03-01T00:00:00Z",
            audit_status="clean",
        ),
    )

    def mock_search_all(self, queries, layer_map=None):
        return [mock_candidate], []

    monkeypatch.setattr(
        "registry.Registry.search_and_fetch_all",
        mock_search_all,
    )

    result = match.match_skills(
        repo_path=repo_dir,
        cache_dir=cache_dir,
        lockfile_path=repo_dir / "skill-fit.lock",
    )

    assert result["resolution_source"] == "live"
    assert len(result["shortlist"]) == 1
    assert result["shortlist"][0]["id"] == "owner/react-live@react-skills"

    # Verify cache was populated
    cached_entry, _ = cache.get_cache(["react"], cache_dir=cache_dir)
    assert cached_entry is not None
    assert len(cached_entry.candidates) == 1


def test_resolution_order_degraded_stale_fallback(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "requirements.txt").write_text("fastapi==0.100.0\n", encoding="utf-8")

    cache_dir = tmp_path / "cache"

    # Pre-populate expired cache
    cache.set_cache(
        tags=["fastapi"],
        candidates=[
            {
                "id": "stale/fastapi@skill",
                "source_repo": "stale/fastapi",
                "skill_name": "skill",
                "layer": "backend",
                "found_by_query": "fastapi",
                "install_count": 2000,
                "meta": {
                    "description": "Stale cached fastapi skill.",
                    "description_tokens": 10,
                    "skill_md_lines": 80,
                    "has_references": True,
                    "audit_status": "clean",
                },
            }
        ],
        cache_dir=cache_dir,
        ttl_s=1,  # 1 second TTL
    )

    # Force network failure
    def mock_fail(self, queries, layer_map=None):
        raise RuntimeError("Network disconnected!")

    monkeypatch.setattr(
        "registry.Registry.search_and_fetch_all",
        mock_fail,
    )

    # Fast forward time to guarantee cache expiration
    future_time = time.time() + 100

    result = match.match_skills(
        repo_path=repo_dir,
        cache_dir=cache_dir,
        lockfile_path=repo_dir / "skill-fit.lock",
        now_epoch=future_time,
    )

    assert result["resolution_source"] == "stale_cache"
    assert result["is_stale"] is True
    assert len(result["shortlist"]) == 1
    assert result["shortlist"][0]["id"] == "stale/fastapi@skill"
    assert any("network" in w.lower() or "stale" in w.lower() for w in result["warnings"])


def test_offline_mode_no_network(tmp_path, monkeypatch):
    mock_network = MagicMock(side_effect=RuntimeError("Network called!"))
    monkeypatch.setattr("urllib.request.urlopen", mock_network)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps({"dependencies": {"vue": "^3.0.0"}}), encoding="utf-8"
    )

    result = match.match_skills(
        repo_path=repo_dir,
        cache_dir=tmp_path / "cache",
        lockfile_path=repo_dir / "skill-fit.lock",
        offline=True,
    )

    assert result["resolution_source"] == "offline_miss"
    assert result["shortlist"] == []
    assert any("offline" in w.lower() for w in result["warnings"])
    mock_network.assert_not_called()


def test_estimated_output_tokens_budget(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8"
    )

    # Generate 10 candidates with descriptions
    cands = [
        Candidate(
            id=f"org/react-{i}@skill",
            source_repo=f"org/react-{i}",
            skill_name=f"skill-{i}",
            layer="framework" if i == 0 else f"layer-{i}",
            found_by_query="react",
            install_count=2000,
            meta=Meta(
                description=f"Description for skill {i} " * 5,
                description_tokens=20,
                skill_md_lines=100,
                has_references=True,
                audit_status="clean",
            ),
        )
        for i in range(5)
    ]

    monkeypatch.setattr(
        "registry.Registry.search_and_fetch_all",
        lambda self, queries, layer_map=None: (cands, []),
    )

    result = match.match_skills(
        repo_path=repo_dir,
        cache_dir=tmp_path / "cache",
        lockfile_path=repo_dir / "skill-fit.lock",
    )

    serialized = json.dumps(result)
    assert result["estimated_output_tokens"] <= 1000
    assert len(serialized) // 4 <= 1000


def test_match_cli(tmp_path, capsys):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8"
    )

    ret = match.main([
        str(repo_dir),
        "--offline",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--human",
    ])
    assert ret == 0
    captured = capsys.readouterr()
    assert "SKILL MATCH SHORTLIST" in captured.out
    assert "OFFLINE_MISS" in captured.out

