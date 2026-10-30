<p align="center">
  <img src="docs/assets/logo.png" alt="skill-fit logo" width="420" />
</p>

<h1 align="center">skill-fit</h1>

<p align="center">
  <strong>Cut Claude Code token waste: a compact project map in <code>CLAUDE.md</code> / <code>AGENTS.md</code>, plus an audit of your idle agent skills.</strong>
</p>

<p align="center">
  <a href="https://github.com/jasimrazi/skill-fit/actions"><img src="https://github.com/jasimrazi/skill-fit/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="Python 3.8+" /></a>
</p>

`skill-fit` is an agent skill that scans your repo with plain Python (no model tokens, no dependencies), writes a project map of at most 600 estimated tokens into your `CLAUDE.md` or `AGENTS.md`, and shows which installed skills cost the most context on every turn.

**Status:** pre-release (v0.1). Scan, audit, and map writing work today. Skill matching is opt-in and experimental ([details](#skill-matching-opt-in-experimental)).

## Quick start

```bash
npx skills add jasimrazi/skill-fit --skill skill-fit       # this project
npx skills add jasimrazi/skill-fit --skill skill-fit -g    # all your projects
```

Then ask your agent: *"audit my agent context"* or *"summarize this project for future sessions"*. It shows its findings and asks before writing anything.

Needs Python 3.8+ and Node (for the CLI). To install by hand, copy `skill/` to `.claude/skills/skill-fit` (or `~/.claude/skills/skill-fit` for all projects).

## What you get

| Script | Result |
|---|---|
| `scan.py` | Fingerprints the repo: stack and versions, runnable commands (`build`, `test`, `lint`, `dev`, `run`), entry points, infra, and architecture hints with confidence scores. |
| `audit.py` | Lists installed skills with their idle token cost, and flags oversized descriptions, overlapping skills, and skills that don't match your stack. |
| `write_map.py` | Writes the project map between marker comments in `CLAUDE.md` or `AGENTS.md`, and checks whether it has gone stale. |

<details>
<summary>Run the scripts directly</summary>

```bash
python3 skill/scripts/scan.py /path/to/repo --human
python3 skill/scripts/audit.py --human
python3 skill/scripts/write_map.py . --target AGENTS.md --dry-run   # preview the diff
python3 skill/scripts/write_map.py . --target AGENTS.md --create    # write or update the block
python3 skill/scripts/write_map.py . --target AGENTS.md --check     # exit 0 if current, 1 if stale
```

Output is JSON by default. `--human` prints a table.
</details>

## Why

Two things waste tokens in agentic coding:

1. **Exploration.** With no project summary, an agent spends turns on `ls`, `find`, and `cat` to learn how to build and test the repo, and sometimes guesses the commands wrong.
2. **Idle skills.** Every installed skill's name and description is loaded on every turn, used or not, and overlapping skills can contradict each other.

## Safe by default

- **Deterministic.** The same repo gives the same output every time.
- **Read-only.** Scanning and auditing never write. The map writer changes only its marked block and supports `--dry-run`.
- **Reads little.** `scan.py` reads manifests, config files, and directory names, never source file contents.
- **No network** unless you ask for skill matching.
- **Hints, not facts.** Flags carry evidence, and nothing is ever removed or disabled automatically.
- **Labeled estimates.** Token counts are characters divided by 4. Use your agent's own context report for exact numbers.

To uninstall, delete the skill folder and the marked block in your `CLAUDE.md` / `AGENTS.md`.

## Skill matching (opt-in, experimental)

Ask your agent to suggest skills for the project. `skill-fit` builds search queries from your detected stack, checks that each candidate exists and fits the stack, and shows a shortlist: Strong, Possible, and Rejected with reasons. It makes network calls (skills.sh and GitHub), installs nothing without your approval for each skill, and treats fetched descriptions as untrusted text.

Suggestion quality is still being evaluated. Treat results as leads, and read a skill before you install it.

## Benchmarks

| Measured | Result |
|---|---|
| Scan time, 10,000-file repo | about 70 ms (author's machine) |
| Project map size | at most 600 estimated tokens (enforced) |

**End-to-end token savings are not yet measured.** They depend on the repo, the task, and the model, so no percentage is claimed. The planned method is in [`bench/`](bench/): fixed tasks, several conditions, 3 or more repetitions, and task success reported next to token counts.

## FAQ

**Does it cost model tokens?** Scanning, auditing, and map writing run locally and spend none. Your agent still reads the map, which is capped at 600 estimated tokens.

**CLAUDE.md or AGENTS.md?** Both work: use `--target CLAUDE.md` or `--target AGENTS.md`.

**Can I run it in CI?** Yes. `write_map.py . --target AGENTS.md --check` exits 0 if the map is current and 1 if it is stale.

**Does it work with Cursor, Codex, or other agents?** The map is plain Markdown, so any agent that reads `AGENTS.md` can use it. The skill itself is built for Claude Code. Other agents are untested.

## Limitations

- Token counts are estimates.
- Overlap and "possibly irrelevant" flags are heuristics. Review them before removing anything.
- Architecture hints come from folder names and carry a confidence score.
- Skill locations are searched best-effort. Use `--skills-dir` for non-standard setups.
- Matching is experimental. It can suggest irrelevant skills or nothing at all. Live search has a 20-second limit and then falls back to a cache or a bundled seed catalog. Unauthenticated GitHub requests are limited to 60 per hour. The install path has known issues that are being fixed.

## Roadmap

Planned, not promised.

- **Next:** measured suggestion quality, a hardened install path, an end-to-end benchmark.
- **v0.2:** check documented commands against the repo's actual scripts, lint `CLAUDE.md` for size and duplication, reversible pruning (move flagged skills aside, never delete), CI mode with a token threshold.
- **v0.3:** optional usage evidence from local session transcripts, a drift hook for stale maps.

## Related tools

- [skillstat](https://github.com/no7z/skillstat): reads session transcripts to find skills that never fire.
- [SkillScope](https://github.com/arsyadal/SkillScope): toggles skills per project using a manifest.

`skill-fit` takes a different angle: a deterministic repo map plus a static audit, with no model in the loop.

## Development

```bash
pip install -e ".[dev]"
pytest -v
ruff check skill/ tests/
```

Only `skill/` is installed into your agent. Tests, fixtures, and benchmarks stay out of its context. Design notes are in [`docs/DESIGN.md`](docs/DESIGN.md). Tests run against fixture repos and never touch your real home directory or installed skills.

## License

MIT. See [LICENSE](LICENSE).