#!/usr/bin/env python3
"""Repo fingerprint -> JSON.

Usage:
    scan.py <path> [--human]

Output fields (JSON):
    project, manifests[], stack[], commands{}, infra[],
    architecture[], entry_points[], dir_purposes{},
    avoid[], hash, warnings[]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Bootstrap: make _common importable when run directly
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))
from _common import safe_read, token_estimate  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IGNORE_DIRS: frozenset = frozenset({
    ".git", "node_modules", "venv", ".venv", "build", "dist",
    "__pycache__", ".dart_tool", "target", ".gradle", ".idea",
    ".tox", ".eggs", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".cache", "coverage", ".coverage",
})

DEPTH_LIMIT = 2  # scan root (0), +1, +2 — do not descend beyond depth 2

# Commands we surface; others from manifests are ignored
KNOWN_COMMAND_KEYS: frozenset = frozenset({
    "build", "test", "lint", "dev", "run", "start",
    "format", "check", "clean", "deploy", "serve", "watch",
})

# Manifest / infra filenames we may open — anything else is skipped immediately.
# This name-check is what keeps 10k-file repos fast: we never open source files.
MANIFEST_NAMES: frozenset = frozenset({
    "package.json", "requirements.txt", "pyproject.toml",
    "pubspec.yaml", "Cargo.toml", "go.mod",
    "Makefile", "makefile", "GNUmakefile",
    "justfile", "Justfile",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "serverless.yml", "serverless.yaml",
})
# Prefixes worth checking even without exact match (Dockerfile.*, docker-compose.*)
MANIFEST_PREFIXES: Tuple[str, ...] = ("Dockerfile.", "docker-compose")

DIR_PURPOSE_MAP: Dict[str, str] = {
    "src": "source code",
    "lib": "library code",
    "test": "test suite",
    "tests": "test suite",
    "spec": "test suite",
    "__tests__": "test suite",
    "docs": "documentation",
    "doc": "documentation",
    "scripts": "scripts",
    "public": "static assets",
    "static": "static assets",
    "assets": "static assets",
    "config": "configuration",
    "configs": "configuration",
    "migrations": "database migrations",
    "alembic": "database migrations",
    "vendor": "vendored dependencies",
    "services": "microservices",
    "packages": "monorepo packages",
    "apps": "monorepo applications",
    "api": "API layer",
    "frontend": "frontend application",
    "backend": "backend application",
    "android": "Android platform code",
    "ios": "iOS platform code",
    "web": "web application",
    "mobile": "mobile application",
    "cmd": "CLI entry points",
    "bin": "executables",
    "examples": "example code",
    "benchmarks": "benchmarks",
    "data": "data files",
    "fixtures": "test fixtures",
    "templates": "templates",
    "middleware": "middleware",
    "models": "data models",
    "views": "views / controllers",
    "controllers": "controllers",
    "routes": "route handlers",
    "handlers": "request handlers",
    "repositories": "data access layer",
    "infrastructure": "infrastructure code",
    "internal": "internal packages",
    "pkg": "shared packages",
    "tools": "developer tools",
}

AVOID_DIR_NAMES: frozenset = frozenset({
    "dist", "build", "migrations", "vendor", "generated", "__generated__",
    "node_modules", "target", ".dart_tool",
})
AVOID_FILE_NAMES: frozenset = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "composer.lock",
})

ENTRY_POINT_NAMES: frozenset = frozenset({
    "main.py", "app.py", "server.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "server.js", "server.ts", "app.js", "app.ts",
    "main.go", "main.rs", "main.dart",
    "Program.cs", "index.html",
})

# Token ceiling for total JSON output (~1 500 estimated tokens = 6 000 chars)
TOKEN_CEILING = 1_500
# Max stack entries before truncation
MAX_STACK = 20

# ---------------------------------------------------------------------------
# TOML loading — stdlib 3.11+, then tomli if installed, then regex fallback
# ---------------------------------------------------------------------------

def _load_toml(content: str) -> Optional[Dict[str, Any]]:
    try:
        import tomllib  # type: ignore  # Python 3.11+
        return tomllib.loads(content)  # type: ignore
    except ImportError:
        pass
    try:
        import tomli as tomllib  # type: ignore  # installed as pytest dep on <3.11
        return tomllib.loads(content)  # type: ignore
    except ImportError:
        pass
    return _toml_regex_fallback(content)


def _toml_regex_fallback(content: str) -> Dict[str, Any]:
    """Minimal regex TOML parser — enough for pyproject.toml fields we need."""
    result: Dict[str, Any] = {}
    current_section: List[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Section header  [tool.poetry.dependencies]
        sec_m = re.match(r"^\[([^\[\]]+)\]$", stripped)
        if sec_m:
            current_section = [s.strip() for s in sec_m.group(1).split(".")]
            _ensure_path(result, current_section)
            continue

        # key = value
        kv_m = re.match(r"^([a-zA-Z0-9_-]+)\s*=\s*(.+)$", stripped)
        if kv_m and current_section:
            key = kv_m.group(1)
            raw = kv_m.group(2).strip()
            # String
            str_m = re.match(r'^"([^"]*)"$', raw) or re.match(r"^'([^']*)'$", raw)
            if str_m:
                value: Any = str_m.group(1)
            # Array
            elif raw.startswith("["):
                value = re.findall(r'["\']([^"\']+)["\']', raw)
            else:
                value = raw
            node = _ensure_path(result, current_section)
            if isinstance(node, dict):
                node[key] = value

    return result


def _ensure_path(d: Dict[str, Any], path: List[str]) -> Any:
    node: Any = d
    for part in path:
        if isinstance(node, dict):
            node = node.setdefault(part, {})
    return node


# ---------------------------------------------------------------------------
# Manifest parsers
# ---------------------------------------------------------------------------

def _parse_package_json(
    content: str, rel_path: str
) -> Tuple[Optional[str], Optional[str], List[Dict], Dict[str, str], bool, Optional[str]]:
    """Returns (name, version, stack, commands, has_workspaces, error)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, None, [], {}, False, str(exc)

    stack: List[Dict] = []
    commands: Dict[str, str] = {}

    for section in ("dependencies", "devDependencies", "peerDependencies"):
        is_dev = section != "dependencies"
        for pkg_name, ver in (data.get(section) or {}).items():
            stack.append({
                "name": pkg_name,
                "version": ver if isinstance(ver, str) else "",
                "source_file": rel_path,
                "dev": is_dev,
            })

    for script_name, cmd in (data.get("scripts") or {}).items():
        if script_name in KNOWN_COMMAND_KEYS:
            commands[script_name] = cmd

    has_workspaces = bool(data.get("workspaces"))
    return (
        data.get("name"),
        data.get("version"),
        stack,
        commands,
        has_workspaces,
        None,
    )


