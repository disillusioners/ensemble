# langgraph-checkpoint-perf-v2 — Final-Gate Whole-Branch Review (Deep-Review Council)

> **Verdict: ✅ APPROVE-WITH-NOTES — 0 🔴 / 3 🟡 — branch ready for the user's personal review (no merge; user decides)**
> Date: 2026-09-04 (UTC)
> Branch: `feature/langgraph-checkpoint-perf-v2` @ `1cbad96d` · Range: `2f80d45b..1cbad96d` (40 commits, 91 files, +21945/−64)
> Method: 🔴 Deep-Review council — governor `e2b9e050-c8e1-4e05-a804-663755f50820` (`cpv2-final-gate-council`) under reviewer[v2] tree-root `347fa33b`. 2 councilors (only 2 canonical models exist: `agentic` + `coding`; requested 4-brief partition combined A+B / C+D, one brief per councilor, disjoint coverage, zero unresolved disagreement). `code-review` skill per councilor; ⛔ read-only directive honored; every executed test DSN-pinned to `ensemble_cpv2_test`; worktree rev-parse bracketed at `1cbad96d`.
> Boundary: per-phase reviews + T5.7 re-review NOT re-litigated (all previously green). Mandate was the cross-phase / whole-branch pass only.

## Findings for the developer (all cross-surface — invisible to per-phase reviews by structure)

### 🟡 1. Side-table orphan leak on the two non-`_cleanup_instance` deletion paths
- `daemon/services/instance_lifecycle.py:2674` — `hard_delete_instance` sweeps `adelete_thread` per tree_id but never calls `delete_for_thread`; `daemon/services/maintenance.py:560` (Operation A `_cleanup_orphaned_threads`) likewise; `_cleanup_instance` step-2 failure window (`maintenance.py:898`: instance row deleted → `adelete_thread` raises → Op A sweeps next cycle) funnels into the same leak.
- T5.19's "fully-cleaned instance leaves ZERO side-table rows" invariant (`daemon/repositories/message_metadata/repository.py:152-158`) is enforced on **1 of 3** deletion surfaces.
- Fix: wire `delete_for_thread` into both loops (same never-raise pattern) or add a periodic anti-join sweep.

### 🟡 2. FR-6 reason-category taxonomy drift + phantom citation
- `daemon/persistence.py:481` emits `reason={type(exc).__name__}` (e.g. `reason=RuntimeError`); the documented token `repo_exception` (`requirements.md:88` + 5 more canonical artifacts incl. `phase5-final-results.md:113`) appears in **zero** log lines — operator/runbook greps for the documented category find nothing. Commit `de7b3f78` claimed alignment.
- `phase5-final-results.md:113` cites `_resolve_repo_missing_reason()` "at persistence.py:495" — no such function exists (inline conditional at `:491-496`).
- FR-6 also still mandates `message_id` + per-message lines while F7's per-thread LRU supersedes by design — never amended.
- Fix: align one direction (emit `reason=repo_exception(<class>)` OR amend FR-6 + closure docs to the class-name contract); fix the stale citation; record F7 semantics in FR-6.

### 🟡 3. Unpinned construction kwarg — silent T5.19 revert class
- `daemon/manager.py:2306` — `CheckpointCleanupJob(..., message_metadata_repo=self._message_metadata_repo)` has no AST pin. Dropping this kwarg silently disables the T5.19 prune (constructor default `None` = backward-compatible skip) with **zero test failures**; symptom is slow table growth only. Same silent-kwarg-drop class the branch itself already pinned for the tap-slot kwargs (its own docstring: "Dropping either kwarg… ZERO test failures").
- Fix: mirror the lifecycle-wiring AST pin onto this call site (assert kwarg passed non-None).

## Consensus zones (both councilors, high confidence — verified, no disagreement)
- **Data-safety:** destructive prune structurally unreachable by default (dual-flag ladder; single gated DELETE call site `checkpoint_prune.py:215`; AST dominance test; `ZERO_REFS_FAIL_SAFE` outranks the destructive flag). SERIALIZABLE wrap + 40001/40P01 retry + HONEST LIMIT wording + race tests intact at tip post-T5.19 (T5.7 folds not regressed). Migration `20260825_000001` dual-driver safe (SQLite checksummed/ordered; PG `create_all` + idempotent `_ensure_postgres_columns`; index name byte-identical across 3 surfaces). No prune on the default boot path (60s delay + idle gate).
- **Seams:** read join runs checkpoint-side; over-record benign, collapses on revive; `_cleanup_instance` ordering (row delete → `adelete_thread` → `delete_for_thread` → callback) correct; AC-13.3 read→revive→read 4/4; revive block untouched by the diff.
- **Drift:** merge-base `2f80d45b` = latest tip — port sits ON TOP of all 9 days of churn, structurally eliminating the revert/shadow class; `checkpoint_adapter.py` +401/−0 pure addition; zero mission/settled vocabulary regressions (all 82 removed daemon lines accounted for).
- **Hygiene/doc-truth:** cherry-pick provenance on every port commit; zero protected-path hits; no `git add -A` sweeps; numbers cross-check (40 commits / 91 files / +21945−64; tests 421→439→484→535; planner-cache 8.557→0.064ms; abs-delta 2.0ms; dual-flag defaults); zero surviving aput-atomicity claims.

## Verification basis / scope notes
- §33 no-saver-imports-in-routers guard **RUN fresh** by councilor: 6/6 PASS, DSN-pinned, rev-parse bracketed at `1cbad96d` (only fresh gate execution this review).
- 9/9 binding gate NOT re-run (explicit budget constraint; T5.7 council evidence accepted). All other evidence static at tip.
- T5.11 send_message-revive coverage gap: documented + signed off for v3 — out of scope, not a silent hole.
- Pre-existing maintenance TOCTOU (terminal instance revived concurrently with cleanup) predates the branch; no new seam introduced.

