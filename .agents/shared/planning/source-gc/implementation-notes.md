# source-gc — Implementation Notes (Phase P4, coder lane)

Companion to the frozen plan set in this directory. The plan docs
(plan-overview.md, decisions.md, phase4-plan.md,
architecture-recommendation.md) are FROZEN and were NOT edited. This
file records what the implementation actually did, where it drifted
from the plan's anchors, and the one deliberate deviation.

## W1 — verified shutdown anchor (grep-once, plan council binding)

Plan anchor: `grep -n "app.state.tmp_image_cleanup = None" daemon/api.py`
→ **:4005** in this tree (verified). The shutdown mirror was inserted
AFTER :4005 and BEFORE the `# --- PlaneSyncWatchdogService shutdown (Phase 3) ---`
comment at :4021 — i.e. at the BLOCK BOUNDARY, never mid-block, per
the W1 re-point. Inserted block is :4007-:4019.

## Start-block anchor drift (±1 line, semantic match)

The plan's start-block anchor ("insert immediately after :1866 (tmp-image
INFO end; live-view block begins :1867)") is off by one line in this
tree: the tmp-image `daemon_logger.info(...)` closes at **:1865**, line
:1866 is the blank separator, and the live-view comment banner begins at
:1867. The block was inserted at the block boundary — after the
tmp-image INFO and before the live-view banner (block :1881-:1894) — so
the seam intent is preserved exactly. Cause: the plan's :1866 was
verified @ 6b571771d; this tree is 6b571771d + the docs-only plan
commit 49dc5f514, which did not touch api.py, so the drift is a
line-counting artifact in the plan (the `)` of the info call is :1865,
not :1866). No behavioral impact.

## Deliberate deviation 1 — `gc_archive_and_delete` signature

Plan binding signature (phase4 §1 / §6 / decisions D6): `gc_archive_and_delete(*, source_id, now, dry_run)`.
Implementation signature: `gc_archive_and_delete(*, source_id, now, retention_days: int = 14, dry_run: bool = False)`.

Reason: the plan's own AM-1 four-leg re-assert enumerates an AGE leg
("age re-parsed from fresh updated_at", decisions D6 bullet 2; phase4 §3
fence 6; commission text), and P16's "drop a leg → RED" mutation
needs that leg to have teeth — which requires a retention threshold the
binding signature does not carry. The repo cannot read config (no
config dependency in the repository layer, and manager.py is on the
NOT-TOUCHED list, so the factory cannot gain a retention parameter).
`retention_days` is therefore an ADDITIVE keyword with the shipped
default (14) as the fail-closed fallback: the service always passes the
live knob; plan-signature callers keep working and still get the age
leg at production-default strength. The deviation is backward-compatible
with every binding call site and strengthens the fence (never
disables the age check). Recorded in the method docstring.

## Deliberate deviation 2 — `_ARCHIVE_COLLISION_WARNED` placement

phase4 §1 places the latch in the "Service file" row; phase4 §2 AM-3
("the repo method keeps a module-level per-source-id latch") and
decisions.md D1 AM-3 place it at the IntegrityError catch site in the
repository module. The latch is defined at module level in
`daemon/repositories/source/repository.py` (the catch site) because that
is where the IntegrityError is raised and the WARNING is emitted. The
service does not re-implement it; it surfaces the resulting skip via
`GcRowResult.skipped_reason`. The §1 service-row mention is a
cross-reference to the same latch, not a second definition.

## Implementation mechanism — remainder computation

The binding `gc_find_candidates` signature returns only the tick's batch
window plus `skipped_unparseable`; the §8 observability contract
requires a `remainder` field (§8.2/§8.3) and §8.8 makes
`remainder=0 deferred` the operator completion signal. The service
therefore re-queries `gc_find_candidates` with a large companion limit
(`_TOTAL_PROBE_LIMIT = 100_000`): dry-run computes
`remainder = total - window` (before the tick); destructive computes
`remainder = len(probe)` AFTER the tick's work (honest still-eligible
count, including per-row failures and raced skips that retry next
tick). Cost is trivial at the live scale and grows linearly with the
archive trip-point; documented here because the plan does not prescribe
the mechanism. No binding signature was changed.

## AM-5 strings

Landed verbatim at daemon/tools/scheduling.py:18 (module docstring) and
:236/:247 (paused field descriptions). The docstring copy is on a single
logical line (the verbatim string appears contiguously) rather than
wrapped across lines, so a plain `grep -F` matches it; the Field
descriptions are single-line already. REST untouched (ScheduleUpdate has
no paused field).

## P11 boot-no-behavior-change

Implemented as a subprocess `git diff --stat 6b571771d..HEAD -- daemon/sources/ daemon/constants.py`
asserting empty output. Guarded by `pytest.skip("git not available")`.
Anchor 6b571771d is reachable from this branch (verified at session
start: HEAD == 49dc5f514, whose ancestry includes 6b571771d).

