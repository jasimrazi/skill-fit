"""Tests for skill/scripts/audit.py.

Phase 0: Tests are written first and EXPECTED TO FAIL because audit.py is a placeholder
that outputs `{}`. Tests assert the real schema audit.py must produce.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
AUDIT_PY = ROOT / "skill" / "scripts" / "audit.py"
SKILLS_DIR = ROOT / "tests" / "fixtures" / "skills-dir"


def run_audit(*extra_args: str) -> dict:
    """Run audit.py with given args and return parsed JSON."""
    cmd = [sys.executable, str(AUDIT_PY)] + list(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"audit.py exited {result.returncode}\nstderr: {result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"audit.py output is not valid JSON: {exc}\nstdout: {result.stdout!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------

def assert_audit_shape(result: dict) -> None:
    required = ["skills", "total_idle_tokens"]
    missing = [k for k in required if k not in result]
    assert not missing, (
        f"audit result is missing required fields: {missing}\n"
        f"Got: {list(result.keys())}"
    )
    assert isinstance(result["skills"], list), "skills must be a list"
    for skill in result["skills"]:
        skill_required = [
            "name", "description_chars", "description_tokens",
            "line_count", "has_references", "has_scripts",
            "flags", "tokens_saved_if_removed", "tokens_saved_if_disabled",
        ]
        missing_s = [k for k in skill_required if k not in skill]
        assert not missing_s, (
            f"Skill entry missing fields {missing_s}: {skill}"
        )


# ---------------------------------------------------------------------------
# skills-dir fixture tests — flags must be exactly right
# ---------------------------------------------------------------------------

def test_audit_skills_dir_shape():
    """audit.py on skills-dir must return well-shaped output."""
    result = run_audit("--skills-dir", str(SKILLS_DIR))
    assert_audit_shape(result)


def test_audit_flags_near_duplicates():
    """skill-alpha and skill-beta have near-duplicate descriptions; must be flagged."""
    result = run_audit("--skills-dir", str(SKILLS_DIR))
    overlaps = result.get("overlaps", [])
    names_in_overlaps = {
        name
        for pair in overlaps
        for name in (pair.get("skill_a"), pair.get("skill_b"))
    }
    assert "python-formatter" in names_in_overlaps or "python-linter" in names_in_overlaps, (
        f"Expected python-formatter/python-linter overlap, got: {overlaps}"
    )


def test_audit_flags_bloated_description():
    """skill-gamma has a 1,456-char description (>1024); must be flagged description_too_long."""
    result = run_audit("--skills-dir", str(SKILLS_DIR))
    gamma = next((s for s in result["skills"] if s["name"] == "db-migration-manager"), None)
    assert gamma is not None, "db-migration-manager skill not found in audit output"
    assert "description_too_long" in gamma["flags"], (
        f"Expected description_too_long flag on db-migration-manager, got: {gamma['flags']}"
    )


def test_audit_no_false_positives_on_clean_skills():
    """skill-epsilon (git-workflow) and skill-zeta (api-testing) must have NO flags."""
    result = run_audit("--skills-dir", str(SKILLS_DIR))
    for skill_name in ("git-workflow", "api-testing"):
        skill = next((s for s in result["skills"] if s["name"] == skill_name), None)
        assert skill is not None, f"{skill_name} not found in audit output"
        assert skill["flags"] == [], (
            f"Expected no flags on {skill_name}, got: {skill['flags']}"
        )


def test_audit_total_idle_tokens_positive():
    """total_idle_tokens must be a positive number."""
    result = run_audit("--skills-dir", str(SKILLS_DIR))
    assert result["total_idle_tokens"] > 0, (
        f"Expected total_idle_tokens > 0, got: {result['total_idle_tokens']}"
    )


def test_audit_flags_possibly_irrelevant_with_stack():
    """When stack-json is supplied, irrelevant skills get flagged possibly_irrelevant."""
    fastapi_stack = ROOT / "tests" / "golden" / "fastapi-docker.json"
    result = run_audit("--skills-dir", str(SKILLS_DIR), "--stack-json", str(fastapi_stack))
    android = next((s for s in result["skills"] if s["name"] == "android-ui-builder"), None)
    assert android is not None, "android-ui-builder not found"
    assert "possibly_irrelevant" in android["flags"], (
        f"Expected possibly_irrelevant flag on android-ui-builder, got: {android['flags']}"
    )
    assert android.get("reason"), "Expected reason explaining absent technologies"
    assert android.get("confidence") is not None and android["confidence"] > 0, (
        f"Expected confidence score, got: {android.get('confidence')}"
    )


def test_audit_general_purpose_skill_never_flagged_with_stack(tmp_path):
    """A skill whose description names no technology from our known vocabulary is never flagged."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Synthetic general-purpose descriptions that name no technology from known vocabulary
    fixtures = [
        ("git-pr-helper", "Automates commit messages, branch naming, and PR summaries."),
        ("markdown-formatter", "Formats markdown tables, fixes list indent, and checks links."),
        ("todo-tracker", "Extracts TODO comments across codebase and renders a summary."),
        ("clean-code-reviewer", "Checks function length, conditionals, and naming style."),
    ]
    for sname, sdesc in fixtures:
        sdir = skills_dir / sname
        sdir.mkdir()
        (sdir / "SKILL.md").write_text(
            f"---\nname: {sname}\ndescription: {sdesc}\n---\n# {sname}\nHelpful guidance.\n",
            encoding="utf-8",
        )

    fastapi_stack = ROOT / "tests" / "golden" / "fastapi-docker.json"
    result = run_audit("--skills-dir", str(skills_dir), "--stack-json", str(fastapi_stack))

    for skill in result["skills"]:
        assert "possibly_irrelevant" not in skill.get("flags", []), (
            f"General-purpose skill {skill['name']} was wrongly flagged: {skill['flags']}"
        )


