# Functional Gate — restart-arm livelock fix (wi-6641579b)

**Date:** 2026-10-31
**Gate:** Independent functional verification (verification-only — NO source fixes)
**Unit under test:** `fix/restart-arm-livelock` @ `4be66e44c9b2089e709bbba01d1b39a6c3e15f3e` (lane commits `f392b18f1` fix → `d02fe3a1a` pins → `4be66e44c` doc-only fold; fold not re-adjudicated per brief)
**Base for reproduction/diffs:** `cbafcc8d9edf7d9be5b7216463903c109784cded`
**Interpreter:** certified pattern — worktree has no `.venv`; `cd <worktree> && /home/nea/ensemble-src/.venv/bin/python -m pytest …`
**Lane state at gate start (verified):**

```
$ git -C /home/nea/ensemble-src-wt-restart-livelock rev-parse HEAD
4be66e44c9b2089e709bbba01d1b39a6c3e15f3e
$ git status --porcelain
(empty)
$ git log --oneline cbafcc8d9..HEAD
4be66e44c docs: restart-arm livelock — correct own-txn predicate + lock_release_run_id docstrings
d02fe3a1a test: restart-arm livelock regression pins (mutation-verified)
f392b18f1 fix: restart-arm livelock — own-txn retryable skip + executor-exit closer hook
```

**Code map verified by reading (not trusting the brief):** PART 1 identity check at `daemon/tools/upgrade_journal.py:1341-1355` (probe `wait_s=0.0`, `busy == run_id and _in_flight_is_own_open_txn(...)` → WARNING + `return None` BEFORE the 30 s wait; helper `_in_flight_is_own_open_txn` :1367, torn-read → `False`; CAS body `_mark_wake_delivering_locked` :1399). PART 2 `close_restart_arm_executor_exit` `daemon/tools/upgrade_journal.py:2211-2313` (guards: op missing/non-restart/run_id mismatch; foreign in_flight run_id; flipped=true → logged no-op; torn read logged no-op; never raises; `lock_release_run_id` on success). Hook at `daemon/services/upgrade_journal_sweep.py:2364-2390` inside `_journal_executor_exit` (:2329), exception-guarded, `close_note` logged on success; BOTH reaper legs funnel through `_journal_executor_exit` (waitpid :2210, ChildProcessError :2218).
**Base shape confirmed:** at `cbafcc8d9`, `mark_wake_delivering` goes straight to `lock_acquire(install_dir, run_id, wait_s=30.0)` — no probe, no identity check (livelock shape present).

**Env fences applied to EVERY python/pytest invocation (no exceptions):**

```
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
  ENSEMBLE_SELF_ENV=dev timeout <N> /home/nea/ensemble-src/.venv/bin/python -m pytest …
```

(Per-variable scrub — the `env -u POSTGRES_*` glob idiom is a silent no-op and was NOT used. No port contact, no server boots, no writes under `/home/nea/agents-ensemble`.)

---

## G1 — ORIGINAL SYMPTOM, both directions — PASS

**Method:** throwaway detached BASE worktree (`git worktree add --detach /tmp/rl-gate-<pid>/base-wt cbafcc8d9`), pins file copied in as UNTRACKED (porcelain showed exactly `?? tests/unit/services/test_restart_arm_livelock_pins.py`), run there with the venv interpreter; HEAD run in the lane worktree (pins tracked there). `_FakeTime` shim keeps everything bounded — the 30-iteration wait burn is OBSERVED as counted no-op sleeps, never suffered as wall clock. Import surface pre-verified at base (all 18 names the pins file needs resolve at `cbafcc8d9` — enumerated via `git show cbafcc8d9:<file> | grep -c`, all ≥ 1), so the file COLLECTS at base and yields per-pin verdicts.

**AT BASE (livelock reproduced — 5 failed, 3 passed in 1.78 s):**

```
FAILED tests/unit/services/test_restart_arm_livelock_pins.py::TestOwnTxnRetryableSkip::test_mark_wake_delivering_skips_immediately_without_wait_burn
FAILED tests/unit/services/test_restart_arm_livelock_pins.py::TestOwnTxnRetryableSkip::test_sweep_tick_with_own_txn_lock_stays_clean_and_pending
FAILED tests/unit/services/test_restart_arm_livelock_pins.py::TestExecutorExitCloser::test_exit_minus_one_closes_arm_then_next_cycle_delivers
FAILED tests/unit/services/test_restart_arm_livelock_pins.py::TestExecutorExitCloser::test_exit_78_closes_arm_the_same_way
FAILED tests/unit/services/test_restart_arm_livelock_pins.py::TestExecutorExitCloser::test_flipped_txn_is_never_touched
5 failed, 3 passed in 1.78s
```

