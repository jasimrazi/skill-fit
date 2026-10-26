import json
import sys
from pathlib import Path

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import lock  # noqa: E402


def create_sample_skill_dir(
    base_dir: Path, skill_name: str, description: str = "Test skill"
) -> Path:
    skill_dir = base_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = (
        f"---\nname: {skill_name}\ndescription: {description}\n---\n"
        f"# {skill_name}\nContent here.\n"
    )
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(exist_ok=True)
    (refs_dir / "guide.md").write_text("# Reference Guide\nDetails.\n", encoding="utf-8")
    return skill_dir


def test_content_hash_deterministic(tmp_path):
    skill_dir = create_sample_skill_dir(tmp_path, "my-skill")

    hash1 = lock.compute_content_hash(skill_dir)
    hash2 = lock.compute_content_hash(skill_dir)

    assert hash1.startswith("sha256:")
    assert hash1 == hash2

    # Modifying a file changes content hash
    (skill_dir / "SKILL.md").write_text("Modified content", encoding="utf-8")
    hash3 = lock.compute_content_hash(skill_dir)
    assert hash3 != hash1

    # Adding a file changes content hash
    (skill_dir / "extra.txt").write_text("extra", encoding="utf-8")
    hash4 = lock.compute_content_hash(skill_dir)
    assert hash4 != hash3


def test_lockfile_roundtrip_byte_identical(tmp_path):
    lock_file = tmp_path / "skill-fit.lock"
    data = {
        "schema": 1,
        "manifest_hash": "manifest-hash-123456",
        "resolved_at": "2026-10-26T14:00:00Z",
        "skills": [
            {
                "approved": True,
                "content_hash": "sha256:abc123456789",
                "description_tokens": 15,
                "id": "vercel/next.js@next-best-practices",
                "layer": "framework",
                "pinned_sha": "a1b2c3d4e5f6",
            }
        ],
    }

    raw_bytes = lock.serialize_lockfile(data)
    lock_file.write_bytes(raw_bytes)

    # Read back and re-serialize
    loaded = lock.read_lockfile(lock_file)
    assert loaded == data

    re_serialized = lock.serialize_lockfile(loaded)
    assert re_serialized == raw_bytes


