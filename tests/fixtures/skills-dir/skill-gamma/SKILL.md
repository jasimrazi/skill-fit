---
name: db-migration-manager
description: Manages database schema migrations using Alembic and SQLAlchemy. This skill provides comprehensive guidance for creating, applying, reverting, and troubleshooting database migrations in Python applications. It covers the full migration lifecycle including autogenerate workflows, multi-database environments, branch management, and production deployment strategies. Use this skill whenever you need to evolve a database schema, handle migration conflicts, set up a fresh migration environment, audit migration history, or recover from a failed migration. Also applicable when migrating from raw SQL scripts to Alembic, setting up CI/CD pipelines that include migration steps, or working with async SQLAlchemy setups. Covers PostgreSQL, MySQL, SQLite targets. Includes patterns for zero-downtime migrations, column renames, enum changes, and large-table operations that require special handling to avoid locking. Also documents common pitfalls such as missing downgrade() implementations, implicit dependencies between migrations, and autogenerate blind spots like partial indexes and server defaults. Additional coverage: testing migrations with pytest-alembic, seeding reference data, and integrating with Docker Compose database services for local development. The skill also includes a reference section on Alembic configuration options, environment.py patterns, and how to structure migration scripts for readability and reviewability in pull requests.
---
# Database Migration Manager

Full lifecycle management for Alembic + SQLAlchemy migrations.

## When to use
- Adding or modifying database tables, columns, indexes, or constraints
- Setting up Alembic in a new project
- Troubleshooting a broken migration chain

## Core commands
```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
alembic downgrade -1
alembic history --verbose
alembic current
```