def test_audit_absent_technology_flagged_with_reason_and_confidence(tmp_path):
    """A skill naming an absent technology is flagged with reason and confidence."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Synthetic skill naming technologies absent from Python FastAPI (e.g. Flutter / Dart)
    sdir = skills_dir / "flutter-ui-kit"
    sdir.mkdir()
    (sdir / "SKILL.md").write_text(
        "---\nname: flutter-ui-kit\n"
        "description: Builds responsive Flutter widgets and screen layouts in Dart.\n---\n"
        "# Flutter\n",
        encoding="utf-8",
    )

    fastapi_stack = ROOT / "tests" / "golden" / "fastapi-docker.json"
    result = run_audit("--skills-dir", str(skills_dir), "--stack-json", str(fastapi_stack))

    target = next((s for s in result["skills"] if s["name"] == "flutter-ui-kit"), None)
    assert target is not None, "flutter-ui-kit not found in audit output"
    assert "possibly_irrelevant" in target["flags"]
    assert "flutter" in target["reason"].lower() or "dart" in target["reason"].lower()
    assert isinstance(target["confidence"], (int, float)) and target["confidence"] > 0


def test_audit_skill_fit_never_flagged_by_own_audit():
    """skill-fit itself is never flagged as irrelevant by its own audit."""
    for golden_name in ("fastapi-docker.json", "react-tailwind.json", "flutter.json", "empty.json"):
        stack_file = ROOT / "tests" / "golden" / golden_name
        result = run_audit("--skills-dir", str(ROOT / "skill"), "--stack-json", str(stack_file))
        skill_fit = next(
            (s for s in result["skills"] if s["name"] in ("skill-fit", "skill_fit")), None
        )
        if skill_fit is not None:
            assert "possibly_irrelevant" not in skill_fit.get("flags", []), (
                f"skill-fit was flagged as irrelevant on {golden_name}: {skill_fit.get('flags')}"
            )


def test_audit_never_deletes_or_modifies():
    """audit.py is read-only: running it twice must not change any file on disk."""
    import hashlib

    def file_hash(path: Path) -> str:
        return hashlib.sha1(path.read_bytes()).hexdigest()

    skill_files = list(SKILLS_DIR.rglob("SKILL.md"))
    before = {f: file_hash(f) for f in skill_files}

    run_audit("--skills-dir", str(SKILLS_DIR))
    run_audit("--skills-dir", str(SKILLS_DIR))

    after = {f: file_hash(f) for f in skill_files}
    assert before == after, "audit.py modified skill files on disk!"


def test_disabled_skill_is_not_loaded(tmp_path):
    """A skill disabled via SKILL.md.disabled is ignored and consumes 0 idle tokens."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    active_dir = skills_dir / "active-skill"
    active_dir.mkdir()
    (active_dir / "SKILL.md").write_text(
        "---\nname: active\ndescription: Active skill\n---\n# Active",
        encoding="utf-8",
    )

    disabled_dir = skills_dir / "disabled-skill"
    disabled_dir.mkdir()
    (disabled_dir / "SKILL.md.disabled").write_text(
        "---\nname: disabled\ndescription: Disabled skill\n---\n# Disabled",
        encoding="utf-8",
    )

    result = run_audit("--skills-dir", str(skills_dir))
    names = [s["name"] for s in result["skills"]]
    assert "active" in names
    assert "disabled" not in names
    assert len(result["skills"]) == 1
    skill_entry = result["skills"][0]
    assert skill_entry["tokens_saved_if_disabled"] == skill_entry["tokens_saved_if_removed"]


def test_audit_discovers_multi_agent_project_paths(tmp_path):
    """audit.py discovers skills in .opencode/skills and .cursor/skills."""
    opencode_dir = tmp_path / ".opencode" / "skills" / "oc-skill"
    opencode_dir.mkdir(parents=True)
    (opencode_dir / "SKILL.md").write_text(
        "---\nname: oc-skill\ndescription: OpenCode skill\n---\n# OC",
        encoding="utf-8",
    )

    cursor_dir = tmp_path / ".cursor" / "skills" / "cursor-skill"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "SKILL.md").write_text(
        "---\nname: cursor-skill\ndescription: Cursor skill\n---\n# Cursor",
        encoding="utf-8",
    )

    result = run_audit("--path", str(tmp_path))
    names = [s["name"] for s in result["skills"]]
    assert "oc-skill" in names
    assert "cursor-skill" in names
    assert len(result["skills"]) >= 2


