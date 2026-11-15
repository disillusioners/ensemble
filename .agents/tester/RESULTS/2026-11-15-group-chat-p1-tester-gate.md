# Group-Chat P1 — Independent Tester Gate (FULL/REGRESSION + behavior)

- **Branch**: `feature/group-chat-p1` @ `cc44d8416b6fa429dec2e1667913fd1c95331b84` (verified by worker) · base `30e7cf69c`
- **Worktree**: `/home/nea/ensemble-src-wt-group-chat-p1` (fenced: `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`)
- **Gate tier**: **TIER-2 whole-tree dual-side** — fired MECHANICALLY (ensure.md §Gate Tiers core-seam list): `daemon/manager.py` TOUCHED; other five seam prefixes untouched.
- **VERDICT: PASS-WITH-NOTES** — no blocking failure on any section. Notes = §1 re-scope + budget disclosures, one design-scope finding, P3 pin-precision items, merge-lane environment items. Full basis below.
- **Workers**: gc-p1-changeset 1b21ef78 · gc-p1-behavior 53194507 · gc-p1-migration 0ff12ca5 · gc-p1-edge 76b6ce38 · gc-p1-regression e6ddddca (in flight)
- Reported-by-developer context (NOT gate evidence): reviewer APPROVED; four-file 96/0; drift class 6/6.

## Scope Decision

Leader floor = `tests/unit/`. Mechanical tier check escalated to Tier-2 whole-tree dual-side (core seam `daemon/manager.py` touched) — unit floor is a subset of the whole-tree run. No scope reduction below the leader's floor was applied.

## Changed-file ledger (13 files, 9 commits — reproduced by gc-p1-changeset)

daemon: `M daemon/manager.py` · `M daemon/repositories/event/models.py` · `A daemon/repositories/room/{__init__,boot,models,repository,service}.py` · migration: `A daemon/migrations/versions/20261115_080000_create_chatroom_tables.sql` · tests: `A tests/unit/repositories/test_room_boot_f1_pin.py` (8n) · `A test_room_models_and_migration.py` (13n) · `A test_room_repository.py` (39n) · `M test_source_gc_repository.py` (36n) · docs: `M .agents/shared/planning/group-chat/decisions.md`.

D1 lane-owned set = 4 test files / 96 nodes (absent-at-base by construction; must be 0F/0E at HEAD — re-verification riding the Tier-2 HEAD leg).

## §2 Behavior validation — PASS (6 scenarios / 57 checks; gc-p1-behavior, reproduced on `ensemble_dev_scratch` PG via worktree venv)

1. **Room create** PASS — chatrooms row + owner membership (`role=owner`, `state=active`, cursors 0/0); same-tx both-or-neither PROVEN via forced PK-duplicate failure (typed base `RoomError`, 0 orphaned rows).
2. **Append** PASS — seq strictly 1..7 monotonic (no gaps/dupes); `message_count` denorm bump consistent; interleaved room B seqs independent (per-room isolation); absent-room append → typed `RoomNotFoundError`.
3. **Cursor advance + re-derivation** PASS — predicate `seq > m.last_read_seq AND seq > m.last_delivered_seq` (repository.py:776) ≡ `seq > max(...)`; MAX-wins cell distinguishes from OR/min; monotonic guard rejects backwards; `unread_count` uses `last_read_seq` only.
4. **Read-predicate fail-closed matrix** PASS — active member ALLOW; LEFT member → typed `RoomNotFoundError` (not empty-success); non-member → typed denial; owner-ANCESTRY (parent + grandparent, transitive, `_owner_ancestry_chain` bound 256) ALLOW; descendant-without-membership DENY (fail-closed; matches design "members ∪ owner-ancestry"). NOTE: the "descendant ALLOWED" gloss in the gate dispatch was a tester transcription error — leader brief + design doc both say ancestry (upward); code matches design. Resolved, no defect.
5. **Purge** PASS — 3 tables emptied for target room only; EXACTLY ONE `room_purged` event (re-purge → `RoomNotFoundError`, count stays 1); payload EXACTLY 5 keys (`room_id, owner_instance_id, tree_root_id, messages_purged, members_purged`), content-free; post-purge read → typed not-found.
6. **Boot regen** PASS — `run_room_boot_regen` (boot.py:41; manager hook manager.py:4821-4824 via `to_thread`, best-effort) returns derived counters matching hand-computed expectation; closed rooms included, LEFT members excluded; PURITY PROVEN: all 6 tables row-identical before/after, 0 `message_queue` writes, pure SELECT, no routing/waking.

Behavior notes (design observations, NOT defects, not in brief requirements): `chatrooms.name` has NO UNIQUE constraint (identity = `room_id`; P2 naming lane may want per-tree uniqueness) · cursor advance has no upper clamp (backwards-guard only) · `MemberNameCollisionError` unreachable via public API (no member-add callable in P1 — P2 scope).

