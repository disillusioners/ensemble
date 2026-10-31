# 2026-10-31 — missed-finalize / zombie-ACTIVE close: FUNCTIONAL GATE (plan phase 4, board wi-78967dba)

**Branch:** `fix/missed-finalize-zombie-active` · **Commissioned anchor:** `9af20578a3a16060b421af0c2340bf2ec7cb8d0d` · **Post-gate tip:** `defa3d8b53d7cf924420e05392a48e1281839633` (sanctioned pin-fold commit, PD-4.2 fix-and-rerun) · **TRUE base:** `0d6489879` (docs-only child of code base `22fe83317`)
**Gate window:** 2026-10-31T04:05Z → ~05:45Z · **Workers:** 16 (recon + 14 evidence lanes + classifier)
**Interpreter:** `/home/nea/ensemble-src/.venv/bin/python` (cd-worktree) · **Env contract:** `ENSEMBLE_SELF_ENV=dev` + per-variable `env -u PORT -u ENSEMBLE_UPGRADE_LIVE -u POSTGRES_{DB,USER,PASSWORD,HOST,PORT}` on every invocation; dual-layer timeout (`timeout 300` outer / `--timeout=120` inner); no xdist; PG SERIAL on scratch DBs only.

## VERDICT: 🟢 **PASS-WITH-NOTES** (net-new = ∅ after the sanctioned PD-4.2 fix-and-rerun cycle; all 7 gates green; disclosures below)

### Five most important evidence lines
1. `NET-NEW: base 330 → HEAD 301 (pre-fold) → 298 (post-fold); 3 genuinely-new = stale adjacent pins on intended contract widening, folded @ defa3d8b5, 3× green re-proven, +3/−3 fingerprint (40cb823b… → ceb69376…)`
2. `G1b: 9/9 packs 60/60 GREEN — canonical notify asserted on actual notify_watchers await-args; kill-switch-OFF rerun = BASE gap held in-pack (3 cycles stay active/locked/tokenless)`
3. `G5: 8/8 mutation legs RED→GREEN sha-proven (incl. T3.12-a1v3 with BYTE-EXACT planted-sha match f7034c499fd7c0a8; both PG legs live)`
4. `G3/G4 real-PG: Pack I 3/3 (ADV4 drift-guard collection assertion PASS, _ACTIVE_JOB_IDS_SUBQUERY spells 'queued'+'active') + wedge-capture 3/3 with real n_live_tup=2/1 read from pg_stat_user_tables on the ANALYZEd scratch schema (db_capture.py:374-395 LEFT JOIN fix)`
5. `G7b FENCE CLEAN: ensemble-main epoch 2026-10-30T19:45:44Z, NRestarts=0 across the whole window; current→releases/v0.27.15; 0 scratch DBs / 0 scratch worktrees / 0 strays; lane tree porcelain-empty @ defa3d8b5`

---

