# ACCEPTANCE RECORD — hard-reboot recovery, P4 E2E + full regression (hrr-20261021)

**Date:** 2026-10-21 · **Lane:** tester acceptance (commissioned by the leader)
**Worktree:** `/home/nea/ensemble-src-wt-hard-reboot-recovery`, branch `feature/hard-reboot-recovery`
**ACCEPTANCE TIP (recorded, verified byte-identical at run time):** `4641b17d5f3bc644b0a0dfd5c2acafa75b86333c`
**TRUE pre-fix BASE (A/B regression):** `4c888fbbbe630ebf0c52877beda0a1b839f7cbd5`
**Base worktree:** `/tmp/hrr-base-4c888fbb` (detached, porcelain EMPTY, import-proved: `daemon.__file__`
→ `/tmp/hrr-base-4c888fbb/daemon/__init__.py`; base `daemon.__version__` = `0.26.2`)
**Shipped-tree final:** `6c318abc0` (dev fold B) — the tip carries that plus the tester's harness/docs/packs commits.

---

## LEADER RULINGS RECORDED (verbatim intent, 2026-10-21)

1. **G6 preflight-success invisibility → DEFER** (documented known gap, not fixed here).
   Rationale: pre-existing, NOT introduced by this commission; the FAILURE path (exit-75) is
   operator-visible; success-silence is normal Unix behavior; changing logging-handler order
   inside `main()` is a boot-critical-path change with poor risk/benefit at the end of this
   commission. Recorded as a known gap with the drill's substitute marker, flagged as a
   **tracked follow-up**.
2. **`scripts/run_tests_scrubbed.sh` under-scrubbing → DEFER to its own follow-up commission**
   (cross-cutting test infrastructure affecting every pack — wider than this commission).
   Recorded prominently: **an ambient `ENSEMBLE_SELF_ENV=live` shell passes its leak gate**, and
   **it cannot wrap a command**. High-priority follow-up; every pack keeps the manual fence
   until then.
3. **`concurrency_atomic_unit_test` 42.7% inert → NO commission now; the qualification GOES INTO
   THIS RECORD:** ensure.md cites it as the deadlock/concurrency gate while the cascade/observer
   race families execute zero nodes. **A green from this pack is NOT coverage of those families.**
4. **Harness overage ACCEPTED** — 635 code lines vs the plan's ~120 soft ceiling, recorded as a
   disclosed test-artifact overage (9 phases + 10-file evidence bundle + fence/cleanup set do not
   fit 120). The PACKS.md integrity gap closure (two pack-less test files) is noted as a good catch.

---

## PART 1 — Shipped-default drill leg (P4-1..6 mandatory): **PASS**

Run at tip `4641b17d5` (pack blob byte-identical to HEAD; HEAD did not move; `tree_drift_during_run: "no"`).
`RESULT: PASS`, `Failed checks: 0`, exit 0, wall **66 s**. Evidence bundle `/tmp/hrr-drill-20261021140755`.

| Item | Verdict | Evidence (verbatim) |
|---|---|---|
| **P4-1** fresh-dead stranded row recovered | ✅ PASS | `[HardReboot] MQ boot pass: reset=1` (14:08:45, post-restart pid 827230); post-boot `message_status: "ready"`; seeded `processing` @ 14:06:28 (fresh-dead, no task row) |
| **P4-2** `processing_count == 0` | ✅ PASS | pre-crash `1` → post-restart `"mq_processing_count": "0"` |
| **P4-3** `pending_count == 1` (moved, not deleted) | ✅ PASS | `"mq_pending_count": "1"` |
| **P4-4** busy-guard predicate + branch | ✅ PASS (shipped default — guard did NOT clear, by design) | `TRUE (1>0 or 0>0)`; `"busy_guard_branch": "visible-pending-shipped-default"`; corroborated by the journal line `P3/decision-f deferred DELETE_PHASE: no doomed ids (operator gate QUEUE_DISCARD_ON_STARTUP may be off, or no rows matched the doomed predicate; DELETE no-ops)` |
| **P4-5** AutoContinue resume | ✅ PASS | `AutoContinue boot pass: ContinueResult(candidates=1, scheduled=1, skipped_no_checkpoint=0, skipped_resume_refused=0, skipped_kill_switch=0, skipped_no_boot_epoch=0, skipped_multi_task_instance=0, errors=0, duration_seconds=0.048…)` + `[BOOT_CONTINUE] instance=ffa0c48f work_id=98130ce5 epoch=2026-10-21T14:08:45.250637` |
| **P4-6** schedule survival | ✅ PASS | same UUID `7564d7e6-af8e-4d91-bfd2-6180918d471f` `running` pre-kill AND post-restart |
| Isolation | ✅ PASS | `0::/system.slice/hrr-drill-20261021140755-825603.service` for BOTH pids (pre-kill 825821, post-restart 827230) — never the live cgroup |
| `/livez` + `/readyz` post-restart | ✅ PASS | 200 / 200; `readyz_post.json` `status: ready`, all four components true |
| Anchoring negative control | ✅ PROVEN | FULL journal **2** `MQ boot pass: reset=` lines (`reset=0` @14:08:09 pid 825821; `reset=1` @14:08:45 pid 827230) vs cursor-anchored view **exactly 1**; `Boot epoch captured` 2-vs-1 corroborates |
| D13 | OUT OF SCOPE | task-less RUNNING ghost count 1 — reported, never failed (critical note e82c89e8) |

Notes for the record: the `orphan-sweep summary: candidates=0` line is a **second, later sweep**
after the first pass consumed the one candidate — not a contradiction. `livez.version` = `0.26.2`
(the feature branch carries the base version; no bump exists on this branch).

## PART 2 — AC6: **PASS (15 s ≤ 30 s), daemon-path basis, blind spots declared**

```json
{"t_end_to_end_seconds": 15, "ac6_criterion_seconds": 30, "ac6_meets_criterion": true,
 "boot_epoch_from_log": "2026-10-21T14:08:45.250637", "mq_reset_count": "1",
 "ac6_reproduced": "daemon-path only — unit stop + fresh start (NO real host reboot), local PG already running (NO parallel PG start race), transient unit is NOT the production unit",
 "ac6_not_reproduced": ["real host power-loss semantics","PG started in parallel with the daemon","production unit Restart=/RestartSec= semantics","G6 After=postgresql.service ordering"]}
```

**Basis (explicit):** the ≤30 s measures the **daemon recovery path only** — transient-unit stop →
fresh start → `/readyz` 200 → recovery markers. It does **NOT** include host-boot time
(firmware/bootloader/kernel), matching the runbook's own qualifier (`host boot time + ~14 s`).

