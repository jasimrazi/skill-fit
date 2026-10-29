"""Tests for Phase 4: Fit-first scoring, quality, popularity tiebreaker, and output tiers."""
import json
import sys
from pathlib import Path

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import rank  # noqa: E402
import match  # noqa: E402


def make_cand(
    cid: str,
    layer: str,
    query: str,
    name: str = "",
    desc: str = "A useful agent skill for the tech stack.",
    tokens: int = 15,
    lines: int = 120,
    has_refs: bool = True,
    installs: int = 5000,
    pinned_sha: str = "1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62",
    tags: list = None,
):
    skill_name = name or (cid.split("@")[1] if "@" in cid else cid)
    return {
        "id": cid,
        "source_repo": cid.split("@")[0] if "@" in cid else "owner/repo",
        "skill_name": skill_name,
        "layer": layer,
        "found_by_query": query,
        "install_count": installs,
        "pinned_sha": pinned_sha,
        "tags": tags or [],
        "meta": {
            "description": desc,
            "description_tokens": tokens,
            "skill_md_lines": lines,
            "has_references": has_refs,
            "has_scripts": False,
            "last_commit": "2026-03-01T00:00:00Z",
            "audit_status": "clean",
        },
    }


def test_fit_first_overcomes_popularity():
    """A high-fit skill (primary + secondary tag match) beats a low-fit skill with massive installs."""
    # cand_fit matches react and tailwind specifically
    cand_fit = make_cand(
        cid="vercel-labs/agent-skills@react-tailwind-patterns",
        layer="framework",
        query="react tailwind",
        name="react-tailwind-patterns",
        desc="Tailwind component styling and design tokens for React applications.",
        installs=500,  # low installs
        tags=["react", "tailwind"],
    )

    # cand_popular is a generic tool that only weakly mentions react in desc, 2M installs
    cand_popular = make_cand(
        cid="popular-org/agent-skills@general-helper",
        layer="framework",
        query="react",
        name="general-helper",
        desc="A broad multipurpose helper utility that works with react or any web code.",
        installs=2000000,  # 2M installs
        tags=["general"],
    )

    score_fit, breakdown_fit = rank.score_candidate(cand_fit, ["react", "tailwind"])
    score_pop, breakdown_pop = rank.score_candidate(cand_popular, ["react", "tailwind"])

    assert breakdown_fit["fit"]["total"] > breakdown_pop["fit"]["total"]
    assert score_fit > score_pop, f"Fit candidate ({score_fit}) should beat popular candidate ({score_pop})"


def test_popularity_weak_tiebreaker():
    """When fit and quality are identical, popularity breaks the tie."""
    cand_a = make_cand(
        cid="org/react-guide-a@react-guide",
        layer="framework",
        query="react",
        name="react-guide",
        desc="React patterns and conventions for modern web applications.",
        installs=100000,  # 100k
    )
    cand_b = make_cand(
        cid="org/react-guide-b@react-guide",
        layer="framework",
        query="react",
        name="react-guide",
        desc="React patterns and conventions for modern web applications.",
        installs=200,  # 200
    )

    score_a, breakdown_a = rank.score_candidate(cand_a, ["react"])
    score_b, breakdown_b = rank.score_candidate(cand_b, ["react"])

    # Fit and quality are identical
    assert breakdown_a["fit"]["total"] == breakdown_b["fit"]["total"]
    assert breakdown_a["quality"]["total"] == breakdown_b["quality"]["total"]

    # Popularity tiebreaker gives slight edge to cand_a
    assert score_a > score_b
    assert breakdown_a["popularity"]["score"] > breakdown_b["popularity"]["score"]
    assert breakdown_a["popularity"]["score"] <= 5  # capped as weak tiebreaker