## P20 seal-trip

`tests/test_chart_image_delivery_e2e.py` seal tripwire: 1 passed
(unchanged). Touch set verified: daemon/sources/*, daemon/constants.py,
daemon/manager.py untouched; seal scope empty per plan §5.

## P16 lock-half — PG interleave leg (added on follow-up)

New file: `tests/postgres/test_source_gc_lock_interleave_pg.py` (2 nodes,
PG-gated via `@pytest.mark.postgres`; deselected by default — confirmed
`37 passed, 2 deselected` when run alongside the unit packs).

W2 branch status: LEG WRITTEN AND EXECUTED LOCALLY (PASS, 2/2 in 4.20s
against the throwaway `ensemble_test` DB at localhost:5432). Result:
- `test_p16_lock_interleave_blocks_resume_then_degrades_to_not_found`:
  connection A acquired `SELECT ... FOR UPDATE` on the row, connection B's
  resume `UPDATE` blocked with a lock-timeout ("canceling statement due
  to lock timeout"), A committed the GC delete, B retried and found the
  row gone, and `gc_archive_and_delete` reported `skipped_reason="not_found"`
  on the gone row.
- `test_p16_positive_control_uncontended_deletes_cleanly`: an
  un-contended collectable row deleted cleanly under the same lock path
  (`deleted=True`, `archived_config=True`, archive row landed).

Executable command for the tester phase (no PG env vars set → skip):
    PG_TEST_DB=ensemble_test PG_TEST_USER=ensemble PG_TEST_PASSWORD=ensemble_dev \\
      pytest tests/postgres/test_source_gc_lock_interleave_pg.py -m postgres \\
      -p no:cacheprovider -o addopts=

No other files touched; no refactors; no test rewrites. Unit packs remain
unchanged (37 passed).

- ① d36e341ea — feat: source-gc archive tables (migration + models, 3-site index lockstep)
- ② dc76082be — feat: source-GC repo methods (gc_find_candidates / gc_archive_and_delete / gc_count_error_rows)
- ③ a6b181ead — feat: SourcesGcService — loop sweep + knobs + api.py wiring
- ④ bc559c81f — feat: AM-5 pause-disclosure strings in scheduling.py (verbatim)
- ⑤ a123bf746 — test: source-gc P1-P21 unit packs (repo + service layers)
- ⑥ (this commit) — docs: implementation notes

## P10 child-first FK order — PG leg (added on second follow-up)

New node in `tests/postgres/test_source_gc_lock_interleave_pg.py`:
`test_p10_child_first_fk_order_under_real_pg` (3 nodes total now).

Seeds a collectable scheduler source with BOTH children (3
`schedule_executions` + 1 `instance_mappings`) under real PostgreSQL FK
enforcement (models.py:113 + :168, NOT NULL, NO ACTION). Runs
`gc_archive_and_delete` and asserts: (a) `deleted=True` with
`deleted_executions=3`, `deleted_mappings=1` (clean completion IS the
order proof — a wrong order would raise ForeignKeyViolation and abort the
txn); (b) archive rows landed 1:1 (config + 3 executions); (c) live
children + parent are gone.

Executed locally: PASS (3/3 in 5.07s against ensemble_test).

## P7 coverage mapping (verifier disclosure closure)

P7 (batch cap) is adjudicated COVERED by P17's
`test_cap_defers_newest` (repo pack) — no separate instrument added.
P10 now has its PG instrument (above). P16 lock-half has its PG
instrument (previous follow-up). All retained pins P1–P15 are covered
except P11 (unit-level git diff assertion).



## Fix cycle (P5 folds) — landing map

Per-item disposition for the code-council + tidier folds. Commits are
listed oldest→newest after the follow-up commits.

| Item | Disposition | Commit | File:line |
|---|---|---|---|
| W8 (P11 lane-only span) | LANDED | 1ecfe2fca | tests/unit/repositories/test_source_gc_repository.py (P11 test) |
| W2 (destructive chat-fence teeth) | LANDED | fdc9e70cf | tests/unit/repositories/test_source_gc_repository.py (TestChatFenceDestructiveTeeth) |
| W4 (dry-run guard) | LANDED | f95f1b213 | tests/unit/repositories/test_source_gc_repository.py (TestDryRunGuard) |
| W1 (P16 lock seam via listener) | LANDED | e17b4668a | tests/postgres/test_source_gc_lock_interleave_pg.py (positive control) |
| W3 (raced-skip coverage) | LANDED | f95f1b213-era + fdf01ed18 | tests/... (TestRacedSkip) + daemon/repositories/source/repository.py (raced branch zero-telemetry) |
| W5 (stuck= in summary line) | LANDED | fdf01ed18 + f2909e289 | daemon/services/sources_gc_service.py (report["stuck"], log lines) |
| tidier 1 (dry-run probe unguarded) | LANDED | fdf01ed18 | daemon/services/sources_gc_service.py (try/except around total probe) |
| tidier 2 (raced zero telemetry) | LANDED | fdf01ed18 | daemon/repositories/source/repository.py (raced branch) |
| tidier 3 (func.count) | LANDED | fdf01ed18 | daemon/repositories/source/repository.py (gc_count_error_rows) |
| tidier 4 (import hygiene) | LANDED | fdc9e70cf-era (folded into the same test-pack hygiene pass; landed alongside W2/W4) | tests/unit/repositories/test_source_gc_repository.py, tests/unit/services/test_sources_gc_service.py |
| tidier 5 (start() disabled comment) | LANDED | fdf01ed18 | daemon/services/sources_gc_service.py (start) |
| tidier 6 (extract _run_destructive_tick / _log_tick_summary / archive assembly) | PARTIAL — archive-row assembly extracted into `_archive_rows` (behaviour-neutral); the sweep_once per-row loop + summary-log extraction was NOT done. Argument: sweep_once is already phased with clear comments and the mutation-teeth tests (W2/W3/W4) are the binding P6 gates, not the refactor; further extraction churns ~190 lines for reviewability with real regression risk. Recorded as deferred-to-reviewer. | fdf01ed18 (partial) | daemon/repositories/source/repository.py (_archive_rows) |
| Lows (seconds-per-day constant, __all__, "Restart to flip.", trailing newline, _GC_DISCLOSURE, soften "default 14d", alphabetical imports) | LANDED | fdf01ed18 + f2909e289 | repository.py, models.py, sources_gc_service.py, scheduling.py |
| W6 (lock order deadlock) | REVERSED -> LANDED (fix). Verifier adjudicated the premise false: PG DELETE takes row-level locks held to commit, so delete_source_config (children then parent) vs gc_archive_and_delete (parent then children) forms a real lock-order cycle on the same source_id. Fix (a): delete_source_config now acquires the parent row lock FIRST (with_for_update=True) before child deletes — both paths take the parent lock first. | fdf01ed18-era + (this commit) | daemon/repositories/source/repository.py (delete_source_config) + tests/postgres/test_source_gc_lock_interleave_pg.py (listener + blocking leg) |
| W7 (StaleDataError on concurrent resume) | LANDED (fix) | fdf01ed18 | daemon/repositories/source/repository.py (update_source_config) — catches StaleDataError -> None; scheduling_service maps None -> ValueError("Schedule not found") (no NOT-TOUCHED file touched; update_source_config lives in repository.py, which is on the touched list). |

## W6 decision (REVERSED — fix landed)

The verifier adjudicated the original argument INCORRECT: PostgreSQL DELETE
acquires row-level locks held to commit, so the REST path is NOT lock-free.
Cycle: gc_archive_and_delete takes the PARENT lock then deletes children
(wants child locks); delete_source_config deletes children (takes child
locks) then deletes the parent (wants the parent lock) -> circular wait
when both target the same source_id. PG aborts one txn: the GC self-heals
(per-row except -> skip) but the REST path surfaces HTTP 500. Medium-High,
intermittent, load-dependent.

Fix (leader-authorized shape (a)): delete_source_config now acquires the
parent row lock FIRST via session.get(SourceConfig, source_id,
with_for_update=True) before any child delete. Both paths now take the
parent lock first -> no cycle. Delete order, return shape, and all other
behavior unchanged beyond locking. The rejected alternative (children-before-
parent in GC) was NOT taken — it would re-open the AM-1 race window.

Pin: tests/postgres/test_source_gc_lock_interleave_pg.py gained
test_w6_delete_source_config_locks_parent_first (listener node asserting
FOR UPDATE emitted by the REAL method, plus a two-connection blocking leg).
Mutation-meaningful: removing with_for_update=True REDs the listener node.
Executed locally: PASS.

## REST update_source edge (ROUTED — not fixed here)

Verifier finding (LOW): routers/sources.py :446-447 calls
_source_to_info(updated) with no None check after our StaleDataError -> None
change in update_source_config. Narrow race window (row vanished between
read and commit). NOT touched: routers/sources.py is on the plan's
NOT-TOUCHED list and needs explicit leader authorization before editing.
Routed to the leader; no code change in this lane.

## W7 decision (recorded)

Reproduced by reasoning + a focused smoke (row vanished between read and
commit). Fix landed in repository.py (NOT a NOT-TOUCHED file):
update_source_config catches sqlalchemy.orm.exc.StaleDataError -> rollback
-> None, and the scheduling_service resume path already maps None to
ValueError("Schedule not found: ...") (scheduling_service.py, :1119-1121
family), so the caller sees the honest 404-family outcome instead of HTTP
500. No NOT-TOUCHED file was touched; no fence exception required.

## D2 amendment (leader-authorized, recorded)

AM-5 disclosure text was ratified VERBATIM (D2 ratification condition 1).
This cycle softened "past the default 14d retention" to "past the
configured retention window (default 14d)" because retention is configurable
(SERVICES_SOURCES_GC_TERMINAL_RETENTION_DAYS), not fixed at 14d. The
disclosure is now extracted as the module-level `_GC_DISCLOSURE` constant
in daemon/tools/scheduling.py and referenced by both paused Field
descriptions. No pin in this repo asserts the old verbatim text, so no
test change was required. Amendment recorded here per the leader's
instruction.

## Ceremony completion criterion (W5)

Updated: the operator watches the work-log for
`remainder=0 deferred` OR `stuck=<N>` with remainder equal to stuck
(stuck-only) as the completion signal — the latter means only
latched-collision ids (permanently uncollectable without operator action)
remain eligible, and RETENTION can be restored.


## Leader authorization — routers/sources.py None-guard (fence carve-out)

AUTHORIZATION: explicit leader authorization for a single confined hunk in
`daemon/routers/sources.py` (otherwise NOT-TOUCHED).

Rationale: the AttributeError-500 is SELF-INTRODUCED by our W7 change
(`update_source_config` now catches `StaleDataError` -> `None`; the REST
`update_source` path called `_source_to_info(updated)` with no None check,
so a vanished row now raises AttributeError -> HTTP 500 instead of a clean
404). The NOT-TOUCHED fence existed to coordinate against the
sources-regression lane, whose work is now merged + staged as v0.29.2 —
so the coordination rationale for fencing routers/sources.py has lapsed.

Scope: ONE hunk, immediately after the `update_source_config` call in
`update_source`: `if updated is None:` -> raise the house 404 shape
(`HTTPException(404, ErrorResponse(code=ErrorCodes.SOURCE_NOT_FOUND,
message=f"Source not found: {source_id}").model_dump())`), mirroring the
other 404 sites in the same file. Nothing else in the file changed.

Fence status: FENCED-EXCEPT-SANCTIONED-HUNK. P11 now pins the sanctioned
hunk by exact line content and asserts the NOT-TOUCHED diff over both the
pre-merge span (6b571771d..2c6b7ab94 -> empty) and the post-merge span
(<latest first-parent merge>..HEAD -> exactly the sanctioned hunk or empty
if a future drift sync absorbs it) stays EXACTLY the sanctioned hunk. Any
additional drift REDs; removing the guard REDs the file pin.

## Self-verification

- Repo pack: 27 passed (tests/unit/repositories/test_source_gc_repository.py)
- Service pack: 10 passed (tests/unit/services/test_sources_gc_service.py)
- Seal tripwire: 1 passed
- Imports: daemon.config / daemon.services.sources_gc_service /
  daemon.repositories.source.repository / daemon.repositories.source.models /
  daemon.tools.scheduling all import cleanly
- Config knobs: defaults + fail-fast (ge=1) verified
- Service smoke: dry-run always-logs + writes nothing; destructive
  collects + first-destructive marker exactly once; no-op silent;
  disabled line + no task; first-tick-immediate (sweep before first sleep)
- Repo smoke: find fences (chat/enabled/age/status), collision latch
  (warn then silent), archive 1:1 integrity, child-first delete counts,
  raced skip, gc_count_error_rows (excludes chat rows)

## Not verified (and why)

- Real-PG two-connection interleave leg of P16 (lock half): SQLite
  renders with_for_update as a no-op (instance/repository.py:1949-1954),
  so it cannot be exercised in the unit pack. Per plan §7 P16/W2, this
  is the tester's (P6) real-PG leg or an explicit inspection-verified
  disclosure. The re-assert half IS unit-pinned (enabled flip +
  updated_at re-stamp + positive unresumed delete).
- Real-PG child-first FK order (P10): SQLite has no FK enforcement by
  default; house PG-gated file is the tester's leg (tests/postgres/
  convention, PG_TEST_* DSN, deselected by default). Repo method uses
  the same child-first order as delete_source_config :522-534.
- Full daemon boot smoke (env-poison rules, ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0,
  sandbox self-env, throwaway PG DB, state.json fence) — routed to P6.
- Whole-tree test run — NOT performed per commission (scoped corpus only;
  full functional gate is the tester's). Only the authored packs + seal
  tripwire were run.
- Concurrent foreign lane (sources-regression, 4 unmerged commits touching
  source/repository.py): this lane appended repo methods at the END of
  the file (after delete_source_config) and added two models at the END
  of models.py, so the merge surface is append-only and should merge
  cleanly; the regression lane's changes to the middle of the file
  (source/repository.py) are untouched by this lane. No merge was
  performed (worktree isolation rule).