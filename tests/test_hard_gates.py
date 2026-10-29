"""Tests for Phase 3: Hard Gates and Regression Tests (a), (b), (c)."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

import rank  # noqa: E402


def test_gate1_rejects_floating_head_or_missing_sha():
    """Gate 1: Candidates with no SHA or floating 'HEAD' are rejected; never shown as pinned."""
    candidates = [
        {
            "id": "unverified/skill@test",
            "skill_name": "test-skill",
            "layer": "backend",
            "pinned_sha": "HEAD",  # Floating HEAD is invalid!
            "meta": {"description": "Some description", "audit_status": "clean"},
            "install_count": 2000,
        },
        {
            "id": "missing/sha@test",
            "skill_name": "no-sha-skill",
            "layer": "backend",
            "pinned_sha": None,  # Missing SHA
            "meta": {"description": "Some description", "audit_status": "clean"},
            "install_count": 2000,
        },
        {
            "id": "valid/sha@test",
            "skill_name": "valid-skill",
            "layer": "backend",
            "pinned_sha": "1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62",  # Real 40-char SHA
            "meta": {"description": "Fastapi valid skill description", "audit_status": "clean"},
            "install_count": 2000,
        },
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=[],
        stack_tags=["fastapi"],
    )

    rejected_ids = {r["id"]: r["reason"] for r in rejected}
    assert "unverified/skill@test" in rejected_ids
    assert "HEAD" in rejected_ids["unverified/skill@test"] or "SHA" in rejected_ids["unverified/skill@test"]
    assert "missing/sha@test" in rejected_ids

    eligible_ids = [c["id"] for c in eligible]
    assert "valid/sha@test" in eligible_ids


def test_gate2_rejects_candidates_not_matching_detected_tags():
    """Gate 2: Candidate must match at least one detected tag by name, description, or path."""
    candidates = [
        {
            "id": "unrelated/repo@unrelated-tool",
            "skill_name": "unrelated-tool",
            "layer": "utility",
            "pinned_sha": "1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62",
            "meta": {"description": "A tool for managing kubernetes clusters", "audit_status": "clean"},
            "install_count": 5000,
        }
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=[],
        stack_tags=["flutter", "dart"],
    )

    assert len(eligible) == 0
    assert len(rejected) == 1
    assert "Does not match any detected tag" in rejected[0]["reason"]


def test_gate3_regression_a_go_linting_for_flutter():
    """Regression test (a): A Go linting skill returned for a Flutter app is rejected."""
    candidates = [
        {
            "id": "golangci/lint@golangci-lint",
            "skill_name": "golangci-lint",
            "layer": "linting",
            "pinned_sha": "25278e7d7b6cf6e6a4e7a6c8e10f14ff22f0eb5b",
            "meta": {
                "description": "Fast linter runner for Go with concurrency and caching",
                "audit_status": "clean",
            },
            "install_count": 8000,
        }
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=[],
        stack_tags=["flutter", "dart", "provider"],
    )

    assert len(eligible) == 0
    assert len(rejected) == 1
    reason = rejected[0]["reason"]
    assert "conflicting" in reason.lower() or "go" in reason.lower()


def test_gate3_regression_b_redis_for_django():
    """Regression test (b): redis-best-practices returned for Django without redis is rejected."""
    candidates = [
        {
            "id": "redis-labs/skills@redis-best-practices",
            "skill_name": "redis-best-practices",
            "layer": "database",
            "pinned_sha": "25278e7d7b6cf6e6a4e7a6c8e10f14ff22f0eb5b",
            "meta": {
                "description": "Best practices for Redis caching, persistence, and cluster topology",
                "audit_status": "clean",
            },
            "install_count": 4500,
        }
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=[],
        stack_tags=["django", "celery", "python"],  # No redis detected
    )

    assert len(eligible) == 0
    assert len(rejected) == 1
    assert "redis" in rejected[0]["reason"].lower()


def test_gate4_install_count_floor():
    """Gate 4: Install count below floor is rejected with explicit reason."""
    candidates = [
        {
            "id": "low-installs/skill@low",
            "skill_name": "low-install-skill",
            "layer": "backend",
            "pinned_sha": "25278e7d7b6cf6e6a4e7a6c8e10f14ff22f0eb5b",
            "meta": {"description": "Fastapi lightweight utility", "audit_status": "clean"},
            "install_count": 50,  # Below floor 1000
        }
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=[],
        stack_tags=["fastapi"],
        min_install_count=1000,
    )

    assert len(eligible) == 0
    assert len(rejected) == 1
    assert "Install count (50) below floor" in rejected[0]["reason"]


def test_gate5_audit_status_failed():
    """Gate 5: Audit status failed or malicious is rejected."""
    candidates = [
        {
            "id": "malicious/skill@bad",
            "skill_name": "bad-skill",
            "layer": "backend",
            "pinned_sha": "25278e7d7b6cf6e6a4e7a6c8e10f14ff22f0eb5b",
            "meta": {"description": "Fastapi utility", "audit_status": "failed"},
            "install_count": 5000,
        }
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=[],
        stack_tags=["fastapi"],
    )

    assert len(eligible) == 0
    assert len(rejected) == 1
    assert "Failed audit status" in rejected[0]["reason"]


def test_gate6_layer_collision_with_installed():
    """Gate 6: Collision with an installed skill's layer is rejected."""
    installed = [
        {"name": "my-existing-linter", "description": "Global code linter", "layer": "linting"}
    ]
    candidates = [
        {
            "id": "another/linter@new-linter",
            "skill_name": "new-linter",
            "layer": "linting",
            "pinned_sha": "25278e7d7b6cf6e6a4e7a6c8e10f14ff22f0eb5b",
            "meta": {"description": "Python linter helper", "audit_status": "clean"},
            "install_count": 5000,
        }
    ]

    eligible, rejected = rank.apply_hard_gates(
        candidates=candidates,
        installed_skills=installed,
        stack_tags=["python"],
    )

    assert len(eligible) == 0
    assert len(rejected) == 1
    assert "Layer collision with installed skill" in rejected[0]["reason"]
