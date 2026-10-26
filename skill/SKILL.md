---
name: skill-fit
description: Scans repos to optimize agent context with a project map, audits idle token costs to audit my skills, and helps set up skills for my stack.
---
# Skill Fit

Optimize agent context, eliminate repo exploration waste, and audit idle skill costs.

## Workflow
1. **Fingerprint**: Run `python skill/scripts/scan.py .` to extract stack, commands, and entry points.
2. **Audit Skills**: Run `python skill/scripts/audit.py --stack-json <(scan-output)` to inspect idle tokens.
3. **Present Findings**: Display total idle tokens, overlaps, and bloat warnings to the user.
4. **Project Map Approval**: Ask the user before writing the map with `write_map.py . [--create]`.
5. **Skill Actions**: Never remove skills automatically. Always ask user confirmation before acting.

## Conventions Policy
The agent may read at most 3 files to identify conventions, propose at most 3 convention lines,
and must present them for user approval before adding them outside the map markers.
