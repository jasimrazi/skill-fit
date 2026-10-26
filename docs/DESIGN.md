# Design Decisions

## ASSUMPTIONS

The following items were checked against local environments and authoritative specifications
as required by the project's verification constraints:

### Skill Discovery Locations

| Ecosystem | Scope | Path | Status |
|---|---|---|---|
| **Antigravity** | Project | `.agents/skills/<name>/SKILL.md` (and aliases `.agent/`, `_agents/`) | Verified via local CLI builtin docs |
| **Antigravity** | User | `~/.gemini/config/skills/<name>/SKILL.md` | Verified via local CLI builtin docs |
| **Antigravity** | Plugins | `.agents/plugins/<p>/skills/`, `~/.gemini/config/plugins/<p>/skills/` | Verified via local CLI builtin docs |
| **Claude Code** | Project | `.claude/skills/<name>/SKILL.md` | Documented standard (agentskills.io) |
| **Claude Code** | User | `~/.claude/skills/<name>/SKILL.md` | Documented standard (agentskills.io) |

**Assumption Note:** `.claude/skills` and `~/.claude/skills` adhere to the common Agent Skills
specification (`agentskills.io`). On the local machine, `~/.claude/` exists but neither
`~/.claude/skills` nor user-level `~/.gemini/config/skills` were pre-installed; `audit.py`
gracefully handles missing directories without error.

### Common Denominator Frontmatter

Both ecosystems parse YAML frontmatter delimited by `---`:
- Required: `name` (alphanumeric/hyphenated string matching directory name)
- Required: `description` (non-empty summary explaining what the skill does and when to activate it)
Extended fields (`allowed-tools`, `model`, `version`) are supported optionally by Claude Code
and safely ignored by standard Antigravity parsers.

---

## Decisions

### 1. Token Budgeting & Progressive Trimming
- **Scan Fingerprint Output (`scan.py`)**: Capped at ~1,500 estimated tokens (`len // 4`). Truncation markers (`+N more packages`) are added if dependencies or manifests threaten context bloat.
- **Project Map Output (`write_map.py`)**: Strictly capped at 600 estimated tokens.
- **Trimming Priority**: When the project map exceeds 600 tokens, sections are omitted from lowest to highest value:
  1. `architecture hints` (trimmed first)
  2. `avoid list`
  3. `directory purposes`
  4. `entry points`
  5. `commands` (trimmed last, as commands represent the highest leverage for agents)

### 2. Confidence Hints Architecture
- Architecture patterns (`monorepo`, `spa`, `api_server`, `containerized`, `layered`) are never stated as absolute facts.
- Every architectural finding is emitted as a heuristic hint with a numeric `confidence` (0.0 to 1.0) and an explicit `evidence` list citing concrete manifests or directory structures.

### 3. Trust Gate & Safe Mutation
- **Zero Silent Modification**: All scripts are strictly read-only by default.
- **Map Marker Isolation**: `write_map.py` only ever modifies text between `<!-- context-audit:start hash=... -->` and `<!-- context-audit:end -->`. User-authored notes outside markers are preserved verbatim.
- **Check-Before-Write**: Target files must exist or be explicitly authorized via `--create`.
- **Conventions Policy**: An agent running `skill-fit` may read at most 3 files to identify repo conventions, propose at most 3 convention lines, and must present them for user approval before appending them outside the map markers.
- **No Automatic Skill Removal**: `audit.py` identifies candidates (`possibly_irrelevant`, `description_overlap`, `description_too_long`), but never deletes or alters skills.

---

## Self-Audit Results

`skill-fit` executed `audit.py` against its own `skill/` directory:

```json
{
  "skills": [
    {
      "name": "skill-fit",
      "scope": "custom",
      "path": "skill",
      "description_chars": 139,
      "description_tokens": 34,
      "line_count": 18,
      "has_references": true,
      "has_scripts": true,
      "flags": [],
      "tokens_saved_if_removed": 34
    }
  ],
  "overlaps": [],
  "total_idle_tokens": 34,
  "warnings": []
}
```

- **Description length**: 139 characters (limit: 1,024) -> **Passed**
- **SKILL.md lines**: 18 lines (limit: 40) -> **Passed**
- **Trigger phrases included**: "optimize agent context", "audit my skills", "set up skills for my stack" -> **Passed**
- **Flags**: 0 warnings, 0 overlaps -> **Passed**
- **Baseline idle footprint**: ~34 tokens -> **Minimal**
