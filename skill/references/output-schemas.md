# Output Schemas

Reference schemas for JSON outputs produced by `context-audit` scripts.

## `scan.py` Output Schema

```json
{
  "project": "string",
  "manifests": ["string"],
  "stack": [
    {
      "name": "string",
      "version": "string",
      "source_file": "string",
      "dev": false
    }
  ],
  "commands": {
    "build": "string",
    "test": "string",
    "lint": "string",
    "dev": "string",
    "run": "string"
  },
  "infra": ["string"],
  "architecture": [
    {
      "hint": "string",
      "confidence": 0.85,
      "evidence": ["string"]
    }
  ],
  "entry_points": ["string"],
  "dir_purposes": {
    "dir_name": "purpose description"
  },
  "avoid": ["string"],
  "hash": "string (sha1)",
  "warnings": ["string"]
}
```

## `audit.py` Output Schema

```json
{
  "skills": [
    {
      "name": "string",
      "scope": "project | user | plugin | custom",
      "path": "string",
      "description_chars": 120,
      "description_tokens": 30,
      "line_count": 25,
      "has_references": true,
      "has_scripts": true,
      "flags": ["description_too_long", "description_overlap", "possibly_irrelevant"],
      "tokens_saved_if_removed": 30
    }
  ],
  "overlaps": [
    {
      "skill_a": "string",
      "skill_b": "string",
      "score": 0.52
    }
  ],
  "total_idle_tokens": 120,
  "warnings": ["string"]
}
```

## `write_map.py` Map Block Schema

Renders between markers inside `CLAUDE.md` or `AGENTS.md`:

```markdown
<!-- context-audit:start hash=XXXX -->
### Project Map: {project}

**Commands**
- `build`: `npm run build`
- `test`: `npm test`

**Entry Points**
- `src/index.ts`

**Directory Purposes**
- `src/`: source code
- `tests/`: test suite

**Avoid / Do Not Edit**
- `package-lock.json`

**Architecture Hints**
- spa [confidence 0.8] (react in deps)
<!-- context-audit:end -->
```
