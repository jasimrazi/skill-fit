import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import install  # noqa: E402
import lock  # noqa: E402


def create_mock_skill(
    base_dir: Path,
    skill_name: str,
    desc: str = "A helpful skill",
    tampered: bool = False,
) -> Path:
    skill_dir = base_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = "Tampered code!" if tampered else f"# {skill_name}\nClean code.\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {desc}\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def test_trust_gate_formatting():
    item = {
        "id": "owner/repo@react-helper",
        "source_repo": "owner/repo",
        "skill_name": "react-helper",
        "layer": "framework",
        "pinned_sha": "a1b2c3d4e5f67890",
        "description": "Helper for React apps with malicious \x1b[31mcolor\x1b[0m code.",
        "description_tokens": 12,
        "score": 85,
        "signals": {
            "audit_status": {"status": "clean"},
            "scripts": {"has_scripts": True},
        },
    }

    gate_info = install.format_trust_gate_item(item, locked_ids=set())
    assert gate_info["id"] == "owner/repo@react-helper"
    assert gate_info["status"] == "unvetted"
    assert gate_info["pinned_sha"] == "a1b2c3d4e5f67890"
    assert gate_info["audit_status"] == "clean"
    assert gate_info["has_scripts"] is True
    assert gate_info["description_tokens"] == 12
    # ANSI code stripped and quoted
    assert "\x1b[31m" not in gate_info["quoted_description"]
    assert gate_info["quoted_description"].startswith('"')
    assert gate_info["quoted_description"].endswith('"')

    # If already in locked_ids -> labeled "locked"
    gate_info_locked = install.format_trust_gate_item(
        item, locked_ids={"owner/repo@react-helper"}
    )
    assert gate_info_locked["status"] == "locked"


def test_dry_run_by_default_no_install_no_lock(tmp_path):
    skills_dir = tmp_path / "skills"
    lock_file = tmp_path / "skill-fit.lock"

    shortlist = [
        {
            "id": "owner/repo@sample-skill",
            "source_repo": "owner/repo",
            "skill_name": "sample-skill",
            "layer": "framework",
            "pinned_sha": "sha123",
            "description": "Sample skill",
            "description_tokens": 10,
        }
    ]

    mock_runner = MagicMock()

    result = install.install_skills(
        shortlist=shortlist,
        manifest_hash="man-hash-1",
        skills_dir=skills_dir,
        lockfile_path=lock_file,
        approved_ids=[],  # No approvals!
        installer_runner=mock_runner,
    )

    # Dry-run: nothing installed, mock_runner never called, lockfile not written
    assert result["dry_run"] is True
    assert result["installed"] == []
    assert len(result["trust_gate"]) == 1
    mock_runner.assert_not_called()
    assert not lock_file.exists()


def test_tampered_content_is_quarantined_and_rejected(tmp_path):
    skills_dir = tmp_path / "skills"
    lock_file = tmp_path / "skill-fit.lock"

    # Pre-create clean skill to calculate expected content hash
    staging_dir = tmp_path / "staging"
    clean_dir = create_mock_skill(staging_dir, "tampered-target", tampered=False)
    expected_hash = lock.compute_content_hash(clean_dir)

    shortlist = [
        {
            "id": "owner/repo@tampered-target",
            "source_repo": "owner/repo",
            "skill_name": "tampered-target",
            "layer": "framework",
            "pinned_sha": "sha123",
            "expected_content_hash": expected_hash,
            "description": "Target skill",
            "description_tokens": 10,
        }
    ]

    # Installer mock that writes tampered content into skills_dir
    def fake_installer(source_repo, pinned_sha, target_dir):
        create_mock_skill(target_dir, "tampered-target", tampered=True)
        return True, ""

    result = install.install_skills(
        shortlist=shortlist,
        manifest_hash="man-hash-1",
        skills_dir=skills_dir,
        lockfile_path=lock_file,
        approved_ids=["owner/repo@tampered-target"],
        installer_runner=fake_installer,
    )

    # Must detect tampering
    assert result["dry_run"] is False
    assert len(result["failures"]) == 1
    failure = result["failures"][0]
    assert failure["status"] == "tampered"
    assert "mismatch" in failure["reason"].lower()

    # The installed skill directory should be quarantined (moved out)
    installed_dir = skills_dir / "tampered-target"
    assert not installed_dir.exists()
    quarantine_dirs = list(skills_dir.glob("tampered-target.quarantine.*"))
    assert len(quarantine_dirs) == 1

    # Lockfile must NOT be written!
    assert not lock_file.exists()


