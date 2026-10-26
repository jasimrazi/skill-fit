import json
import sys
from pathlib import Path

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import rank  # noqa: E402


def create_candidate(
    cid: str,
    layer: str,
    query: str,
    description: str = "A useful agent skill.",
    tokens: int = 15,
    lines: int = 100,
    has_refs: bool = True,
    has_scripts: bool = False,
    last_commit: str = "2026-03-01T00:00:00Z",
    audit_status: str = "clean",
    installs: int = 5000,
):
    return {
        "id": cid,
        "source_repo": cid.split("@")[0] if "@" in cid else "owner/repo",
        "skill_name": cid.split("@")[1] if "@" in cid else cid,
        "layer": layer,
        "found_by_query": query,
        "install_count": installs,
        "meta": {
            "description": description,
            "description_tokens": tokens,
            "skill_md_lines": lines,
            "has_references": has_refs,
            "has_scripts": has_scripts,
            "last_commit": last_commit,
            "audit_status": audit_status,
        },
    }


def test_deterministic_output():
    candidates = [
        create_candidate("b/react-best@react", "framework", "react best practices"),
        create_candidate("a/fastapi-patterns@fastapi", "backend", "fastapi patterns"),
        create_candidate("c/tailwind-helper@tailwind", "styling", "tailwind css patterns"),
    ]

    result1 = rank.rank_candidates(
        candidates=candidates, stack_tags=["react", "fastapi", "tailwind"]
    )
    result2 = rank.rank_candidates(
        candidates=candidates, stack_tags=["react", "fastapi", "tailwind"]
    )

    assert json.dumps(result1.to_dict()) == json.dumps(result2.to_dict())
    assert len(result1.shortlist) == 3


def test_one_skill_per_layer():
    # Multiple candidates in the framework layer
    candidates = [
        create_candidate("owner/react-a@skill", "framework", "react best practices", lines=50),
        create_candidate("owner/react-b@skill", "framework", "react best practices", lines=250),
        create_candidate("owner/tailwind@skill", "styling", "tailwind css patterns", lines=100),
    ]

    result = rank.rank_candidates(candidates=candidates, stack_tags=["react", "tailwind"])
    shortlist_layers = [item["layer"] for item in result.shortlist]

    # Only one framework skill selected
    assert shortlist_layers.count("framework") == 1
    assert "styling" in shortlist_layers

    # The other framework candidate is listed under rejected
    rejected_ids = [r["id"] for r in result.rejected]
    assert "owner/react-b@skill" in rejected_ids or "owner/react-a@skill" in rejected_ids
    assert any("layer collision" in r["reason"].lower() for r in result.rejected)


def test_budget_enforcement():
    # Budget of 35 tokens: can take one 20-token skill, but not both 20-token skills
    candidates = [
        create_candidate("a/skill1@skill", "framework", "react", tokens=20),
        create_candidate("b/skill2@skill", "backend", "fastapi", tokens=20),
        create_candidate("c/skill3@skill", "styling", "tailwind", tokens=10),
    ]

    result = rank.rank_candidates(
        candidates=candidates,
        stack_tags=["react", "fastapi", "tailwind"],
        budget=35,
    )

    total_tokens = sum(item["description_tokens"] for item in result.shortlist)
    assert total_tokens <= 35
    assert result.total_description_tokens <= 35
    assert len(result.shortlist) == 2  # 20 + 10 = 30 tokens


def test_install_count_never_affects_score_or_order():
    # Two identical candidates except one has 1,500 installs and the other 1,500,000
    cand_high = create_candidate("repo/cand-b@skill", "framework", "react", installs=1500000)
    cand_low = create_candidate("repo/cand-a@skill", "framework", "react", installs=1500)

    score_high, breakdown_high = rank.score_candidate(cand_high, ["react"])
    score_low, breakdown_low = rank.score_candidate(cand_low, ["react"])

    assert score_high == score_low
    assert "install_count" not in breakdown_high
    assert "install_count" not in breakdown_low

    # Ranking them together: tie is broken by ID alphabetically
    result = rank.rank_candidates([cand_high, cand_low], stack_tags=["react"])
    assert result.shortlist[0]["id"] == "repo/cand-a@skill"


