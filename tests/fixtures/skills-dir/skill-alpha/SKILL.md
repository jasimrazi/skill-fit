---
name: python-formatter
description: Formats Python source code using Black and isort. Run this skill when you need to format or lint Python files in a project.
---
# Python Formatter

Instructions for formatting Python code with Black and isort.

## When to use
- Before committing Python code
- When the codebase style is inconsistent

## Steps
1. Run `black .` to format
2. Run `isort .` to sort imports
3. Run `ruff check .` to lint
