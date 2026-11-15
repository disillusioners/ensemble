# Group-Chat P2 — Independent Tester Gate (FULL/REGRESSION + behavior)

- **Lane**: `feature/group-chat-p2` @ **`12f41b0f0`** (31 commits, 16 files, +4007/−47) · base **`e3787d4b8`** (P1 merge)
- **Worktree**: `/home/nea/ensemble-src-wt-group-chat-p2` (fenced `.env`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`, Python 3.14.7 / pytest 9.0.2)
- **Gate tier**: **TIER-2 mechanically** — `daemon/manager.py` touched (ensure.md §Gate Tiers core-seam list); other five seam prefixes untouched (verified from `git diff --name-status`). Re-scoped to the leader's floor `tests/unit/` per the P1 precedent.
- **VERDICT: PASS-WITH-NOTES** — no net-new regression, all behavior sections green. Notes = 6 claim/ledger/coverage disclosures (§ Notes). Full basis below.
- **Workers**: gc-p2-changeset `3b295259` · gc-p2-naming `b178806b` · gc-p2-admission `27fa57bb` · gc-p2-spawntx `52581b4d` · gc-p2-serial-core `15fa8960` · gc-p2-reg-head `15c69b8c` · gc-p2-reg-base `77cee218` · gc-p2-digest `9286d98b` · gc-p2-fencegap `a8f13f99` (9 dispatches, 0 re-dispatches, 0 worker failures)
- HEAD `12f41b0f0` unchanged throughout; porcelain byte-identical before/after every leg (` M .agents/tidier/notes.md` only, pre-existing). No commits, no checkouts, no production/test edits — validate-only mandate honored.

## Scope Decision

Full-tier request → mechanical Tier-2 check fired (`daemon/manager.py`). Re-scoped to the leader's floor (dual-side `tests/unit` + serial `tests/unit/repositories/` both sides), which on this lane reached **complete floor coverage**: HEAD 26,601/26,601 nodes executed, BASE 26,521/26,521 executed — **zero unexecuted nodes**, including the 4 fence-family files (165 nodes) that the first pass had to exclude (closed by a dedicated sanctioned-shape dual-side run, §2.6). Regression budget: HEAD leg 22 min compute (775 s + 547 s; one 74 s aborted probe), BASE leg 25.5 min (672 s + 626 s + 231 s) — **under the 90-min advisory, no breach, no timeout**. Full whole-tree (non-floor) coverage NOT attempted, matching the P1 disposition.

## 1. Changeset & collections reconciliation — PASS (exact)

- Diff: **31 commits / 16 files / +4007−47**. daemon ×9 (`manager.py`, `config.py`, `repositories/room/{__init__,membership,repository,service}.py`, `services/{instance_lifecycle,room_naming}.py`, `tools/instance.py`); tests ×6 (`helpers/room_harness.py`, `repositories/test_room_boot_f1_pin.py` M, `repositories/test_room_membership.py` A, `services/test_lifecycle_room_join.py` A, `services/test_room_naming.py` A, `tools/test_spawn_room_join.py` A); docs ×1.
- Collections: HEAD **26,601** (rc 0) vs BASE **26,521** (rc 0, `daemon.__file__` proven inside the extraction) → **head-only 80 / base-only 0 / unexplained 0**. Per-file delta: 37 + 4 + 17 + 22 = 80, exactly the 4 new P2 test files. **Reconciles exactly to the lane's file set.**
- Base-debt re-proof (leader-named): `git diff e3787d4b8..HEAD -- tests/manager/test_skill_service_init.py tests/test_instance_title.py` = EMPTY; executed both legs → **same 5 failing nodes**, signatures identical after address normalization (`MigrationError` ×3, `DetachedInstanceError` ×2).

## 2. Full regression — PASS, NET-NEW EMPTY

### 2.1 Dual-side execution (identical command shape, per-variable fence, t=1750/shard, `-n 8`, junitxml canonical)

| Leg | Scope | rc | Counts | Wall |
|---|---|---|---|---|
| HEAD shard A | 507 files / 12,969 nodes | 1 | 135F / 12,772P / 62S (xml 132F+3E) | 775 s |
| HEAD shard B | 519 / 13,092 | 1 | 73F/21E / 12,974P / 23S / 1xF (xml 72F+22E) | 547 s |
| HEAD repositories (serial) | 375 | **0** | **375P** | 285 s |
| BASE shard A | 504 / 12,926 | 1 | 284F/2E / 12,575P / 65S | 672 s |
| BASE shard B | 518 / 13,090 | 1 | 123F/43E / 12,899P / 24S / 1xF | 626 s |
| BASE repositories (serial) | 338 | 1 | 4F / 334P | 231 s |
| Fence-family, both legs (§2.6) | 165 ×2 | **0 / 0** | **165P / 165P** | 37 s / 34 s |

Accounting closure exact on every shard (HEAD A 12,969+144 excluded = 13,113; HEAD B 13,092+21 = 13,113; BASE A 12,926+144 = 13,070; BASE B 13,090+21+2 = 13,113). No rc=124 anywhere; 4 xdist worker deaths (timeout=30-thread shaped, load-shaped) error-marked in-flight tests only — **0 tests lost**.

### 2.2 Digest equality (BARE-ID, `failed-digest-equality-gate` v1.0.2)

| Set | Count | sha256 (prefix) |
|---|---|---|
| head_failed | **229** | `a9963ce58c7c9118…` |
| base_failed | **456** | `eec3ac72dd5c7239…` |
| both | **212** | `bdd3b4292ab2b244…` |
| head_only | **17** | `c29ba56150397d81…` |
| base_only | **244** | `4150f670c5555a70…` |

Arithmetic exact: 212+17=229; 212+244=456.

### 2.3 head_only adjudication ladder (17 ids → 16 flaky + 1 pre-existing; 0 net-new)

- **16 FLAKY (shard-composition, `-n 8` only)**: `test_paused_auto_resume_fallback` ×7, `test_symptom_repair_engine` ×1, `test_symptom_repair_engine_failover_e2e` ×4, `test_turn_end_injection_drain` ×4 → **P/P/P isolated at HEAD**; full-file serial corroboration 66P/0F.
- **1 PRE-EXISTING (standing stale pin)**: `tests/unit/test_nest_report_rest_route.py::TestAM1TheRouteSetPin::test_the_metrics_router_source_is_untouched_by_this_lane` — F/F/F at HEAD, **fence-invariant** (fails under scrubbed *and* ambient-auth variants). Mechanism: shells `git diff --name-only 22c144000 -- daemon/routers/metrics.py` and asserts empty (test :405-416). `git log 22c144000..e3787d4b8 -- daemon/routers/metrics.py` = **non-empty** (`a67b74066`, pre-lane drift), `git diff e3787d4b8..HEAD -- …metrics.py` = **EMPTY** (zero lane reach). Base-side pass was **vacuous** (gitless extraction → empty stdout). Ledger row added (QUARANTINE.md).
- **Zero ids in lane-added files; zero deterministic head-only id with lane production-code reach** (lane diff = room surface only: config/manager/room repos/service/naming/lifecycle/instance tools + 5 new tests).

### 2.4 base_only (244) — favorable direction, mechanism proven

- **193 auth/oauth/bearer/redirect-shaped** (`test_auth_*` 158, `test_oauth_*` 66, `test_bearer_*` 8, mcps-redirect 5): the BASE leg ran the plain 10-var fence while the HEAD leg scrubbed `ENSEMBLE_AUTH_*`; ambient `AuthConfig(enabled=True)` leaks into `_parse({})` and turns router reads into 302s (`test_hide_kb_instances`: `assert 302 == 200`).
- Remaining 51 = same auth-on redirect class + extraction artifacts. Direction argument: base-side auth noise can only create **base_only** entries; scrub-induced **head_only** reds are excluded empirically (all 16 flaky ids pass under the HEAD's scrubbed fence; the 1 deterministic red fails under both variants).

### 2.5 Repositories: HEAD 375P/0F vs BASE 4F → ENVIRONMENT-ATTRIBUTED (gitless extraction)

4 base reds are `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange` (`post_merge_span`, decision/group_chat/memoir pins). The class shells `git rev-list --first-parent --merges -1 HEAD` (:766-771) and fails at the **first** assertion (`assert merge, "no first-parent merge found"` :840/:892/:915/:940) — never reaching a drift comparison; the extraction has no `.git`. `git log e3787d4b8..HEAD -- <file>` = EMPTY, and the file's three newest touchers are all ancestors of base → file byte-frozen across the lane. **Not "fixed at HEAD"** (an earlier worker phrase, corrected): pure extraction artifact.

### 2.6 Coverage closure — fence family 165/165 on BOTH legs

4 files hard-abort their session fence on the pinned dev DB (`allowed_dbs={ensemble_dev_test_only, ensemble_test_only}`; unset/`""`/`ensemble_prod` → self-pins a canary name that is never dialed — all engines in these files are SQLite). First pass: 165 nodes **executed nowhere** (HEAD excluded, BASE isolates aborted). Closed by a dedicated dual-side run in the sanctioned **unset-`POSTGRES_DB`** shape behind a contact-free DSN probe (`SAFE`: database `ensemble`, host `localhost` — never prod): **HEAD 165P / BASE 165P, both rc 0, empty digests identical (`e3b0c442…`), 0 divergence, residual gap 0.**

### 2.7 Pre-existing-red ledger (HEAD 229)

- **161 attributed** to QUARANTINE rows (unit-routers block ×54, whole-tree unregistered population ×40, builtin_mcp ×17, job_queue_proxy_phase1 ×7, claim_lane ×6, hide_kb ×6, paused-M2 ×5, context7 ×4, sync-runner ×3, inner_soul_compaction ×3, plus exact/family singles).
- **68 unledgered** → `/tmp/gc_p2_gate/digest/head_unledgered.txt` — **56 both-side** (stable unregistered families: governor_recursion_guard 10, bash_spawn_reaper 4, child_report_next_turn 3, anomaly_report 5, compaction_escape 3, nest_report 2, live_views 2, dbpool 2, + 25 singles) and **12 head-only** (all adjudicated: 11 shard-composition flakes + the 1 stale metrics pin). Zero net-new; this is **ledger documentation debt**, same class as P1's 228.

## 3. Behavior validation of the P2 surface — ALL PASS

### 3.1 Naming cascade — PASS 70/70 checks ×2 suites, outcomes identical (`daemon/services/room_naming.py`)

`ROOM_NAME_MODEL_TIMEOUT_S=3.0` (:71, `wait_for` :179-188); tiers `parent_provided/quick_model/agent_fallback` + tier-4 registry.
- **T1** parent name verbatim-sanitized (`"Log Analyzer"`→`log-analyzer`), quick seam ctor calls = **0**.
- **T2 success** canned name, single attempt, prompt carries purpose only.
- **T2 TIMEOUT** measured **3.097 s / 3.105 s** → degrades, no exception escapes.
- **T2 CONSTRUCTION FAILURE** (RuntimeError / ValueError / ImportError) → all three degrade to tier-3, ctor calls = 1 each, **no retry loop** (the newest fix holds).
- **T3** `capture` ×3 identical, 19–37 ms; terminal fallback `member` for an all-symbol agent id.
- **T4** `capture-1→-2→-3`, per-room scoping, smallest-free (`{x,x-2}→x-1`), suffix note present iff final ≠ base.
- Final name present in the spawn result at **every** tier: `room join: room=<id> member_name='<final>' (naming: tier …)` (formatter `daemon/tools/instance.py:3171-3189`, readback :3892-3894).

### 3.2 Admission matrix + fail-closed + leave/restore + suffix registry — PASS 26/26 (`daemon/repositories/room/membership.py`)

- 5 conjuncts, **distinct typed subclasses** of `RoomJoinError` (:133): UnknownRoom (:262) / RoomState (:266) / CrossTree (:271) / MemberCap (:286) / RoomCap (:292-301); caps `16`/`8` (:122-123). `RoomLeaveOwnerError` (:176) deliberately outside the family.
- **A-P2-1 excludes-target proven mechanically**: predicate `room_id != target`; 8 active rooms → allow (sibling_count 7); 9 → typed refusal.
- Fail-closed: `join_room=""` / `"   "` → typed malformed refusal, 0 rows; `None` → legacy inert (no room tables); blank/whitespace/NULL `member_name` with join → tier-2 engaged (quick lane asserted called).
- Leave → LEFT row (not deleted), excluded from cap (15 active + 1 LEFT → 16th active admitted); owner leave refused; restore_eligibility two-conjunct (room ∈ {active,closed} ∧ member active) proven by independent negatives.
- Suffix registry: sequential smallest-free; **LEFT rows keep their name** (departed `capturer-4` blocks reuse — matches the pinned semantics); **concurrent 6-racer ×3 runs → one bare + `-1..-5`, no lost names, no untyped exception**.

### 3.3 Spawn transaction — PASS 72/72 (real `_spawn_instance_db_sync` seam, `daemon/services/instance_lifecycle.py:5596`)

- One transaction: membership insert (:5667) + `join_room` JSONB (:5666) + `member_name` JSONB (:5678) + instance/hierarchy INSERT (:5697/:5699) — happy path asserted row-for-row and via the production read path.
- Forced collision (uncommitted ghost row in a second connection → UNIQUE backstop): `MemberNameCollisionError` (`MRO = [MemberNameCollisionError, RoomError, RoomJoinError, …]`) ×3 stable; **zero orphans** (instance 0, membership 0, hierarchy 0, no JSONB key); handler order load-bearing (`instance.py:4035 < :4044` — reversed order catches the generic).
- **Non-room `IntegrityError` propagates raw** (`instances_pkey` duplicate → raw sqlalchemy `IntegrityError`); fingerprint gate rejects 5 lookalikes (same-prefix `…member_seq`, dotted lookalikes, bare `member_name` substring), PG catalog confirms no real constraint embeds the fingerprint.
- Multi-room: second join via `RoomRepository.join_room` adds a membership row without clobbering the spawn-time JSONB keys (shape reported: single-room spawn metadata + multi-room membership rows by design).

## 4. ensure.md Core (scoped) — PASS (quarantine-adjudicated)

- **R1 Critical `concurrency_atomic_unit_test`**: raw exit 1 → **5F/94P/74S** (236.9 s); the 5 = **QUARANTINE row 152 node-for-node** (`tests/test_instance_metadata_atomic.py`, `DetachedInstanceError`, base-proven `f49006b92`) → **zero non-quarantined failures**.
- **R2 Critical `dev.sh --timeout-graceful-shutdown 10`**: present at `dev.sh:159`.
- **R3 Important await call-sites**: `_get_system_prompt_tokens` 3/3, `_compute_context_usage` 1/1, `get_queue_stats` 5/5 awaited; strict negative sweep = **0 unawaited**.
- No ensure.md contradictions → no Improvement Notices; ensure.md untouched.

## 5. Concurrency pack / F4 (P1 carry-over)

- Registered pack has **zero room candidates** (`grep -nE 'room|Room' test/packs/concurrency_atomic_unit_test.sh` → no matches) — room-scoped concurrency coverage genuinely absent.
- Ad-hoc: **20 room race/lock/suffix/collision nodes ×3 runs = 20/20/20** (~25 s/run, no flake).
- **Permanent row warranted** (proposed text captured in PACKS.md; NOT applied to the registered script — landing is the lane's call).

## 6. Findings

- **F1 — claim precision**: developer-reported "136/0 on the 6-file bracket" is not reproducible. The lane diff adds **5 test files + 1 helper = 88 collected nodes**; measured **88/88/88 PASS** across 3 runs (`test_lifecycle_room_join.py` also 4/4 ×3 solo). 136 appears to include P1-era room files that pre-exist at base. Not a failure — a number to correct in the lane record.
- **F2 — ledger debt**: 68 of the 229 HEAD reds are unledgered (56 both-side stable families + 12 adjudicated head-only). List preserved for the ledger owner. Same class as P1's 228.
- **F3 — standing stale pin**: `tests/unit/test_nest_report_rest_route.py:405-416` compares `git diff 22c144000 -- daemon/routers/metrics.py` to empty; pre-lane drift `a67b74066` reddens it on every branch from `e3787d4b8`. Deterministic, fence-invariant, lane-innocent. QUARANTINE row added (documentary); durable fix = re-pin to a post-drift commit.
- **F4 — pack coverage gap**: no room files in the concurrency pack (proposal provided; verify the registered script's `RESULT:`-echo gap separately — it exits 1 bare on pytest failure, so classify by exit code + junitxml).
- **F5 — harness knowledge banked**: (a) fence-family × xdist worker death → sanctioned **unset-`POSTGRES_DB`** shape closes the gap dual-side (165/165); (b) a pool `SET search_path` connect-listener is silently reverted by `reset_on_return="rollback"` → schema-scoping via `connect_args options=-csearch_path=…`; (c) the BASE leg MUST scrub the `ENSEMBLE_AUTH_*` family too, else ~193 auth-shaped base-only reds flood the digest (fence asymmetry, favorable direction but noisy); (d) git-subprocess pins cannot run in a gitless extraction — classify ENVIRONMENT-ATTRIBUTED, never "fixed at HEAD".
- **F6 — environment**: worktree carries one pre-existing modified file (`.agents/tidier/notes.md`); the "4 uncommitted planning docs" from P1 are no longer present. Ambient shell carries `ENSEMBLE_SELF_ENV=live`, live `OPENAI_BASE_URL`, `ENSEMBLE_AUTH_*`, and an `ENSEMBLE_UPGRADE_SCRIPTS_DIR` — the per-variable fence is load-bearing on every leg.

## 7. Disposition

RESULTS + PACKS.md registration + one QUARANTINE documentary row are written into the worktree **uncommitted** (rogue-p2/P1 precedent); HEAD remains `12f41b0f0`. Commit/landing is the leader/merge lane's call. The registered concurrency pack and any production/test code were NOT modified.

**Gate verdict: PASS-WITH-NOTES — NET-NEW EMPTY, behavior 168/168 checks green (naming 70×2, admission 26, spawn-TX 72), floor coverage 100 % dual-side (0 unexecuted nodes), ensure.md Core satisfied.**
