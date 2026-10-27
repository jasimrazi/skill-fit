#!/usr/bin/env python3
"""Installed skills -> idle cost, overlaps, bloat.

Usage:
    audit.py [--stack-json FILE] [--overlap-threshold 0.4] [--skills-dir DIR] [--path DIR] [--human]

Output fields (JSON):
    skills[]:
        name, scope, path, description_chars, description_tokens,
        line_count, has_references, has_scripts, flags[], tokens_saved_if_removed
    overlaps[]:
        skill_a, skill_b, score
    total_idle_tokens: int
    warnings[]: list[str]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

# Bootstrap _common import
sys.path.insert(0, os.path.dirname(__file__))
from _common import parse_frontmatter, safe_read, token_estimate  # noqa: E402

# ---------------------------------------------------------------------------
# Stopwords for Jaccard description overlap
# ---------------------------------------------------------------------------
STOPWORDS: frozenset = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "did", "do", "does", "doing", "don't", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is",
    "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't",
    "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "should", "so", "some", "such", "than", "that", "the", "their",
    "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we",
    "were", "weren't", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "won't", "would", "you", "your", "yours", "yourself", "yourselves",
    # Skill-specific generic filler verbs
    "skill", "use", "using", "used", "run", "running", "activate", "action",
    "needed", "need", "provides", "provide", "guidance", "instructions",
})


def _tokenize(text: str) -> Set[str]:
    """Tokenize text into lowercase alphanumeric words, minus stopwords."""
    words = re.findall(r"\b[a-z0-9_-]+\b", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _find_skills_in_dir(parent_dir: str, scope: str) -> List[Tuple[str, str]]:
    """Scan subdirectories of parent_dir for SKILL.md. Returns (skill_dir, scope)."""
    skills: List[Tuple[str, str]] = []
    if not os.path.isdir(parent_dir):
        return skills

    # Direct skill directory?
    if os.path.isfile(os.path.join(parent_dir, "SKILL.md")):
        return [(parent_dir, scope)]

    try:
        entries = sorted(os.scandir(parent_dir), key=lambda e: e.name)
    except OSError:
        return skills

    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            skill_md = os.path.join(entry.path, "SKILL.md")
            if os.path.isfile(skill_md):
                skills.append((entry.path, scope))
    return skills


def discover_skills(
    project_root: str, explicit_skills_dir: Optional[str] = None
) -> List[Tuple[str, str]]:
    """Find installed skills across project, user, and plugin locations."""
    if explicit_skills_dir:
        return _find_skills_in_dir(explicit_skills_dir, "custom")

    found: List[Tuple[str, str]] = []
    seen_paths: Set[str] = set()

    def add_skills(dir_path: str, scope: str) -> None:
        for s_path, s_scope in _find_skills_in_dir(dir_path, scope):
            norm = os.path.normcase(os.path.abspath(s_path))
            if norm not in seen_paths:
                seen_paths.add(norm)
                found.append((s_path, s_scope))

    # Project-level locations
    for sub in [".agents/skills", ".agent/skills", "_agents/skills", ".claude/skills"]:
        add_skills(os.path.join(project_root, sub), "project")

    # Project plugins
    proj_plugins = os.path.join(project_root, ".agents", "plugins")
    if os.path.isdir(proj_plugins):
        try:
            for p_entry in os.scandir(proj_plugins):
                if p_entry.is_dir(follow_symlinks=False):
                    add_skills(os.path.join(p_entry.path, "skills"), "plugin")
        except OSError:
            pass

    # User-level locations
    home = os.path.expanduser("~")
    add_skills(os.path.join(home, ".gemini", "config", "skills"), "user")
    add_skills(os.path.join(home, ".claude", "skills"), "user")

    # User plugins
    user_plugins = os.path.join(home, ".gemini", "config", "plugins")
    if os.path.isdir(user_plugins):
        try:
            for p_entry in os.scandir(user_plugins):
                if p_entry.is_dir(follow_symlinks=False):
                    add_skills(os.path.join(p_entry.path, "skills"), "plugin")
        except OSError:
            pass

    return found


# ---------------------------------------------------------------------------
# Stack keywords extraction & Known Vocabulary
# ---------------------------------------------------------------------------

# Known technology vocabulary: specific frameworks, platforms, languages, and tools.
# A skill is only evaluated for stack relevance if its description names at least one of these.
KNOWN_TECH_VOCABULARY: frozenset = frozenset({
    # Mobile & UI platforms
    "android", "ios", "flutter", "react-native", "swiftui", "swift", "kotlin", "objc",
    # Frontend frameworks & styling
    "react", "vue", "angular", "svelte", "sveltekit", "next", "nextjs", "nuxt",
    "astro", "remix", "tailwind", "tailwindcss",
    # Backend frameworks & runtimes
    "fastapi", "django", "flask", "express", "nestjs", "fastify", "rails",
    "spring", "spring-boot", "gin", "fiber", "echo", "axum", "actix",
    # Languages & ecosystems
    "python", "rust", "golang", "go", "ruby", "java", "scala", "elixir", "php",
    "csharp", "dotnet", "typescript", "javascript", "dart",
    # Data & persistence
    "postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis",
    "prisma", "drizzle", "typeorm", "sqlalchemy", "alembic",
    # Infrastructure & containerization
    "docker", "kubernetes", "k8s", "terraform", "ansible",
    # Testing
    "pytest", "jest", "vitest", "cypress", "playwright",
})


def _extract_stack_keywords(stack_json_path: str) -> Tuple[Set[str], Optional[str]]:
    """Load stack JSON and extract package/technology keywords."""
    content, err = safe_read(stack_json_path)
    if err:
        return set(), f"Cannot read stack JSON: {err}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return set(), f"Invalid JSON in stack file: {exc}"

    keywords: Set[str] = set()
    # Check stack list
    stack_list = data.get("stack") if isinstance(data, dict) else None
    if isinstance(stack_list, list):
        for item in stack_list:
            if isinstance(item, dict) and "name" in item:
                keywords.update(_tokenize(str(item["name"])))
            elif isinstance(item, str):
                keywords.update(_tokenize(item))

    # Check manifests
    manifests = data.get("manifests") if isinstance(data, dict) else None
    if isinstance(manifests, list):
        for m in manifests:
            m_lower = str(m).lower()
            keywords.update(_tokenize(m_lower))
            if any(p in m_lower for p in ("requirements", "pyproject", "setup.py", "pipfile")):
                keywords.add("python")
            elif any(p in m_lower for p in ("package.json", "tsconfig", "yarn.lock")):
                keywords.update({"javascript", "typescript", "node", "npm"})
            elif "cargo" in m_lower:
                keywords.update({"rust", "cargo"})
            elif "go.mod" in m_lower or "go.sum" in m_lower:
                keywords.update({"go", "golang"})
            elif "pubspec" in m_lower:
                keywords.update({"flutter", "dart"})

    # Check commands
    cmds = data.get("commands") if isinstance(data, dict) else None
    if isinstance(cmds, dict):
        for cmd_val in cmds.values():
            keywords.update(_tokenize(str(cmd_val)))

    # Check infra
    infra = data.get("infra") if isinstance(data, dict) else None
    if isinstance(infra, list):
        for inf in infra:
            keywords.update(_tokenize(str(inf)))

    # Check architecture
    arch = data.get("architecture") if isinstance(data, dict) else None
    if isinstance(arch, list):
        for a in arch:
            if isinstance(a, dict) and "hint" in a:
                keywords.update(_tokenize(str(a["hint"])))

    # Check entry points
    entry_points = data.get("entry_points") if isinstance(data, dict) else None
    if isinstance(entry_points, list):
        for ep in entry_points:
            ep_lower = str(ep).lower()
            keywords.update(_tokenize(ep_lower))
            if ep_lower.endswith(".py"):
                keywords.add("python")
            elif ep_lower.endswith((".ts", ".tsx")):
                keywords.update({"typescript", "javascript"})
            elif ep_lower.endswith((".js", ".jsx")):
                keywords.add("javascript")
            elif ep_lower.endswith(".rs"):
                keywords.add("rust")
            elif ep_lower.endswith(".go"):
                keywords.update({"go", "golang"})
            elif ep_lower.endswith(".dart"):
                keywords.update({"flutter", "dart"})

    return keywords, None


# ---------------------------------------------------------------------------
# Audit core
# ---------------------------------------------------------------------------

def audit(
    project_root: str = ".",
    skills_dir: Optional[str] = None,
    stack_json: Optional[str] = None,
    overlap_threshold: float = 0.4,
) -> Dict[str, Any]:
    warnings: List[str] = []
    discovered = discover_skills(project_root, explicit_skills_dir=skills_dir)

    stack_keywords: Optional[Set[str]] = None
    if stack_json:
        stack_keywords, kw_err = _extract_stack_keywords(stack_json)
        if kw_err:
            warnings.append(kw_err)

    skills_data: List[Dict[str, Any]] = []
    token_sets: List[Tuple[str, Set[str]]] = []  # (skill_name, tokens)

    for skill_path, scope in discovered:
        skill_md_path = os.path.join(skill_path, "SKILL.md")
        content, err = safe_read(skill_md_path)
        if err:
            warnings.append(f"{skill_path}: read error: {err}")
            continue

        frontmatter = parse_frontmatter(content)
        name = frontmatter.get("name") or os.path.basename(skill_path)
        description = frontmatter.get("description", "").strip()

        desc_chars = len(description)
        desc_tokens = token_estimate(description)
        lines = content.splitlines()
        line_count = len(lines)

        has_references = os.path.isdir(os.path.join(skill_path, "references"))
        has_scripts = os.path.isdir(os.path.join(skill_path, "scripts"))

        flags: List[str] = []
        if not description:
            flags.append("missing_description")
        elif desc_chars > 1024:
            flags.append("description_too_long")

        if line_count > 300:
            flags.append("skill_md_too_long")

        # tokens saved if removed = idle tokens of this skill (its description tokens)
        tokens_saved = desc_tokens

        skills_data.append({
            "name": name,
            "scope": scope,
            "path": skill_path.replace(os.sep, "/"),
            "description_chars": desc_chars,
            "description_tokens": desc_tokens,
            "line_count": line_count,
            "has_references": has_references,
            "has_scripts": has_scripts,
            "flags": flags,
            "reason": None,
            "confidence": None,
            "tokens_saved_if_removed": tokens_saved,
            "_tokens": _tokenize(f"{name} {description}"),
        })
        token_sets.append((name, _tokenize(description)))

    # Compute pairwise description overlaps (Jaccard similarity)
    overlaps: List[Dict[str, Any]] = []
    n = len(skills_data)
    overlapping_names: Set[str] = set()

    for i in range(n):
        for j in range(i + 1, n):
            set_a = token_sets[i][1]
            set_b = token_sets[j][1]
            score = jaccard_similarity(set_a, set_b)
            if score >= overlap_threshold:
                name_a = skills_data[i]["name"]
                name_b = skills_data[j]["name"]
                overlaps.append({
                    "skill_a": name_a,
                    "skill_b": name_b,
                    "score": round(score, 3),
                })
                overlapping_names.add(name_a)
                overlapping_names.add(name_b)

    for skill in skills_data:
        if skill["name"] in overlapping_names:
            skill["flags"].append("description_overlap")

    # Stack relevance heuristic (if stack_keywords provided)
    if stack_keywords is not None and len(stack_keywords) > 0:
        for skill in skills_data:
            # skill-fit itself is never flagged as irrelevant by its own audit
            if skill["name"] in ("skill-fit", "skill_fit"):
                continue

            s_tokens = skill["_tokens"]
            # A skill is only evaluated for stack relevance if it explicitly names a technology
            # from our known technology vocabulary.
            named_tech = {t for t in s_tokens if t in KNOWN_TECH_VOCABULARY}

            # Rule 1: A skill whose description names no technology from
            # our known vocabulary is never flagged
            if not named_tech:
                continue

            # Rule 2: If it names technologies from the vocabulary, check if any match the stack.
            overlap = named_tech & stack_keywords
            if not overlap:
                absent_list = sorted(list(named_tech))
                absent_str = ", ".join(absent_list)
                skill["flags"].append("possibly_irrelevant")
                skill["reason"] = f"Names technology absent from detected stack: {absent_str}"
                skill["confidence"] = 0.85

    # Clean internal temporary keys
    for skill in skills_data:
        skill.pop("_tokens", None)

    total_idle_tokens = sum(s["tokens_saved_if_removed"] for s in skills_data)

    return {
        "skills": skills_data,
        "overlaps": overlaps,
        "total_idle_tokens": total_idle_tokens,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Human output
# ---------------------------------------------------------------------------

def _human_output(data: Dict[str, Any]) -> str:
    lines = [
        "=" * 70,
        f"Installed Skills Audit  (Total Idle Cost: ~{data.get('total_idle_tokens', 0)} tokens)",
        "=" * 70,
        f"{'Skill':<25} {'Scope':<8} {'Chars':<7} {'Tokens':<8} {'Lines':<7} {'Flags'}",
        "-" * 70,
    ]
    for s in data.get("skills", []):
        flag_str = ", ".join(s.get("flags", [])) if s.get("flags") else "-"
        if s.get("confidence") is not None:
            flag_str += f" (conf: {s['confidence']:.2f})"
        lines.append(
            f"{s['name']:<25} {s['scope']:<8} {s['description_chars']:<7} "
            f"{s['description_tokens']:<8} {s['line_count']:<7} {flag_str}"
        )
        if s.get("reason"):
            lines.append(f"    |-- reason: {s['reason']}")

    if data.get("overlaps"):
        lines.append("")
        lines.append("-- Description Overlaps " + "-" * 46)
        for ov in data["overlaps"]:
            lines.append(f"  {ov['skill_a']} <-> {ov['skill_b']} (score: {ov['score']:.2f})")

    if data.get("warnings"):
        lines.append("")
        lines.append("-- Warnings " + "-" * 58)
        for w in data["warnings"]:
            lines.append(f"  WARN  {w}")

    lines.append("=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit installed skills for idle token cost and bloat."
    )
    parser.add_argument(
        "--stack-json", dest="stack_json", default=None, help="Path to scan.py JSON output"
    )
    parser.add_argument(
        "--overlap-threshold", dest="overlap_threshold", type=float, default=0.4,
        help="Jaccard similarity threshold for description overlap (default 0.4)",
    )
    parser.add_argument(
        "--skills-dir", dest="skills_dir", default=None, help="Explicit skills directory to audit"
    )
    parser.add_argument(
        "--path", dest="path", default=".", help="Base project directory to search (default .)"
    )
    parser.add_argument(
        "--human", dest="human", action="store_true", help="Print human-readable table"
    )

    args = parser.parse_args()
    result = audit(
        project_root=args.path,
        skills_dir=args.skills_dir,
        stack_json=args.stack_json,
        overlap_threshold=args.overlap_threshold,
    )

    if args.human:
        print(_human_output(result))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
