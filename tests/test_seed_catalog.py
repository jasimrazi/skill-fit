"""Tests for Phase 3: Seed Catalog Verification against offline recorded responses."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

from _common import parse_frontmatter  # noqa: E402

SEED_CATALOG_FILE = ROOT / "skill" / "references" / "seed-catalog.json"
RECORDED_RESPONSES_FILE = ROOT / "tests" / "fixtures" / "seed_catalog_responses.json"


def test_seed_catalog_entries_all_resolve_offline():
    """Verify every seed catalog entry resolves against recorded offline responses and has a real SHA."""
    assert SEED_CATALOG_FILE.exists(), f"Missing {SEED_CATALOG_FILE}"
    assert RECORDED_RESPONSES_FILE.exists(), f"Missing {RECORDED_RESPONSES_FILE}"

    with open(SEED_CATALOG_FILE, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    with open(RECORDED_RESPONSES_FILE, "r", encoding="utf-8") as f:
        recorded_responses = json.load(f)

    candidates = catalog.get("candidates", [])
    assert len(candidates) > 0, "Seed catalog must contain candidates"

    hex40_pattern = re.compile(r"^[0-9a-fA-F]{40}$")

    for cand in candidates:
        cid = cand.get("id")
        assert cid, f"Candidate missing id: {cand}"

        # 1. SHA verification: Must have a real 40-char SHA, never 'HEAD' or floating
        sha = cand.get("pinned_sha")
        assert sha, f"Seed candidate '{cid}' missing pinned_sha"
        assert sha != "HEAD", f"Seed candidate '{cid}' must not be pinned to floating 'HEAD'"
        assert hex40_pattern.match(sha), f"Seed candidate '{cid}' has invalid commit SHA: '{sha}'"

        # 2. Resolution check against recorded offline responses
        assert cid in recorded_responses, f"Seed candidate '{cid}' cannot be resolved offline (missing in recorded responses)"
        rec = recorded_responses[cid]
        assert rec.get("status") == 200, f"Seed candidate '{cid}' resolution failed with status {rec.get('status')}"
        assert rec.get("sha") == sha, f"SHA mismatch for '{cid}': {sha} vs recorded {rec.get('sha')}"

        # 3. Content and frontmatter verification
        content = rec.get("content", "")
        fm = parse_frontmatter(content)
        assert "description" in fm and fm["description"].strip(), f"Seed candidate '{cid}' has empty description in frontmatter"
        assert len(fm["description"].split()) >= 5, f"Seed candidate '{cid}' description is too short"
