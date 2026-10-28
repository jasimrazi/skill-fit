# Distribution & Community Reach Playbook for skill-fit

This playbook contains ready-to-submit templates and community launch strategies to drive developer traffic, backlinks, and organic reach.

---

## 1. Curated "Awesome" Lists Submissions

"Awesome" lists have thousands of GitHub stars and rank prominently on Google. Submitting a Pull Request to these repositories provides permanent backlinks and high developer intent traffic.

### Target 1: `awesome-claude-code` or `awesome-claude`
- **Category:** Tools / Context Management / Agent Skills
- **Entry Markdown:**
  ```markdown
  - [skill-fit](https://github.com/jasimrazi/skill-fit) - Zero-token repo scanner that generates compact `CLAUDE.md`/`AGENTS.md` project maps (<600 tokens) and audits idle skill context bloat.
  ```

### Target 2: `awesome-agent-skills` / `awesome-ai-devtools`
- **Category:** Skills / Context Engineering
- **Entry Markdown:**
  ```markdown
  - [skill-fit](https://github.com/jasimrazi/skill-fit) - Agent skill and CLI to audit idle skill context costs, resolve tech-stack overlap, and generate deterministic repo maps.
  ```

---

## 2. Hacker News: "Show HN" Launch Template

Post this to Hacker News between **8:00 AM – 11:00 AM EST (Tuesday–Thursday)** for maximum visibility.

- **Title:** `Show HN: skill-fit – Cut Claude Code token waste with deterministic maps and skill audits`
- **Link:** `https://github.com/jasimrazi/skill-fit`
- **First Comment Body:**
  ```markdown
  Hey HN!

  While using Claude Code and coding agents on large repositories, I noticed two major sources of token waste and context bloat:

  1. Exploration turns: Without an upfront summary, an agent spends 3–6 turns running `ls`, `cat`, and `grep` just to figure out how to build the project and run tests (and often guesses commands wrong).
  2. Idle skill context tax: Every installed agent skill loads its description into context on *every single turn*. Having 10+ skills installed silently burns hundreds of tokens before you even type your prompt.

  I built `skill-fit` to address both issues deterministically:
  - `scan.py`: Fingerprints repo stack, runnable commands (`test`, `build`, `lint`), entry points, and infra in ~70ms.
  - `audit.py`: Scans project and global skill directories, estimates idle token costs, and flags bloat, high overlap (Jaccard similarity >= 0.4), and stack mismatches.
  - `write_map.py`: Writes a project map strictly capped at 600 tokens directly into `CLAUDE.md` or `AGENTS.md` between isolated marker comments.

  Key design principles:
  - 100% offline and local by default. Zero model tokens spent to scan or audit.
  - Pure Python 3.8+ standard library (no runtime dependencies).
  - Non-destructive: nothing is pruned without explicit approval, and map writes are strictly confined to marker tags.

  Repo: https://github.com/jasimrazi/skill-fit

  Would love feedback on the heuristic thresholds and token budgeting approach!
  ```

---

## 3. Reddit Launch Templates

### Community 1: `r/ClaudeAI`
- **Title:** `I built an open-source tool to cut Claude Code token waste and audit idle skills`
- **Post Body:**
  ```markdown
  If you use Claude Code frequently, you've probably noticed that complex repos burn through context fast. Two main culprits:

  1. Claude Code explores the directory tree blindly with bash commands to find scripts.
  2. Every installed skill in your skills folder loads its frontmatter into context on every message turn, whether you use it or not.

  I built **skill-fit** (MIT, Python 3.8+ stdlib, zero dependencies):
  - Automatically writes a compact project map (<600 tokens) into `CLAUDE.md` so Claude knows your build, test, and dev commands on turn 1.
  - Audits your installed skills, showing exact idle token costs and flagging overlaps or skills irrelevant to your current repo.
  - Runs 100% offline and spends 0 API tokens.

  GitHub: https://github.com/jasimrazi/skill-fit

  Feedback and contributions welcome!
  ```

### Community 2: `r/LocalLLaMA` / `r/programming`
- **Angle:** Focus on context engineering, deterministic AST/manifest parsing, and the 600-token enforced ceiling vs dumping raw repos.