def test_verify_matching_clean(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_dir = create_sample_skill_dir(skills_dir, "clean-skill")
    c_hash = lock.compute_content_hash(skill_dir)

    lock_file = tmp_path / "skill-fit.lock"
    lock_data = {
        "schema": 1,
        "manifest_hash": "hash123",
        "resolved_at": "2026-10-26T14:00:00Z",
        "skills": [
            {
                "id": "owner/repo@clean-skill",
                "layer": "framework",
                "pinned_sha": "sha123",
                "content_hash": c_hash,
                "description_tokens": 10,
                "approved": True,
            }
        ],
    }
    lock_file.write_bytes(lock.serialize_lockfile(lock_data))

    result = lock.verify_lockfile(lock_file, skills_dirs=[skills_dir])
    assert result["verified"] is True
    assert result["drifts"] == []
    assert len(result["skills_checked"]) == 1


def test_verify_detects_hash_drift(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_dir = create_sample_skill_dir(skills_dir, "tampered-skill")
    original_hash = lock.compute_content_hash(skill_dir)

    lock_file = tmp_path / "skill-fit.lock"
    lock_data = {
        "schema": 1,
        "manifest_hash": "hash123",
        "resolved_at": "2026-10-26T14:00:00Z",
        "skills": [
            {
                "id": "owner/repo@tampered-skill",
                "layer": "framework",
                "pinned_sha": "sha123",
                "content_hash": original_hash,
                "description_tokens": 10,
                "approved": True,
            }
        ],
    }
    lock_file.write_bytes(lock.serialize_lockfile(lock_data))

    # Tamper with the skill directory
    (skill_dir / "SKILL.md").write_text("Tampered malicious code", encoding="utf-8")

    result = lock.verify_lockfile(lock_file, skills_dirs=[skills_dir])
    assert result["verified"] is False
    assert len(result["drifts"]) == 1
    drift = result["drifts"][0]
    assert drift["id"] == "owner/repo@tampered-skill"
    assert drift["status"] == "modified"
    assert drift["expected_hash"] == original_hash
    assert drift["actual_hash"] != original_hash


def test_verify_detects_missing_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    lock_file = tmp_path / "skill-fit.lock"
    lock_data = {
        "schema": 1,
        "manifest_hash": "hash123",
        "resolved_at": "2026-10-26T14:00:00Z",
        "skills": [
            {
                "id": "owner/repo@missing-skill",
                "layer": "framework",
                "pinned_sha": "sha123",
                "content_hash": "sha256:000000000000",
                "description_tokens": 10,
                "approved": True,
            }
        ],
    }
    lock_file.write_bytes(lock.serialize_lockfile(lock_data))

    result = lock.verify_lockfile(lock_file, skills_dirs=[skills_dir])
    assert result["verified"] is False
    assert len(result["drifts"]) == 1
    assert result["drifts"][0]["status"] == "missing"


def test_stack_change_prints_diff_and_leaves_file_untouched(tmp_path):
    lock_file = tmp_path / "skill-fit.lock"
    original_lock_data = {
        "schema": 1,
        "manifest_hash": "old-manifest-hash",
        "resolved_at": "2026-10-26T14:00:00Z",
        "skills": [
            {
                "id": "owner/repo@react-skill",
                "layer": "framework",
                "pinned_sha": "sha1",
                "content_hash": "sha256:1111",
                "description_tokens": 10,
                "approved": True,
            }
        ],
    }
    original_bytes = lock.serialize_lockfile(original_lock_data)
    lock_file.write_bytes(original_bytes)

    # Proposed new skills with a NEW manifest hash (stack changed!)
    proposed_skills = [
        {
            "id": "owner/repo@vue-skill",
            "layer": "framework",
            "pinned_sha": "sha2",
            "content_hash": "sha256:2222",
            "description_tokens": 12,
            "approved": True,
        }
    ]

    # Without approval flag, it must NOT write and must report stack change diff
    status, diff = lock.propose_lockfile(
        lockfile_path=lock_file,
        new_manifest_hash="new-manifest-hash",
        proposed_skills=proposed_skills,
        approved=False,
    )

    assert status == "stack_changed"
    assert diff["manifest_hash_changed"] is True
    assert diff["old_manifest_hash"] == "old-manifest-hash"
    assert diff["new_manifest_hash"] == "new-manifest-hash"
    assert len(diff["added_skills"]) == 1
    assert len(diff["removed_skills"]) == 1

    # Verify file was NOT modified!
    assert lock_file.read_bytes() == original_bytes

    # Now with approved=True, it updates the lockfile
    status_appr, _ = lock.propose_lockfile(
        lockfile_path=lock_file,
        new_manifest_hash="new-manifest-hash",
        proposed_skills=proposed_skills,
        approved=True,
    )
    assert status_appr == "written"
    assert lock_file.read_bytes() != original_bytes
    updated = lock.read_lockfile(lock_file)
    assert updated["manifest_hash"] == "new-manifest-hash"
    assert updated["skills"][0]["id"] == "owner/repo@vue-skill"


def test_lock_cli(tmp_path, capsys):
    skills_dir = tmp_path / "skills"
    skill_dir = create_sample_skill_dir(skills_dir, "cli-skill")
    c_hash = lock.compute_content_hash(skill_dir)

    lock_file = tmp_path / "skill-fit.lock"
    proposed_file = tmp_path / "proposed.json"
    proposed_data = [
        {
            "id": "test/cli@cli-skill",
            "layer": "testing",
            "pinned_sha": "commit123",
            "content_hash": c_hash,
            "description_tokens": 8,
            "approved": True,
        }
    ]
    proposed_file.write_text(json.dumps(proposed_data), encoding="utf-8")

    # CLI create with write approval
    ret = lock.main([
        "create",
        "--proposed",
        str(proposed_file),
        "--manifest-hash",
        "hash-cli",
        "--lockfile",
        str(lock_file),
        "--approve",
    ])
    assert ret == 0
    assert lock_file.exists()

    # CLI verify
    ret_v = lock.main([
        "verify",
        "--lockfile",
        str(lock_file),
        "--skills-dir",
        str(skills_dir),
        "--human",
    ])
    assert ret_v == 0
    captured = capsys.readouterr()
    assert "VERIFIED: PASS" in captured.out
