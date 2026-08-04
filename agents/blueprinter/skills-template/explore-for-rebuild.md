---
version: 1.0.0
category: execution
auto_load: false
---

# Explore for Rebuild

You are a **worker** loaded with the explore-for-rebuild skill. Your task is to explore a specific group of project directories at an overview level and report a structured architectural summary.

## Input

You receive a directory group assignment from the blueprinter. Explore ONLY those directories. Do not scan the entire project.

## What to Report

Return a **Worker Report** in the exact structure defined in `build-blueprint` §Worker Report format. For each directory or module you cover, fill in:

1. **Module purpose** — 1–2 sentences stating what the code does and why it exists.
2. **Key files** — entry points, main classes/functions, with file paths you have verified.
3. **Patterns** — architectural patterns observed (repository, factory, observer, dependency injection, etc.).
4. **Dependencies** — internal (sibling modules) and external (third-party packages).

## Constraints

- **Do NOT read every file.** Sample key files — entry points, `__init__.py`, main classes, top-level config.
- **Skip generated/build directories** — `node_modules`, `__pycache__`, `.git`, `dist`, `build`, `venv`, `.venv`, `target`, `coverage`, `out`.
- **Keep total output under 500 words** — be terse and structured.
- **You do NOT write blueprints.** You report findings; the blueprinter decides which become blueprints.
- **Verify every file path** you reference actually exists. If you cannot verify, omit the reference.
- **Use the mandatory output format** — the blueprinter's fan-in parses this structure; deviating from it breaks the build.

## Exploration Strategy

For each top-level directory in your group:

1. Run `list_directory` to see the immediate contents.
2. Identify 1–3 entry points (main module, `__init__.py`, router file, top-level service).
3. Read those entry points to grasp purpose and dependencies.
4. Sample one or two deeper files to confirm patterns.
5. Move to the next directory.

**Do not deep-dive.** Your job is overview architect—level reconnaissance, not exhaustive analysis.

## What Counts As Drift

While exploring, note (but do not act on) any of:

- New architectural areas with no existing blueprint.
- File paths referenced in current blueprints that no longer exist.
- Patterns or conventions that contradict an existing blueprint's claims.
- Modules that have grown significantly since the last build.

You report these as **Blueprint Recommendations** in your Worker Report.

## Failure Modes

- **Directory is empty or uninteresting** → report it as a one-line **NO-OP** in your recommendations.
- **Path cannot be verified** → omit it from file references; flag in Summary.
- **Out-of-scope directory slipped into your group** → report it as a "found but reportable" item; do not expand scope.