Real failure lines from base (the functional livelock, not an import error):

```
E   assert {'flipped': False, 'kind': 'restart', 'owner_pid': 213287, 'run_id': 'r-20261031-071535-d526', ...} is None
WARNING  daemon.tools.upgrade_journal:upgrade_journal.py:1324 upgrade_journal: mark_wake_delivering lock NOT acquired run_id=r-20261031-071535-d526 busy=r-20261031-071535-d526 — skip
E   AssertionError: assert 'flipped=true' in ''
```

i.e. at base: the incident seed (in_flight `{kind=restart, flipped=false, owner_pid=<own pid>}` + lock held by own pid + pending wake + journaled `executor_exit`) leaves `in_flight` OPEN after an observed exit (no closer), the CAS burns the 30-iteration wait (`lock NOT acquired` after the loop), and the retryable-skip warning never exists.

**Red-at-base list (EXACTLY the livelock-class pins — 5):**
1. `test_mark_wake_delivering_skips_immediately_without_wait_burn`
2. `test_sweep_tick_with_own_txn_lock_stays_clean_and_pending`
3. `test_exit_minus_one_closes_arm_then_next_cycle_delivers`
4. `test_exit_78_closes_arm_the_same_way`
5. `test_flipped_txn_is_never_touched`

**Green-at-base (3 — narrowing/control pins whose semantics predate the fix):** `test_foreign_holder_keeps_wait_semantics` (30-sleep burn is the ASSERTED original behavior), `test_foreign_run_exit_does_not_close_our_arm`, `test_exit_without_arm_envelope_is_harmless`.

**AT HEAD (cure — 8 passed in 0.93 s):**

```
$ cd /home/nea/ensemble-src-wt-restart-livelock && env -u … ENSEMBLE_SELF_ENV=dev timeout 300 …pytest tests/unit/services/test_restart_arm_livelock_pins.py -q --tb=short -rfE --timeout=120
........                                                                 [100%]
8 passed in 0.93s
```

Cure components proven: (a) immediate retryable skip with ZERO wait-loop sleeps (`assert fake_uj_time.sleep_calls == 0` + `"own open in_flight txn"`/`"retryable skip"` logged, wake stays pending, lock NOT released); (b) executor-exit hook closes the txn (`in_flight=None`, `pending_op=None`, `restart_abandoned` event with `exit_code=-1`/`exit_code=78` + `flipped=false` attribution, `lock_release_run_id` frees the lock); (c) the NEXT sweep cycle delivers the wake (`result.delivered == 1`, `enqueue_message.await_count == 1`, pending list empty).

**G1 wall time:** ~4 min (incl. worktree setup + import pre-verification; pytest legs 1.78 s + 0.93 s).

---

## G2 — MUTATION VERIFY, both parts independently, digest-proven — PASS

**Method:** separate throwaway worktree `mut-wt` detached at `4be66e44c` (porcelain clean, pins tracked). Pristine baseline in it: `8 passed`. Membership derived from pin names/docstrings BEFORE planting (recorded in the gate transcript); planted via exact line-range excision with content assertions; per leg: RED membership at mutation → `git checkout --` restore → restored sha256 must equal pristine → re-run 8/8. Pristine digests:

```
3224cdd1f6723a2d729d68b6f71114dbf2cca62b35683f4c83c1d350ba2c318c  daemon/tools/upgrade_journal.py
366d543e9d9636f60576d288479b7ad34ab484154e09e7629cbfe6b5cfd3b137  daemon/services/upgrade_journal_sweep.py
```

| Mut | Surface planted | Planted sha256 | RED pins (observed) | RED predicted | Count match | Restored sha | Restore run |
|-----|-----------------|----------------|---------------------|---------------|-------------|--------------|-------------|
| M1 | remove PART-1 probe+identity block, `upgrade_journal.py` file lines 1341-1355 (15 lines) | `3277d7546bfabafe4909846b819dd8823ea87efda18c2a6350a001882a5aa1ba` | `test_mark_wake_delivering_skips_immediately_without_wait_burn`, `test_sweep_tick_with_own_txn_lock_stays_clean_and_pending` | same 2 | ✅ 2=2 | `3224cdd1…` ✅ | 8/8 |
| M2 | remove PART-2 closer call (integration block in `_journal_executor_exit`), `upgrade_journal_sweep.py` file lines 2364-2390 (27 lines) | `d0b9b1800b58b70282402433fcb7570695a46d96661e9813fdf4d7038a32497b` | `test_exit_78_closes_arm_the_same_way`, `test_exit_minus_one_closes_arm_then_next_cycle_delivers`, `test_flipped_txn_is_never_touched` | same 3 | ✅ 3=3 | `366d543e…` ✅ | 8/8 |
| M3 | remove flipped=true guard INSIDE `close_restart_arm_executor_exit`, `upgrade_journal.py` file lines 2265-2272 (8 lines) | `1811f206e735ecb96420fe9f04137a482bf11031ab3ddc2195e9c446afc3350d` | `test_flipped_txn_is_never_touched` (only) | same 1 | ✅ 1=1 | `3224cdd1…` ✅ | 8/8 |

