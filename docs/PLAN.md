# Plan

## Phase 0: Scaffolding ✅
- Directory layout
- Fixture repos (react-tailwind, fastapi-docker, flutter, monorepo, malformed-manifest, empty, skills-dir)
- 6 synthetic skills in skills-dir
- Placeholder scripts (scan.py, audit.py, write_map.py output `{}`)
- Failing golden tests

## Phase 1: scan.py ✅
- `_common.py`: token estimate, safe file read, frontmatter parser
- `scan.py`: repo fingerprint → JSON
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
- `skill/references/matching.md` live matching reference (resolution order, flags, timeout behaviour)
- Self-audit: `audit.py` passes on `skill/` with 0 flags and ~34 idle tokens after description update
- `docs/DESIGN.md`: ASSUMPTIONS, Decisions (budget, hints, trust gate), Self-Audit results
- `README.md`: project guide, updated "What it does" table, corrected design principles

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

### Phase 2: Registry Adapter ✅
- `registry.py`: skills.sh public search (primary), npx skills find (fallback), GitHub raw metadata
- Rate-limit handling (403/429 → `partial=true`, clear warning, graceful degradation)
- Timeout handling: `LIVE_SEARCH_WALL_TIMEOUT_S = 20.0`; `handle_timeout()` degrades identically to
  a rate-limit hit (`partial=true`, warning, fall-through to cache / seed catalog)
- ANSI stripping, byte-capped frontmatter fetch (8 KB)
- Fixtures: `github_rate_limit_403.json`, `npx_find_*.txt`, `skills_sh_api_search_fastapi.json`
- Tests: `test_registry.py` — fixture parsing, rate-limit degradation, byte-cap, sanitization
- **Timeout test added**: `test_batch_search_wall_timeout_degrades` — verifies `partial=true` and
  warning emitted when `LIVE_SEARCH_WALL_TIMEOUT_S` is patched to near-zero

### Phase 3: Ranking & Budget Selection ✅
- `rank.py`: multi-factor score (tag overlap, install count, idle token cost, age)
- Greedy token-budget selection within --budget cap (default 1500)
- Auto-relaxes min-installs filter to 0 on empty shortlist

### Phase 4: Lockfile & Cache ✅
- `lock.py`: reads/writes `skill-fit.lock` with `manifest_hash` + pinned SHAs
- `cache.py`: TTL cache (default 7 days) keyed by tag-set fingerprint

### Phase 5: match.py Orchestrator ✅
- 5-step resolution order (lockfile → cache → live → stale cache → seed catalog)
- `--refresh`, `--offline`, `--budget`, `--min-installs`, `--max-queries` flags
- Seed catalog fallback: `references/seed-catalog.json`

### Phase 6: install.py ✅
- SHA-pinned `npx skills add` with non-interactive `-y` flag
- Project-scope and user-scope installs

---

## Known Issue: Live Search Timeouts ⚠️ (built, timing out)

Live search (`match.py` step 3) works end-to-end on fast connections but times out on slow or
metered networks. The timeout now degrades like a rate-limit (partial result, clear warning,
automatic fallback to stale cache or seed catalog) rather than raising an unhandled exception.

**Status:** degradation path is implemented and tested. End-to-end reliability on slow connections
is not yet benchmarked. Do not advertise live matching as reliable until the bench/ results are in.

Next: add a `--timeout` CLI flag to `match.py` so callers can tune the wall-clock budget.
