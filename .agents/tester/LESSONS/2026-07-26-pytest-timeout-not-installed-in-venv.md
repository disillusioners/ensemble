# Lesson: pytest-timeout not installed in venv despite being declared

**Date:** 2026-07-26
**Found during:** LoopRepairer RemoveMessage ID mismatch fix validation
**Severity:** Environment (non-blocking) — affects test-pack script-internal timeout layer

## Symptom
The `--timeout=N` pytest flag fails with:
```
ERROR: usage: pytest [options] ... [file_or_dir]
pytest: error: unrecognized arguments: --timeout=N
```
Exit code 4.

## Root Cause
`pyproject.toml` (line ~43) declares `pytest-timeout>=2.3` as a dev dependency, and the project's pytest config uses `timeout = 30` + `timeout_method = "thread"`. However, the actual `.venv` does **not** have the plugin installed — `.venv/bin/pip` is absent and `import pytest_timeout` fails. A declared dependency is not the same as an installed one (the venv was likely created before the dependency was added, or `pip install -e .` / `uv sync` was not re-run).

## Impact on Test Packs
The `--timeout=N` script-internal (inner) layer of the dual-layer timeout invariant silently fails to apply. The command-level outer guard (`timeout 300`) still works, but if a single test hangs, the outer guard kills the whole run at 5 min rather than pytest interrupting the hung test gracefully. Also: **with the plugin absent, the pyproject `timeout = 30` config keys are no-ops** (they only raise an "Unknown config option" warning), so async tests are NOT killed at 30s — this is actually why most runs still succeed.

## Fix Applied (by worker)
`uv pip install pytest-timeout>=2.3` → installed `pytest-timeout==2.4.0`. This is additive and reversible; restores the expected environment.

## Recommendation
1. **Re-sync dev deps:** run `uv sync` (or `pip install -e ".[dev]"`) in the project venv so all declared dev dependencies (including `pytest-timeout`) are present.
2. **Pre-Send Self-Check addition:** before dispatching a pack that uses `--timeout=N`, verify the timeout backend is importable:
   ```
   .venv/bin/python -c "import pytest_timeout" 2>/dev/null && echo OK || echo MISSING
   ```
3. **Fallback recipe:** if `pytest-timeout` is genuinely unavailable, substitute a Python `subprocess.run([...], timeout=N)` wrapper as the inner layer to preserve the dual-layer invariant. The integration worker (`02ed8eb3`) used this pattern successfully.

## Related
- Worker reports: `221d2441` (unit, installed plugin), `02ed8eb3` (integration, subprocess wrapper fallback)
- Skill feedback filed on `test-pack-execution`: add a Pre-Execution Self-Check bullet to verify the timeout backend is importable.
