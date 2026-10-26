---
name: git-workflow
description: Manages Git branching, commit conventions, and pull request workflows. Activate when working with version control, reviewing PRs, or setting up branch protection rules.
---
# Git Workflow

Instructions for consistent Git workflow across the team.

## When to use
- Starting work on a new feature or bug fix
- Preparing a pull request for review
- Resolving merge conflicts

## Steps
1. `git checkout -b feature/my-feature` — create branch
2. Make commits following Conventional Commits format
3. `git push origin feature/my-feature` — push
4. Open PR, request review, address feedback
5. Squash and merge to main