def _parse_requirements_txt(content: str, rel_path: str) -> List[Dict]:
    stack = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip options / recursive includes
        if line.startswith("-"):
            continue
        # Strip inline comment
        line = line.split("#")[0].strip()
        if not line:
            continue
        # Strip extras: uvicorn[standard] -> uvicorn
        name = re.split(r"[\[>=<!~\s;@]", line)[0].strip()
        # Version constraint
        ver_m = re.search(r"[><=!~].*", line.split("[")[0] if "[" in line else line)
        version = ""
        if ver_m:
            after_name = line[len(name):]
            # Re-match from after name (after extras too)
            after_extras = re.sub(r"\[[^\]]*\]", "", after_name)
            ver_m2 = re.match(r"\s*([><=!~][^\s;]+)", after_extras)
            if ver_m2:
                version = ver_m2.group(1)
        if name and re.match(r"^[A-Za-z0-9]", name):
            stack.append({
                "name": name,
                "version": version,
                "source_file": rel_path,
                "dev": False,
            })
    return stack


def _parse_pyproject_toml(
    content: str, rel_path: str
) -> Tuple[Optional[str], Optional[str], List[Dict], Dict[str, str], Optional[str]]:
    data = _load_toml(content)
    if data is None:
        return None, None, [], {}, "TOML parse failed"

    stack: List[Dict] = []
    commands: Dict[str, str] = {}
    name = version = None

    project = data.get("project") or {}
    if isinstance(project, dict):
        name = project.get("name")
        version = project.get("version")
        for dep in (project.get("dependencies") or []):
            if isinstance(dep, str):
                dep_name = re.split(r"[\[>=<!~\s;@]", dep)[0].strip()
                if dep_name:
                    stack.append({
                        "name": dep_name,
                        "version": "",
                        "source_file": rel_path,
                        "dev": False,
                    })
        for script_name, cmd in (project.get("scripts") or {}).items():
            if script_name in KNOWN_COMMAND_KEYS:
                commands[script_name] = str(cmd)

    # Poetry
    poetry = (data.get("tool") or {}).get("poetry") or {}
    if isinstance(poetry, dict):
        name = name or poetry.get("name")
        version = version or poetry.get("version")
        for dep_name, spec in (poetry.get("dependencies") or {}).items():
            if dep_name.lower() == "python":
                continue
            ver = str(spec) if not isinstance(spec, dict) else ""
            stack.append({"name": dep_name, "version": ver, "source_file": rel_path, "dev": False})
        for dep_name in (poetry.get("dev-dependencies") or {}):
            stack.append({"name": dep_name, "version": "", "source_file": rel_path, "dev": True})

    return name, version, stack, commands, None