Raw run lines:

```
M1: 2 failed, 6 passed in 0.95s   (expected EXACTLY pins 1,2)
M2: 3 failed, 5 passed in 0.88s   (expected EXACTLY pins 4,5,6)
M3: 1 failed, 7 passed in 1.02s   (expected EXACTLY pin 6)
```

**M3 distinctness ruling:** distinct surface — different function (`close_restart_arm_executor_exit` body vs the M2 call-site in the sweep) AND different RED membership ({pin 6} alone vs {pins 4,5,6}). Not foldable into M2; planted and proven separately.

**End-state check (post all restorations):**

```
$ cd /tmp/rl-gate-<pid>/mut-wt && git rev-parse HEAD && git status --porcelain
4be66e44c9b2089e709bbba01d1b39a6c3e15f3e
(porcelain EMPTY)
```

Both files' sha256 after final restore == pristine digests (pasted above). Mutation worktree removed at teardown.

**G2 wall time:** ~7 min (three plant/run/restore/run cycles + setup; each pytest leg < 1.1 s).

---

## G3 — NET-NEW-FAILURE on the touched surface — PASS (net_new = ∅)

**Battery discovery:** `grep -rl "upgrade_journal_sweep" tests/unit --include="*.py"` returned 13 packs. Battery = the 3 brief-required paths + all 10 grep-discovered non-pins packs (13 explicit paths, identical command both sides). The pins pack (`tests/unit/services/test_restart_arm_livelock_pins.py`) is HEAD-ONLY (absent at base) — stated per brief: it is EXCLUDED from the base-side battery command; its HEAD result is the G1 head run (8/8, 0.93 s).

Battery paths (identical both sides):

```
tests/unit/services/test_auto_continue_boot_pass.py
tests/unit/services/test_auto_continue_interleaving.py
tests/unit/services/test_post_restart_arm_notify_sweep.py
tests/unit/test_post_restart_arm_notify_no_parallel.py
tests/unit/tools/test_agent_restart.py
tests/unit/tools/test_df4_sweep_reserved_fallback.py
tests/unit/tools/test_promote_cgroup_survivorship_python.py
tests/unit/tools/test_supervision_python.py
tests/unit/tools/test_upgrade_journal.py
tests/unit/tools/test_upgrade_journal_sweep_liveness.py
tests/unit/tools/test_upgrade_preflight_daemon_side.py
tests/unit/tools/test_upgrade_tools.py
tests/unit/tools/test_upgrade_registration.py
```

Command form (both sides; only `<worktree>` differs):

```
cd <worktree> && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
  -u POSTGRES_PORT ENSEMBLE_SELF_ENV=dev timeout 1500 /home/nea/ensemble-src/.venv/bin/python \
  -m pytest <13 paths> -q --tb=no -rfE --timeout=120 2>&1 | tee <LOG> >/dev/null
```

**Results:**

```
BASE: 1 failed, 484 passed, 1 skipped, 19 warnings in 45.89s   (PYTEST_EXIT=1)
HEAD: 1 failed, 484 passed, 1 skipped, 19 warnings in 41.90s   (PYTEST_EXIT=1)

BASE_FAILED:
FAILED tests/unit/services/test_auto_continue_boot_pass.py::TestLifespanEnvelope::test_api_py_wraps_pass_in_try_except
HEAD_FAILED:
FAILED tests/unit/services/test_auto_continue_boot_pass.py::TestLifespanEnvelope::test_api_py_wraps_pass_in_try_except

$ diff /tmp/g3-base-failed.txt /tmp/g3-head-failed.txt
(identical FAILED lists — net_new = EMPTY)
```

The single failure is the briefed known pre-existing `test_api_py_wraps_pass_in_try_except` — **verified RED AT BASE** (it is in BASE_FAILED), therefore NOT HEAD-attributable. Counts, skip count, and warning count identical across sides.