def test_install_count_floor_filter():
    candidates = [
        create_candidate("a/good@skill", "framework", "react", installs=2000),
        create_candidate("b/low@skill", "backend", "fastapi", installs=300),
    ]

    result = rank.rank_candidates(
        candidates=candidates,
        stack_tags=["react", "fastapi"],
        min_install_count=1000,
    )

    shortlist_ids = [item["id"] for item in result.shortlist]
    assert "a/good@skill" in shortlist_ids
    assert "b/low@skill" not in shortlist_ids

    rejected_map = {r["id"]: r["reason"] for r in result.rejected}
    assert "b/low@skill" in rejected_map
    assert "floor" in rejected_map["b/low@skill"].lower()


def test_installed_skill_layer_collision_filter():
    installed_skills = [
        {
            "name": "my-fastapi-helper",
            "description": "FastAPI backend helpers and patterns",
            "description_tokens": 12,
        }
    ]

    candidates = [
        create_candidate("a/react@skill", "framework", "react"),
        create_candidate("b/fastapi@skill", "backend", "fastapi patterns"),
    ]

    result = rank.rank_candidates(
        candidates=candidates,
        installed_skills=installed_skills,
        stack_tags=["react", "fastapi"],
    )

    shortlist_ids = [item["id"] for item in result.shortlist]
    assert "a/react@skill" in shortlist_ids
    assert "b/fastapi@skill" not in shortlist_ids

    rejected_map = {r["id"]: r["reason"] for r in result.rejected}
    assert "b/fastapi@skill" in rejected_map
    assert "layer collision with installed skill" in rejected_map["b/fastapi@skill"].lower()


def test_hard_filters_audit_and_missing_description():
    candidates = [
        create_candidate("a/clean@skill", "framework", "react"),
        create_candidate("b/failed@skill", "styling", "tailwind", audit_status="failed"),
        create_candidate("c/nodesc@skill", "backend", "fastapi", description=""),
    ]

    result = rank.rank_candidates(
        candidates=candidates, stack_tags=["react", "tailwind", "fastapi"]
    )

    shortlist_ids = [item["id"] for item in result.shortlist]
    assert "a/clean@skill" in shortlist_ids
    assert "b/failed@skill" not in shortlist_ids
    assert "c/nodesc@skill" not in shortlist_ids

    rejected_map = {r["id"]: r["reason"] for r in result.rejected}
    assert "audit" in rejected_map["b/failed@skill"].lower()
    assert "description" in rejected_map["c/nodesc@skill"].lower()


def test_scripts_flag_informational_zero_bonus():
    cand_no_scripts = create_candidate(
        "a/noscripts@skill", "framework", "react", has_scripts=False
    )
    cand_with_scripts = create_candidate(
        "b/withscripts@skill", "framework", "react", has_scripts=True
    )

    score_no, _ = rank.score_candidate(cand_no_scripts, ["react"])
    score_with, _ = rank.score_candidate(cand_with_scripts, ["react"])

    # Scripts presence must not award extra bonus points
    assert score_no == score_with


def test_rank_cli(tmp_path, capsys):
    candidates = [
        create_candidate("org/next@skill", "framework", "nextjs best practices"),
        create_candidate("org/prisma@skill", "orm", "prisma patterns"),
    ]
    cand_file = tmp_path / "candidates.json"
    cand_file.write_text(json.dumps(candidates), encoding="utf-8")

    # Run CLI
    ret = rank.main([str(cand_file), "--tags", "next,prisma", "--human"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "SHORTLIST" in captured.out
    assert "org/next@skill" in captured.out
    assert "org/prisma@skill" in captured.out