def _parse_pubspec_yaml(
    content: str, rel_path: str
) -> Tuple[Optional[str], Optional[str], List[Dict]]:
    stack: List[Dict] = []
    name: Optional[str] = None
    version: Optional[str] = None
    in_deps = False
    in_dev_deps = False

    for raw_line in content.splitlines():
        stripped = raw_line.strip()

        # Top-level (non-indented) key
        if raw_line and not raw_line[0].isspace():
            if stripped.startswith("name:"):
                name = stripped.split(":", 1)[1].strip().strip("\"'")
            elif stripped.startswith("version:"):
                version = stripped.split(":", 1)[1].strip().strip("\"'")
            in_deps = stripped.startswith("dependencies:")
            in_dev_deps = stripped.startswith("dev_dependencies:")
            continue

        # Dependency lines: exactly 2-space indent
        if (in_deps or in_dev_deps) and re.match(r"^  [a-zA-Z]", raw_line):
            dep_m = re.match(r"^  ([a-zA-Z][a-zA-Z0-9_]*)\s*:?(.*)", raw_line)
            if dep_m:
                dep_name = dep_m.group(1)
                rest = dep_m.group(2).strip().strip("\"'")
                if dep_name == "flutter":
                    stack.append({
                        "name": "flutter",
                        "version": "sdk",
                        "source_file": rel_path,
                        "dev": in_dev_deps,
                    })
                elif dep_name not in ("sdk", "dart"):
                    stack.append({
                        "name": dep_name,
                        "version": rest if rest else "",
                        "source_file": rel_path,
                        "dev": in_dev_deps,
                    })

    return name, version, stack


def _parse_cargo_toml(
    content: str, rel_path: str
) -> Tuple[Optional[str], Optional[str], List[Dict]]:
    data = _load_toml(content) or {}
    package = data.get("package") or {}
    stack: List[Dict] = []

    for section in ("dependencies", "dev-dependencies"):
        is_dev = section == "dev-dependencies"
        for dep_name, spec in (data.get(section) or {}).items():
            if isinstance(spec, dict):
                ver = spec.get("version", "")
            elif isinstance(spec, str):
                ver = spec
            else:
                ver = ""
            stack.append({
                "name": dep_name,
                "version": ver,
                "source_file": rel_path,
                "dev": is_dev,
            })

    return package.get("name"), package.get("version"), stack


def _parse_go_mod(content: str, rel_path: str) -> Tuple[Optional[str], List[Dict]]:
    stack: List[Dict] = []
    mod_name: Optional[str] = None
    in_require = False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            mod_name = stripped.split()[1]
        elif stripped in ("require (", "require("):
            in_require = True
        elif stripped == ")" and in_require:
            in_require = False
        elif stripped.startswith("require ") and not in_require:
            parts = stripped.split()
            if len(parts) >= 3:
                stack.append({
                    "name": parts[1],
                    "version": parts[2],
                    "source_file": rel_path,
                    "dev": False,
                })
        elif in_require and stripped and not stripped.startswith("//"):
            parts = stripped.split()
            if len(parts) >= 2:
                stack.append({
                    "name": parts[0],
                    "version": parts[1],
                    "source_file": rel_path,
                    "dev": "// indirect" in line,
                })

    return mod_name, stack