## Gate 1 — Original symptom, BOTH directions: 🟢 PASS
**(a) BASE side (G1a, worker a022b8a3):** committed phase-0 gap artifacts verified substantively on BOTH engines —
`gap-proof-base-worktree-{sqlite,pg}.json`: 3 verdict variants (completed/failed/cancelled) × 3 drift cycles each = 9 entries/engine; every cycle `admission_state=='active'`, `terminal_reason==null`, `lock_rows==1`, `reconciled==0`; E1 self-hold verdict `live=true` captured (reason verbatim-matches `mission_live_guard.py:1110-1116`); per-cycle patterns only `orphan_active_skipped_mission_live{,_f1}` (P1 = PENDING-class, structurally inapplicable ⇒ zero P1 warnings); identical `GAP-PROVEN` verdict line both engines.
**Anomalies disclosed (doc-level, not blocking):**
- **A1 — no self-attested base sha inside the JSONs** (top-level keys: backend/cycles/e1_self_hold/verdict). Base identity corroborated three ways: evidence doc header (`evidence-phase0-phase3.md:5` — plan-set base `0d6489879`), read-only ancestry (`22fe83317 → 0d6489879`, docs-only child, code-identical), and behavioral impossibility (the captured `active`+`reconciled=0`-across-3-cycles state cannot be produced at HEAD: identical fixture closes in ONE cycle — T1.7; load-bearing modules +529 lines base→HEAD).
- **A2 — evidence-md prose mis-attribution:** md says the E1 hold is via "childless-root fallback"; the JSON's verbatim reason matches the LIVE-LEG branch (`mission_live_guard.py:1110-1116`). `live=true` itself is verbatim-correct; prose-only defect, routed to the lane owner.
**(b) HEAD side (G1b, worker 7302d1e0):** all 9 packs GREEN, 60/60 (core 6, e1_discount 4, negatives 6, races 4, failclosed 3, knobs 9, tokens_watchers 7, static 9, amendments 12; ~54s):
- **(a)** 3-verdict close with provenance tokens `zombie_active_{completed,failed,cancelled}` + `admission_state=='done'` + `lock_rows==0` + `finalized==1` (core :73/:95/:113; token greppability static :132).
- **(b)** canonical notify status asserted against actual `notify_watchers` await-args (`args[1]=='failed'` etc.; NEVER raw token, NEVER f2-literal) — tokens_watchers :124/:139/:146.
- **(c)** lock released LAST in the same txn — `done ∧ lock-present` unobservable from a SECOND connection (races :180); S4 NULL-instance closes vacuously, lock DELETE safely skipped (static :172).
- **(d)** audit/counter lines: A1 counter block (examined>0 gate, no `and reconciled` conjunct — the broadened a1v3 form), A3 `notify=<outcome>` in close line + detail record, PP1 zero-watcher correlation (`PP1` + `notify=notified:0`).
- **(e) T1.7 smoke shape (phase1-plan.md:39), all five legs:** closes within one cycle of eligibility (inclusive age boundary core :131); E1 self-hold flips `live=False` under `discount_instance_id` proven BOTH directions (e1_discount :165 + companions :119/:140); lock gone; token present; **kill-switch-OFF rerun ⇒ NO close** (`core::test_base_shape_holds_by_construction_when_disabled` :214 — 3 cycles stay active/locked/tokenless, examined==0; knobs OFF = zero candidate queries :74).
- **T4.4 concordance:** pack evidence ran under the SAME run root/fences as the whole-tree corpus (same worktree, same env contract).

## Gate 2 — Whole-tree net-new vs TRUE base: 🟢 PASS (after sanctioned fix-and-rerun; the cycle is disclosed, never silently excised)
**Corpus (Phase-0 command set, same commands both sides; over-cap dirs chunked by `--collect-only -q` identities per house practice):**
| Shard | Scope | Ids executed | Coverage proof | Reds captured |
|---|---|---|---|---|
| W-A | tests/integration (5→7 leaf chunks; 2× rc124 splits recovered; 1 outer-cap resume) | 1155 | 1155==1155 exact | 44 (40F+4E) |
| W-B | tests/unit top-level (7→11 leaf chunks; 4× splits; 1 script-cap re-run from scratch) | 10433 | 10433==10433 exact | 89 (68F+21E) |
| W-C | unit routers/tools/services/repositories/plugin_subsystem | 9303 | 827/3240/4383 exact per dir | 101 (99F+2E) |
| W-D | 12 unit light subdirs | 665 | byte-exact vs base counts | 0 |
| W-E | 13 mid dirs + job_queue 2 chunks + 11 rc5 dirs | 4390 | 1148+1148==2296 exact; rc5 ×11 confirmed | 67 (67F) |
| **Total** | | **25,946** | | **301 pre-fold** |

