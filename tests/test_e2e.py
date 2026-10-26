import json
import sys
from pathlib import Path

# Ensure scripts can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))

import cache  # noqa: E402
import install  # noqa: E402
import lock  # noqa: E402
import match  # noqa: E402
from scan import scan  # noqa: E402


def test_e2e_scan_match_approve_install_lock(tmp_path):
    # Setup test workspace
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps({
            "name": "my-app",
            "dependencies": {
                "react": "^18.2.0",
                "tailwindcss": "^3.3.0",
            },
            "scripts": {
                "build": "next build",
                "test": "jest",
            },
        }),
        encoding="utf-8",
    )
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "index.tsx").write_text("console.log('hi');\n", encoding="utf-8")

    cache_dir = tmp_path / "cache"
    skills_dir = repo_dir / ".agents" / "skills"
    lockfile_path = repo_dir / "skill-fit.lock"

    # Step 1: Scan
    scan_result = scan(str(repo_dir))
    assert scan_result["hash"] is not None
    assert any(s["name"] == "react" for s in scan_result["stack"])

    # Step 2: Populate offline cache fixture so match doesn't hit real network
    clean_skill_content = (
        "---\nname: react-best\ndescription: React patterns and best practices.\n---\n"
        "# React Best\nGuidance here.\n"
    )
    staging_skill_dir = tmp_path / "staging_react"
    staging_skill_dir.mkdir()
    (staging_skill_dir / "SKILL.md").write_text(clean_skill_content, encoding="utf-8")
    expected_content_hash = lock.compute_content_hash(staging_skill_dir)

    cache.set_cache(
        tags=["react", "tailwindcss"],
        candidates=[
            {
                "id": "facebook/react@react-best",
                "source_repo": "facebook/react",
                "skill_name": "react-best",
                "layer": "framework",
                "found_by_query": "react best practices",
                "install_count": 50000,
                "pinned_sha": "sha-react-123",
                "expected_content_hash": expected_content_hash,
                "meta": {
                    "description": "React patterns and best practices.",
                    "description_tokens": 8,
                    "skill_md_lines": 10,
                    "has_references": False,
                    "has_scripts": False,
                    "last_commit": "2026-03-01T00:00:00Z",
                    "audit_status": "clean",
                },
            }
        ],
        cache_dir=cache_dir,
    )

    # Step 3: Match skills (Resolution Step 2: Cache hit)
    match_result = match.match_skills(
        repo_path=repo_dir,
        cache_dir=cache_dir,
        lockfile_path=lockfile_path,
        offline=True,
    )

    assert match_result["resolution_source"] == "cache"
    assert len(match_result["shortlist"]) == 1
    shortlisted_item = match_result["shortlist"][0]
    assert shortlisted_item["id"] == "facebook/react@react-best"

    # Step 4: Dry-run check (Trust gate)
    dry_run_report = install.install_skills(
        shortlist=match_result["shortlist"],
        manifest_hash=scan_result["hash"],
        skills_dir=skills_dir,
        lockfile_path=lockfile_path,
        approved_ids=[],  # no approval yet
    )
    assert dry_run_report["dry_run"] is True
    assert not lockfile_path.exists()

    # Step 5: Approve and install
    def offline_installer(source_repo, pinned_sha, target_dir):
        dest = target_dir / "react-best"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(clean_skill_content, encoding="utf-8")
        return True, "Installed offline fixture"

    install_report = install.install_skills(
        shortlist=match_result["shortlist"],
        manifest_hash=scan_result["hash"],
        skills_dir=skills_dir,
        lockfile_path=lockfile_path,
        approved_ids=["facebook/react@react-best"],
        installer_runner=offline_installer,
    )

    assert install_report["dry_run"] is False
    assert len(install_report["installed"]) == 1
    assert lockfile_path.exists()

    # Step 6: Verify lockfile
    verify_result = lock.verify_lockfile(lockfile_path, skills_dirs=[skills_dir])
    assert verify_result["verified"] is True
    assert len(verify_result["skills_checked"]) == 1

    # Step 7: Subsequent match uses Resolution Step 1: Lockfile! (Zero network, zero cache)
    subsequent_match = match.match_skills(
        repo_path=repo_dir,
        cache_dir=cache_dir,
        lockfile_path=lockfile_path,
        offline=True,
    )

    assert subsequent_match["resolution_source"] == "lockfile"
    assert len(subsequent_match["shortlist"]) == 1
    assert subsequent_match["shortlist"][0]["id"] == "facebook/react@react-best"
