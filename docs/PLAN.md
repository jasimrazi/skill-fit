# Plan

## Phase 0: Scaffolding ✅
- Directory layout
- Fixture repos (react-tailwind, fastapi-docker, flutter, monorepo, malformed-manifest, empty, skills-dir)
- 6 synthetic skills in skills-dir
- Placeholder scripts (scan.py, audit.py, write_map.py output `{}`)
- Failing golden tests

## Phase 1: scan.py ✅
- `_common.py`: token estimate, safe file read, frontmatter parser
- `scan.py`: repo fingerprint -> JSON
- All fixtures match golden output
- Malformed fixture yields warning, not crash
- 70.2 ms on 10k-file repo (< 200 ms target)

## Phase 2: audit.py ✅
- Discover installed skills (project + user + plugin)
- Per-skill metrics and heuristic flags
- skills-dir fixture flags exactly the intended skills with zero false positives on clean ones

## Phase 3: write_map.py ✅
- Template rendering + 600-token ceiling enforcement
- Trimming order: architecture hints, avoid list, dir purposes, entry points, commands
- Marker isolation, idempotence, --dry-run, --check semantics, --create requirement

## Phase 4: SKILL.md + references/ ✅
- `skill/SKILL.md` (<40 lines, description <300 chars with trigger phrases)
- `skill/references/output-schemas.md` containing all JSON schemas
- Self-audit: `audit.py` passes on `skill/` with 0 flags and ~39 idle tokens
- `docs/DESIGN.md`: ASSUMPTIONS, Decisions (budget, hints, trust gate), Self-Audit results
- `README.md`: project guide and before/after benchmark table