**Diff (classifier 7bc0cef7, PD-4.2/PD-4.3 ladder):** base 330 (raw sha `58cb8201385bf50435e5d19a42b82ba843d39a58cb52bef05b08c56456858027` == recorded) vs HEAD union 301 (sha `40cb823b8a8515684aa8377e3ab99ead08269a5dd61b6cda5d4bfa0440828651`): **pre-existing 293 / net-new candidates 8 / fixed 37** (293+37==330 ✓, 293+8==301 ✓).
- **Net-new ladder:** 8 candidates × 3 isolated sequential runs = 24/24 deterministic (ZERO order/load flakes). Base-check in scratch worktree @ `0d6489879` (created, used, `--force` removed, `git worktree list` re-verified clean):
  - **3 GENUINELY NEW** (green-at-base, 3× red-at-HEAD): `test_work_status_terminal_reason_variants.py::…test_completed_has_no_aliases_beyond_itself` (work_status now carries the `zombie_active_completed` alias); `test_reconcile_empty_cycle_info_log.py::…test_empty_cycle_completes_with_zero_reconciled` + `…logged_at_info_both_sites` (heartbeat payload gained the `zombie_close` counters block, `job_recovery_service.py:1863/:2092`). All three = **stale adjacent pins vs the lane's intended contract** — the new behavior is positively pinned GREEN by the lane's own suite (reverse-index auto-derivation requires `zombie_active_*`; `ZOMBIE_CLOSE_COUNTER_KEYS` 7-tuple; A1 block) ⇒ adjudicated ZERO behavior regressions.
  - **5 PRE-EXISTING-UNLISTED** (red at base too, under isolation): `empty_guard sqlite roundtrip`, `message_metadata_liveness` ×2, `wc_wake agent_tool/job_inject` — all one family: the known mock-shadowing `ImportError` (langgraph.checkpoint.base `Checkpoint`/`WRITES_IDX_MAP`), order/process-grouping-sensitive; absent from the base list because base ran whole-dir single processes. NOT lane-caused (reproduced at base), disclosed.
- **FIXED accounting (37/37 isolated GREEN at HEAD, zero list-mismatch):** 23 INTENTIONAL (test files importing lane-touched modules — `daemon/config.py` importers ×22 + the p4_5 defer-seam pin ×1; attribution mechanical per rule (a), causal strength varies — disclosed), 14 INCIDENTAL (outside the lane diff; named cause: base whole-dir sequential capture vs HEAD chunked shards ⇒ load/order-sensitive-at-base).
- **Class-flip disclosure:** `wc_wake_pure_hang::test_http_post_messages_wakes_parked_wc_parent` base F → HEAD E (id-match ⇒ red-both-sides ⇒ pre-existing; recorded in class_flips.tsv).
**Fix-and-rerun (worker 4af2d576, PD-4.2 / v0.27.13-G3.5 pattern) — commit `defa3d8b5` (test-only, targeted adds):**
- Pin 1 folded to exact-set `{"completed","zombie_active_completed"}` and RENAMED `test_completed_carries_only_the_zombie_provenance_alias` (old name contradicted the folded body — rename disclosed; sibling family pins are membership-semantics by design, untouched).
- Pins 2+3 folded to exact-dict `{"reconciled":0,"details":[],"zombie_close":dict.fromkeys(ZOMBIE_CLOSE_COUNTER_KEYS,0)}` (7 keys, all zero on empty cycle; constant-sourced, no third hardcoded tuple; log-text pin kept verbatim — LOG TEXT is byte-unchanged, only the return payload gained the block).
- Re-verify: 2 files whole 8/8 · 3 ids ×3 isolated 9/9 · 9 lane packs 60/60 (fold contradicts no lane pin) · fingerprint 301→298 (post-fold sha `ceb693763efea06eac0a93fe7c936fe5d5864052979db0404b9f194909d5621b`). **net_new = ∅ post-fold.**

## Gate 3 — Real-PG Pack I (first true PG execution): 🟢 PASS
Worker 4f10cb1a; scratch `ensemble_test_mfz_g3` (createdb as role ensemble, `rolcreatedb=t`), serial, `--override-ini="addopts=" -m postgres --timeout=120` under `timeout 300`: **3/3 PASSED** (~4.5s) — `test_close_survives_trigger_suite_releases_lock_same_txn`, `test_candidate_query_populated_on_real_pg`, `test_seeded_end_to_end_three_verdicts`. ADV4 admission-pair drift-guard: collection-time assertion PASS; production `_ACTIVE_JOB_IDS_SUBQUERY` spells both `'queued'` and `'active'`. Teardown: `dropdb --if-exists` rc=0 + residual SELECT 0 rows. Tree clean pre/post.

