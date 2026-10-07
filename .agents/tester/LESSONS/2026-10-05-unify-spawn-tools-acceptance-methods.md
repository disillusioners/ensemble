# LESSONS — unify-spawn-tools acceptance gate (2026-10-05)

Gate: worktree `/home/nea/ensemble-src-wt-unify-spawn-tools` @ `b822c7ec` vs base `ac399874`.
Full evidence: `RESULTS/2026-10-05-unify-spawn-tools-acceptance.md`; artifacts `/tmp/unify_recon/`.

## 1. Per-test timeout levers are silently no-op on asyncio stream hangs
`pytest-timeout` with `--timeout=60` — BOTH signal and thread methods — never fired on tests wedged in
`selectors.poll` inside an asyncio event loop (`llm_stream_watchdog.py:246` → `openai/_streaming.py` iter_bytes).
The per-test timer anchors to a running test; an event-loop block parked in a C-level poll defeats both methods.
**Fix that worked: file-level diagnostic bisect** — run each file solo with `timeout 90`, capture rc WITHOUT a pipe
(`; echo rc=$?` — pipes mask rc via `tail`), rc=124/1-with-Timeout-marker = wedge host. Then re-pack the chunk
minus the wedge host ("wedge-minus-host pattern") to cover the remainder. Named hosts this way: `bound_escalation`,
`revive_after_escalation`, `performance`, `idle_orphan_incident`; exonerated siblings: `marker_bound`, `runbook_drift`.

## 2. Count TESTS, not FILES, when splitting chunks
A 15-file unit held 606 tests (opencode ~40/file); a 5-file unit held 18. File-count splits twice produced
280s-cap TIMEOUTs on content that later ran in 2.7s once correctly sized (~150-175 tests/unit at mixed rates).
Perfile counts from `--collect-only -q` are cheap (113s once) and make splits deterministic.

## 3. Runner word-splitting breaks on parametrized ids with spaces
`pytest $(cat chunk.txt)` splits `test_x[param with spaces]` into phantom args → rc=4 usage error, ZERO tests run,
looks like a collection failure. **Durable fix: `mapfile -t NODEIDS < "$CHUNK_FILE"` + `"${NODEIDS[@]}"`.**
Also: pytest `--collect-only -q` output is BYTE-FAITHFUL for `\u2192`/`\u2014` escapes in param ids — a
grep-based "over-escaping" heuristic produced a false-positive stop; verify via pytest-API repr before halting.

## 4. Base-attribution A/B is cheap and settles everything
Detached worktree at base + `uv sync` (cache-warm ~2s) + same-file isolated runs both sides + normalized
FAILURES-section diff (strip paths/line numbers/addresses/uuids/timestamps) → byte-identity proof.
8 legs attributed 277 reds in ~45 min of worker time; exactly 1 branch-caused (a seal-guard, process-class).
Highest-value targets first: reds in files the branch TOUCHES (manager.py, registry.py, instance.py adjacency).

## 5. Argless pytest is lethal in this repo
No `testpaths` configured → pytest scans the stray `test/` probe-pack tree; `test/packs/wc_wake_off_bytecompat_probe_test.py`
calls `sys.exit(1)` at import → INTERNALERROR aborts the whole session even with `--continue-on-collection-errors`.
ALWAYS pass explicit file lists (the chunk-runner pattern).

## 6. Collapse clusters for the fix backlog (all PRE-EXISTING at base, quantified by this gate)
- `service_tool` stale Mock fixture: **23 setup-ERRORs** (context7 ×4, webfetch ×2, builtin_mcp ×17) — one fixture repair clears all.
- vision `allowed_models` env/config family: **30+ reds** (governor 16+10, send_message 22 fixture-variant, documented 6, +singles).
- mirror-seam admission/status reconciliation: **15+ reds** (turn_reconciler 3, complete_cancel 4, pause/resume 4, proxy_phase1 7-QUARANTINE-docstrings).
- PG-dialect migration `20260714_000001` on SQLite fixtures: **27 reds** (progressive_dispatch 18 + phase4/skill services 9).
- Hardcoded macOS paths (`/Users/nguyenminhkha/...`, `/opt/homebrew/...`): **~8 reds** — unrunnable on Linux by construction.
- PP1 zero-claimed terminal fire at `work_notifier.py:886`: real defect, EXISTS AT BASE (defect-detection red, not branch-caused) — commission-worthy independently.