**Blind spots (declared, no silent passes):**
- No real host reboot — kernel power-loss semantics, fsync-loss of in-flight writes, cold-PG WAL replay NOT reproduced (`kill -9` of a process ≠ host power loss).
- PG **not** started in parallel with the daemon — the local cluster was already up, so AC6's PG-start race (daemon first, preflight exit-75, backoff) is NOT reproduced. G6 ordering is instead proven by the unit-template check (Part 4, C4) and the P1 `connect_timeout` pack.
- The transient unit is **not** the production unit — `Restart=`/`RestartSec=10`/`KillMode`/`TimeoutStopSec` only partially mirrored.
- **STR is not observable within a 5-minute cap** — threshold default 10 min (`daemon/config.py:1821`), boot-time epoch clamp defers a frozen-heartbeat orphan to ≈boot+threshold (`daemon/services/stale_task_recovery.py:763`), background loop 60 s (`:211`). "All mechanisms fire" claims must state STR was not observed, **by design**.
- **G6 preflight success is unobservable in logs** (leader ruling #1 — deferred known gap): `daemon/__main__.py main()` emits INFO before `daemon.api` installs handlers, so `lastResort=WARNING` drops it (empirically proven: the `__main__` logger produced zero records for its own INFO lines). Covered by the fail-closed exit-75 contract + the observed `Creating PostgreSQL engine: 127.0.0.1:5432/<scratch>` marker.
- LLM-dependent observations are structural (dead local `OPENAI_BASE_URL`, no live proxy/credential).
- Turn-resume vs re-dispatch is not distinguishable; the one-shot scheduler duplicate-fire marker is in-RAM; interrupted tool calls may re-run (NG-4 at-least-once).

## PART 3 — P4-7 override lane (QUEUE_DISCARD_ON_STARTUP=true): **PASS — busy-guard CLEARED**

**Clearly labeled OVERRIDE DEMO leg — never blended into the shipped-default verdict.**
Ran at harness commit `186768717838333a404434012c84f39b95278514` (`test: add HRR_EXTRA_ENV
passthrough to the drill harness`, +44 lines, harness file ONLY, no fence weakened, NOT pushed).
The shipped-default leg (Part 1) ran at `4641b17d5`; the only delta between them is this
harness-only passthrough — **no daemon-code difference between the two legs.**

**Passthrough mechanism + fence:** `HRR_EXTRA_ENV="KEY=VAL,…"`, appended to the scratch
`daemon.env` **after** the allow-list (the transient unit takes no caller env, so `daemon.env` is
the only channel in). Blocked-key denylist (`POSTGRES_*`, `PORT`/`DAEMON_PORT`/`HOST`,
`ENSEMBLE_SELF_ENV`, `OPENAI_*`, `LD_*`, `HOME`, `PATH`, …) + malformed-input refusal — verified
by an 8-case matrix; refusals are loud (`RESULT: FAIL`, exit 1), never silent.

**P4-7 verdict: CLEARED.** `pending_count == 0` **AND** `processing_count == 0` post-boot →
predicate `(pending>0 OR processing>0)` = **FALSE** → the busy guard clears. The stranded row was
**DISCARDED by the deferred DELETE phase** (not moved to pending). `busy_guard_branch` =
**`cleared-override-true`** (the label keys off observed counts; no hand-edit).

**Override-reached-the-daemon — proven 3 independent ways:**
1. Pack echo `[PASS] HRR_EXTRA_ENV passthrough applied to daemon.env: QUEUE_DISCARD_ON_STARTUP=true` + bundle `hrr_extra_env.txt`.
2. **The cursor-anchored DELETE_PHASE journal sequence** (post-restart view): `clear_all preserve_in_flight=True about to DELETE 1 … doomed_message_ids=['hrr-msg-…']` → `CAPTURE_PHASE complete — stashed 1 doomed id(s)` → `deferred DELETE_PHASE armed: 1 doomed id(s)` → `firing via arm=+1s+readyz-green (elapsed=3.5s)` → `JOURNAL: MessageQueueRepository.delete_doomed_ids (DELETE_PHASE) — DELETED 1 message_queue row(s)`. The doomed id is **exactly the seeded stranded row**.
   **Negative control:** the PRE-crash boot of the same unit (same `daemon.env`, flag live) stashed **0** doomed ids — because the row is seeded after that boot. The post-restart "armed: 1" is therefore attributable to the injected flag, not coincidence.
3. Post-boot SQL: `processing=0`, `pending=0`, `message_status=""` (row gone).

**Items unaffected by the override (as expected — the override touches the discard lane, not the resume lane):** `reset=1` still fired (reset runs before the discard decision); AutoContinue `candidates=1, scheduled=1` + `[BOOT_CONTINUE] instance=7c26b49f work_id=e9fbec25 epoch=2026-10-21T15:52:14.961980`; schedule survived (same UUID `7cee5122…`); isolation asserted (`/system.slice/hrr-drill-…service`, both pids); `/livez`+`/readyz` 200; AC6 `T_end_to_end = 15 s`.

**The pack's overall `RESULT: FAIL` (exit 1, 2 failed checks) is BY DESIGN and is NOT a P4-7
failure:** the two reds are `P4-1 stranded row status=''` and `P4-3 pending_count == 0 (expected
>= 1)` — the **shipped-default expectations the override deliberately invalidates** (the row is
deleted, not moved to `ready`). Reported as-is; evidence immutable; nothing forced.

**Zero residue + live untouched** (independently verified): drill's own scratch db/role 0/0,
`hrr-*` units 0, ports 10000/10001/8088 free, mock gone, MainPID 528942 / NRestarts 0 / demo 898 /
symlink `v0.26.3` — all identical pre/post. (Two `hrr%` DBs + one role observed live belong to the
still-running D3 regression lane and are expected to be dropped by it.)

**Deferred harness TODO (adjudicated, not done mid-acceptance):** make P4-1/P4-3 branch-aware
(assert `status='ready'` only when the shipped-default branch is active; row-absent when the
override deleted it) so an override leg reports `RESULT: PASS` instead of FAIL-with-two-by-design
reds. NOT done here because it would change the pack the shipped-default leg was verified
against, invalidating the byte-identical-to-HEAD claims in this record — commission it together
with a re-verify of both legs. **Commit disposition:** `186768717` stays local-only, consistent
with every other commit on this branch (the promote ceremony owns pushes).

## PART 4 — ensure.md gates at the tip

### Static gates (C1–C4): **ALL PASS** at `4641b17d5` (porcelain-clean over `daemon/ tests/ test/ scripts/`)

| Gate | Verdict | Evidence |
|---|---|---|
| C1 `dev.sh` graceful-shutdown flag | ✅ PASS | `dev.sh:155` `--timeout-graceful-shutdown 10` on the only uvicorn invocation; `git diff 4c888fbbb..HEAD -- dev.sh` EMPTY |
| C2 async-await census (3 fns, AST over 2,024 files) | ✅ PASS — **zero unawaited call sites** | `_get_system_prompt_tokens` 3/3 daemon awaited + 11 AsyncMock assignments + 3 asyncio.run; `_compute_context_usage` 1/1 awaited; `get_queue_stats` 5/5 daemon awaited + 12 real awaited tests + ~20 AsyncMock assignments. Non-`.py` consumers: zero. **Parity vs `8eb8ef643` verified byte-identical (130==130 mention lines)**, not assumed |
| C3 dead code | ✅ PASS | 4 added `def`s (`find_stuck_messages_before` `repository.py:425`, `reset_stuck_message_to_ready` `:447`, `_boot_pass_enabled` `message_queue_boot_recovery.py:52`, `recover_stuck_messages_on_startup` `:63`) — **each with ≥1 production caller**; 0 removed `def`/`class`; `HARD_REBOOT_BOOT_PASS_ENV` alive |
| C4 G6 unit-template ordering | ✅ PASS | `scripts/systemd/ensemble-daemon.service:90 After=postgresql.service`; **NO `Wants=postgresql.service`** (only `:91 Wants=network-online.target`); **no `Requires=` anywhere**; diff vs base = exactly 3 comment lines + the one `After=` line, placed between the two existing ordering directives |

### Pack gates (pre-acceptance signal, re-affirmed at the tip by the harness-prep lane + regression sweep)

[PENDING — carried from the regression sweep results]

---

## PART 5 — Full regression (A/B, net-new discipline)

**[ALL 17 PARTITIONS REPORTED — SWEEP COMPLETE]**

### Part 5 aggregate — the net-new verdict

| Metric | Value |
|---|---|
| Partitions | **17/17** (D1's first lane terminated + re-dispatched; replacement completed) |
| Selected (executed) nodes per leg | **≈29,264** (sum of per-partition selected counts; corroborates the v0.26.3 whole-tree figure of 28,933) |
| **NET-NEW failures (branch-introduced)** | **0 — across all 17 partitions, both legs** |
| Genuinely fixed by the branch | **0** (5 raw candidates across R2/U5/D2/D6/D1 — every one adjudicated to an environment artifact or a flake; none credited) |
| Pre-existing stable reds (fail at BOTH legs) | **355** |
| FLAKY nodes (≥1P+≥1F, no-code-change, 3× both legs) | **13** (R1 ×4, D4 ×2, U3 ×3, R3 ×1, D6 ×1, D1 ×2) |
| Sign-flip catches by the 2-shot protocol | **4 partitions** (R1, D4, R3, D1) — each produced a false net-new **and/or** a false fix on a single shot |

**The claim "zero net-new" is backed by whole-tree coverage, not a targeted subset**: identical
commands + identical 22-var scrub on both legs, per-ID FAILED-list diffs, `red-at-base` ≠ net-new,
and a 3× no-code-change flaky re-proof for every single-leg discrepancy.

**Base-leg artifact taxonomy (5 classes, all adjudicated, none credited to the branch):**
1. `/tmp`-path exemption (`workspace_guard`) — U5 ×5
2. base-worktree **name** failing `^ensemble-src(-wt-…)?$` — U3 ×1
3. base worktree lacks `.venv` — U3 ×2, R2 ×1, D2 ×3
4. load-sensitive wall-clock tests under concurrent-lane contention — the 13-node flaky bucket
5. **gitignored `data/ensemble.json` divergence** (TIP=postgres vs BASE auto-created sqlite) — U4;
   state-matched before the verdict; the bias direction manufactures FALSE NET-NEW, the worst case

**Pre-existing reds, by disposition:** quarantine-covered families dominate (test_api ×6,
progressive_dispatch ×18, memory_integration ×16, builtin_mcp ×17, context7 ×4,
configure_builtin_idempotency ×15, coder_developer_migration ×5, job_queue families ×15,
migration traps, spawn_team_members ×20, spawn_limit ×11, the `_MinimalManagerProxy` stale-stub
family ×29, and others). **Non-quarantined base-evidenced reds ≈ 100+ nodes are consolidated as
ONE test-debt list** for a single follow-up quarantine/test-debt commission — not scattered.

### Consolidated follow-up commissions surfaced by this sweep (NOT blockers; routed)

1. 🔴 **`tests/support/conftest.py` WAL-pragma deadlock** — set `busy_timeout` BEFORE
   `journal_mode=WAL`; currently deadlocks and cascades within a pytest process.
2. 🔴 **145 unmarked `tests/integration/` files** — `pytestmark` sweep to make the tier inert.
3. 🟠 **`test_report_deferred_migration_pg.py` stale stub** (`_MinimalManagerProxy` missing
   `_migrate_overloaded_image_refs_rows`, `daemon/manager.py:8357`) — masks 29 tests' assertions.
4. 🟠 **`daemon/auth/cli.py` unhandled `OperationalError` embeds the connection URL in stderr**
   (U4) — leak class uncovered by existing tests.
5. 🟠 **`test_jq_proxy_phase2_constraints` leaks a CONSTRAINT TRIGGER** (D3) — order-dependence.
6. 🟠 **`tests/e2e/test_context_injection_hybrid.py` module-level HTTP GET at import** (D4) —
   collection-abort class.
7. 🟠 **`run_tests_scrubbed.sh` under-scrub** (leader ruling #2 — already deferred to its own
   commission).
8. 🟢 `pytest timeout_method="thread"` kills whole sessions on first timeout (D1) — switch to
   `signal` for A/B work; `-v` required because pytest 9.0.2 omits FAILED/ERROR summary lines.

| Partition | Scope | TIP | BASE | Net-new | Pre-existing | Fixed | Flaky |
|---|---|---|---|---|---|---|---|
| **U7** | tests/unit 12 small subdirs (27 files) | 507P/0F/0E/0S/0D ×2 passes | 507P/0F/0E/0S/0D ×2 passes | **∅** | ∅ | ∅ | ∅ |
| **U6** | tests/unit/test_[n-z]*.py (162 files, 5 chunks) | 3555P/**9F**/0E/53S/0D | 3555P/**9F**/0E/53S/0D | **∅** | **9** (identical both legs) | ∅ | ∅ |
| **R1** | tests/test_[a-h]*.py (51 files / 1413 nodes, 2 shots/leg) | 1337P/28F/48S/8D (shot1) · 1335P/30F (shot2) | 1334P/31F/48S/8D (shot1) · 1337P/28F (shot2) | **∅** | **28** (stable core, all 4 shots) | ∅ | **4** |
| **R2** | tests/test_[i-q]*.py (84 files / 2228 nodes, 8 chunks) | 2103P/**50F**/59S/16D/0E | 2102P/**51F**/59S/16D/0E | **∅** | **50** (identical ID sets, per-ID `comm`) | 1* | ∅ |
| **U5** | tests/unit/test_[f-m]*.py (111 files / 2545 nodes, 4 chunks) | 2526P/**17F**/2S/0D/0E | 2500P/**22F**/2S/0D/0E | **∅** | **17** (identical both legs) | 0** | ∅ |
| **U2** | tests/unit/tools/ (77 files / 3993 nodes, 6 chunks) | 3974P/**13F**/6S/0D/0E | 3974P/**13F**/6S/0D/0E | **∅** | **13** (identical node IDs) | ∅ | ∅ |
| **D4** | tests/services/ + tests/e2e/ (49 files / 655 nodes, 2 shots/leg) | 625P/**16F**/14S/51D (both shots) | 626P/15F + 624P/17F (shots 1/2) | **∅** (one-shot "1" dissolved) | **15** (all 4 shots) | ∅ | **2** |
| **R3** | tests/test_[r-z]*.py (60 files / 2137 nodes, 3 chunks ×2 shots) | 2032P/**33F**/35S/37D (shot1) · 33F (shot2) | **34F** (shot1) · **33F** (shot2) | **∅** | **33** (all 4 runs) | ∅ | **1** |
| **U3** | tests/unit/{plugin_subsystem,routers,repositories}/ (66 files / 1605 nodes, 6 chunks) | 1600P/**5F**/0E/0S/0D | 1594P/**7F**+**4E** | **∅** | **5** (3/3 FAIL both legs) | 0*** | **3** |
| **U4** | tests/unit/test_[a-e]*.py (119 files / 2687 nodes, 6 chunks; 1 base-excluded = 9 nodes) | 2618P/**27F**+22E/2S/0D | 2610P/**26F**+21E/2S/0D | **∅** (after confound neutralised) | **47** (identical per-ID both legs) | ∅ | **2** |
| **U1** | tests/unit/services/ (135 files / 2932 nodes, 9 chunks ×2 shots) | 2923P/**9F**/0S/0D (both shots) | 2923P/**9F**/0S/0D (both shots) | **∅** | **9** (byte-identical error signatures, 6× re-proof) | ∅ | ∅ |
| **D7** | tests/static/ + tests/lint/ (2 files / 3 nodes) | 1P/**2F**/0S/0D ×2 shots | 1P/**2F**/0S/0D ×2 shots | **∅** | **2** (18 re-proof runs, stable-red) | ∅ | ∅ |
| **D5** | tests/{message_queue_redesign,migration,tools}/ (53 files / 948 nodes, 5 chunks) | 913P/**17F**/18S/3D | 914P/**16F**/18S/3D | **∅** (1 raw candidate adjudicated) | **16** (identical both legs, 3/3) | ∅ | 1 adjudicated |
| **D2** | tests/job_queue/ (114 files / 2155 nodes, 9 chunks ×2 shots) | 2099P/**15F**/38S/3D | 2096P/**18F**/38S/3D | **∅** (both shots, zero sign-flips) | **15** (byte-identical signatures, 3/3 both legs) | 0† | ∅ |
| **D3** | tests/postgres/ (40 files) + tests/vfy_pg/ (1 file) — **PG-MARKED, marker ENABLED**, scratch PG | 301P/**11F**/33S/2X/**24E** | 301P/**11F**/33S/2X/**24E** | **∅** | **35** (identical both legs) | ∅ | ∅ |
| **D1** | tests/integration/ (200 files / **1085 EXECUTED** nodes — see headline) | 46 FAILED (per-file isolated) | 44 FAILED | **∅** (2 raw candidates → FLAKY) | **44** | ∅ | **2** |

**D1 HEADLINE — the partition is NOT inert, and the brief's premise was false.** 145 of 200
`tests/integration/` files **lack** the `integration` mark and therefore **EXECUTE 1085 nodes
under the house default gate**. `--collect-only` on both legs: **1085/1492 collected, 407
deselected — identical on both legs.** AST census of the mark (module `pytestmark` incl.
list/conditional forms, OR per-test decoration) cross-validated at runtime by pytest rc=5 →
**zero disagreement**: 55 files marked (46 module-level + **9 marked only by per-test
decoration — a `pytestmark` grep alone MISSES these**), 145 unmarked → executing.

**Why the first lane hung — TWO causes, both confirmed:**
- **Cause A — real outbound LLM calls.** `tests/integration/conftest.py` deliberately does not
  mock langgraph, so the unmarked files make **real HTTPS calls**: peers captured via
  `/proc/<pid>/net/tcp` = `10.44.0.1:443` (`llm.ensem.dev`, the ambient `OPENAI_BASE_URL`) and
  `162.159.136.234:443` (`api.openai.com`, the pydantic-settings default). **Env-based egress
  fencing is INEFFECTIVE** — clearing `OPENAI_*` and setting a dead `HTTPS_PROXY` changed runtime
  not at all (67.9 s vs 70.4 s); the client is built from explicit config. **Disclosed: real LLM
  calls occurred during this partition. No daemon started; no prod DB/install/systemd touched.**
- **Cause B — a genuine test-harness deadlock.** `tests/support/conftest.py:80-84` runs
  `PRAGMA journal_mode=WAL` inside the SQLAlchemy `connect` event under `NullPool` (fires on
  EVERY connection) and sets `busy_timeout=30000` only **afterward** → the WAL pragma has no
  busy timeout and blocks forever against a concurrent reader's shared lock. **It cascades within
  a pytest process** — once one test times out, every later test burns the full timeout too.
  → **a real defect worth its own fix commission (swap the pragma order).**

**Timeout-method finding:** the repo ini sets `timeout_method="thread"`, which `os._exit(1)`s on
the first timeout — the whole session dies with **no FAILED lines and no summary**. `--timeout-method=signal`
is required for any A/B work here (turns a hang into a per-test FAILED). Per-file isolation was
also required: chunking let Cause B cascade.

**Standing coverage finding (routed):** 145 unmarked files = 1085 nodes of real-LLM-calling,
deadlock-prone code running in what is presumed to be an inert tier. Fix = a
`pytestmark = pytest.mark.integration` sweep over the 145 files — makes the tier genuinely inert
and eliminates this hang class from the default gate entirely.

**D1's 2 raw net-new candidates:** both FLAKY on 3× re-proof (one 3P/3 on both legs — a
chunk-context artifact; one flipping on both legs).
| **D6** | tests/{repositories,api,property,performance,perf,manager,vfy}/ (25 files / 507 nodes, 2 shards) | 490P/**17F**/0S/0D | 489P/**18F**/0S/0D | **∅** | **17** (3× both legs, symmetric) | 0‡ | 0‡ |

†D2's 3 raw "fixed" nodes are **LOCATION-ATTRIBUTED** (missing `.venv` at base — the test spawns
`.venv/bin/pytest` as a subprocess). Adjudicated in a throwaway `git archive` tree with a `.venv`
bridge + `PYTHONPATH` pin: **9/9 PASS** with the bridge, **9/9 FAIL** without. Reclassified out
of "fixed"; matches QUARANTINE 2026-10-04. **Genuinely-fixed-by-branch count remains 0.**

**D6 detail — the fourth "fixed" candidate, and the right epistemic move.** D6's single raw
"fixed" node (`test_auth_middleware_latency::…::test_protected_path_401_path_latency`) **passes
6/6 at BOTH legs** (3 batch + 3 file-scope) → **FLAKY**; a one-shot reading would have claimed a
false "1 fixed". Root cause: wall-clock gates by construction (`assert p99 < 5_000 µs`;
`wc_ratio < 2.0`) — during one rep the *sibling* node `test_public_path_p99_under_threshold`
failed instead, the same load-sensitivity class, proving the shard asymmetry was pure timing noise.

**Vacuous-re-proof discipline recorded:** the mandated node-level re-proof of the 2 perf-matrix
cells was **vacuous** — in isolation they **SKIP** (`perf-matrix parametrized cells were skipped
(PG unreachable)`), because a cell depends on sibling parametrized cells populating a shared matrix
fixture. The lane escalated to whole-file shots and recorded the isolated "2 skipped" as **no
evidence**, not as stability. **A skip is not a pass.**

**HEAD drift proven benign:** a sibling lane's `186768717` landed mid-shard; it changed exactly
one file (a shell pack pytest never collects) and the **entire `tests/` subtree is byte-identical**
across the drift, with a spot-check re-run at the new head reproducing the identical failures.
The scrubbed test env resolves PG to `localhost:5432`, which holds **0** `ensemble_prod`/
`ensemble_demo` databases — the real-PG test files are structurally disjoint from live.

**D3 detail — the lane read the fixtures FIRST and found the env contract is `PG_TEST_*`, not
`POSTGRES_*`** (the lane's brief assumed `POSTGRES_*`; that would have silently failed it):
`PG_TEST_HOST/PORT/DB/USER/PASSWORD` → `PG_URL` (`tests/postgres/conftest.py:68-74`).
`POSTGRES_*` is NOT a connection source — it is *scrubbed* by 3 test files to prove the factory
does not leak — yet it **must be exported to the scratch coords** (asserted, not scrubbed) because
`daemon/repositories/factory.py::create_postgres_engine` prefers `os.environ.get("POSTGRES_*", cfg)`
and ambient is LIVE `10.44.0.2/ensemble_prod`.
Two further contracts that would each have silently broken the lane:
- **`tests/vfy_pg/` HARD-REFUSES port 5432** — `_assert_disposable_target()` `pytest.fail`s unless
  `PG_TEST_HOST == "127.0.0.1"` and `15000 <= PG_TEST_PORT <= 19999`; the lane stood up a disposable
  `initdb -A trust` cluster on **15533** (cluster B), with cluster A = local PG16 on 5432.
- **Scratch-shape fence:** `PG_TEST_DB` must not be `ensemble_prod/ensemble_dev/ensemble_sandbox/ensemble`
  AND must contain `test` or `scratch` (`test_resource_monitor_estimated_bool_pg.py:90-117`).

**Per-test outcome-set symmetry (not just counts):** PASSED diff **0**, SKIPPED diff **0**, XFAIL
diff **0**, vfy_pg diff **0**. **15 runs/leg with zero run-to-run variation** in any cell.

**Two real defects surfaced (pre-existing, identical both legs, NOT branch-caused):**
1. **29 of the 35 reds are one stale-stub defect** — `AttributeError: '_MinimalManagerProxy' object
   has no attribute '_migrate_overloaded_image_refs_rows'` (`daemon/manager.py:8357`). It masks
   whatever those 29 tests were meant to assert → **own fix commission**.
2. **A hidden cross-file DB-object leak:** `test_jq_proxy_phase2_constraints.py:151` installs a
   `CONSTRAINT TRIGGER trg_job_queue_items_active_lock_guard` and never drops it inside the
   session; the next file in the partition then gets `IntegrityConstraintViolation:
   admission_state=active requires a job_locks row`. Proven with a 2-file repro (both files → 2
   failed; the second alone → 5 passed). **Order-dependent by construction.**
   Plus `test_readiness_pg` alone → `relation "secrets_audit" does not exist` (the conftest's model
   list omits `daemon.repositories.secrets.models`; the partition run is fine because collection
   imports every module first).

**Tooling caveat for this repo:** pytest 9.0.2 here **omits the `FAILED`/`ERROR` short-summary
lines entirely** (even with `-rfE`) — `-v` is required for any A/B identity diff. The summary
*counts* are unaffected, which is why the per-leg arithmetic reconciled everywhere.

**Teardown proven:** cluster A DBs + role dropped (`hrr%` → 0 rows / 0 roles); cluster B stopped
(`pg_ctl -m fast`), data dir gone, 15533 not listening. 64 `ensemble_blob_prune_*` DBs owned by the
shared `ensemble` role are **other lanes' residue, not this lane's** — left untouched, noted for the
final attestation.

**D2 detail:** zero sign-flips across 4 legs × 18 nodes; all 15 pre-existing reds are
**quarantine-covered (15/15 + 3/3)** — the cleanest quarantine cross-reference of the sweep.
The adjudication tree's `PYTHONPATH` pin was verified explicitly — without it the child
console-script would have silently tested TIP code and produced a false "parity" pass.

**Known-uncovered node set (adjudicated, recorded):** `test_defer_gate_post_settle_window.py::TestIdlePredicatePgSqliteParity`
×3 are `postgres`-marked and therefore **deselected by the default addopts on BOTH legs** — this
family can never be adjudicated by a default-addopts partition. The brief's expectation that they
would "run red at both legs" was wrong (the lane corrected it). Recorded as a known-uncovered set
rather than expanding scope mid-gate: the family is quarantine-covered as pre-existing
(2026-09-16 row, the test's own int-literal seed), and the branch has no reach into the defer
gate. If the D3-PG lane succeeds, the same mechanism can close this later.

**D1 (tests/integration/, 200 files) — FIRST LANE TERMINATED, RE-DISPATCHED.** The original lane
(`58deeaec`) sat in a single bash call for >15 min (advisory fired) and was terminated; its
transcript showed it still in setup, i.e. it had almost certainly started executing integration
tests that **lack** the `integration` marker — the same premise error D4 exposed for `tests/e2e/`
(7 of 21 files unmarked). Terminated rather than waited: the partition's expected answer is a fast
deselection count, so a multi-minute hang was the wrong thing to sit on. Replacement lane
`bdd497b3` was re-dispatched with an inverted order — **collect-only counts FIRST**, then the
marked/unmarked file census (the actual deliverable), then bounded execution of only the unmarked
files with per-test timeouts and a hard "move on, do not retry" rule on timeout.

**D5 detail — the partition MOST relevant to the branch, adjudicated to zero.** D5 contains
`tests/message_queue_redesign/` — the branch modified `daemon/repositories/message_queue/repository.py`.
The single raw net-new candidate
(`test_atomic_status_transitions.py::…::test_update_activity_concurrent_does_not_clobber_terminal_status`)
was adjudicated to ∅ on **three grounds**:
1. **Red at BASE too** — interleaved file-context re-proofs (×8) flipped this exact node red at
   BASE; chunk-0 BASE repeats also surfaced two more members of the same StaticPool/SQLite
   thread-race family at BASE (both QUARANTINE-listed) plus one **new** family member
   (`test_retry_concurrent_double_call_only_one_increments`).
2. **QUARANTINE exact match** — the node is explicitly listed on the 2026-10-05 StaticPool row.
3. **Zero causal reach** — the branch's `repository.py` diff is **purely additive (0 deleted
   lines)**: two new methods; the `update_activity` body is **byte-identical** (only a line
   shift). The test file's sha256 is identical at both revs; its import closure never reaches
   `factory.py`/`api.py`/`__main__.py`/`message_queue_boot_recovery.py`. The test builds its own
   StaticPool in-memory SQLite engine and its own docstring flags the order-sensitivity.

**HEAD movement mid-campaign disclosed and proven non-contaminating:** a sibling lane committed
`186768717` (the harness passthrough) after D5's partition legs but before its re-proofs —
`git diff 4641b17d5..186768717` touches **0 `.py` files** (harness shell script only).

15 further stable pre-existing reds are **not ledgered** (`send_message_context_param` ×6,
`send_message_load_skill` ×6, `usage_limit_worker_seam` ×2, `auto_kill_integration` ×1) →
**test-debt candidates**.

**D7 detail:** micro-partition (3 nodes) but adjudicated **structurally as well as behaviourally**:
`cmp` proves all 5 files the two static guards scan (`manager.py`, `task_processor.py`,
`worker_pool.py`, `instance_messaging.py`, `job_recovery_service.py`) are **byte-identical**
TIP vs BASE, and the branch's change set is **disjoint** from the scan set — the branch
structurally cannot have produced these failures. Both guards are baseline-diff guards, so a
future branch legitimately touching those files will trip them (recorded as a standing caveat).

**Fence self-test correction (methodological):** the negative self-test form as literally
prescribed — `POSTGRES_DB=ensemble_prod env -u POSTGRES_DB …` — **cannot pass**: the scrub strips
the poison before the in-child fence reads it, so the child body runs and exits 0 (a false
verdict). D7 proved the fence properly with a 4-case matrix instead (in-child poison → rc=1 with
the body never executed; positive control; scrub-strips-ambient proof; fence-only-catches-ambient
proof) and filed a `skill_fix`. Standing rule: **the negative fence test must inject the poison
INSIDE the scrubbed child, not before the `env -u`**.

**U1 detail — and the correction of the "foreign KMS lane" narrative.** U1 root-caused the
recurring `install-audit.jsonl` dirt: it is **NOT a foreign lane** —
`tests/unit/services/test_kms_resolver.py` mints 9 real KMS handles →
`daemon/services/kms_lite.py::issue_handle` → `append_install_audit(event="kms_issue")` with no
workdir → `resolve_install_audit_path(workdir=None)` → `Path.cwd()/.agents/shared/planning/designer-agent/install-audit.jsonl`
— **a TRACKED file**. Proven by an isolated scratch-CWD probe (9 lines/run) and per-chunk
attribution (only the chunk containing that file writes). The earlier lanes' "concurrent KMS
lane" attribution is therefore **wrong in part** — the appends came from the test suites
themselves running from the worktree cwd (their own base legs and sibling lanes' base legs
sharing the tree). `skill_fix` filed against the parity skill's now-falsified "provably not
yours" claim.

**Two consequences recorded:** (a) a **test-hygiene defect** — a test whose side effect writes a
tracked file when run from the repo cwd; the base leg cannot use a scratch CWD (11 CWD-dependent
tests fail), so U1's handling — run from the worktree, capture the diff, then `git checkout --`
the single file — is the correct pattern. (b) The earlier "base-worktree hygiene conflict"
narratives (R1/R2/U5/R3/U4) should be read as *self/sibling-inflicted*, not foreign.

U1's 9 pre-existing reds are deterministic across **6 runs** (3× per leg), and 7 of the 9 are one
defect — the `JobItem` mirror overriding Instance-derived status in `job_queue_proxy_phase1`.
**Adjudicated (same rule as U2):** frozen as base-evidenced known-reds, consolidated into the
single test-debt list; NOT re-triaged inside the P4 verdict.

**U4 detail — the confound that could have poisoned the whole sweep, found and neutralised.**
`data/ensemble.json` is **gitignored**, so `git status` cannot see it — and the fresh base
worktree did not have it. U4's first base leg **auto-created** it with the default
`"database": "sqlite"`, while TIP carries `"database": "postgres"` ⇒
`EnsembleConfig.is_postgres` differed between legs ⇒ `daemon/auth/cli.py::resolve_engine` took
different paths ⇒ **apparent net-new failures that were pure leg-divergence**. The lane:
diagnosed it; **disproved the branch hypothesis** (the branch's f-string→`URL.create` rewrite
renders the same URL and fails identically on connect — not implicated); **state-matched** the
base by copying TIP's `ensemble.json` (md5 `21728729…` both legs); re-ran the FULL base leg —
the apparent net-new collapsed to the 2 flaky nodes.

**Artifact class #5 (ADJUDICATION RULE): gitignored state files can silently differ between legs.**
Before trusting any A/B on this repo, state-match `data/ensemble.json` (and any other
config-resolved gitignored file) between the tip and base trees. Direction of the bias here:
TIP(postgres) fails where BASE(sqlite) passes → manufactures FALSE NET-NEW, the worst direction.
U4's remediation left the state-matched file in the base tree so re-verification reproduces it.
The other 15 partitions all reported net-new = ∅, so none was visibly affected — but the rule is
now on record for any future sweep.

**Latent defect surfaced (NOT a branch regression — flagged for an owner-side commission):**
`daemon/auth/cli.py` catches only `CliError`, so a PG `OperationalError` escapes as an unhandled
traceback that **embeds the connection URL (host/port/db/user) in stderr**. The existing
`test_bearer_p5` only asserts the bootstrap *secret* isn't echoed, so this leak class is
uncovered by tests. Recorded in the KB.

The 47 pre-existing are dominated by known quarantine families (`test_builtin_mcp_servers` ×17 +
`test_context7_builtin` ×4 = the long-standing mock-gap rows 2026-09-04/10-02/10-12/10-16;
`test_configure_builtin_idempotency` ×15; `test_coder_developer_migration` ×5) — 21 of them are
PG-dependent errors that fail because the mandated scrub removes `POSTGRES_PASSWORD` while local
PG requires auth.

**U3 detail — two MORE base-leg artifact classes identified (total now 4).** The 6 BASE-only
nodes are all environmental, mechanisms read from the logs, not assumed:
- **Dir-name artifact:** `test_plugin_registry` asserts
  `re.match(r"^ensemble-src(-wt-[a-z0-9-]+)?$", DEFAULT_PLUGINS_ROOT.parent.name)` — the base
  worktree is named `hrr-base-4c888fbb`, which does not match → deterministic base-only failure.
- **Missing-`.venv` artifact:** `test_sync_runner`'s fixture runs
  `subprocess.run([".venv/bin/python", …], cwd=repo_root)`; the read-only base has no `.venv`
  → `FileNotFoundError` at setup (×2 nodes).
- **3 chunk-context flakes** in `[postgres]`-parametrized auth-repository tests: 1F/1E in the
  chunk run, 3/3 PASS isolated → FLAKY bucket.

**Complete base-leg artifact taxonomy for this sweep (adjudication rules):**
1. `/tmp`-path exemption (`workspace_guard` exempts temp dirs) — U5, 5 nodes
2. base-worktree **name** not matching `^ensemble-src(-wt-…)?$` — U3, 1 node
3. base worktree lacks `.venv` — U3 ×2, R2 ×1
4. load-sensitive wall-clock tests under concurrent-lane contention — R3, D4, R1 (the flaky bucket)
**None of these is creditable to the branch. Genuinely-fixed-by-branch count remains 0.**

**Two methodology upgrades recorded for sibling lanes:** (a) `-rf` **hides ERROR node IDs** —
they surface only as `E` in the progress line; recover with `-ra` + progress-line→collect-order
mapping (U3 hit 4 hidden ERRORs). (b) The import proof should be an **in-pytest probe** (a temp
file outside both repos), not just `python -c` — `tests/unit/routers` and `tests/unit/repositories`
lack `__init__.py`, so pytest's basedir insertion never reaches the repo root and the
`python -m` form is what actually makes the BASE leg resolve correctly.

**R3 detail — third sign-flip catch.** The only node not failing in all four runs
(`tests/test_worker_notification.py::…::test_multi_worker_notification`) flipped `P F F P`
across (TIP1, BASE1, TIP2, BASE2). Isolated 3×: 6/6 green — a scope that **hides** the flake;
file-scope 3×: ≥1P+≥1F at BOTH legs → **FLAKY**. Root cause characterized: the test drives a
real `WorkerPool` with hard wall-clock deadlines (2.0 s claim / 3.0 s run / 1.0 s
distinct-worker) + `time.sleep(0.05)` polling — load-sensitive by construction, and this sweep
ran under concurrent-lane contention. Not branch-related (file untouched by the branch; fails
identically at base).

**R3's stable core is 33 nodes ≈ 44% of the partition's selected nodes failing at BASE** —
`test_spawn_team_members.py` ×20, `test_spawn_limit_edge_cases.py` ×11,
`test_skill_evolution_config.py` ×2, `test_resource_monitor_storage.py` ×1 — a large
base-inherited debt with no quarantine row → **test-debt candidates**. The lane correctly notes
this partition alone is a weak regression signal; the A/B set-diff is what carries the verdict.

**Dispatcher brief correction recorded:** the brief labelled the scrub "22-var" but the command
as written contains **20** `-u` flags (the lane executed it verbatim and counted). Safe here —
the ambient dangerous set is fully covered and the `PG*`/`DATABASE_URL_POSTGRES`/`PGSERVICE*`
names were already unset — but the label was wrong. Also flagged for future briefs:
`ENSEMBLE_UPGRADE_SCRIPTS_DIR` is ambient-set and NOT on the scrub list (affects promote scripts
only, not tests).

**D4 detail — second sign-flip catch.** Shot 1 reported `net-new = 1`; shot 2 reported
`net-new = 0, fixed = 1`. Both candidates live in `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution`
— isolated runs pass **6/6 on both legs**, file-scope flips → classic inter-test coupling →
**FLAKY**, not a branch defect. Stable core 15 = `test_process_message_metrics.py` ×10,
`test_instance_messaging_queue_routing.py` ×1, pause-family e2e ×4 — red in ALL 4 full-leg shots,
base-inherited, no quarantine row → **test-debt candidates**.

**Two dispatcher-premise corrections recorded (the lane corrected me, correctly):**
1. **e2e is NOT 0-executed.** 30 e2e nodes DO execute (7 of 21 e2e files lack the `integration`
   mark): auth_lifecycle 12, pause_resume_turn_handle 6, pause_turn_then_resume 3,
   full_chain_reconciler 3, answer_dismiss 3, promote_stop_frozen 2, pause_resume_unchanged 1.
   The remaining 14 files are module-marked → 0 nodes. 4 of the 30 executed are in the stable
   failing core — pre-existing at BOTH legs, not branch-caused.
2. **One symmetric list exclusion, applied identically to both legs:**
   `tests/e2e/test_context_injection_hybrid.py` performs a **module-level HTTP GET to
   `localhost:8079` at import time** (line 50), which aborts the entire pytest session with a
   collection ERROR. Dropped from both run lists; it contributes 0 executable nodes (both its
   tests are integration-marked). **Flagged as a test-hygiene defect** — a module-level network
   call at import is the collection-abort class, worth its own small fix.

**U2 detail:** the lane went beyond the mandate — since the mandated re-proof set
(net-new ∪ fixed) was empty, it **3× re-proved all 13 pre-existing reds on BOTH legs**: every
node `1 1 1` on both legs → stable reds, not flaky reds masking a regression. The 13
(`test_inner_soul_compaction.py` ×3, `test_instance_tools.py` ×2, `test_knowledge_tools.py` ×2,
`test_prompt_section_reference_integrity.py` ×5, `test_snapshot_tools.py` ×1) have no current
quarantine row → **test-debt candidates**, frozen as the proven `tests/unit/tools/` baseline at
`4c888fbbb`. They do not gate the P4 verdict.

**Adjudication of the U2 lane's two questions (within the tester mandate):**
1. *The 13 baseline reds* → **frozen as base-evidenced pre-existing, NOT re-triaged inside the P4
   acceptance verdict.** Consolidated with the other partitions' non-quarantined base reds into a
   single test-debt list in this record (see Part 5 summary) for ONE follow-up
   quarantine/test-debt commission — not scattered per-partition commissions.
2. *The unscrubbed concurrent pytest against `/tmp/base_tree` with ambient live PG coordinates* →
   **NOT one of this lane's 17 commissioned partitions** (none was briefed with that path); it is
   a foreign lane's process. Recorded as an env-poison exposure and **escalated to the leader in
   the acceptance report** — not killed and not modified (it is not mine to signal).

**Base-leg artifact class (ADJUDICATION RULE for the whole sweep):** the shared base worktree
lives at `/tmp/hrr-base-4c888fbb`, and `daemon/services/workspace_guard` **exempts temp-dir
paths** — so any filesystem-path-sensitive test run from the base leg violates its own
"non-temp workdir" precondition and fails **at base only**, masquerading as "fixed by the
branch". U5 caught it and **proved it two ways**: (1) git-object proof — the two test files and
`workspace_guard.py` are blob-identical base↔tip and the branch's `daemon/` delta never touches
their code path; (2) location proof — the SAME base code, `git archive`d into a non-`/tmp` path,
passes **3/3**. Rule: **any BASE-only failure in a filesystem-path-sensitive test is an
environment artifact until a non-`/tmp` probe says otherwise — never credited to the branch.**
U5's 5 BASE-only nodes are so classified. **Genuinely-fixed-by-branch count for the sweep remains 0.**

*R2's single "fixed" node is **environment-attributable, NOT branch-attributable** and is NOT
credited to the branch: `tests/test_post_restart_arm_notify_drill_smoke.py::test_drill_smoke`
fails at base only because the read-only base worktree has no `.venv` (the drill hard-requires
`$REPO_ROOT/.venv/bin/python`) while the tip does. Deterministic 3/3 PASS at tip, 3/3 FAIL at
base — mechanically fixed, environmentally caused. **Genuinely-fixed-by-branch count for the
sweep remains 0.**

**U5 detail:** 111 files, 4 chunks, identical boundaries. Excluded 1 base-absent file:
`tests/unit/test_message_queue_boot_recovery.py` (the branch's new test, 21 nodes = exactly the
2545−2524 collection delta — accounted, not mysterious). The 17 pre-existing failures
(`test_governor_recursion_guard.py` ×9, `test_gaia_agent.py` ×3, `test_maintenancer_agent.py` ×3,
`test_maintenancer_kb_coverage.py` ×1) are identical on both legs; none carries a current
quarantine row → **test-debt candidates**. Import proof passed both legs; the venv's editable
install is a static-path `.pth` (no MetaPathFinder), so the `PYTHONPATH` pin wins `sys.path`
ordering deterministically. **Live-fence corroboration:** `:9797` livez
`{"status":"alive","version":"0.26.3"}`, uptime 4.5 h → no restart occurred during the sweep.

**U5 also cleaned up after itself precisely:** a daemon import appended 23 `kms_issue` lines to
the tracked `install-audit.jsonl` in the BASE tree during its base leg — restored to HEAD
(porcelain EMPTY). The TIP tree's copy was already dirty from a prior lane; it removed **only its
own 51 lines** (timestamps strictly inside its run windows), leaving the file byte-identical to
its pre-run prefix, with backups kept. That is the correct way to revert on a shared tree.

**R2 detail:** 84 files, 8 chunks with identical boundaries both legs. NET-NEW = **∅** (verified
per-ID via `comm`, not by count). The 50 pre-existing failures are the identical ID set on both
legs; the set is dominated by known families — `test_progressive_dispatch.py` ×18 (QUARANTINE
2026-09-06 fresh-SQLite migration-trap row), `test_memory_integration.py` ×16 (2026-10-14
Cluster E row), `test_migration_api_comprehensive.py` ×2 (U9 row) — plus
`test_memory_system.py` ×8, `test_job_queue_tools.py` ×4, `test_models.py` ×1,
`test_phase8_docs_gate.py` ×1, `test_prompt_version_resolution_core.py` ×1 which are
base-evidenced (red at both legs) without a current quarantine row → **test-debt candidates**.
Full lists preserved in `/tmp/hrr-part-R2.{tip,base}-failed.txt`.

**Flaky bucket ∅ for R2** — the one asymmetry (the drill-smoke node) is deterministic in BOTH
directions (3/3 PASS at tip, 3/3 FAIL at base), which is the signature of an environmental
difference, not flake. The lane diagnosed the mechanism instead of pocketing it as a "fix":
`git diff base..tip` over that drill and its test is EMPTY.

**Base-worktree hygiene conflict disclosed:** the foreign KMS lane's `install-audit.jsonl`
append recurred inside R2's base-leg window; R2 reverted that single file (diff saved to
`/tmp/hrr-part-R2.base-residue-install-audit.diff`) restoring porcelain to EMPTY. R1 had left
the same append untouched. Both handlings are recorded; the file is under `.agents/` and affects
no code path under test.

**R1 detail — the sign-flip lesson, live.** This partition ran **2 shots per leg** and caught
exactly the failure mode a single shot would have produced: shot 1 reported **"3 fixed"**
(BASE-only failures) and shot 2 reported **"2 net-new"** (TIP-only failures) — the same four
nodes flipping sign between shots. Every one of the four (`tests/test_chart_tools_reuse_integration.py`,
order/timing-sensitive: `sqlite:///{db_path}` + 10 `asyncio.sleep`/`wait_for` markers) shows ≥1P
**and** ≥1F at **both** legs → **FLAKY**, not fixed, not net-new. A one-shot read would have
produced a false improvement claim *or* a false regression claim depending on timing.

The **28-node stable core** (red in every shot at every leg):
- `tests/test_api.py` ×6 — **verified against QUARANTINE.md row 11 (2026-10-19)**, exact match
  (4× stale `source=None` @ `daemon/routers/instances.py:468` + 2× Mock→AsyncMock @ `daemon/routers/messages.py:326`).
- `tests/test_governor_integration.py` ×16 · `test_charter_render_capture.py` ×3 ·
  `test_agents_api.py` ×2 · `test_enqueue_shared.py` ×1 — **NOT currently in QUARANTINE.md**;
  base-evidenced by this A/B (red at both legs, deterministic across 4 shots) but with **no
  quarantine row yet → flagged for a test-debt row** (22 nodes).

Zero of the 51 partition files changed between base and tip — the partition is a pure behavioural
blast-radius check on the 5 changed daemon files. 8 deselected nodes are marker-marked
(integration/postgres/slow) and identical on both legs.

**Concurrent-lane contention disclosed:** other regression partitions ran against the same two
worktrees during this sweep (shared `data/instances.db` + `data/logs/`). Bias direction favours
the conclusion — contention causes spurious *failures*, not spurious passes — so net-new = ∅ under
contention is the strong direction.

**Base-worktree note:** a foreign concurrent KMS lane appended to
`.agents/shared/planning/designer-agent/install-audit.jsonl` inside the BASE worktree at 14:51:34
(attributed by content — pytest cannot mint KMS handles). The file is under `.agents/`, not
`daemon/` or `tests/`, so the code A/B is unaffected; recorded for provenance.

**U6 detail:** 162 files, byte-identical base→tip (0 content delta) — a clean A/B of the
shipped-code delta against a fixed corpus. The 9 pre-existing failures are **deterministic and
byte-identical on both legs** (the 3 failing files were re-run 2× per leg: identical FAILED set,
md5 `532dc5343858`, 9F/81P on all 4 runs). **All 9 are QUARANTINE-covered known families:**
`test_repository_claim_lane.py` ×6 (QUARANTINE 2026-10-12 row — StrictTwoWay / B1ConditionalFailOpen /
LaneSignatureContract drift cluster), `test_project_manager_agent.py` ×2 (row 2026-10-07 —
project_manager prompt cross-refs), `test_terminal_reason_mirror_set_regression.py` ×1 (row
2026-09-03 — MIRROR_SET). Root causes captured at BASE (branch-independent): claim-lane
`assert None is not None` ×6; `workflow.md must reference soul.md` prompt drift ×2; undeclared
`terminal_reason` literals ×1.

**Correction to the dispatcher's partition premise (recorded):** the U6 brief claimed
`test_message_queue_boot_recovery.py` ('m') and `test_connect_timeout.py` ('c') fall in
`[n-z]` — they do not; glob `[n-z]` matches only the first letter after `test_`. They belong to
U5 (f–m) and U4 (a–e) respectively, which handle the base-absent exclusion. The lane caught and
corrected the premise rather than following it.

**Standing scrub-gap confirmation:** the ambient `QUEUE_DISCARD_ON_STARTUP=true` (not on the
prescribed scrub list) was inherited identically by both legs — A/B validity preserved — and no
partition test reads it (reads only in `daemon/config.py`, `manager.py`, `api.py`).

**U7 detail:** both legs fully green, run **twice** each (the confirmation pass was added by the
lane itself because a single green/green sample cannot separate "stable" from "lucky"). Import
proofs passed on both legs (base resolves inside `/tmp/hrr-base-4c888fbb`). 0 of 27 files absent
at base. Arithmetic check `collected == P+F+E+S+D` holds on every leg. Base porcelain stayed
EMPTY. Chunked 3×9 files per leg, sequential, max 86 s per invocation. Disclosure: pytest's
assertion-rewrite hook wrote gitignored `.pyc` into the base tree's `__pycache__`
(`PYTHONDONTWRITEBYTECODE` cannot stop it) — porcelain-empty is the sanctioned gate; the lane did
not delete them (deletion on a read-only tree would be worse than the write).

Method and full partition table: `.agents/tester/RESULTS/2026-10-21-hrr-p4-regression-plan.md`.
Discipline enforced: identical commands + identical 22-var scrub on both legs; full FAILED-list
diffs; `red-at-base` = pre-existing; `green-at-base → red-at-tip` = **net-new by name**;
3× no-code-change flaky re-proof for every single-leg discrepancy; fully-deselected partitions
reported as 0 nodes, never as a pass.

---

## PART 6 — Live-surface attestation

**[PENDING — final re-attestation at lane close]**
Standing baseline across all prior runs (7 drill runs + all recon/ensure lanes):
`ensemble-main` MainPID **528942**, NRestarts **0**, ActiveEnterTimestamp **Wed 2026-10-21 10:38:47 UTC**;
`ensemble-demo` MainPID **898`; `/home/nea/agents-ensemble/current` → **`releases/v0.26.3`**;
demo → `releases/v0.16.3`; ports 9797/7979 held by the same live `ensemble-prod` pids (529346 / 1147);
port 8088 never signalled; `/etc/systemd/system/*` never touched; remote PG `10.44.0.2/ensemble_prod`
never connected.

---

## ENVIRONMENT HAZARD RECORDED (acceptance-relevant)

**The ambient agent shell carries `QUEUE_DISCARD_ON_STARTUP=true`** — inherited from the live
daemon's env alongside the live PG poison set (verified via `ens_env_read`). The shipped-default
drill leg stayed on the shipped branch **only** because the harness's `daemon.env` is an explicit
allow-list that excludes the flag and the transient unit takes no caller env — proven three ways
(branch label; `DELETE_PHASE: no doomed ids … DELETE no-ops`; `reset=1` with `pending_count==1`).
**Any future unfenced daemon boot from an agent shell silently lands on the override branch and
masks the shipped default.** Recommendation: add this flag to the standing scrub list for any run
whose subject is queue-discard behavior, and always assert the OBSERVED branch label.

---

## PART 6 — Live-surface attestation at lane close: **UNCHANGED**

Final attestation lane `071098c4`, read-only, 12/12 checks, at ≈20:15Z:

| Indicator | Observed | Baseline |
|---|---|---|
| `ensemble-main` MainPID / NRestarts / ActiveState / epoch | **528942 / 0 / active / Wed 2026-10-21 10:38:47 UTC** | exact match |
| `ensemble-demo` MainPID / NRestarts | **898 / 0** | exact match |
| `:9797` livez | `200 {"status":"alive","version":"0.26.3","uptime_seconds":34599.7}` — uptime arithmetically consistent with the 10:38:47Z epoch (**no restart during the entire lane**) | match |
| `:9797` / `:7979` owners | `ensemble-prod` pids **529346 / 1147** | exact match |
| `8079 / 8088 / 10000 / 10001 / 15533` | **all FREE** (drill, mock, self-system, and the disposable PG cluster all released) | match |
| Release symlinks | `releases/v0.26.3` / `releases/v0.16.3` | exact match |
| `hrr-*` units | **0** (units, unit-files, and system-wide grep) | match |
| PG16 `hrr%` roles / DBs | **0 / 0** — this lane created ZERO surviving PG residue | match |
| Drill/mock/daemon processes | **none** | match |
| Base worktree | porcelain **EMPTY**, HEAD `4c888fbbb` | match |

**Four NON-LIVE residue deltas disclosed (adjudicated: disclose, do not delete):**
1. **TIP worktree dirty** — `install-audit.jsonl` +170 `kms_issue` lines (last write 15:48:11Z,
   ~4.5 h before attestation; **no active writer** — this is the U1-root-caused test side
   effect, not a live process) + 2 untracked tester RESULTS files (**committed by this lane**,
   resolving them).
2. **Tip HEAD at `186768717`** (35 commits past base — the P0–P4 output). Note:
   `shared_meta_kv.wt.claim.hard-reboot-recovery` is **stale** (records head `4c888fbb…`,
   heartbeat 05:33:30Z) — left for the claim's owner (giter) to refresh.
3. **567 throwaway PG test DBs** on the local cluster (64 `ensemble_blob_prune_*` + 503
   `ensemble_promptver_test_*`, owners `ensemble`/`ens_p2_coder`) — **other commissions' residue,
   not this lane's** (this lane's `hrr%` count is 0). Routed to a cleanup commission; not dropped
   here (shared-role ownership, possible active use).
4. **/tmp residue** — 12 `/tmp/hrr-drill-*` evidence bundles + ~600 lane artifact files + the
   329 MB base worktree. **The evidence bundles and failed-lists are the acceptance evidence and
   are deliberately retained** (cited throughout this record); `/tmp` is volatile and
   self-cleans on reboot. The base worktree is **retained for re-verification reproducibility**
   (one command to re-materialize if lost); a duplicate registered worktree at the same commit
   (`/home/nea/ensemble-src-wt-hrr-base`) and 20+ stale stage worktrees are noted for the giter
   cleanup lane.

---

## VERDICT

# **ACCEPT-WITH-QUALIFICATIONS**

Every mandated acceptance item **PASSED**:

| Item | Verdict |
|---|---|
| P4-1 fresh-dead stranded row recovered (shipped default) | ✅ PASS |
| P4-2 `processing_count == 0` | ✅ PASS |
| P4-3 `pending_count == 1` (moved, not deleted) | ✅ PASS |
| P4-4 busy-guard predicate + shipped-default branch | ✅ PASS (visible-pending, guard NOT cleared — correct for this branch) |
| P4-5 AutoContinue resume (`candidates=1, scheduled=1` + per-instance success line) | ✅ PASS |
| P4-6 schedule survival | ✅ PASS |
| P4-7 busy-guard clears (OVERRIDE lane, `QUEUE_DISCARD_ON_STARTUP=true`) | ✅ PASS — CLEARED, proven 3 ways incl. the DELETE_PHASE journal sequence + negative control |
| AC6 recovery ≤ 30 s | ✅ PASS — **15 s**, daemon-path basis |
| Isolation (transient unit, cgroup asserted) | ✅ PASS |
| Full regression, net-new failures | ✅ **0 across all 17 partitions, ≈29,264 nodes/leg, whole-tree** |
| ensure.md Core gates | ✅ 4 packs green + 4 static gates green |

### The qualifications (enumerate — none blocks; all are recorded facts a reader must see)

1. **AC6 is daemon-path-only evidence.** No real host reboot (kernel power-loss semantics, fsync-loss, cold-PG WAL replay NOT reproduced); **PG was NOT started in parallel** with the daemon (the PG-start race is unreproduced); the transient unit is not the production unit. The G6 ordering is evidenced by the unit-template check (C4) + the P1 pack, not by this drill.
2. **STR was not observed — by design.** It is a boot+10min backstop; no 5-minute-capped pack can observe it. "All mechanisms fire" claims must say so.
3. **G6 preflight SUCCESS is invisible to operators** (leader ruling #1 — DEFERRED known gap; the FAILURE path exit-75 is visible; the drill substitutes the stronger scratch-PG engine marker).
4. **`concurrency_atomic_unit_test` is 42.7% inert** (leader ruling #3): a green is NOT coverage of the cascade/observer race families. Recorded so nobody reads it as such.
5. **`scripts/run_tests_scrubbed.sh` under-scrubs** (leader ruling #2 — deferred to its own commission): an ambient `ENSEMBLE_SELF_ENV=live` shell passes its leak gate and it cannot wrap a command.
6. **Harness LOC overage accepted** (leader ruling #4): 635 code lines vs the ~120 soft ceiling, disclosed.
7. **D13 task-less RUNNING ghosts are OUT OF SCOPE** (reported, never failed); **lane-6 healing does not ship** (never asserted).
8. **355 pre-existing stable reds + 13 flaky nodes** exist at BASE — none branch-caused; the non-quarantined subset is consolidated as ONE test-debt commission. **8 follow-up commissions routed** (see Part 5), including two 🔴 (the `tests/support/conftest.py` WAL-pragma deadlock; the 145 unmarked `tests/integration/` files executing 1085 real-LLM nodes in the default gate).
9. **Environment hazard recorded:** the ambient agent shell carries `QUEUE_DISCARD_ON_STARTUP=true`; any unfenced daemon boot silently lands on the override branch. The drill is safe by construction (allow-list `daemon.env`).
10. **Branch carries base version** `0.26.2` in `/livez` (no bump on this branch) — expected.

**Gate status: the hard-reboot-recovery branch at `186768717` (shipped tree final `6c318abc0`) is ACCEPTED for merge/promote consideration, with the qualifications above attached to the record.**