## Gate 4 — Wedge-capture PG re-pointed (R-10): 🟢 PASS
Worker 2df0d317; scratch `ensemble_test_mfz_g4`: **3/3 PASSED** (4.37s). **POPULATED proof:** census SQL at `db_capture.py:374-395` does `LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid` reading `s.n_live_tup::bigint`; direct query on the pack-populated + ANALYZEd scratch schema returns `checkpoints reltuples=2 n_live_tup=2`, `checkpoint_blobs 1/1` (16384 B each); test (ii) requires populated census (no `"status"` key), per-table `{reltuples,n_live_tup,total_bytes}`, `reltuples>=1`, NULL-safe `n_live_tup>=0`, `total_bytes>=8192`, summary keys — all held. Old-form plant (`c.n_live_tup`) flips it to `failed:UndefinedColumn` (mutation leg T3.9, gate 5). Teardown verified 0 rows.

## Gate 5 — Mutation matrix sample: 🟢 PASS 8/8 RED-proven (mandate was ≥6)
Worker 25356fd4 in scratch worktree `/home/nea/ensemble-src-wt-mfz-mut` @ 9af20578a (added, used, removed; lane tree untouched). Legs: **W2-5th-status** (cardinality RuntimeError guard fires, FAIL(2)), **T3.12-a1v3** (FAIL(1) exact id; **planted sha BYTE-EXACT matrix match** `f7034c499fd7c0a8`), **T3.12-a3v2** (FAIL(1) exact id), T3.7-w1-raw-token (FAIL(3), canonical-notify assert `'zombie_active_cancelled' == 'cancelled'`), T3.5-fail-open (FAIL(2)), T3.4-rowcount (FAIL(3), race_lost_noop observed 0), T3.9-R10-PG (FAIL(1) `UndefinedColumn`), ADV4-PG (collection ERROR, drift-guard message byte-matching). Every restore sha==baseline + porcelain-empty; every GREEN re-run green (leg-8 GREEN re-proven on a re-created scratch DB after a first drop produced `3 skipped` — the silent-skip trap, disclosed). Matrix planted-sha matches are bonus-only (1/8 recoverable — recorded as KB insight); all legs validated on exact RED signature. Scratch worktree + DB torn down to zero.

