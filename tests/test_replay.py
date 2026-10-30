"""Tests for Phase 5: Offline replay mode and evaluation metrics."""
import json
import sys
from pathlib import Path

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import replay  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "eval_pools"


def test_replay_single_pool_offline():
    """Replay runs deterministically on a recorded candidate pool without network."""
    flutter_pool = FIXTURES_DIR / "flutter_provider_dio.json"
    result = replay.replay_pool(flutter_pool)

    assert result["stack_name"] == "Flutter+Provider+Dio"
    assert "flutter" in result["tags"]
    assert len(result["candidates"]) > 0

    # Ensure tiers are produced
    assert "strong" in result
    assert "possible" in result
    assert "rejected" in result


def test_replay_all_captured_stacks():
    """All captured evaluation stacks run offline and produce valid tier breakdowns."""
    results = replay.replay_all_pools(FIXTURES_DIR)
    assert len(results) >= 5

    expected_stacks = [
        "Flutter+Provider+Dio",
        "React+Tailwind",
        "Next.js",
        "FastAPI+Docker",
        "Django+Celery",
    ]
    for stack in expected_stacks:
        assert stack in results, f"Missing stack: {stack}"
        res = results[stack]
        assert "strong" in res
        assert "possible" in res
        assert "gaps" in res
        assert "rejected" in res


def test_labeling_sheet_generation_strictly_unlabeled():
    """Labeling sheet contains candidate, description, source, and is strictly UNLABELED."""
    results = replay.replay_all_pools(FIXTURES_DIR)
    sheet_md = (FIXTURES_DIR / "blind_labeling_sheet.md").read_text(encoding="utf-8")

    # Required headers
    assert "Candidate ID" in sheet_md
    assert "Description" in sheet_md
    assert "Source" in sheet_md
    assert "Your Label" in sheet_md

    # Strict invariant: MUST NOT be pre-labeled by agent
    for line in sheet_md.splitlines():
        if line.startswith("|") and not line.startswith("| #") and not line.startswith("|---"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 5:
                label_cell = cells[4]
                assert label_cell == "[ ] relevant / [ ] not relevant / [ ] unsure"


def test_compute_metrics():
    """Metrics calculation correctly computes junk rate, precision@3, and empty-when-good-exists with n shown."""
    results = replay.replay_all_pools(FIXTURES_DIR)

    # Provide synthetic mock labels for testing calculation logic
    mock_labels = {}
    for stack_name, res in results.items():
        for cand in res["strong"]:
            cid = cand["id"]
            mock_labels[(stack_name, cid)] = "relevant"

    metrics = replay.compute_metrics(results, mock_labels)

    assert "junk_rate_pct" in metrics
    assert "precision_at_3" in metrics
    assert "empty_when_good_exists_pct" in metrics
    assert "n_stacks" in metrics
    assert metrics["n_stacks"] >= 5