def test_successful_install_verifies_and_writes_lockfile(tmp_path):
    skills_dir = tmp_path / "skills"
    lock_file = tmp_path / "skill-fit.lock"

    staging_dir = tmp_path / "staging"
    clean_dir = create_mock_skill(staging_dir, "clean-target", tampered=False)
    expected_hash = lock.compute_content_hash(clean_dir)

    shortlist = [
        {
            "id": "owner/repo@clean-target",
            "source_repo": "owner/repo",
            "skill_name": "clean-target",
            "layer": "framework",
            "pinned_sha": "commit-sha-999",
            "expected_content_hash": expected_hash,
            "description": "Clean target skill",
            "description_tokens": 8,
        }
    ]

    def fake_installer(source_repo, pinned_sha, target_dir):
        create_mock_skill(target_dir, "clean-target", tampered=False)
        return True, ""

    result = install.install_skills(
        shortlist=shortlist,
        manifest_hash="man-hash-clean",
        skills_dir=skills_dir,
        lockfile_path=lock_file,
        approved_ids=["owner/repo@clean-target"],
        installer_runner=fake_installer,
    )

    assert result["dry_run"] is False
    assert len(result["installed"]) == 1
    assert result["failures"] == []
    assert lock_file.exists()

    # Verify written lockfile
    lock_data = lock.read_lockfile(lock_file)
    assert lock_data["manifest_hash"] == "man-hash-clean"
    assert lock_data["skills"][0]["id"] == "owner/repo@clean-target"
    assert lock_data["skills"][0]["content_hash"] == expected_hash
    assert lock_data["skills"][0]["pinned_sha"] == "commit-sha-999"


def test_runner_failure_leaves_lockfile_untouched(tmp_path):
    skills_dir = tmp_path / "skills"
    lock_file = tmp_path / "skill-fit.lock"

    shortlist = [
        {
            "id": "owner/repo@failing-skill",
            "source_repo": "owner/repo",
            "skill_name": "failing-skill",
            "layer": "backend",
            "pinned_sha": "sha-fail",
            "description": "Failing skill",
            "description_tokens": 10,
        }
    ]

    def failing_installer(source_repo, pinned_sha, target_dir):
        return False, "Network timeout or command not found"

    result = install.install_skills(
        shortlist=shortlist,
        manifest_hash="man-hash-fail",
        skills_dir=skills_dir,
        lockfile_path=lock_file,
        approved_ids=["owner/repo@failing-skill"],
        installer_runner=failing_installer,
    )

    assert len(result["failures"]) == 1
    assert result["failures"][0]["status"] == "install_failed"
    assert not lock_file.exists()


def test_install_cli(tmp_path, capsys):
    skills_dir = tmp_path / "skills"
    lock_file = tmp_path / "skill-fit.lock"

    shortlist = [
        {
            "id": "cli/repo@test-cli",
            "source_repo": "cli/repo",
            "skill_name": "test-cli",
            "layer": "orm",
            "pinned_sha": "sha-cli",
            "description": "CLI test skill",
            "description_tokens": 14,
        }
    ]
    shortlist_file = tmp_path / "shortlist.json"
    shortlist_file.write_text(json.dumps(shortlist), encoding="utf-8")

    # Dry-run CLI
    ret = install.main([
        str(shortlist_file),
        "--manifest-hash",
        "hash-cli",
        "--skills-dir",
        str(skills_dir),
        "--lockfile",
        str(lock_file),
        "--human",
    ])
    assert ret == 0
    captured = capsys.readouterr()
    assert "TRUST GATE" in captured.out
    assert "DRY RUN" in captured.out
    assert "unvetted" in captured.out.lower()
    assert not lock_file.exists()


def test_unpinned_candidate_resolves_sha(tmp_path, monkeypatch):
    """An unpinned shortlist candidate (HEAD) is resolved to an exact commit SHA before install."""
    skills_dir = tmp_path / "skills"
    lock_file = tmp_path / "skill-fit.lock"

    monkeypatch.setattr(
        install,
        "resolve_remote_sha",
        lambda repo, timeout_s=5.0: "1234567890abcdef1234567890abcdef12345678",
    )

    shortlist = [
        {
            "id": "owner/repo@unpinned-skill",
            "source_repo": "owner/repo",
            "skill_name": "unpinned-skill",
            "layer": "framework",
            "pinned_sha": "HEAD",
            "description": "Unpinned skill",
            "description_tokens": 8,
        }
    ]

    recorded_shas = []

    def mock_installer(source_repo, pinned_sha, target_dir):
        recorded_shas.append(pinned_sha)
        create_mock_skill(target_dir, "unpinned-skill", tampered=False)
        return True, ""

    result = install.install_skills(
        shortlist=shortlist,
        manifest_hash="man-hash-unpinned",
        skills_dir=skills_dir,
        lockfile_path=lock_file,
        approved_ids=["owner/repo@unpinned-skill"],
        installer_runner=mock_installer,
    )

    assert result["dry_run"] is False
    assert len(result["installed"]) == 1
    assert recorded_shas == ["1234567890abcdef1234567890abcdef12345678"]
    assert (
        result["installed"][0]["pinned_sha"]
        == "1234567890abcdef1234567890abcdef12345678"
    )

