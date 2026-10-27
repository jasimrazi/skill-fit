---
name: skill-fit
description: Scans a repo and writes a project map, audits idle skill token costs, then offers stack-matched skill recommendations on request. Reduces exploration waste and idle context bloat.
---
# Skill Fit

Optimize agent context: project map, idle skill audit, and optional skill recommendations.

## Workflow

### Act 1 — Scan & Audit
1. Run `python skill/scripts/scan.py .` and save output as `$SCAN`.
2. Run `python skill/scripts/audit.py --stack-json <($SCAN)` (reuse `$SCAN`; do not rescan).
3. Present combined findings: detected stack, commands, entry points, idle token total, overlap flags.
4. Ask two separate yes/no questions — never bundle them:
   - **Write the project map?** → run `write_map.py . --create` on yes.
   - **Act on removal suggestions?** → confirm each flagged skill individually on yes.

### Act 2 — Recommend Skills (on request only)
5. Offer once: *"Want me to search for skills that fit this stack? This makes network calls."*
6. On yes: run `python skill/scripts/match.py . [--offline]`; present ranked shortlist.
7. Install only after per-skill explicit approval via `install.py`. Never install automatically.

## Conventions Policy
Read at most 3 files to identify conventions, propose at most 3 convention lines, and present
them for user approval before adding anything outside the map markers.
