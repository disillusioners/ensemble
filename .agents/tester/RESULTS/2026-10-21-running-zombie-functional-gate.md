# Independent Functional Verification Gate — running-zombie sweep

**Date:** 2026-10-21
**Target:** `fix/running-zombie-sweep` @ `9b206836917d579df0f4ae69b8ff2853082269ee` (17 commits over base `33e1ef513f3504b4c5edf11b085087cf977029c3`)
**Worktree:** `/home/nea/ensemble-src-wt-rz-fix` (fence: `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`, `ENSEMBLE_SELF_ENV=dev`)
**Commission:** independent functional gate — the last gate before the merge/stage HOLD. Nothing from the dev lane or the two governor councils was re-used as evidence; every number below was produced by this gate's own workers.

## Verdict

# 🟢 PASS-WITH-PREEXISTING

**No blocking defect. No false-completion risk found. No false claim found in the branch's §5/§10 substance.** 6 independent lanes + a gating recon, 0 re-dispatches, 0 pack timeouts, 0 production-code edits, 0 promotes.

Pre-existing debt and non-blocking findings are itemized in [Findings register](#findings-register). 5 items are 🟠 should-fix (one is a truth defect in an in-tree comment, three are missing pins, one is a house-fence idiom that silently no-ops) and 3 are 🟢 nice-to-have. None of them can finalize a root that should not be finalized; where the gate found a *false-completion* risk class it found the pins sound.

| Section | Verdict | Headline evidence |
|---|---|---|
| A — E2E heal, both shapes, real guard | ✅ PASS | 71/71 pack (19.72s); real completion chain invoked (call-through spy, 4/4 variants; Tier-B with transcript seam UNPATCHED → real checkpointer `aget`); loud line `transitioned=completed reason=finalize_gap` |
| B — commissioned negatives | ✅ PASS (1 PARTIAL) | 8 rows PASS with quoted skip-classes; **B3 attribution-only gap** (behavior proven safe by probe) |
| C — F1 / false-completion negatives | ✅ PASS | 19/19 `-k MemberAliveCompanionLeg`; C1 `member-live-task` + `drive.await_count==0`; C2/C3 heal as specified; C4 behavior verified (no pin) |
| D — guard posture | ✅ PASS | probe-error fail-MISS on **both** seams (`live=True, error=True`); INFO bound = exactly 1 aggregate line; **D2b truth defect** found (count discarded on the hold path) |
| E — mutation re-proof | ✅ PASS | 6/6 mutations MEANINGFUL, **0 ineffective pins**; M3 signature exactly as predicted (`'gate-block' != 'member-live-task'`) |
| F — ★ PG-dialect confirmation | ✅ PASS | **23/23 probes on real PostgreSQL 16.15**; both heal shapes healed on PG; predicates proven; dialect-independent verdict confirmed |
| G — regression base vs HEAD | ✅ PASS | FAILED-list sha256 `82557ce0b62d…e4043` **byte-identical** base↔HEAD (6 runs, same digest); census `38/31/7 → 39/32/7` via the census's own walker |
| H — honesty spot-checks | ✅ PASS | 10/10 consumer sites re-derived; row 8/9 semantics confirmed exactly; §5/§10 substance-true; id-scrub 4/4 |

## Scope decision

The commission named the full A–H matrix explicitly (release-gate class), so **no scope reduction was applied**. One claimed evidence item was **not reproducible by construction** and was superseded by a stronger invariant of the same intent:

- §5's "13 guard-consumer packs / 165 passed" — the 13-pack list is **recorded nowhere** (only the aggregate survives in commit `e775f56a5`; the design doc never mentions 13/165). A brute-force subset search found 146 candidate 13-subsets summing to 165 (not uniquely derivable). **Superseded** by the whole-`tests/job_queue/` base↔HEAD FAILED-digest invariant (section G), which is broader than any 13-pack spot-list and is what the commission's section G mandates.

## Instances

| Lane | Instance | Scope | Skill |
|---|---|---|---|
| recon | `85a20e2b-4cba-4816-9800-1770dfd0d58d` | worktree/env/pack inventory (read-only, gating) | none |
| A+B | `a23028fd-5415-4fea-80dd-076a3a2439d1` | E2E both shapes + 9 negatives + real-chain probe | none (DIY) |
| C+D | `2255a723-c609-4dc9-a201-c33e72e441cd` | F1 negatives + guard posture + 3 unpinned probes | none (DIY) |
| E | `d6ac7953-3498-40ef-b747-bb67d5387c36` | mutation re-proof M0–M5 (scratch worktree) | `mutation-grade-pin-review` (found via explicit search; not auto-injected) |
| F | `22ee9775-8c25-4448-90c3-6649595e95dc` | PG-dialect confirmation (real PG 16.15) | `pg-dialect-behavioral-parity-pass`, `pg-scratch-db-independent-verification`, `sqlalchemy-predicate-claim-verification` |
| G | `9d72b4f6-6923-4ca5-82f1-66da08dda7bb` | regression digest + RZ/f2 + census | `db599e01`, `a41b7258`, `20f916b3` (house skills, consulted mid-run) |
| H | `373bb125-d365-448d-a700-e172ca0402b7` | honesty spot-checks | none (DIY) |

Scratch artifacts (not committed): `/tmp/rz-probe/` (A+B), `/tmp/rz-lane-cd-probe/` (C+D), `/tmp/rzgate/` (G), `/tmp/rzgate-T86j7I/` (F). Lane E's scratch worktree was created and removed (teardown proven: `worktree list` count back to 37, path absent).

---

## A — E2E heal, both shapes, through the REAL guard

**Verdict: PASS** (A1 and A1-real-chain; A2)

Baseline: `timeout 300 env -u POSTGRES_* -u DATABASE_URL .venv/bin/pytest tests/job_queue/test_running_zombie_sweep.py -q --tb=short -p no:randomly` → **71 passed in 19.72s** (zero reds). `--collect-only -q` independently confirms 71 node ids.

| Row | Proving node | Evidence |
|---|---|---|
| A1 Oct-19 all-terminal shape | `TestRunningZombieCycle2TreeLeg::test_incident_shape_heals_through_the_real_guard` (:1519-1534) | `assert outcome["healed"] is True` (:1529); instance row `status == InstanceStatus.COMPLETED.value` (:1531-1533) |
| A2 Oct-21 depth-3 idle member | `TestRunningZombiePredicate::test_stale_depth_three_member_heals` (:820-845) | `healed is True` (:841); `status == COMPLETED` (:842-844); guard log `member-alive probe … live_work=False no live task row ⇒ stale-eligible` — the heal came via **staleness expiry**, not the all-terminal short-circuit (exactly the commissioned claim) |
| A1-real-chain | probe `/tmp/rz-probe/probe_a1_realchain.py` (4 variants) | real `_process_child_completion_and_notify_parent` invoked 1× per variant; `running → completed`, v1→2; loud line `mission_strand_healer\|running-zombie: … transitioned=completed reason=finalize_gap`; lifecycle publish awaited once |

**Disclosure corrected (stronger than briefed).** The brief's premise — "the pack replaces `_process_child_completion_and_notify_parent` with a spy drive" — is inaccurate: that patch sits in a **negative** node (whose point is `assert drive.await_count == 0`). The A1 **positive** does not replace the chain; `_patch_drive_seams()` (:537-576) mocks only 3 upstream transcript seams (`get_instance_messages`, `get_last_assistant_timestamp`, `MainLoopBridge.run_async_no_wait`). The gate's Tier-B probe removed even those: `daemon.persistence.get_instance_messages` ran unpatched against a real checkpointer (`saver_aget_calls=1`). **The guard, the healer decision logic and the production completion chain are all real in A1.**

**Effect-by-effect audit.** `status == COMPLETED` asserted; `outcome["healed"]` asserted but the sub-fields (`reason="finalize_gap"`, `drove=True`, `completed_age=90000`, `tree=11`) are asserted only in the probe, not in-node; the **loud heal line is asserted in a different node** (`TestRunningZombieObservability::test_heal_emits_the_greppable_line` :1146-1161: `"mission_strand_healer|running-zombie:" in joined`, `"transitioned=completed" in joined`, `"reason=finalize_gap" in joined`).

**D2 (expectation vs design, no defect).** The commission's "outcome recorded, epoch projection ends (terminal_reason set), JobItem healed" does not match this lane's design: `terminal_reason` is a `job_queue_items` column (`Instance` has no such field), and the epoch is a **read-time projection** — the instance's terminal row IS the epoch end. Probe (4 variants): the JobItem stays untouched (`admission_state=done`, `terminal_reason` NULL, version 0→0) while the **instance** flips `running → completed` and the lifecycle event publishes — matching design §2.2 step 4 (the chain's own DB stamp writes the instance row; the observer skips the JobItem UPDATE when `job_id=None`) and §1.1 ("the mirror JobItem never stranded in this shape" — f2 owns it). The pack pins the idempotency direction (`test_already_done_job_item_is_untouched`).