## Gate 6 — SC7 migration count: 🟢 PASS (recon 84e2eff8 check 8)
`daemon/migrations/versions/*.sql` == **115** at HEAD; `git diff --name-only 0d6489879..HEAD -- daemon/migrations/versions/` == **0 files** (empty .sql delta; stage-time re-derivation remains the lane-giter's job).

## Gate 7 — Envelope: 🟢 PASS (G7a CLEAN + G7b CLEAN)
**G7a (1aca1ce2):** lane test surface = exactly 12 files (10 under `tests/job_queue/` incl. `zombie_close_helpers.py`, 2 PG packs; `tests/dbpool_wedge_capture_pg.py` M). ZERO hits: `9797`, `ensemble_prod`, live-install paths, `$HOME`/`expanduser`, `rm -rf`, non-localhost hosts. The single `env -u POSTGRES_` site is the SAFE per-variable form (wedge PG pack :56-59: scrub + `mktemp -d` FAKE_HOME + localhost scratch DSN). PG conftest fence verified (exact-name allowlist; FORBIDDEN = prod/dev/prod_test; no ambient POSTGRES_* fallback). Doc-only caveats: PG-pack docstring names a foreign lane worktree (stale recipe path, routed to lane owner); dispatcher's `tests/unit/tools/` static-pack expectation was stale (the static pin pack is `tests/job_queue/test_zombie_active_close_static.py`; the pre-existing `tests/unit/tools/test_dbpool_wedge_capture_static.py`, 6 tests, zero lane edits, exercised green via W-C).
**G7b (fd448392), post-gate:** ensemble-main `MainPID=991 NRestarts=0 ActiveEnterTimestamp=2026-10-30 19:45:44 UTC` (epoch predates the gate window by ~8h20m — continuity across the window); `state.json` sha256 `3b036f68ccc4debe9fddde47c81802afe7b6edbab6e40ffd8de417004971b100` (post-gate fingerprint; no pre-gate baseline existed — disclosed); `current → releases/v0.27.15`; :9797 listener alive (ensemble-prod pid 1484), :8079 down (dev daemon not booted by the gate — correct); PG scratch residue ZERO; mfz worktrees ZERO; stray processes ZERO (the W-B inner-timeout-cut pytest confirmed gone); lane worktree porcelain-empty @ `defa3d8b5`; evidence tree `/tmp/mfz-gate/` retained (11M).

## Disclosures & notes (the WITH-NOTES payload)
1. **Branch tip advanced past the commissioned anchor:** the gate added exactly ONE commit — `defa3d8b5` (test-only pin fold, parent `9af20578a`). The lane-giter must re-derive the stage tree from `defa3d8b5` (v0.27.16 re-derivation was already planned "at lane-end stage").
2. **The 3 genuinely-new reds were folded, not excised** — full pre-fold evidence retained (`/tmp/mfz-gate/classify/`, pre-fold union copy `head_union.txt.pre-fold`); adjudication rationale: intended contract positively pinned by the lane suite, verified independently green by gates 1/5 before the fold.
3. **5 pre-existing-unlisted** mock-shadowing ids (one family) — pre-existing at base under isolation, absent from the base capture for process-grouping reasons; cheap group-fix candidate for a future lane; NOT this lane's debt.
4. **37 fixed:** 23 intentional (mechanical file+import attribution; `p4_5 defer-seam` strongly causal, `config.py` importers weakly linked — disclosed), 14 incidental (load/order-sensitive-at-base).
5. **G1a A1/A2** provenance/prose anomalies (above) — routed to the lane owner; artifacts stand substantively.
6. **install-audit.jsonl test-debris events:** KMS-bootstrap tests appended to the tracked file during shard windows (+32 rows W-A window incl. siblings, +3 W-C, +3 W-B); each shard restored via `git restore` with sha-verified as-run backups (`/tmp/mfz-gate/{wa,wb}/install-audit.jsonl*.bak`); zero data loss; the CWD foot-gun is a standing KB item. Classifier and G7b confirm the file NOT dirty at end-state.
7. **Mechanical recoveries during the corpus:** W-A 2× chunk-124 splits + 1 outer-1800s resume; W-B 4× splits + 1 script-2400s interruption re-run from scratch (no id skipped or double-counted — coverage proofs exact); W-C 2× splits + 1 outer-cap part-2 resume. All recorded in shard artifacts.
8. **No pre-gate `state.json` baseline** — post-gate fingerprint stands combined with epoch continuity.

## Artifacts
- Evidence tree: `/tmp/mfz-gate/{wa,wb,wc,wd,we,g1b,g3,g4,classify}/` (logs, id lists, rc/wall ledgers, fingerprints)
- Classifier outputs: `/tmp/mfz-gate/classify/{head_union.txt(+.pre-fold),net_new.txt,fixed.txt,pre_existing.txt,classification.tsv(338 rows),fingerprints.txt,class_flips.tsv,lane_diff_files.txt}`
- Base artifacts (committed): `.agents/shared/planning/missed-finalize-zombie-active/phase0-artifacts/` (base-failed.txt 330, base-summary, base-unit-summary, gap-proof ×2)
- Gate commits on branch: `defa3d8b5` (pin fold, test-only)
- This document: `.agents/tester/RESULTS/2026-10-31-zombie-close-functional-gate.md`

### Overall
- Gate 1 (symptom both directions): ✅ · Gate 2 (whole-tree net-new): ✅ post-fold (cycle disclosed) · Gate 3 (PG Pack I): ✅ · Gate 4 (wedge PG re-pointed): ✅ · Gate 5 (mutations 8/8): ✅ · Gate 6 (migrations 115/115/0-delta): ✅ · Gate 7 (envelope G7a+G7b): ✅
- **VERDICT: PASS-WITH-NOTES — ceremony-relevant for the lane owner; net-new ∅ holds at `defa3d8b5`.**
