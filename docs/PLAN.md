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

## Live Skill Matching Extension

### Phase 0: Verification Spike (Blocking) ✅
- Verified `npx skills find` output shape and non-interactive execution
- Verified `npx skills add` commit SHA pinning syntax (`#<sha>` / `@<sha>`)
- Verified `skills.sh/api/search` unauthenticated public endpoint vs 401 on detail endpoint
- Captured GitHub unauthenticated 403 rate-limit response and headers
- Fixtures committed under `tests/fixtures/registry/`

### Phase 1: Tag-to-Query Table ✅
- `skill/references/tag-queries.json` seeded with 19 tags across 8 layers
- `skill/scripts/queries.py` building deduplicated, deterministic, <= 10 query list from scan data
- `no_coverage` list tracking unrecognized tags
- Golden tests: all 6 existing fixtures verified against `tests/golden/queries/`
- Full test suite: 31 passed (0 failures)

