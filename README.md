<p align="center">
  <img src="docs/assets/logo.png" alt="skill-fit logo" width="680" />
</p>


<h1 align="center">skill-fit</h1>

<h3 align="center">Cut Claude Code Token Waste with an Automated Project Map & Skill Audit</h3>

<p align="center">
  <strong>Deterministic context engineering for Claude Code, Cursor, and AI agents. Generates compact <code>CLAUDE.md</code> / <code>AGENTS.md</code> project maps and eliminates idle skill token bloat.</strong>
</p>

> **TL;DR:** `skill-fit` is an open-source agent skill and CLI tool that scans your codebase in ~70ms, writes a compact project map (<600 tokens) into `CLAUDE.md` or `AGENTS.md`, and audits installed skills for idle context costs, overlapping triggers, and tech-stack mismatches. Zero model tokens are consumed during scanning, and core scripts run completely offline with zero runtime dependencies.

[![CI](https://github.com/jasimrazi/skill-fit/actions/workflows/ci.yml/badge.svg)](https://github.com/jasimrazi/skill-fit/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Standards: CLAUDE.md | AGENTS.md](https://img.shields.io/badge/standards-CLAUDE.md%20%7C%20AGENTS.md-blueviolet.svg)](#what-it-reads-and-writes)
[![Zero Model Tokens](https://img.shields.io/badge/model%20tokens-0%20spent-success.svg)](#design-principles)

**Status:** pre-release (v0.1). Scanning, auditing, and map writing work today. Skill matching is built but opt-in and experimental; live search is known to time out on slow connections (see [Limitations](#limitations)).

## Why: Solving Context Window Exhaustion in Claude Code

Two invisible issues cause severe token waste and prompt bloat in agentic coding environments like Claude Code:

1. **Exploration loops.** An agent without a project summary spends 3–6 turns running `ls`, `find`, and `cat` just to understand how the repo is structured, which package manager is used, and how to execute tests—often guessing commands incorrectly.
2. **Idle skill context tax.** Every installed agent skill loads its name and description into your system prompt on **every single turn**, whether relevant to the current task or not. Overlapping or unused skills silently burn hundreds of tokens per turn and introduce conflicting instructions.

`skill-fit` solves both challenges using fast, deterministic Python scripts with zero LLM API overhead: your agent reads a verified, compact project map instead of wandering.

## Comparison: How skill-fit Compares

| Feature | `skill-fit` | Manual `CLAUDE.md` | Context Dumpers (e.g. Repomix) | Transcript Analyzers (e.g. Skillstat) |
|---|---|---|---|---|
| **Approach** | Fast AST & manifest parsing | Hand-written notes | Full-repo concatenation | Session transcript log mining |
| **Model Tokens to Run** | **0 tokens (100% offline)** | 0 tokens | 0 tokens (huge prompt output) | 0 tokens |
| **Output Token Size** | **Enforced ceiling (<600 tokens)** | Unpredictable (prone to bloat) | 20k–100k+ tokens (context overload) | N/A (report only) |
| **Skill Bloat Audit** | **Yes** (idle cost, overlaps, stack fit) | No | No | Yes (post-session usage) |
| **Safe Isolation** | **Yes** (updates only marked block) | Manual editing | Overwrites or dumps to prompt | File moves |
| **Runtime Dependencies** | **None** (Python 3.8+ stdlib) | None | Node.js / CLI packages | Python / CLI packages |

## What it does (v0.1)

| Script | Job |
|---|---|
| `scan.py` | Fingerprints a repo: manifests, stack and versions, runnable commands (`build`, `test`, `lint`, `dev`, `run`), entry points, infra, and architecture hints with confidence scores. |
| `audit.py` | Finds installed skills (project and user locations), estimates their idle token cost, and flags oversized descriptions (over 1,024 chars), overlapping skills (Jaccard similarity of 0.4 or higher), and skills that don't match the detected stack. |
| `write_map.py` | Writes a project map of at most 600 estimated tokens into `CLAUDE.md` or `AGENTS.md`, only between marker comments, and can check whether the map is stale. |

**Opt-in / experimental (makes network calls):**

| Script | Job |
|---|---|
| `match.py` | 5-step resolution: lockfile → cache → live search → stale fallback → seed catalog. Returns a ranked shortlist within a token budget. Live search times out at 20 s and degrades gracefully. |
| `rank.py` | Scores candidates by stack tag overlap, install count, description length (idle cost), and age. |
| `registry.py` | Adapter: `skills.sh` public API primary, `npx skills find` fallback, GitHub raw for metadata. |
| `install.py` | Calls `npx skills add` with SHA pinning for reproducibility. Requires explicit per-skill approval. |

## Design principles

- **Deterministic.** The same repo produces the same output, every run.
- **Read-only by default.** Scanning and auditing never write. The map writer changes only the marked block in the target file, and supports `--dry-run`.
- **No network by default.** Scanning, auditing, and map writing make no network calls. Skill matching is opt-in and explicit; the agent asks before running it. Python 3.8+ standard library only.
- **Hints, not facts.** Architecture patterns come with a confidence score and the evidence behind it. Overlap and relevance flags are suggestions, and nothing is ever removed or disabled automatically.
- **Estimates are labeled.** Token counts use characters divided by 4. For exact accounting, use your agent's own context report.

## Security & Trust Model

`skill-fit` is engineered for safety in autonomous agent environments:

1. **Local & offline by default (Act 1):** `scan.py`, `audit.py`, and `write_map.py` make zero network requests, require no third-party Python dependencies, and only read manifests and file trees (never secrets or sensitive file contents). Map modifications are strictly confined between marker comments.
2. **Indirect prompt injection defense (Act 2):** When searching for stack-matched skills (`match.py`), third-party descriptions and metadata fetched from `skills.sh` or GitHub are sanitized (stripping ANSI escapes, removing ASCII control characters, and normalizing whitespace), capped at safe lengths, and treated strictly as passive data. Agents are explicitly instructed never to execute instructions found in external skill descriptions.
3. **Supply chain & transitive installation guardrails (`install.py`):** `skill-fit` never installs skills autonomously. Candidates must pass an interactive trust gate requiring explicit, per-skill human approval. Installations are SHA-pinned for reproducibility, and content hashes are re-verified post-install. Tampered or mismatched content is immediately quarantined and aborts lockfile creation.

## Install

**Skills CLI (requires Node):**

```bash
# This project only
npx skills add jasimrazi/skill-fit --skill skill-fit

# All your projects
npx skills add jasimrazi/skill-fit --skill skill-fit -g
```

**Manual** (works anywhere an agent reads a skills folder): copy the `skill/` folder into your agent's skills directory under the name `skill-fit`.

```bash
# This project only
mkdir -p .claude/skills
cp -r /path/to/skill-fit/skill .claude/skills/skill-fit

# All your projects
cp -r /path/to/skill-fit/skill ~/.claude/skills/skill-fit
```

Requires Python 3.8+ (`python3` on macOS and Linux).

## Use

Ask your agent something like "audit my agent context" or "summarize this project for future sessions". The trigger phrases are listed in [`skill/SKILL.md`](skill/SKILL.md). The agent runs the scripts, shows you the findings, and asks before writing the map or acting on any disable suggestion.

You can also run the scripts directly:

```bash
# 1. Fingerprint a codebase
python3 skill/scripts/scan.py /path/to/repo [--human]

# 2. Audit installed skills
python3 skill/scripts/audit.py [--stack-json scan.json] [--skills-dir /path/to/skills] [--human]

# 3. Write or check the project map
python3 skill/scripts/write_map.py . --target AGENTS.md --dry-run   # preview the diff
python3 skill/scripts/write_map.py . --target AGENTS.md --create    # write or update the block
python3 skill/scripts/write_map.py . --target AGENTS.md --check     # exit 0 if current, 1 if stale
```

All scripts print JSON by default, and `--human` prints a readable table.

## What it reads and writes

| Script | Reads | Writes |
|---|---|---|
| `scan.py` | Manifests, config files, and directory names. Never source file contents. | Nothing |
| `audit.py` | Skill frontmatter and file sizes in skill folders | Nothing |
| `write_map.py` | Scan output and the target file | Only the marked block in the target file |

To uninstall, delete the skill folder and the marked block in your `CLAUDE.md` / `AGENTS.md`.

## Benchmarks

**Measured:**

| Measurement | Result | Notes |
|---|---|---|
| Scan latency, 10,000-file repo | ~70 ms | On the author's machine, so expect variation by disk |
| Project map size | at most 600 estimated tokens | Enforced ceiling, chars / 4 |
| skill-fit's own idle footprint | ~34 estimated tokens | Its frontmatter description, chars / 4 |

**Not yet measured:** end-to-end token savings. How much exploration a map saves depends on the repo, the task, and the model, and a script's output size alone doesn't show that. No savings percentage is claimed until the benchmark has run.

Planned method (see [`bench/`](bench/)): fixed tasks per repo, run under four conditions, 3 or more repetitions each, with the model version held constant.

| Condition | Median tokens | Tool calls | Task success |
|---|---|---|---|
| Baseline (no map, no skill) | pending | pending | pending |
| Map only | pending | pending | pending |
| Prune only | pending | pending | pending |
| Map and prune | pending | pending | pending |

A result only counts if task success holds up. Fewer tokens with worse answers is not a win, and the report will flag it.

## Frequently Asked Questions (FAQ)

### How does skill-fit prevent Claude Code from exhausting context window limits?
By providing a compact project map (<600 tokens) directly in `CLAUDE.md` or `AGENTS.md`, Claude Code immediately knows how to build, test, lint, and run the project without spending turns running exploratory shell commands. Additionally, `skill-fit audit` detects idle and bloated skills, preventing hundreds of irrelevant tokens from polluting the system prompt on every conversational turn.

### What is the difference between CLAUDE.md and AGENTS.md?
`CLAUDE.md` is the project instruction file used natively by Claude Code. `AGENTS.md` is an emerging open standard adopted by multi-agent frameworks and IDEs (such as Antigravity, Cursor, and Codex tools). `skill-fit` supports both seamlessly via `--target CLAUDE.md` or `--target AGENTS.md`.

### Does skill-fit consume LLM API tokens to run?
No. Act 1 (`scan.py`, `audit.py`, `write_map.py`) uses 100% local deterministic file parsing and token estimation heuristics (character length / 4) in Python standard library. It requires zero API keys and spends zero model tokens.

### Can skill-fit run in CI/CD pipelines to prevent map drift?
Yes. You can run `python3 skill/scripts/write_map.py . --check` in GitHub Actions or your pre-commit hooks. It returns exit code `0` if the map is up-to-date and `1` if the project manifest or architecture has changed, alerting developers to update their project map.

### Can I use skill-fit with Cursor, Windsurf, or other AI agents?
Yes. While developed with Claude Code workflows in mind, `skill-fit` writes standard Markdown maps and reads Agent Skills frontmatter (`agentskills.io` standard). Any agent or IDE that reads `AGENTS.md` or repository rules benefits immediately from the structured commands and directory purposes.

### How does skill-fit protect against indirect prompt injection?
External skill recommendations (Act 2) are treated strictly as untrusted display data. External descriptions are sanitized (stripping ANSI escapes, removing control characters, and normalizing whitespace) and enclosed in passive quotes. Agents are explicitly instructed never to execute instructions embedded inside external descriptions. Furthermore, installations require explicit per-skill user consent and SHA commit pinning.

## Limitations

- Token counts are estimates and will differ from your agent's exact accounting.
- Overlap and "possibly irrelevant" flags are heuristics. Review them before removing anything.
- Architecture hints come from folder names and are marked with a confidence score.
- Skill locations are searched on a best-effort basis. Use `--skills-dir` for non-standard setups.
- Developed for Claude Code. Other agents that read the Agent Skills format may work, but they are untested.
- **Live search can time out.** The matching phase has a 20-second total wall-clock limit. On timeout it degrades to stale cache or seed catalog, but the shortlist may be less relevant than a live result. Do not rely on `match.py` for latency-sensitive workflows.

## Roadmap

Planned, not promised.

- **v0.2:** check documented commands against the repo's actual scripts, lint `CLAUDE.md` for size and duplication, reversible pruning (move flagged skills aside, never delete), CI mode with a token threshold.
- **v0.3:** optional usage evidence from local session transcripts, a drift hook that reports a stale map, a committed project profile.
- **v0.4:** live skill matching with a local cache, a project lockfile with pinned commits and content hashes, and an approval step before any install.

## Related tools

skill-fit overlaps in places with other projects. These are worth a look:

- [skillstat](https://github.com/no7z/skillstat): reads session transcripts to find skills that never fire, and can move idle ones aside.
- [SkillScope](https://github.com/arsyadal/SkillScope): toggles skills per project using a manifest.

skill-fit's angle is a deterministic repo map plus a static audit, with no model in the loop.

## Repository layout

```
skill-fit/
├── skill/                        # The shippable unit (no runtime dependencies)
│   ├── SKILL.md                  # Trigger description and workflow (under 40 lines)
│   ├── scripts/
│   │   ├── _common.py            # Token estimator, safe file reader, frontmatter parser
│   │   ├── scan.py               # Repo fingerprint -> JSON
│   │   ├── audit.py              # Installed skills -> idle cost, overlaps, bloat
│   │   └── write_map.py          # Project map writer with marker isolation and ceiling
│   ├── assets/
│   │   └── map.template.md       # Project map template
│   └── references/
│       └── output-schemas.md     # JSON output schemas
├── tests/
│   ├── fixtures/                 # react-tailwind, fastapi-docker, flutter, monorepo, and more
│   ├── golden/                   # Expected JSON per fixture
│   └── test_*.py
├── bench/                        # Benchmark harness and results
└── docs/
    ├── PLAN.md                   # Phased plan and verification checklist
    └── DESIGN.md                 # Assumptions, decisions, token budgets, self-audit
```

Only `skill/` is meant to be installed. Tests, fixtures, and benchmarks stay out of your agent's context.

## Development

```bash
pip install -e ".[dev]"
pytest -v
ruff check skill/ tests/
```

Tests run against fixture repos and never touch your real home directory or installed skills.

## License

MIT. See [LICENSE](LICENSE).