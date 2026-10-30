"""Regression tests for synthetic distractors and observed failure modes.

These test cases verify that observed failures (e.g. Go linter returned for Flutter,
Redis returned for Django, unverified floating HEAD entries) are rejected deterministically by rule.
They are strictly synthetic fixtures and do not feed into evaluation metrics.
"""
import json
import sys
from pathlib import Path

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import rank  # noqa: E402

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "regression" / "synthetic_distractors.json"


def test_synthetic_distractor_regressions_rejected_by_rule():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(data) >= 8

    for item in data:
        case_name = item["case"]
        stack_tags = item["stack_tags"]
        cand = item["candidate"]
        expected_reason = item["expected_reason_substr"].lower()

        # Provenance must be labeled synthetic
        assert item["provenance"] == "synthetic"

        result = rank.rank_candidates(
            candidates=[cand],
            stack_tags=stack_tags,
            min_install_count=1000,
        )

        # Must never be in shortlist or strong
        assert len(result.shortlist) == 0, f"Case {case_name} should not be in shortlist"
        assert len(result.strong) == 0, f"Case {case_name} should not be in strong"

        # Must be rejected with expected reason
        rejected_ids = [r["id"] for r in result.rejected]
        assert cand["id"] in rejected_ids, f"Case {case_name} ({cand['id']}) was not rejected"

        matching_rejections = [r for r in result.rejected if r["id"] == cand["id"]]
        reason_found = any(expected_reason in r["reason"].lower() for r in matching_rejections)
        assert reason_found, (
            f"Case {case_name}: expected reason '{expected_reason}', got: "
            f"{[r['reason'] for r in matching_rejections]}"
        )