## §3 Migration execution proof — PASS (gc-p1-migration; canonical SQLite-runner path, reproduced)

- Inventory from file: 3 tables (`chatrooms`, `chatroom_members`, `room_messages`) + 4 indexes (`idx_chatrooms_tree_root_id`, `idx_chatrooms_state_closed_at`, `idx_chatrooms_state_last_post_at`, `idx_chatroom_members_instance_id`), all `IF NOT EXISTS`.
- Seam: `MigrationRunner(engine).run_pending_migrations()` (daemon/migrations/runner.py:186; executable-SQL logic INLINE in `apply_migration` runner.py:670-673 — **no `_executable_sql` helper exists**; P3's O-1 pin should anchor `apply_migration`, not a helper name).
- Fresh tmp SQLite UP: reproduced the DOCUMENTED pre-existing defect `20260714_000001` (SQLite-invalid `ALTER TABLE ... DROP CONSTRAINT IF EXISTS`) → sanctioned TMP-DB-ONLY ledger pre-mark → **70 migrations applied, `20261115_080000` confirmed**; post-UP objects MATCH inventory 3/3 tables + 4/4 indexes (sqlite_master, exact names); ledger tip row correct (checksum 6ea1ef7b1ca7, 49 ms).
- Idempotent re-run: PASS — runner returns `[]` ("No pending migrations"); object + ledger counts identical (104 tables / 256 indexes / 124 ledger rows before==after). Direct `apply_migration` re-fire on a ledgered migration → `IntegrityError` on `schema_migrations.version` (re-entry prevention, not a defect).
- PG leg: fresh-DB creation SKIPPED (`ensemble_dev_app` lacks CREATEDB — rc 1 `permission denied`, documented). Bonus (reproduced): model-vs-file shape parity field-for-field on PG (10+9+10 columns, 4 named indexes, 3 PKs, 2 UNIQUEs, 2 FKs NO ACTION); raw `psql -f` of the file on PG fails at `AUTOINCREMENT` (statement 7) — **file is SQLite-only BY DESIGN** (header: PG schema comes from `SQLModel.metadata.create_all`).

## §4 Edge cases — 13/14 PASS + 1 design-scope finding (gc-p1-edge, reproduced on `ensemble_dev_scratch`)

- **Limit guard 7/7 PASS** — `list_messages`/`read_messages` `limit` 0/-1/-200 → `ValueError: limit must be positive: N`; guard fires BEFORE any DB work / access predicate (proven via nonexistent-room + non-member orderings); service facade inherits; positive control 1 row.
- **Duplicate member_name (substrate plane) 4 PASS** — exact-tuple collision → `MemberNameCollisionError` via real `uq_chatroom_members_room_member_name` violation fed through the repo classifier, named-constraint FINGERPRINT match demonstrated by recomputation (NOT bare-substring: proper-substring name inserts fine; composite-PK duplicate → generic `RoomError`, never mistyped). Rejoin-after-LEFT (disclosed direct-state construction — P1 has no leave callable) still constraint-refused → typed collision, matches documented intent (suffix registry is P2).
- **FINDING (design-scope, not a defect)**: P1 exposes NO member-add callable (join/naming = P2); the brief's literal "add duplicate member" scenario has no public surface — typed error reachable only via the constraint classifier. B1 observed generic `RoomError` from `create_room` PK-shape collisions (code comment prescribes exactly this fall-through).
- **Concurrent append race 3/3 PASS** — two independent sessions bypassing the asyncio-only service lock (repo is lock-free): barrier race → ONE winner commits, loser gets typed `RoomSeqConflictError` (`uq_room_messages_room_seq`); deterministic variant (uncommitted in-flight seq) → block-then-conflict semantics proven PG-side; SUBSEQUENT append SELF-HEALS seq=MAX+1; final seq set contiguous 1..6, no dupes/gaps. Counter-note: C2's raw-INSERT bypass skipped `_bump_room_counters` (test-construction artifact; pure-repo race C1 produced no drift).
- Latent parity-pin note for P3 O-1: constraint fingerprint classification is driver-dependent — named-constraint fingerprint operative on psycopg3; dotted-pair fingerprint SQLite-only in practice.

## §1 Full regression impact — PASS (Tier-2 whole-tree dual-side, re-scoped to the unit floor; gc-p1-regression; evidence pack `/tmp/gc_p1_tier2_gate_866678/`)

- **Tier trigger verified**: `M daemon/manager.py` in `30e7cf69c..cc44d8416`. Recipe load performed first (whole-tree-base-proven-diff v1.0.1 + failed-digest-equality-gate v1.0.2 + blast-radius-leg §(a) + ensure.md §Gate Tiers).
- **Collections**: HEAD 38,376 collected / 1,271 deselected (rc=0, 82s) · BASE 38,315 (rc=0, 121s) · delta 61 = exactly the lane's 4 test files (+8/+13/+39/+1).
- **Budget (fail-loud)**: shard-0 probe 3,312 nodes → 1,142 nodes/min measured; re-projection checkpoint: projected 100–110 min > 90-min advisory, breach SURVIVED re-projection → STOP → ONE sanctioned re-scope executed (leader's floor): dual-side `tests/unit/` (1,027 files / 26,183 nodes via prepared manifests) + serial `tests/unit/repositories/` leg (338 nodes) + dual-side of already-executed out-of-floor shard-0 (3,187 nodes). **Actual ≈106 min — breach disclosed, not silent. Re-scope cost: 1,686 non-floor collected nodes UNEXECUTED** (job_queue/integration-unmarked/services-top/tools-top/message_queue_redesign/repositories-top/root files) — disclosed for the ledger; Tier-2 whole-tree coverage NOT achieved, unit floor (leader's minimum) ACHIEVED dual-side.
- **D-classification correction (evidence over prompt)**: `test_source_gc_repository.py` is **M not A** → moved to D2 (dual-side comparable; 1 lane-added test inside it treated D1-like). D1 = 3 added files / 60 nodes → **0F/0E at HEAD ✓**. Lane-first serial leg: **96/96 PASS** (reproduces the developer-reported four-file 96/0) + **35/35 PASS at base** for the pre-existing nodes of the M file.
- **Execution**: LLM fence verbatim both legs (per-variable `env -u` family, OPENAI_BASE_URL=127.0.0.1:9 both primary+backup, retry=1); identical command shape; `--junitxml -rf -rs`; every shard under explicit timeout (t=300/600/900/1750); serial buckets honored (scratch-DB DF-4, no_xdist); pre-exclusion registry applied (tests/packs, context_injection_hybrid, test_message_api_cost 12 ids — symmetric). HEAD accounted 29,678/29,678 ✓; BASE 29,617 = 29,678−60 D1−1 lane-added ✓.
- **Two recovered xdist INTERNALERROR deaths** (R_mass_00/02): fence-family (`allowed_dbs={ensemble_dev_test_only, ensemble_test_only}`) refuses the pinned dev DB → `pytest.exit` kills the xdist worker. Recovery: crashers isolated solo (fence-exit confirmed), remainders re-run green, family shard run in its sanctioned unset-`POSTGRES_DB` shape → **165/165 PASS both legs**. Anomaly `44920f6e` filed (major, pre-existing harness trap — NOT lane-caused).
- **Digest (canon `sweep_digest_verdict.sh`)**: BARE-ID HEAD 278 ids (sha 0be04e9408b7…) vs BASE 279 ids (sha 6317c6832af9…) → raw net-new 13 → adjudication ladder: 1 id = explained junit artifact of the 2 disclosed xdist deaths; **12 real head-only ids** (`test_defer_blocked_api.py` ×5, `test_paused_auto_resume_fallback.py` ×5, `test_plane_sync.py` ×2 — NONE in the lane's 13-file diff) **PASS 3×3 isolated serial at HEAD and at BASE** → shard-composition load-order flakes, not regressions. **Post-adjudication NET-NEW = EMPTY.** GREP-FULL form sparse (22/23) — pytest 9.0.2 can print "N failed" with zero FAILED summary lines; junitxml was the canonical id source (anomaly `89bf5f37`, minor).
- **Repo integrity**: base worktree removed + pruned; `install-audit.jsonl` sha-identical pre/post; HEAD unmoved `cc44d8416`; no commits; prod never contacted.

## Pre-existing-red ledger (base-side reds — NOT gate failures)

279 base-side reds: **265 red on BOTH sides** (pre-existing by the ladder; includes 3 `test_inner_soul_compaction` instance-identity reds; ERROR parity HEAD E=22 == BASE E=22) + **14 base-only** (deterministic base-reds + base load-flakes — favorable direction, ledger only). Quarantine attribution vs the 23-item list: **51 attributed** (26 `tests/unit/tools` dir row, 17 `test_builtin_mcp_servers.py`, 3 `TestConventionCompliance`, 5 exact-id) · **228 not in the quarantine ledger** — full list preserved at `/tmp/gc_p1_tier2_gate_866678/base_red_final.txt` for the ledger owners (quarantine-candidates for future maintenance, incl. the known ~17 `tests/unit/services/` family).

## ensure.md Core validation (scoped to this change set) — PASS (gc-p1-ensure; fence-proven)

- **R1 Critical — `concurrency_atomic_unit_test` pack: PASS (quarantine-adjudicated)** — raw exit 1 → 5F/94P/74S (173 nodes, 114.3s, under both the 300s command cap and the script's internal 280s cap). All 5 failures = `tests/test_instance_metadata_atomic.py` ×5, node-for-node match to QUARANTINE row 152 (2026-11-10, deterministic pre-existing, DetachedInstanceError, base-proven at `f49006b92`); `git diff 30e7cf69c..HEAD -- <file>` = 0 bytes. **Zero non-quarantined failures.** Env fence: per-variable scrub + explicit dev values, pre-flight `FENCE OK: resolved DB != ensemble_prod` printed before run.
- **R2 Critical — dev.sh `--timeout-graceful-shutdown 10`: PASS** — present at dev.sh:159 (line drifted from historically-cited anchors; flag present).
- **R3 Important — await call-sites: PASS** — 9/9 call sites awaited across `_get_system_prompt_tokens` (3), `_compute_context_usage` (1), `get_queue_stats` (5); strict negative sweep empty (remaining hits = comments/docstrings).
- No ensure.md contradictions found → no Improvement Notices. Ensure.md untouched (user-owned).
- Scope observation (report-only): the concurrency pack's fixed candidate list predates `daemon/repositories/room/` — the per-room asyncio lock's direct coverage rides the lane's own `tests/unit/repositories/test_room_*.py` (96/96 ✅); consider a room-scoped pack row at P2+.

## Findings (final)

- **F1 — design-scope gap, not a defect (§4/B1)**: P1 exposes NO member-add/leave callable (join/naming/suffix registry = P2). The brief's literal "add duplicate member" scenario has no public surface; `MemberNameCollisionError` is reachable only via the constraint classifier (substrate-plane semantics PROVEN: exact-tuple → typed error with named-constraint fingerprint; substring ≠ collision; composite-PK → generic `RoomError`).
- **F2 — P3 O-1 pin-precision (§3)**: O-1's committed pin should anchor `apply_migration` (runner.py:670-673) — no `_executable_sql` helper exists; and should pin the driver-dependence of constraint-fingerprint classification (named-constraint operative on psycopg3; dotted-pair SQLite-only in practice).
- **F3 — design observations (§2, non-blocking)**: `chatrooms.name` carries NO UNIQUE constraint (identity = `room_id`; P2 naming may want per-tree uniqueness) · cursor advance has no upper clamp (backwards-guard only).
- **F4 — gate coverage (§1)**: Tier-2 whole-tree coverage NOT achieved — re-scoped to the leader's floor (dual-side `tests/unit/` + repositories serial + out-of-floor shard-0 dual); **1,686 non-floor collected nodes unexecuted**; actual ≈106 min vs 90-min advisory (fail-loud, one sanctioned re-scope).
- **F5 — merge-lane environment**: worktree carries 4 modified-UNCOMMITTED planning docs (pre-existing, untouched) · this lane has NO PACKS.md registration block in either copy (this gate's registration added post-gate by tester, see PACKS.md) · 228 base-reds not in the quarantine ledger (list at `/tmp/gc_p1_tier2_gate_866678/base_red_final.txt`, candidates for maintenance).
- Pre-existing harness traps re-confirmed (not lane-caused): fence-family × xdist worker death (anomaly `44920f6e`) · pytest 9.0.2 zero-FAILED-lines quirk (anomaly `89bf5f37`) · `20260714_000001` fresh-SQLite abort (documented, sanctioned tmp-ledger workaround) · scratch-DB privilege split on `ensemble_dev_scratch`.

## Anomalies / environment notes

- Worktree dirt: 4 modified-UNCOMMITTED planning docs (`.agents/shared/planning/group-chat/{decisions,architecture-recommendation,emperor-open-questions,plan-overview}.md`) — pre-existed the gate, code/tests clean at HEAD `cc44d8416`; merge lane must resolve (disclosed, untouched by gate).
- PACKS.md registration debt: NO group-chat P1 pack block in worktree or main copy (worktree copy = superset with rogue-p2 block; drift is one-directional).
- Scratch-DB privilege split on `ensemble_dev_scratch`: `ensemble_dev_app` owns room tables but not `instances`/`event`/`message_queue` → behavior worker used a private shadow schema (`gcval_26841`, dropped after); superuser-scratch provisioning gap noted (POK e2s0-01).
- Both workers hit `/bin/sh` (dash) harness quirks (`PIPESTATUS`/process-substitution unsupported) — worked around, results unaffected.
- Python 3.14 langchain_core deprecation warning on import — ambient.

## Disposition

UNCOMMITTED landing (rogue-p2 precedent): this RESULTS file is written into the worktree uncommitted; HEAD remains `cc44d8416`. Commit/landing is the leader/merge lane's call.
