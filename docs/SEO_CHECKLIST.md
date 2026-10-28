# GitHub SEO & Discoverability Checklist for skill-fit

This checklist contains high-impact maintainer configurations in the GitHub web interface that cannot be set directly from code files. Completing these will significantly boost `skill-fit`'s ranking on GitHub Search, Google, and social platforms.

---

## 1. Repository "About" Section (Top Right on GitHub Repo)

GitHub's search engine indexes the repository description heavily, ranking repos with matching exact-phrase keywords near the top.

- **Description (max 350 chars):**
  > Cut Claude Code & AI agent token waste. Deterministic repo scanner that writes compact CLAUDE.md/AGENTS.md project maps (<600 tokens) and audits idle skill context bloat. Zero model tokens spent.

- **Website URL:**
  Set to `https://github.com/jasimrazi/skill-fit#readme` (or your docs site if deployed).

- **Include in Home Page:**
  - [x] Releases
  - [x] Packages
  - [ ] Environments (uncheck if unused)

---

## 2. GitHub Topics (Repo Tags)

Topics drive discovery on `github.com/topics/<topic>` and feed GitHub's recommendation algorithm. In your repository header, click the **⚙ (gear)** next to About and add the following 15 topics:

```
claude-code
claude
agent-skills
claude-md
agents-md
context-engineering
token-optimization
context-window
ai-coding
developer-tools
llm-agents
prompt-engineering
python
ast
zero-dependency
```

---

## 3. Custom Social Preview Card (Open Graph Image)

When developers share your repository on **X / Twitter, LinkedIn, Reddit, Discord, or Hacker News**, GitHub displays your social preview card. A compelling banner increases click-through rates by up to 300%.

### Specifications
- **Dimensions:** 1280 x 640 pixels (PNG or JPG).
- **Location in GitHub Settings:** Go to **Settings** → **General** → scroll down to **Social preview** → click **Edit** → **Upload an image**.
- **Design Structure:**
  - Center: The clean `skill-fit` logo (`docs/assets/logo.png`).
  - Headline: *Cut Claude Code Token Waste*.
  - Subtitle: *Deterministic Project Maps & Idle Skill Audit (<600 Tokens)*.
  - Footer pills: `CLAUDE.md` • `AGENTS.md` • `0 Model Tokens` • `Python 3.8+ Stdlib`.

---

## 4. GitHub Releases as Search Engine Landing Pages

Google indexes `github.com/<owner>/<repo>/releases` with high priority for version and feature queries.

- When publishing `v0.1.0`:
  - **Release Title:** `v0.1.0: Deterministic Project Maps, Idle Skill Audit, and Token Budgeting`
  - Include the key search terms (*Claude Code token reduction*, *CLAUDE.md map*, *idle skill context audit*) in the release notes body.
  - Link back to the main README documentation.

---

## 5. PyPI Package Publication (Optional but High Domain Authority)

Publishing to PyPI creates `pypi.org/project/skill-fit/`. PyPI has an exceptionally high domain authority (`DA 92`) on Google.

```bash
# Build wheel and sdist
python -m pip install --upgrade build twine
python -m build

# Check package metadata
twine check dist/*

# Upload to PyPI
twine upload dist/*
```
