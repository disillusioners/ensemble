# Shared-process module-identity pollution (sys.modules teardown) + gate patterns — 2026-08-28

Context: gate on `feature/injection-marker-serialization` (@51f5dc54, base 22d03844). Four reusable lessons.

## 1. `_RealLangGraph`-style sys.modules teardown MUST restore identities, never delete

**Root cause.** `tests/conftest.py` globally mocks `langgraph.*` (conftest.py:213-221). The branch's new integration file used a context manager that evicted the mocks (to get real LangGraph) and on `__exit__` DELETED `daemon.persistence` / `daemon.graph` / `daemon.manager` / `daemon.compaction` from `sys.modules`. In a SHARED pytest process, later test files then re-imported those modules FRESH: their collection-time `from daemon.persistence import X` bindings still referenced the ORIGINAL module object, while `patch("daemon.persistence.X")` patched the FRESH one → patches never intercepted → real code ran → real lazy import hit the conftest langgraph mocks (`ModuleNotFoundError: langgraph.checkpoint.postgres` despite the package being installed) plus silent assert-call-count failures.

**Why it hid from every individual run.** Triage matrix (all reproduced): victim file standalone 23/23 PASS; polluter+victim pair → 2 FAIL; victim + 2 non-polluter files (control) → PASS. Only the pair combination exposes it. Single-file CI or per-file xdist would NEVER see it.

**Collateral signature.** 108 httpx setup errors (`TypeError: object.__new__() takes exactly one argument` at `httpx.AsyncClient(...)`) + 5 body failures across 6 files (opencode/api/vscode) that all pass 48/48, 37/37, 13/13, 9/9, 4/4, 8/8 in isolation — the httpx TypeError is what broken module identity surfaces as, NOT an httpx version problem. Before blaming the environment, run the affected file in ISOLATION.

**Fix pattern (commit 83a1a8b7, +17/−5).** `__enter__` snapshots the daemon module OBJECTS; `__exit__` re-binds the SAME identities into sys.modules instead of deleting. Post-fix: 4-file bundle 215/215; 7-file shared-process discrimination run → httpx errors 108→0.

**Rule.** Any test that swaps `sys.modules` entries must restore the ORIGINAL OBJECT IDENTITIES on exit in a repo where other tests patch by dotted path. Deleting repo modules mid-session splits patch-target identity. (A pytest-shared-process triage skill was captured by the worker: `pytest-shared-process-pollution-triage`.)

## 2. Discriminating "env error" from "pollution" — the isolation run

When a sweep shows setup ERRORs (TypeErrors at construction, ImportError on installed packages): (1) re-run ONE affected file standalone; (2) if it passes, the sweep failure is shared-process state, not environment; (3) find the polluter by pair-bisection (integration files that manipulate sys.modules are prime suspects); (4) verify the fix with the polluter+victim pair in one process. Cost: seconds. Prevents wrong "pin the dependency" conclusions.

## 3. Base-evidence worktree A/B — the cheap unmapped-failure killer

26 sweep failures could not be mapped to any QUARANTINE family. Worktree A/B (`git worktree add /tmp/x 22d03844`, run the exact test IDs both sides, same `.venv`, cwd=worktree so `tests/__init__.py` package marker puts the worktree root first on sys.path) resolved all 26 in one dispatch: 20 identical-signature pre-existing, 1 flaky (passes isolated 4/4), 5 env-artifacts. Result: 0 new regressions, and a new QUARANTINE family row so future gates classify on sight instead of re-deriving. Note: hypothesis/property tests need 2×/side (base even had an EXTRA failure mode HEAD no longer hits — the branch REDUCED failure surface).

## 4. Recurring pattern: frozen asserts vs call-contract changes (4th recurrence)

`test_job_create_explicit_source_not_overridden` asserted `source=='manual'`; production (P2.3 B3.5 anti-forgery) unconditionally derives `agent:<caller>`. Same class as the 3 prior spawn_team_members recurrences. Fixed test-only (1884d95c: rename + invert + docstring citing the anti-forgery contract). Standing advice stands: on ANY production call-contract change (kwargs, refusal text, source derivation), `grep -rn "assert_called_once_with\|== 'manual'\|== '<literal>'" tests/` around the seam before hand-off.

## Meta: write-tool discipline

This gate's own PACKS/QUARANTINE edits hit the known silent-corruption class twice: (a) an edit anchored on a DISPLAY-TRUNCATED line prefix glued the original tail onto the inserted row; (b) an edit_file "not found" on bytes grep confirmed (transient). Both caught by immediate grep-re-verify + line-length/tail scans and repaired via python heredoc with count==1 assertions. Re-verify EVERY doc write in this repo — do not trust the SUCCESS reply alone.