def test_output_tiers_strong_possible_gaps_rejected():
    """Candidates are segregated into strong, possible, gaps, and rejected tiers."""
    # Strong candidate in framework layer (score >= 70)
    strong_cand = make_cand(
        cid="vercel-labs/agent-skills@react-patterns",
        layer="framework",
        query="react",
        name="react-patterns",
        desc="High quality React and Next.js design patterns.",
        installs=50000,
        tags=["react"],
    )

    # Second candidate in same framework layer (score >= 70, but layer collision -> possible)
    alt_cand = make_cand(
        cid="another-org/skills@react-components",
        layer="framework",
        query="react",
        name="react-components",
        desc="Alternative component library guidelines for React.",
        installs=10000,
        tags=["react"],
    )

    # Moderate score candidate (between 40 and 69 -> possible)
    weak_cand = make_cand(
        cid="community/skills@misc-notes",
        layer="styling",
        query="react",
        name="misc-notes",
        desc="Generic notes loosely mentioning react.",
        tokens=45,
        lines=450,
        has_refs=False,
        installs=1500,
        tags=["general"],
    )

    # Rejected candidate (fails gate: negative conflict or invalid)
    rejected_cand = make_cand(
        cid="golang-org/skills@go-linter",
        layer="lint",
        query="go",
        name="go-linter",
        desc="Go linter enforcing golang standard practices.",
        installs=50000,
    )

    stack_tags = ["react", "tailwind", "zustand"]
    result = rank.rank_candidates(
        candidates=[strong_cand, alt_cand, weak_cand, rejected_cand],
        stack_tags=stack_tags,
        strong_threshold=70,
    )

    # Check strong tier: exactly 1 per layer, score >= 70
    assert len(result.strong) == 1
    assert result.strong[0]["id"] == "vercel-labs/agent-skills@react-patterns"
    assert result.strong[0]["score"] >= 70

    # Check possible tier: contains alternative framework skill and weak skill
    possible_ids = [c["id"] for c in result.possible]
    assert "another-org/skills@react-components" in possible_ids
    assert "community/skills@misc-notes" in possible_ids

    # Check rejected tier: contains go-linter due to language conflict
    rejected_ids = [r["id"] for r in result.rejected]
    assert "golang-org/skills@go-linter" in rejected_ids

    # Check gaps: tailwind and zustand were in stack_tags but had no candidates
    assert "tailwind" in result.gaps
    assert "zustand" in result.gaps
    assert "react" not in result.gaps


def test_no_strong_match_honest_reporting():
    """When no candidate scores above strong_threshold, honestly say so and do NOT recommend weak ones."""
    # Only a weak candidate exists (score < 70, e.g. score ~ 57)
    weak_cand = make_cand(
        cid="community/skills@general-notes",
        layer="framework",
        query="flutter",
        name="general-notes",
        desc="Some draft notes loosely mentioning flutter in passing.",
        tokens=45,
        lines=450,
        has_refs=False,
        installs=2000,
        tags=["general"],
    )

    result = rank.rank_candidates(
        candidates=[weak_cand],
        stack_tags=["flutter", "provider"],
        strong_threshold=70,
    )

    # Shortlist and strong must be empty!
    assert len(result.strong) == 0
    assert len(result.shortlist) == 0
    assert result.has_strong_matches is False
    assert "no strong match" in result.message.lower()

    # Weak candidate is visible in possible (near misses)
    assert len(result.possible) == 1
    assert result.possible[0]["id"] == "community/skills@general-notes"

    # Provider and flutter gaps reported
    assert "provider" in result.gaps

    # Human rendering clearly reflects "NO STRONG MATCH"
    rendered = rank.render_human(result)
    assert "NO STRONG MATCH" in rendered
    assert "Near Misses" in rendered or "Possible" in rendered
    assert "Gaps" in rendered


def test_shortlist_json_under_1000_tokens():
    """The serialized output JSON strictly stays under 1,000 estimated tokens."""
    candidates = [
        make_cand(f"repo/skill-{i}@skill-{i}", f"layer-{i % 3}", "react", installs=1000 * i)
        for i in range(12)
    ]
    result = rank.rank_candidates(
        candidates=candidates,
        stack_tags=["react", "typescript", "tailwind", "prisma", "docker"],
        strong_threshold=70,
    )

    payload = {
        "resolution_source": "live",
        "manifest_hash": "a" * 64,
        "stack_tags": ["react", "typescript", "tailwind", "prisma", "docker"],
        "shortlist": result.shortlist,
        "strong": result.strong,
        "possible": result.possible,
        "gaps": result.gaps,
        "rejected": result.rejected,
        "has_strong_matches": result.has_strong_matches,
        "message": result.message,
        "total_description_tokens": result.total_description_tokens,
        "budget": result.budget,
        "is_stale": False,
        "warnings": [],
    }

    budgeted = match.enforce_output_token_budget(payload)
    est_tokens = budgeted["estimated_output_tokens"]
    assert est_tokens < 1000, f"Output exceeded 1000 tokens: {est_tokens}"