## B — commissioned negatives

**Verdict: PASS (8 rows) + 1 PARTIAL (B3, attribution only)**

| Row | Node | Skip class / evidence |
|---|---|---|
| B1 running-with-live-child | `test_member_with_live_task_row_is_not_healed[pending\|running\|processing]` (:1859-1896) | `skip_class == "member-live-task"`; `drive.await_count == 0`; status stays `running` |
| B2 pending-question-in-tree | `test_pending_question_is_skipped` / `test_durable_question_shadow_is_skipped` / `test_awaiting_answer_handle_blocks_the_heal` / `test_awaiting_answer_handle_probe_is_exact` | `skip_class == "pending-question"`; handle node asserts no-heal + status unchanged (no skip_class by design — the `non-terminal-task` leg fires first) |
| B3 long legit turn (root's latest task live) | **no exact node** → probe `/tmp/rz-probe/probe_b_coverage.py` | **PARTIAL**: probe proves root NOT swept + status unchanged for `processing`/`pending`/`running` (3h old) — skip class `latest-task-not-completed`, i.e. the *leg* differs from the pack's node (which seeds a COMPLETED task as the latest, so `non-terminal-task` fires). **Behavior safe; attribution-only coverage gap** |
| B4 young zombie (<2h) | `test_young_zombie_is_skipped` | `skip_class == "young"`; status unchanged |
| B5 kill-switch no-op | `test_kill_switch_is_a_complete_noop` (:1039) | `scanned == 0`, `healed == 0`, status unchanged; log-once asserted in the sibling node `test_kill_switch_warns_once_per_instance` (:1163-1189, `len(kill_switch_warnings) == 1`) — count is **level-filtered** (first tick WARN, repeat ticks DEBUG) |
| B6 non-root | `test_non_root_running_is_skipped` | `skip_class == "not-root"` |
| B7 no-task-row root | `test_no_task_row_is_skipped` | `skip_class == "no-task-row"`; status unchanged |
| B8 ERROR/FAILED latest task | `test_latest_task_failed_is_skipped`, `test_latest_task_cancelled_is_skipped` | `skip_class == "latest-task-not-completed"` |
| B9 tree-probe truncation | `test_breadth_truncated_tree_is_skipped`, `test_depth_truncated_tree_is_skipped` | `skip_class == "tree-truncated"` |

**Premise correction (commission vs code).** The commission said "both kill-switches off". There is **exactly one** kill-switch: `ServicesConfig` exposes 4 `mission_strand_heal*` fields of which one is boolean (`mission_strand_heal_running_zombie_enabled`); the healer takes `running_zombie_enabled` and **no** `enabled`. Two pins actively forbid a second master switch (`assert "enabled" not in params`, `assert "mission_strand_heal_enabled" not in fields`). Targeted run `-k "KillSwitchSurface or kill_switch_is_a_complete_noop"` → **3 passed, 68 deselected** (0.50s).

## C — F1 / false-completion negatives

**Verdict: PASS (C1–C4), 3 missing pins**

One scoped run: `… -k "MemberAliveCompanionLeg"` → **19 passed, 52 deselected**.

| Check | Evidence |
|---|---|
| C1 member live task younger than 21600s ⇒ NOT finalized | `skip_class == "member-live-task"`, `drive.await_count == 0`, root stays `running` (:1862, asserts :1887-1895); guard `:670 if age_seconds < timeout_seconds:` → `:676-686` `live=True, member_task_hold=True`; horizon `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS = 6*60*60` (:216) |
| C2 member task older than horizon ⇒ heals | `live is False`, `member_task_hold is False`, `timed_out is True` (:2255); `outcome["healed"] is True` + root `COMPLETED` (:2291) |
| C3 terminal member + lingering non-terminal task ⇒ still heals | `healed is True` + root `COMPLETED` (:1974, all-terminal short-circuit :944-959); 4-param matrix `test_a_terminal_members_task_row_does_not_hold_a_mixed_tree[pending\|running\|paused\|processing]` → `live is False`, `member_task_hold is False` (candidacy filter :567-574) |
| C4 JobItem-only live work ⇒ NOT held (disclosed) | probe: `live=False … member_task_hold=False` → root healed. Mechanism: the dateable-evidence read queries the **`task` table only** (`mission_live_guard.py:486-491`) → `attempt_at is None` ⇒ not held (:637-668). Docstring :556-560 + design §10.4-4. **No pin exists** |

## D — guard posture

**Verdict: PASS (D1–D3) + D2b truth defect**

| Check | Evidence |
|---|---|
| D1 probe-error fail-MISS (unpinned in repo) | probes built: (a) `has_live_work` seam raises → `live=True, error=True`; (b) task-age seam raises → `live=True, error=True`. Both → healer outcome `healed=False`, `skip_class="guard-error"`, `drove=False`, root stays `running`. Code: `:598-614` (live-work probe error), `:620-636` (task-age probe error); healer consumes the error verdict **fail-closed** (`mission_strand_healer.py:1182-1188`, before the live branch :1189-1198) |
| D2 aggregate-INFO noise bound (unpinned in repo) | probe with 3 undatable members → **exactly one** INFO record (`members_with_undatable_live_work=3 ⇒ NOT held …`), zero per-member INFO, 3 per-member DEBUG (:655). Source: `mission_live_guard.py:585-591`. **No pin exists** |
| D3 guard-level fail-OPEN posture | `test_repo_error_fails_open`, `test_guard_error_fails_open_to_finalize` → **2 passed**. Coherence: the *leg* fails MISS (`:598-614`, `:620-636`) while the *guard's tree-walk* fails OPEN (`:405-421` — "at-least-once terminal delivery takes precedence"), exactly as the module docstring states (`:45-50`); the healer then treats a leg error as fail-closed |
| **D2b** truth defect | on the young-task HOLD path the aggregate count is **discarded**: the `:676-686` early return does not call `_flush_undatable_note()` (flush sites are only `:605`, `:627`, `:697`) → **0 INFO lines** where 3 members were counted (3 DEBUG lines prove the count). Contradicts the in-tree comment `:581-584` ("emitted at EVERY exit … a hold cannot discard the count") and `design-decision.md:294`. **Observability only — the hold itself is fail-MISS (safe direction)** |

## E — mutation re-proof

**Verdict: PASS — 6/6 mutations MEANINGFUL, 0 ineffective pins.** Scratch worktree `/home/nea/ensemble-src-wt-rzgate-mut` (detached @ `9b2068369`, own `.venv`), removed at teardown; final revert → **71 passed**, `git diff --exit-code` clean.

| Mut | Change | Reds | Verdict | Reconciliation vs dev/brief claim |
|---|---|---|---|---|
| M0 | baseline | 0 (71P) | GREEN | — |
| M1 | member-alive leg never holds (`mission_live_guard.py:568`) | **8** | MEANINGFUL | **claim correction**: dev/brief said 4 = the A-1 set; measured **8 with the A-1 4 GREEN** — A-1 asserts the leg does *not* hold, so neutering makes it trivially true. The A-1 signature belongs to **M4**. Literal reading is strictly stronger coverage (8 holds-surface + 4 candidacy-surface, disjoint) |
| M2 | time bound removed (`:670 if age_seconds < timeout_seconds:` → `if True:`) | **3** | MEANINGFUL | claim "≥2" → actual **3** (the 2 claimed + `test_an_undatable_started_at_is_bounded_by_the_row_age`, the W-3b row-age pin) |
| M3 | H1 fail-MISS floor disabled (`:516 return now_utc_naive()` → `None`) | **1** | MEANINGFUL | **exact**: `test_member_with_undatable_task_stamp_is_not_healed`, failure verbatim `assert 'gate-block' == 'member-live-task'` — the predicted signature |
| M3b | W-3b `created_at` bound disabled (`:502`) | **1** | MEANINGFUL | disjoint single-point pin over the other undatable branch |
| M4 | terminal-candidacy filter dropped (`:572`) | **4** | MEANINGFUL | **exact**: the A-1 parametrization; failure `MissionLiveVerdict(live=True, … member_task_hold=True)` |
| M5 | anchor reverted to include the root's own `last_activity_at` (:868-877, sourced from `git show 47b69bf00^`) | **11** | MEANINGFUL | **yes — `test_stale_depth_three_member_heals` goes red** |

**M5 mechanism (strengthens the fix; gate-relevant).** Instrumented walks show the root's `last_activity_at` is refreshed by the **heal's own completion write** — `daemon/services/child_reports.py:3577` (WAITING_CHILDREN→RUNNING discharge UPDATE sets `last_activity_at=now_dt`) — not only by inbound messages. With the root inside the anchor, the **post-drive** re-evaluation sees a fresh anchor → `live=True` → the emission gate blocks → `skip_class='gate-block'` → the heal never converges. Under the fix (root excluded) the anchor stays the member's stale stamp → the backstop fires → the heal completes. The anchor fix is therefore pinned **11× over**, and the in-tree rationale comment understates it (advisory F3).

## F — ★ PG-dialect confirmation (the standing SQLite-only limit)

**Verdict: PASS — 23/23 probes on real PostgreSQL 16.15.** Scratch DB `ensemble_rzgate_pg` (owner `ensemble_dev_app`, created via `sudo -u postgres createdb -O`), schema created through the **production PG boot path** (`SQLModel.metadata.create_all`, `daemon/manager.py:798`). `SELECT current_database()` asserted **before every connect**; dropped at the end (`DROP DATABASE` OK; `pg_database` count = 0). `ensemble_dev` was never written (one sanctioned read-only `SELECT current_database()`).

| Item | Result |
|---|---|
| F1 member-ownership predicate | `Task.status.in_(sorted(NON_TERMINAL_TASK_STATUSES))` (`mission_live_guard.py:489`) over 7 seeded statuses → **exactly 4** returned (`pending, running, paused, processing`; `completed/failed/cancelled` excluded) |
| F2 `has_live_work` EXISTS probe (`daemon/repositories/instance/repository.py:2112`) | both polarities: live task **True**, live JobItem **True**, live child **True**, dead **False** |
| F3 E2E A1 on PG | `healed=True, drove=True, reason='finalize_gap', completed_age=90000, tree=8`; instance `status → completed`; JobItem `admission_state` stays `done` (idempotent, f2-finalized shape) |
| F4 E2E A2 on PG (depth-3 idle member, 25h stale) | `healed=True, tree=11, status=completed` — staleness path under the fixed non-root anchor |
| F5 sweep on PG | `scanned=1 healed=1 skipped=0 errors=0` |
| F6 F1 negatives on PG | member task 1800s old ⇒ **held**, `skip_class='member-live-task'`; task 25h old ⇒ **heals**; terminal member + lingering non-terminal task ⇒ **heals** |
| F7 dialect parity | 5 seeded scenarios PG vs parallel in-memory SQLite → **identical verdicts** (4× healed, 1× `member-live-task`) |
| F8 JSONB metadata (production write path `set_metadata_many`, read via `metadata ->> :key`, `repository.py:2298`) | `{'status':'pending','question_id':'q-pg-1'}` read back ⇒ heal **blocked**, `skip_class='pending-question'` |
| F9 session-timezone invariance | `Etc/UTC` engine vs `Asia/Ho_Chi_Minh` engine → **both healed**; age math is pure Python (`now_utc_naive` `timestamps.py:51`, `_age_seconds` `mission_strand_healer.py:1477`, `parse_completed_at_naive` `mission_live_guard.py:257`) — no DB-side age math |

**Landmine results (one briefed claim REFUTED, one NEW found).**

- **REFUTED (as stated): the JSONB 42804.** The pack's exact raw form `UPDATE instances SET metadata = :m` with `json.dumps(...)` is **accepted** on PG (`jsonb_typeof` = `object`, payload round-trips). The briefed mechanism (`JSONBType.process_bind_param` is a no-op) is irrelevant — `text()` bypasses the type engine either way. Real mechanism: psycopg3 sends a `str` param with **OID 0 (unspecified)**, so PG infers `jsonb` from the target column (proof: `SELECT pg_typeof(%s)` on a str param → `IndeterminateDatatype`). The landmine is **dormant, not dead**: it arms when the expression type is pinned (`CAST(:m AS text)` → **42804**; `CAST(:m AS jsonb)` → accepted).
- **NEW (not in the brief): the pack's raw `task` INSERT is not PG-portable.** It binds integer literal `0` for four **BOOLEAN** columns → **42804** `column "cancel_requested" is of type boolean but expression is of type integer`. Catalog-proven on the scratch DB (`cancel_requested/retry_scheduled/is_deferred/is_background = 'boolean'`; `created_at/completed_at = 'timestamp without time zone'`). Production agrees (`manager.py:7927` writes `cancel_requested = TRUE`); the SQLite migration `20260415_000001` declares `INTEGER` ⇒ **test portability only, not a production defect**.
- **Timestamp semantics:** all three formats cast; **PG drops the offset** (`+00:00` and `+07:00` both → `11:59:17.564831`). A non-UTC offset yields a **measured 7h (25200.0s) divergence** between PG (offset dropped) and SQLite (offset kept). Mitigation already in-repo: bind naive-UTC digits (`now_utc_naive`).
- **Conditional ORDER BY divergence** for *mixed* timestamp string formats on the same date: PG `[probe-1, probe-0]` (chronological) vs SQLite `[probe-0, probe-1]` (lexicographic — `' '` 0x20 < `'T'` 0x54) ⇒ can change which candidate a batch-capped sweep serves first. Production writes uniform naive-UTC digits ⇒ does not arise in practice.
- **Read-back tz asymmetry:** PG returns naive, SQLite returns aware (SQLAlchemy 2.0 parses the stored ISO string) ⇒ the SQLite pack exercises the *aware* branch, PG the *naive* branch. Production `_age_seconds` differs by **24 µs** (93600.152562s vs 93600.152538s).

**Disclosure:** the completion-chain drive seam is spied (the pack's own `_patch_drive_seams`; `events_service._publish_instance_lifecycle_event` mocked) — **the guard was never mocked**: real `evaluate_mission_live`, real repositories, real PG rows. Lane scope: `tests/postgres/` was **not** exercised (whole-project PG suite out of scope).

## G — regression: base `33e1ef513` vs HEAD `9b2068369`

**Verdict: PASS — byte-identical FAILED-list digest, zero net-new failures.** Base leg ran in a detached throwaway worktree (`wt-rzgate-base`, own `.venv`, import resolution asserted per leg — no `.pth` cross-talk), removed at teardown.

| Metric | BASE | HEAD | Δ |
|---|---|---|---|
| collected (selected) | 2152 | 2227 | **+75** |
| passed | 2099 | 2174 | +75 |
| failed | 15 | 15 | **0** |
| skipped / deselected | 38 / 3 | 38 / 3 | 0 |
| errors | 0 | 0 | 0 |
| runtime (3 runs/leg) | 97.00s / 89.51s / 99.50s | 88.84s / 89.38s / 90.88s | — |

**Digest invariant:** FAILED-list sha256 = **`82557ce0b62d5f64ed5b78e7e267bbfae618ac70c55d13c42c1d7f27692e4043`** on **both** legs — `cmp` clean, `comm -23` = ∅, `comm -13` = ∅. Six whole-suite runs (3 per leg: 2 unfenced + 1 fully fenced) produced the **same** digest every time ⇒ STABLE, not flake. Parity buckets: matched 15 / extra-at-base 0 / missing-at-base 0.

+75 attribution (exact): RZ pack **+71** (new file at HEAD) + f2/gate 5→9 **+4** + census/u7/watcher-loss-gap **+0**. All 15 reds are pre-existing and identical on both legs.

HEAD-only packs: **RZ 71/71 (17.05s)**, **f2/gate 9/9 (1.17s)**.

**Census (via the census's own walker, `discover_terminal_write_sites`, per-tree `DAEMON_ROOT` asserted):**

| Leg | DISCOVERED | CLASSIFIED | UNLISTED |
|---|---|---|---|
| BASE | 38 | 31 | 7 |
| HEAD | **39** | **32** | **7** |

Unlisted set identical on both legs (`daemon/manager.py:6757` + 6 `prompt_version` sites) ⇒ **zero new unlisted sites**; the branch adds one discovered site and classifies it. Census red-id digest `a008ea88bbfa7f7f354e4a0378a9f412e18f6e80aed8b9d86cfdf3014751f6f4` identical across 6 runs (3 per leg) ⇒ deterministic pre-existing, not flake.

**Correction to the brief's expectation (measured, not assumed):** the tripwire `test_hooked_entries_point_at_live_notify_calls` is **RED at HEAD *and* at BASE** with a **byte-identical 14-entry stale set** (`child_reports.py:3941 ×4`, `job_feedback_observer.py:2071`, `job_queue_service.py:1175/1296×2/1362/3967`, `job_recovery_service.py:3925/4112`, `task_processor.py:1087 ×2`). The branch's census-fixture edits (`job_recovery_service.py:4255→4261`, `child_reports.py:5173→5329`) touched entries **not** among the stale 14 ⇒ the branch did not fix the tripwire, and introduced no new staleness. Pre-existing fixture-rot, advisory only, net-new 0. The brief's own framing ("main gate red at BOTH sides for pre-existing debt only; stale line-pins 14, zero branch entries") is **CONFIRMED**.

## H — honesty spot-checks

**Verdict: PASS** — 10/10 guard consumers re-derived from source; every §5/§10 claim that could be checked held in substance.

| # | consumer | actual site | behavior class | doc row |
|---|---|---|---|---|
| 1 | pre-stamp gate | `child_reports.py:2752` | `live` ⇒ hold + `_schedule_strand_heal` ⇒ stamp REFUSED | match |
| 2 | emission gate | `child_reports.py:2590` | `live` ⇒ publish BLOCKED | match |
| 3 | healer tree leg | `mission_strand_healer.py:1163` | `member_task_hold` ⇒ skip `member-live-task` | match |
| 4 | f1 drift reconciler | `job_recovery_service.py:2666` | HOLD (`continue`) | match |
| 5 | f2 drift reconciler | `job_recovery_service.py:2970` | HOLD (`continue`) | match |
| 6 | boot sweep | `job_queue_service.py:635` | sweep does NOT fire terminal | match |
| 7 | zombie-GC held watch | `job_queue_service.py:874` | leg never runs | match |
| 8 | orphan release | `job_queue_service.py:1005` | **diagnostic-only** — token gated on `timed_out` (:1032) while `notify_watchers(...)` (:1065) fires on **all three** paths ⇒ **token suppressed, release FIRES** | match |
| 9 | stored-watch reconcile | `job_queue_service.py:1236` | hold ⇒ loss-case leg ⇒ **re-arm on the ORIGINAL work_id** (CAS `armed → rehydrated`; docstring :1131-1163; counters :1166/:1181/:1200) | behavior match, **citation drift** (:1231 cited) |
| 10 | work notifier | `work_notifier.py:687` | watcher retained, not notified | match |

Proof runs: orphan-release + held-watch pins **3 passed** (2.56s); stored-watch recovery file **11 passed** (2.15s) incl. `assert result["rehydrated"] == 1` (:610) and `_stored_status(...) == "rehydrated"` (:611).

**§10 material present and accurate:** 10.1 census framing (38/31/7 → 39/32/7; "39==39 clean" withdrawn; main gate red both sides; `manager.py:6757` verified and that file is untouched by the branch); 10.2 line-pins (17 examined = 14 still-stale + 3 branch-affected, re-pointed **in the census test file**, `job_recovery_service.py` untouched; tripwire named; the gate independently re-derived exactly **14 stale entries and zero `child_reports.py:53xx` entries**); 10.3 id-scrub (**3 branch-added live ids → descriptive placeholders** — verified from `e775f56a5`; `git diff 33e1ef513..HEAD -- daemon/ | grep '^+' | grep -oE '\b[0-9a-f]{8,32}\b'` = **EMPTY**; `432988b8` absent from `daemon/` at HEAD; retained exactly 2× in the design doc as marked grep aids); 10.4 the four disclosures (f2's behavior widens though its code is untouched; the probe-error hold posture — **exactly 4** A-2 log sites, emission gate excluded; a member inside a legitimate >6h turn stays indistinguishable from stale; a member whose only live work is a JOBITEM row is not held + the batch-cap bypass is unbounded).

---

## Findings register

### 🟠 Should-fix (5 — none blocking the HOLD)

| ID | Finding | Evidence | Impact |
|---|---|---|---|
| **F1** | **Truth defect**: the aggregate INFO count is **discarded on the young-task HOLD path** (`:676-686` early return skips `_flush_undatable_note()`; flush sites only `:605`/`:627`/`:697`), contradicting the in-tree comment `:581-584` and `design-decision.md:294` | Lane C+D probe D2b: 3 members counted (3 DEBUG lines) → **0 INFO lines** | Observability/claim honesty only; the HOLD itself is fail-MISS (safe direction). One-line fix + pin |
| **F2** | **Row-9 hold path is unpinned**: "hold ⇒ rehydrate on the original work_id" is source-verified but has **no** test; the branch's new `member_task_hold` leg can flip a stored watch from RELEASE to HOLD there. The briefed `:585`/`:664` anchors are **not** hold-side (`:585` = carrier fence, `:664` = legitimate consume) | Lane H sweep of every `reconcile_stored_watches` caller — the only guard-consulting stored-watch test has a TERMINAL mission | Mitigating: the branch's change at that consumer is **log-only**; the release/rehydrate decision is pre-existing. Test-debt |
| **F3** | Anchor-fix rationale understates the mechanism: the root's `last_activity_at` is refreshed by the **heal's own completion write** (`child_reports.py:3577`), not only by inbound messages — the fix is load-bearing against self-inflicted freshness (pinned 11×) | Lane E M5 instrumented walks (walk #3: root refreshed to now) | Doc/comment nit; the fix itself is correct and over-pinned |
| **F4** | **Missing pins** (4 behaviors, 0 tests): C4 (JobItem-only member ⇒ not held); D1a/D1b (both probe-error fail-MISS seams); D2 (aggregate-INFO single-line bound) | Lanes C+D | Test debt; each behavior is load-bearing and currently unguarded |
| **F5** | **House fence idiom is a silent no-op**: `env -u POSTGRES_*` does **not** scrub (bash does not glob environment-variable names) — ambient `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2` remained visible to test processes. **No prod contact occurred** (SQLite/mock scope; zero PG client markers in any log); Lane G re-ran both legs with an explicit per-var scrub + dead-port LLM fence → identical digest and counts ⇒ verdict unaffected | Lane G D2 (proof: `pg_typeof` of an unset literal; process env still lists 5 POSTGRES_* vars) | **House-wide**: every lane using this idiom believes it is fenced when it is not. Fix the idiom repo-wide (`-u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL`) |

### 🟢 Nice-to-have (3)

| ID | Finding |
|---|---|
| N1 | Citation drifts in the branch's docs: §2.8.1.1 row 9 cites `job_queue_service.py:1231` (actual `:1236`); §10.2 cites `child_reports.py:5173 → :5321` (census file says `:5329`/`:5345`). Substance correct; a ~2-line re-point preserves the honesty claim. Root cause: truth-commit-2 edited the same file in the same commit that computed the re-point, shifting the site by +5 |
| N2 | §5's mutation table mis-attributes the leg-neuter signature (dev/brief: 4 reds = A-1; measured: **8 reds, A-1 green under M1**, A-1 belongs to M4) and the reviewer's digest `f53d151a…` is truncated |
| N3 | §10.2's "of the census's 17 line-pins" invites misreading (14 still-stale at HEAD vs 17 examined over the branch's life) |

### 📌 Observations outside this gate's scope (no action taken)

- The live daemon is **v0.26.3** (`current → releases/v0.26.3`, symlink mtime 2026-10-21 10:38Z, MainPID 528942, NRestarts 0, epoch predates every lane's first command) while the project metadata still records live = v0.26.2 / v0.26.3 "not promoted". **Project KV appears stale** — flagged for the dispatcher; promote is user-nonce-gated and was not touched.
- The main checkout `/home/nea/ensemble-src` was dirty (29→47 paths) during the window; the growth belongs to a concurrent lane (nest-ops), not this gate.
- Lane G's evidence tree (`/tmp/rzgate/`) is outside the repo by design (its no-write mandate forbade a committed RESULTS doc); this document cites the numbers and digests.

## Disclosed limits

1. **Completion-chain drive seam is spied** in both the SQLite pack and the PG lane (pack's `_patch_drive_seams`; lifecycle publish mocked). The **guard, healer decision logic and real DB stamp are never mocked**; Lane A+B's Tier-B probe additionally ran the transcript read unpatched against a real checkpointer.
2. **Not run:** the whole-project PG suite (`tests/postgres/`), the release-gate full suite, and any daemon-boot E2E (no daemon was booted in any lane — every lane was in-process).
3. **Base choice:** `33e1ef513` (the branch merge-base). The branch's own doc used three different bases across cycles (`33e1ef513` / `8e45a62da` / `21a2be541`); absolute counts are therefore environment- and base-fragile (dev 2165P/18F, this gate 2099P/15F) — the invariant proven here is the **within-run base↔HEAD digest equality**.
4. **The "13 guard-consumer packs / 165" claim is not reproducible** (list unrecorded, 146 candidate subsets) and was superseded by the whole-dir digest.
5. **Conditional dialect risks** (F section): mixed-format timestamp ORDER BY divergence, non-UTC-offset 7h divergence, and the 24 µs naive/aware age difference are real but require input shapes production does not write (uniform naive-UTC digits).
6. **Scratch probes were not committed** (worktree read-only under the HOLD) ⇒ the F1–F4 pins and the D2b fix are **recommendations**, not landed artifacts.

## Fences held (all lanes)

`ensemble_prod` — never written (Lane F: only a scratch DB `ensemble_rzgate_pg`, asserted `!= ensemble_prod` before every connect, dropped + catalog-proven gone; Lane F's one sanctioned read was `SELECT current_database()` on `ensemble_dev`). `:9797` / `ensemble-main` / `/home/nea/agents-ensemble` / `current` / `releases/` — untouched (read-only observations only). No process killed (8088 self-system intact). No daemon booted; no port bound. Shared worktree porcelain empty before and after every lane. All runs `timeout 300`-wrapped with `env -u POSTGRES_* -u DATABASE_URL` (**see F5 — this idiom is a no-op**), no `-x`, no HOME-shaped paths, no secret values printed. Scratch trees/worktrees created and removed with teardown proven (`wt-rzgate-mut`, `wt-rzgate-base` — both absent from `worktree list` at the end). No merge, no push, no touch of `latest`.

## Recommendation

**The merge/stage HOLD can be released on this branch's functional surface.** The two coupled behavioral changes (anchor fix, member-alive companion leg) and the re-arm are proven by real-guard E2E on both SQLite and **real PostgreSQL**, by a full negative matrix, by six meaningful mutations with zero ineffective pins, and by a byte-identical base↔HEAD regression digest. Nothing found can finalize a root that should not be finalized.

Before/with the merge, the leader should route: **F1** (one-line flush + pin — honesty), **F2** (row-9 hold pin), **F4** (4 missing pins), **F5** (house-wide env-scrub idiom — affects every lane in this house, not just this branch), and optionally **N1/N2** (doc re-point + mutation-table attribution). **F3** is a comment improvement. None of these require re-running the matrix; F1/F2/F4 are additive test/observability work, F5 is a fence correction.

---
*Gate executed by the tester (independent verification lane). 6 lanes + 1 gating recon, 0 re-dispatches, 0 timeouts, 0 production-code edits, 0 promotes, 0 writes outside `/tmp` scratch + this document.*