## Adjudicated touching point
Councilor-2's C-pass stated three structured reasons (`row_absent`, `manager_missing`, `repo_missing`) "all observable"; Councilor-1's A2 grep dive found the *exception* path emits the class name and `repo_exception` never appears. Not contradictory (observability of emitted tokens vs documented-vs-emitted taxonomy); governor sided with Councilor-1 on taxonomy — the discrepancy itself corroborates Finding 2.

---

# Verify Round (fix pass `68b3a2a3..2ed11d66`) — 2026-09-04

> Method: 3 execution-based verify workers (`code-review` skill, one per finding), rev-parse bracketed at `2ed11d66`, DSN-pinned disposables only, ⛔ read-only held.
> **Verdicts: Finding 1 RESOLVED · Finding 2 RESOLVED · Finding 3 RESOLVED · Runbook §10 PASS · Range hygiene PASS · Overall: branch READY-FOR-USER-REVIEW.**

## Resolution evidence (summary — full reports in session transcripts)

**F1 (`714f58ff`)** — canonical never-raise at BOTH new sites (`instance_lifecycle.py:2711-2736` hard_delete step-4b: sibling of the adapter block, iterates the FULL tree_ids snapshot, own per-tree try/except; `maintenance.py:568-592` Operation A per-thread); step-2 failure window closed via Op A orphan sweep (both checkpoint data and side rows removed next cycle); completeness grep: exactly 3 `adelete_thread` sites in daemon/, ALL now pruned (project-delete path funnels into Op A transitively); tests are real-PG row read-backs with real-class no-raise proofs (no mock-theater); live runs **5/5 new + 4/4 T5.19**, zero skips.

**F2 (`720a2a4a`)** — repo-wide `repo_exception` sweep (incl. hidden `.agents/`): **zero uncorrected live canonical prose**; all 13 sites in 6 artifacts carry dated `[Correction 2026-09-04, Fix 2]` annotations with originals preserved (amendment discipline, no silent rewrite); citations exact vs `persistence.py` (`:467` row_absent, `:481` class-name, `:493-496` inline conditional, F7 rationale `:458-462`); F7 supersession unambiguous; `phase5-final-results.md:127` verbatim test-quote byte-verified (312==312 chars) — JUSTIFIED, quote must not be edited; runtime doc claims verified by execution (21 passed).

**F3 (`d5e0f10b`)** — pin test runs green at `2ed11d66` (3 passed, pure AST, no DB); **negative proofs 4/4 + `**splat` probe replayed LIVE** against the pin's real functions on mutated source under /tmp — kwarg-drop / None / second-site / relocation-out-of-`initialize()` / dict-splat ALL fire; not theater. Original finding premise re-verified: constructor default None = silent skip (`maintenance.py:389`, gated `:568`/`:945`), zero errors anywhere on drop.

**Runbook §10 (`71c1072b`+`58fc1abd`)** — PASS: SQL precedence fix correct (`AND (… OR …)` parenthesized; removed `current_setting()` would-throw); log-line citation exact post-correction (`persistence.py:199-202`); `.env`-clobber + misleading-print notes verified against real `dev.sh:58-63` / `.env:57` / `factory.py:189-198` / zero-`dotenv`-in-daemon.

**Range hygiene** — PASS: explicit-path discipline per commit; ZERO protected-path hits (planning edits confined to the branch's OWN planning dir); `+916/−27` = per-commit-sum convention EXACT (net range diff +901/−12, 11 unique files); "+8 test passes" exact (3 pin + 5 deletion-path). Claimed "12 files" unreconstructible under either convention — bookkeeping note only, no hygiene impact.

## Residual follow-ups (non-blocking — developer's option: fold now in minutes or park)

- 🟡 **Pin scan scope is manager.py-only** (`tests/integration/test_checkpoint_cleanup_job_wiring_pin.py:38`): a future production construction site anywhere else in `daemon/` silently escapes the "exactly one site" invariant. 2-line glob fix (`daemon/**/*.py`). No live hole today — grep-verified single production site.
- 🟡 **Stale-on-arrival Pointer citation** (`docs/runbooks/checkpoint-blob-prune-restore.md:336`): cites `daemon/persistence.py:79-89` as F-DR1-2 root-cause — true only on `latest`; on this branch the DSN builder is `:136-151` (which the same section cites correctly). One-line refresh. Introduced by `71c1072b`; `58fc1abd` fixed the sibling but missed this one.
- 🟢 `manager.py:8616-8621` compose docstring omits step-4b (one-line tidier follow-up); deletion-paths test `:558-567` re-implements the repo DELETE instead of capturing the original pre-monkeypatch; test docstring mint line-pin `:218`; `requirements.md:88` cosmetic phrasing.
- 🔵 **log-truthy at new prune sites: LEAVE** (recommended): volume asymmetry (0-rows is the common case per-thread vs canonical's once-per-instance), Op A already emits the aggregate line, failures still WARN with exc_info, and the ran-vs-unwired ambiguity is covered by the d5e0f10b pin. `logger.debug` for the 0-row case is the ceiling if symmetry is ever wanted.

## Verify-round workers
- `779499f0-346d-4299-bd7b-90a95c2d58e1` (verify-w1-f1-deletionpaths) — F1 + 🔵
- `37a9a79b-a640-4bec-b633-12244a635b84` (verify-w2-f2-doctruth) — F2 + runbook §10
- `7b32ecd3-4242-4568-be72-70323d25ecb5` (verify-w3-f3-pin-hygiene) — F3 + hygiene
