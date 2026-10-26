---
name: python-linter
description: Formats and lints Python source code with Black and isort style enforcement. Activate when formatting Python files is needed.
---
# Python Linter

Instructions for linting Python code with Black and isort.

## When to use
- When you need to enforce style in Python files
- Before opening a pull request with Python changes

## Steps
1. Run `black --check .` to verify formatting
2. Run `isort --check .` to verify import order
3. Fix any reported issues
