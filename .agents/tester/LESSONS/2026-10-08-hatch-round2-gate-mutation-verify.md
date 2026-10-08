# 2026-10-08 — Hatch round-2 gate: worktree venv mechanics, mutation-verify pattern, stale-test finding

Context: official test gate for escape-hatch hardening round 2 (worktree `/home/nea/ensemble-src-wt-compaction-escape-hatches`, HEAD `687cc4096`, base `74b36b478`). Full evidence: `RESULTS/2026-10-08-hatch-round2-official-gate.md`.

## Environment gotchas (worktree gates)

1. `uv` is NOT on PATH on this host — always `/home/nea/.local/bin/uv`. A bare `uv sync` fails with "not found" and burns a worker step.
2. Worktree uv venv resolved to **CPython 3.14.7** — the historical py3.13 string-union collection rot does NOT apply; collection errors in such venvs are findings, not rot. Never assume the rot; check `.venv/bin/python --version`.
3. Editable-install trap verified clean via `daemon.__file__` inside the worktree — keep this as the mandatory pre-run gate for every worker.
4. `dash` (`/bin/sh`) is the exec env — `PIPESTATUS` is unavailable; use `cmd > log; RC=$?` for rc capture in loops.
5. `uv sync` in a temp base worktree is near-instant (shared uv cache ~1s) — base-commit adjudication via `git worktree add --detach /tmp/<name> <sha>` is cheap and strong evidence; always `git worktree remove` + verify (list + ls).

## Mutation-verify pattern (worked well)

- Minimal single-line semantic kill (`if False and <original condition>:`) at the exact effect-site beats commenting out whole blocks: syntax stays valid, restore is trivially verifiable via `git checkout -- <file>` + `cmp` against a pre-mutation backup + SHA256 + empty `git diff`/`git status` (5-way proof).
- A well-written pin test makes this self-evident: the round-2 pin's failure message names its own kill site (`daemon/graph.py:7499-7522`) — that message quality is itself evidence the pin is genuine (cross-validated by the static mock audit).
- Discipline that mattered: mutated window runs ONLY the pin test; full-module re-run after restore proves zero contamination.

## Finding F1 (routes to developer, NOT fixed by gate)

`tests/unit/test_graph_retry_integration.py::TestReactiveCompaction::test_reactive_compaction_returns_none` — PASSES on base, FAILS on HEAD: asserts single `compact_state` invocation, but the lane intentionally re-invokes with `force=True` (punch-through) when the first pass returns None. Stale test vs intentional behavior; CLE re-raise contract still holds. Test-contract update owed (expect 2 calls, second with `force=True`) — or reconsider the reactive-path punch-through if unintended. Pattern: **behavior-changing lanes must sweep PRE-EXISTING tests asserting call-count contracts on the changed seam**, not just the lane's own modules.
