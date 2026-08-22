# Lesson: venv dependency drift silently disables e2e pack Layer-2 timeout

**Date:** 2026-08-22 · **Arc:** auto-restart-phase1 pre-merge gate

## Symptom
`pyproject.toml` declares `pytest-timeout>=2.3` (added c9055718, 2026-06-25), but `.venv` did NOT have it installed. Consequences:
- pyproject `timeout = 30` / `timeout_method = "thread"` ini keys parsed as "unknown config option" warnings (inert).
- e2e packs' `PYTEST_TIMEOUT=280` inner guard (Layer 2) **silently no-oped** — only the outer `timeout 300` shell wrapper protected against hangs. Several pack reports across prior arcs noted "unknown config option: timeout" warnings without connecting the dots.

## Detection pattern (cheap, should run before e2e gates)
```
.venv/bin/python -c "import pytest_timeout" || echo "LAYER-2 GUARD INACTIVE"
```
Any pack whose report carries `PytestConfigWarning: Unknown config option: timeout` is running with an inert Layer-2.

## Fix applied this session
`uv pip install 'pytest-timeout>=2.3' --python .venv` (venv-local; `.venv/bin/pip` doesn't exist in uv-managed venvs). Not committed — nothing in-repo changed.

## Root cause hypothesis (unverified)
uv sync with a changed lock / partial install / venv reused across dependency edits. Worth a periodic `uv sync --frozen` check before release gates.

## Rule of thumb
Dual-layer timeout is only dual if both layers are alive. Verify the plugin that powers Layer 2 exists before trusting pack TIMEOUT semantics — a hung test would still hit the 300s shell cap, but per-test timeout attribution (which test hung) is lost without it.