def _parse_makefile(content: str) -> Dict[str, str]:
    """Extract known command targets from a Makefile."""
    commands: Dict[str, str] = {}
    for line in content.splitlines():
        # Targets start at column 0, followed by colon
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9_\-]*)\s*:", line)
        if m:
            target = m.group(1).lower()
            if target in KNOWN_COMMAND_KEYS:
                commands[target] = f"make {m.group(1)}"
    return commands


def _parse_justfile(content: str) -> Dict[str, str]:
    """Extract known recipes from a justfile."""
    commands: Dict[str, str] = {}
    for line in content.splitlines():
        # Recipe line: name (args)* :
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9_\-]*)(?:\s+[a-zA-Z0-9_\-\$@\s]*)*\s*:", line)
        if m:
            recipe = m.group(1).lower()
            if recipe in KNOWN_COMMAND_KEYS:
                commands[recipe] = f"just {m.group(1)}"
    return commands


# ---------------------------------------------------------------------------
# Architecture heuristics
# ---------------------------------------------------------------------------

def _architecture_hints(
    top_dirs: List[str],
    stack_names: frozenset,
    infra: List[str],
    has_workspaces: bool,
    workspace_patterns: List[str],
) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []

    # Monorepo
    mono_evidence = []
    if has_workspaces:
        mono_evidence.append("package.json workspaces field")
    if workspace_patterns:
        mono_evidence.append(f"workspace patterns: {', '.join(workspace_patterns[:3])}")
    apps_pkg = {"apps", "packages"}.intersection(top_dirs)
    if apps_pkg:
        mono_evidence.append(f"top-level dirs: {', '.join(sorted(apps_pkg))}")
    if mono_evidence:
        hints.append({
            "hint": "monorepo",
            "confidence": 0.85 if has_workspaces else 0.6,
            "evidence": mono_evidence,
        })

    # SPA (React / Vue / Angular / Svelte)
    spa_frameworks = {"react", "vue", "angular", "@angular/core", "svelte"}
    build_tools = {"vite", "webpack", "rollup", "parcel", "@vitejs/plugin-react"}
    matched_fw = spa_frameworks & stack_names
    matched_bt = build_tools & stack_names
    if matched_fw:
        hints.append({
            "hint": "spa",
            "confidence": 0.75 if matched_bt else 0.5,
            "evidence": (
                [f"{n} in deps" for n in sorted(matched_fw)[:2]]
                + ([f"{n} in devDeps" for n in sorted(matched_bt)[:1]] if matched_bt else [])
            ),
        })

    # API server
    api_frameworks = {"fastapi", "flask", "django", "starlette", "litestar",
                      "express", "fastify", "hapi", "koa",
                      "gin", "echo", "fiber",
                      "actix-web", "axum", "warp",
                      "rocket"}
    matched_api = api_frameworks & stack_names
    if matched_api:
        hints.append({
            "hint": "api_server",
            "confidence": 0.75,
            "evidence": [f"{n} in deps" for n in sorted(matched_api)[:2]],
        })

    # Containerized
    if "docker" in infra or "docker-compose" in infra:
        ev = []
        if "docker" in infra:
            ev.append("Dockerfile present")
        if "docker-compose" in infra:
            ev.append("docker-compose file present")
        hints.append({"hint": "containerized", "confidence": 0.85, "evidence": ev})

    # Mobile (Flutter)
    if "flutter" in stack_names:
        hints.append({
            "hint": "mobile_cross_platform",
            "confidence": 0.9,
            "evidence": ["flutter SDK dependency in pubspec.yaml"],
        })

    # Layered (services + repositories)
    if "services" in top_dirs and "repositories" in top_dirs:
        hints.append({
            "hint": "layered",
            "confidence": 0.6,
            "evidence": ["services/ and repositories/ directories at root"],
        })

    return hints


# ---------------------------------------------------------------------------
# Truncation helpers
# ---------------------------------------------------------------------------

