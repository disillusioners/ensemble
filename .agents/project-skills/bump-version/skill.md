# Change Version

## Overview

Skill for bumping versions across project files with conventional commit support.

## Default Behavior

**When user says nothing specific**, bump **patch** version only (e.g., `1.0.0` → `1.0.1`).

## Explicit Version Bumps

| User says | Action |
|-----------|--------|
| "nothing" / "default" / bare request | Patch: `1.0.0` → `1.0.1` |
| "minor" | Minor: `1.0.0` → `1.1.0` |
| "major" | Major: `1.0.0` → `2.0.0` |
| "prerelease" / "beta" | Prerelease: `1.0.0` → `1.0.1-beta.1` |

## Triggers

- "increase version" / "bump version" / "new version"
- "release" / "publish"
- "sync versions"

## Workflow

**Preferred: Use the automation script**
```bash
python scripts/bump_version.py [patch|minor|major|prerelease]
```

**Manual workflow (if script unavailable):**
1. Identify current version from `pyproject.toml` or `package.json`
2. Determine bump type (default: patch)
3. Update all version files atomically:
   - `pyproject.toml` (version field)
   - `daemon/__init__.py` (`__version__`)
   - `tests/test_api.py` (version assertion)
   - `uv.lock` (via `uv sync`)
4. Create git commit: `! New version`
5. Run tests to verify

## Version Sources

| Project Type | Files to Update |
|-------------|-----------------|
| Python (uv) | `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` |
| Node.js | `package.json`, `package-lock.json` |
| Rust | `Cargo.toml` |

## Automation Script

Located at `scripts/bump_version.py` — handles all Python project files atomically.
