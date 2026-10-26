# Live Skill Matching Reference

## Resolution Order
1. **Lockfile Hit**: If `skill-fit.lock` exists and its `manifest_hash` matches the scanner's current hash, use the lockfile. Zero network calls.
2. **Cache Hit**: If cache entry for the repository's stack tags exists and is within TTL (default 7 days), use cached candidates. Zero network calls.
3. **Live Search**: Query registry adapters (`skills.sh`, GitHub raw, or `npx skills`), score candidates deterministically, select greedy shortlist under token budget, and write cache.
4. **Degraded Mode**: On network failure, fall back to stale cache with a visible `is_stale: true` marker. Never guess or invent candidates.
5. **Install & Lock**: Present shortlist to user via trust gate. On approval, install via pinned SHA, recompute SHA-256 directory content hash, quarantine if tampered, and write `skill-fit.lock`.

## Flags
- `--refresh`: Bypass lockfile and cache reads.
- `--offline`: Only use lockfile and cache; never touch network.
- `--budget`: Idle token budget (default 1500).
- `--cache-dir`: Override cache directory location.
- `--human`: Print formatted ASCII tables.