def _truncate_list(lst: List[Any], max_items: int, label: str = "item") -> List[Any]:
    """Return at most *max_items* entries; append a '+N more' sentinel if trimmed."""
    if len(lst) <= max_items:
        return lst
    extra = len(lst) - max_items
    return lst[:max_items] + [f"+{extra} more {label}s"]


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------

def scan(root: str) -> Dict[str, Any]:  # noqa: C901
    root = os.path.abspath(root)
    warnings: List[str] = []
    manifests: List[str] = []
    all_stack: List[Dict] = []
    # Commands: use priority dict — first writer wins (package.json > pyproject > Makefile)
    all_commands: Dict[str, str] = {}
    makefile_commands: Dict[str, str] = {}  # lower priority
    infra: List[str] = []
    entry_points: List[str] = []
    manifest_blobs: List[str] = []  # for hash

    project_name: Optional[str] = None
    project_version: Optional[str] = None
    has_workspaces = False
    workspace_patterns: List[str] = []

    # Top-level directory listing (for hash + dir_purposes + avoid)
    try:
        top_entries = os.listdir(root)
    except OSError as exc:
        warnings.append(f"Cannot list root: {exc}")
        top_entries = []

    top_dirs = sorted(
        e for e in top_entries
        if os.path.isdir(os.path.join(root, e)) and e not in IGNORE_DIRS
    )

    # Avoid list: top-level entries matching known avoid patterns
    avoid: List[str] = sorted(
        e for e in top_entries
        if e in AVOID_DIR_NAMES or e in AVOID_FILE_NAMES
    )

    # Dir purposes from top-level dirs + common second-level dirs
    dir_purposes: Dict[str, str] = {}
    for d in top_dirs:
        if d in DIR_PURPOSE_MAP:
            dir_purposes[d] = DIR_PURPOSE_MAP[d]

    # -----------------------------------------------------------------------
    # Walk — fast os.scandir-based traversal (name-check before any open())
    # -----------------------------------------------------------------------
    # We use a manual stack instead of os.walk so we can bail out of
    # subdirectories with zero manifest candidates without enumerating files.
    # For a 10k-file repo with no manifests in sub-dirs, this skips all reads.
    walk_stack: List[Tuple[str, int]] = [(root, 0)]  # (dirpath, depth)

    while walk_stack:
        dirpath, depth = walk_stack.pop()

        try:
            entries = list(os.scandir(dirpath))
        except OSError as exc:
            warnings.append(f"{os.path.relpath(dirpath, root)}: scandir error: {exc}")
            continue

        subdirs: List[os.DirEntry] = []
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in IGNORE_DIRS:
                    subdirs.append(entry)
            else:
                # Fast name filter: only proceed if it might be a manifest/infra file
                fname = entry.name
                is_manifest = (
                    fname in MANIFEST_NAMES
                    or fname.startswith("Dockerfile.")
                    or fname.startswith("docker-compose")
                )
                if not is_manifest:
                    # Check entry points (cheap — comparing against small set)
                    if depth <= 1 and fname in ENTRY_POINT_NAMES:
                        rel_path = os.path.relpath(entry.path, root).replace(os.sep, "/")
                        if rel_path not in entry_points:
                            entry_points.append(rel_path)
                    # Check GitHub Actions (yaml in .github/workflows)
                    if (
                        fname.endswith((".yml", ".yaml"))
                        and ".github" in dirpath
                        and "workflows" in dirpath
                        and "github_actions" not in infra
                    ):
                        infra.append("github_actions")
                    continue

                filepath = entry.path
                rel_path = os.path.relpath(filepath, root).replace(os.sep, "/")

                # ---- package.json ----
                if fname == "package.json":
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    manifest_blobs.append(content)
                    name, ver, stk, cmds, ws, parse_err = _parse_package_json(content, rel_path)
                    if parse_err:
                        warnings.append(f"{rel_path}: {parse_err}")
                    else:
                        manifests.append(rel_path)
                        if depth == 0:
                            project_name = project_name or name
                            project_version = project_version or ver
                            has_workspaces = ws
                            try:
                                ws_raw = json.loads(content).get("workspaces")
                                if isinstance(ws_raw, list):
                                    workspace_patterns = ws_raw
                            except Exception:
                                pass
                        if depth == 0:
                            all_stack.extend(stk)
                            for k, v in cmds.items():
                                if k not in all_commands:
                                    all_commands[k] = v
                        else:
                            if name:
                                all_stack.append({
                                    "name": name,
                                    "version": ver or "",
                                    "source_file": rel_path,
                                    "dev": False,
                                })

                # ---- requirements.txt ----
                elif fname == "requirements.txt":
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    manifest_blobs.append(content)
                    manifests.append(rel_path)
                    stk = _parse_requirements_txt(content, rel_path)
                    all_stack.extend(stk)

                # ---- pyproject.toml ----
                elif fname == "pyproject.toml":
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    manifest_blobs.append(content)
                    name, ver, stk, cmds, parse_err = _parse_pyproject_toml(content, rel_path)
                    if parse_err:
                        warnings.append(f"{rel_path}: {parse_err}")
                    else:
                        manifests.append(rel_path)
                        if depth == 0:
                            project_name = project_name or name
                            project_version = project_version or ver
                        all_stack.extend(stk)
                        for k, v in cmds.items():
                            if k not in all_commands:
                                all_commands[k] = v

                # ---- pubspec.yaml ----
                elif fname == "pubspec.yaml":
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    manifest_blobs.append(content)
                    manifests.append(rel_path)
                    name, ver, stk = _parse_pubspec_yaml(content, rel_path)
                    if depth == 0:
                        project_name = project_name or name
                        project_version = project_version or ver
                    all_stack.extend(stk)

                # ---- Cargo.toml ----
                elif fname == "Cargo.toml":
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    manifest_blobs.append(content)
                    manifests.append(rel_path)
                    name, ver, stk = _parse_cargo_toml(content, rel_path)
                    if depth == 0:
                        project_name = project_name or name
                        project_version = project_version or ver
                    all_stack.extend(stk)

                # ---- go.mod ----
                elif fname == "go.mod":
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    manifest_blobs.append(content)
                    manifests.append(rel_path)
                    name, stk = _parse_go_mod(content, rel_path)
                    if depth == 0:
                        project_name = project_name or name
                    all_stack.extend(stk)

                # ---- Makefile / justfile ----
                elif fname in ("Makefile", "makefile", "GNUmakefile"):
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    cmds = _parse_makefile(content)
                    for k, v in cmds.items():
                        if k not in makefile_commands:
                            makefile_commands[k] = v

                elif fname in ("justfile", "Justfile"):
                    content, err = safe_read(filepath)
                    if err:
                        warnings.append(f"{rel_path}: read error: {err}")
                        continue
                    cmds = _parse_justfile(content)
                    for k, v in cmds.items():
                        if k not in makefile_commands:
                            makefile_commands[k] = v

                # ---- Infra (by filename) ----
                if fname == "Dockerfile" or fname.startswith("Dockerfile."):
                    if "docker" not in infra:
                        infra.append("docker")
                elif fname.startswith("docker-compose"):
                    if "docker-compose" not in infra:
                        infra.append("docker-compose")
                elif fname in ("serverless.yml", "serverless.yaml"):
                    if "serverless" not in infra:
                        infra.append("serverless")

                # GitHub Actions
                if (
                    fname.endswith((".yml", ".yaml"))
                    and ".github" in dirpath
                    and "workflows" in dirpath
                    and "github_actions" not in infra
                ):
                    infra.append("github_actions")

                # Entry points
                if depth <= 1 and fname in ENTRY_POINT_NAMES:
                    if rel_path not in entry_points:
                        entry_points.append(rel_path)

        # Enqueue subdirs for next depth level (enforcing depth limit)
        if depth < DEPTH_LIMIT:
            for sd in subdirs:
                walk_stack.append((sd.path, depth + 1))
            # Enrich dir_purposes from discovered subdirs
            if depth == 0:
                for sd in subdirs:
                    if sd.name in DIR_PURPOSE_MAP and sd.name not in dir_purposes:
                        dir_purposes[sd.name] = DIR_PURPOSE_MAP[sd.name]
            elif depth == 1:
                for sd in subdirs:
                    if sd.name in DIR_PURPOSE_MAP and sd.name not in dir_purposes:
                        dir_purposes[sd.name] = DIR_PURPOSE_MAP[sd.name]

    # Merge Makefile commands (lower priority — don't override manifest commands)
    for k, v in makefile_commands.items():
        if k not in all_commands:
            all_commands[k] = v


    # -----------------------------------------------------------------------
    # Hash: sha1(sorted manifest blobs + sorted top-level dirs)
    # -----------------------------------------------------------------------
    hash_input = "\n---\n".join(sorted(manifest_blobs)) + "\n" + "\n".join(sorted(top_dirs))
    repo_hash = hashlib.sha1(hash_input.encode("utf-8", errors="replace")).hexdigest()

    # -----------------------------------------------------------------------
    # Architecture hints
    # -----------------------------------------------------------------------
    stack_name_set = frozenset(s["name"].lower() for s in all_stack)
    architecture = _architecture_hints(
        top_dirs, stack_name_set, infra, has_workspaces, workspace_patterns
    )

    # -----------------------------------------------------------------------
    # Dedup stack (keep first occurrence of each name)
    # -----------------------------------------------------------------------
    seen_names: set = set()
    deduped_stack: List[Dict] = []
    for entry in all_stack:
        key = entry["name"].lower()
        if key not in seen_names:
            seen_names.add(key)
            deduped_stack.append(entry)
    all_stack = deduped_stack

    # -----------------------------------------------------------------------
    # Truncate lists to stay under token ceiling
    # -----------------------------------------------------------------------
    all_stack = _truncate_list(all_stack, MAX_STACK, "package")

    result: Dict[str, Any] = {
        "project": project_name or os.path.basename(root),
        "manifests": manifests,
        "stack": all_stack,
        "commands": all_commands,
        "infra": infra,
        "architecture": architecture,
        "entry_points": entry_points,
        "dir_purposes": dir_purposes,
        "avoid": avoid,
        "hash": repo_hash,
        "warnings": warnings,
    }

    # Final token check: trim stack further if needed
    raw = json.dumps(result)
    if token_estimate(raw) > TOKEN_CEILING:
        # Trim stack down until we fit
        max_s = MAX_STACK
        while max_s > 0 and token_estimate(json.dumps(result)) > TOKEN_CEILING:
            max_s -= 2
            result["stack"] = _truncate_list(deduped_stack, max_s, "package")

    return result


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _human_output(data: Dict[str, Any]) -> str:
    lines = ["=" * 60, f"Project : {data.get('project', '-')}"]
    lines.append(f"Hash    : {(data.get('hash') or '')[:12]}...")
    lines.append("")

    lines.append("-- Stack " + "-" * 51)
    for item in data.get("stack", []):
        if isinstance(item, str):
            lines.append(f"  {item}")
        else:
            dev = " (dev)" if item.get("dev") else ""
            lines.append(f"  {item['name']:<30} {item.get('version', ''):<15}{dev}")

    lines.append("")
    lines.append("-- Commands " + "-" * 48)
    for key, cmd in (data.get("commands") or {}).items():
        lines.append(f"  {key:<12} {cmd}")

    lines.append("")
    lines.append("-- Infra " + "-" * 51)
    for item in data.get("infra", []):
        lines.append(f"  {item}")

    lines.append("")
    lines.append("-- Architecture hints " + "-" * 38)
    for hint in data.get("architecture", []):
        if isinstance(hint, dict):
            ev = ", ".join(hint.get("evidence", [])[:2])
            lines.append(f"  [{hint['confidence']:.1f}] {hint['hint']:<25} ({ev})")

    if data.get("warnings"):
        lines.append("")
        lines.append("-- Warnings " + "-" * 48)
        for w in data["warnings"]:
            lines.append(f"  WARN  {w}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    human = "--human" in args
    paths = [a for a in args if not a.startswith("-")]

    if not paths:
        print("Usage: scan.py <path> [--human]", file=sys.stderr)
        sys.exit(1)

    root = paths[0]
    if not os.path.isdir(root):
        print(f"Error: {root!r} is not a directory", file=sys.stderr)
        sys.exit(1)

    data = scan(root)

    if human:
        print(_human_output(data))
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
