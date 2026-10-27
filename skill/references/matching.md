# Live Skill Matching Reference

> **Opt-in / experimental.** Matching makes network calls and may time out.
> Nothing runs automatically. The agent offers it once and waits for your answer.

## Resolution Order

Each step is tried in sequence; the first hit is returned with no further network calls.

1. **Lockfile** — `skill-fit.lock` exists and its `manifest_hash` matches the current scan → use
   locked SHAs directly. Zero network.
2. **Cache** — a cache entry for this tag-set exists and is within TTL (default 7 days) → use cached
   candidates. Zero network.
3. **Live search** — query `skills.sh/api/search` (primary) or `npx skills find` (fallback), score
   results, select a greedy shortlist under the token budget, and write the cache.
   - Capped at `--max-queries` live queries (default 4) to bound CLI latency.
   - Total wall-clock budget: 20 s. On timeout: `partial=true`, clear warning, fall through to step 4.
4. **Stale cache** — on any network failure or timeout, fall back to the most recent cache entry with
   `is_stale: true` visible in output.
5. **Bundled seed catalog** — `references/seed-catalog.json` pre-vetted entries matched by tag. Always
   available offline. Returns results for known stacks even on a cold-start with no network.

## Timeout & Degradation

A live-search timeout is treated identically to a rate-limit hit:

- `partial: true` on the Registry object.
- A human-readable warning in `result.warnings` explaining elapsed vs. limit.
- Automatic fall-through to stale cache → seed catalog; never an empty result for known stacks.

## Flags

| Flag | Default | Effect |
|---|---|---|
| `--refresh` | off | Bypass lockfile and cache reads; force a new live search |
| `--offline` | off | Lockfile, cache, and seed catalog only; never touch network |
| `--budget` | 1500 | Idle token budget for greedy shortlist selection |
| `--min-installs` | set in rank.py | Minimum install count; auto-relaxes to 0 on empty shortlist |
| `--max-queries` | 4 | Max concurrent live search queries |
| `--cache-dir` | platform default | Override cache directory location |
| `--human` | off | Print formatted ASCII table instead of JSON |

## Install Step

Approved skills are installed via `install.py`, which calls:

```
npx skills add <owner/repo#sha> --skill <name> -y [--global]
```

SHA pinning is verified before writing `skill-fit.lock`. The lockfile records `manifest_hash`,
pinned SHAs, and a SHA-256 content hash of each installed skill directory.