**G3 wall time:** ~5 min (base 45.89 s + head 41.90 s + discovery/diff).

---

## G4 — FENCES — PASS

**Migration delta (0 lines):**

```
$ git -C /home/nea/ensemble-src-wt-restart-livelock diff cbafcc8d9..4be66e44c -- daemon/migrations/versions/ | wc -l
0
$ git diff --stat cbafcc8d9..4be66e44c -- daemon/migrations/versions/
(empty — zero migration changes)
```

**Zero live contact (belt-and-braces sha pair — READ-ONLY access, the ONLY touch of the live install):**

```
BEFORE 2026-10-31T09:30:59Z: bc324f021ad5cdb531ce742d7f2a319e5f0fa428467a0a9ef0aaa0387e2db155  /home/nea/agents-ensemble/releases/state.json
AFTER  2026-10-31T09:31:58Z: bc324f021ad5cdb531ce742d7f2a319e5f0fa428467a0a9ef0aaa0387e2db155  /home/nea/agents-ensemble/releases/state.json
```

Identical. Session command surface audited: git (rev-parse/status/diff/log/show/worktree), sha256sum (single READ of live state.json), pytest (writes only under tmp_path via the pack's own fixtures), cp/mkdir/rm strictly on throwaway paths under `/tmp`, nl/sed/grep source reads. NO network probes; ports 9797/7979/8088 never contacted; no server boots; no promote/stage/nonce; no pushes to latest.

**Env scrub evidence:** per-variable scrub form (pasted at top) used on every python/pytest invocation across all gates — 8 runner-script legs + direct invocations. The `env -u POSTGRES_*` glob idiom never used.

**Teardown:** both throwaway worktrees removed via `git worktree remove --force` (confirmed absent from `git worktree list`); `/tmp` scratch dir + gate runner scripts deleted; lane worktree restored:

```
$ git -C /home/nea/ensemble-src-wt-restart-livelock rev-parse HEAD
4be66e44c9b2089e709bbba01d1b39a6c3e15f3e
$ git branch --show-current
fix/restart-arm-livelock
$ git status --porcelain
(empty)
```

**G4 wall time:** ~3 min.

---

## Disclosures

1. **M1 planted-tree residue:** after the M1 excision, `grep -c "retryable skip"` still returns 2 — both hits are DOCSTRING text (the excision assertion verified the executable probe+identity block was removed; the docstring is inert). Mutation truth is behavioral (2 pins flipped red).
2. **grep rendering artifact:** in mutation runs, `grep -E "^FAILED"` printed one test name twice (pytest's `--tb=line` summary renders the failure line + the `-rfE` summary line); authoritative counts are pytest's own summary lines (`2 failed, 6 passed` / `3 failed, 5 passed` / `1 failed, 7 passed`) — membership lists were deduped (`sort -u`) before comparison.
3. **Pin 6's red-at-base mode:** its failing assert at base is the caplog line (`'flipped=true' in ''`) — the guard's log line is part of the shipped surface, so a missing-log red is a legitimate behavioral red, not a collection error.
4. **G1 base-worktree untracked file:** the pins file existed at the base worktree ONLY as the gate's untracked copy (method requirement); it was removed with the worktree at teardown; the lane tree was never touched by any mutation (all mutations in `mut-wt`).
5. **Other worktrees seen in `git worktree list`** belong to other lanes; none were created, modified, or removed by this gate.
6. **Battery scope note:** `test_upgrade_registration.py` is brief-required (not in the grep set); the 10 grep-discovered packs include 4 auto-continue/supervision/cgroup packs that merely reference the sweep module — included anyway for breadth; all green both sides except the one base-proven pre-existing failure.

---

## VERDICT

**PASS** — all four gates clean:
- G1 original symptom: red-at-base EXACTLY the 5 livelock-class pins, green-at-head 8/8 (cure components a/b/c each proven).
- G2 mutations: M1 {2 pins}, M2 {3 pins}, M3 {1 pin} — count AND membership match predictions exactly; all planted/restored sha256 pairs recorded; restore runs 8/8 × 4 (pristine + 3).
- G3 net-new = ∅ (identical FAILED lists; sole failure base-proven pre-existing).
- G4 fences: migration delta 0 lines; live state.json sha identical; per-variable env scrub throughout; teardown clean, lane @ 4be66e44c porcelain empty.

*Gate runner: independent functional-gate lane (wi-6641579b). Evidence logs retained at `/tmp/g1-*.log`, `/tmp/g2-*.log`, `/tmp/g3-*.log` for the session; this document is the durable record.*
