# Independent Test Verification — completion-subsystem Phase 1 + END-OF-TRAIN full-pool A/B

- **Date:** 2026-10-24 (23:24–… UTC window)
- **Verifier:** tester (Test Leader) + dispatched workers: recon=ed4813c9 · focused=dbe948ab · edge-probes=8b468827 · mock-discipline=2f936520 · mutations=5a233bdd · e2e-traps=c5ef02fc · unit-A/B=1988c1d7 · full-A/B=5fb5e663 · full-pool=aea6979c
- **Branch:** `feature/completion-subsystem-p0p1` @ `1c4f8556a4b2334c03953ff0351df1f263900298` (11 commits on base `074833b45127462a1b7276baa09145681f40a3c4` = `latest`)
- **Worktrees:** W1=…-wt-completion-subsystem-p0p1 (HEAD leg, fenced .env sha256-16 `3601796e5c91207a`, DAEMON_PORT=8098) · W2=…-wt-csp0p1-base (base leg, detached @ 074833b45, `.env` byte-identical, `uv sync --frozen`, Python 3.13.3 both) · W3=…-wt-csp0p1-mut (mutation leg, detached @ HEAD, DAEMON_PORT=8099)
- **Mandate:** INDEPENDENT verification, no source modification, no fixes. Dev's exact commands recovered verbatim from the daemon DB (threads 97355c26/fdaeeab8/14922ad0; 81 command blocks).

## Verdict summary

**GATE = FAIL (test-side only). The end-of-train full-pool A/B closed the 81.4% tail and found 6 DETERMINISTIC BRANCH-CAUSED failures — all six from ONE root cause (a stale 2-arg mock in `tests/test_manager.py` vs the branch's new `populate_existing=True` kwarg). Zero production defects; 415-node identical failing-ID core; the other 15 diff nodes are classified session-state flakes. Every other measurement claim verified (two with restated numbers). Gaps in the new tests: no critical; 5 important (coverage holes, none behavioral).**

## Per-claim verdict table

| # | Developer claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Focused 30-file run 694/0 | ✅ **CONFIRMED** (restated: **27 files / 700/0 at HEAD**) | Verbatim 27-file selection re-run @ HEAD: `700 passed, 10 warnings in 21.66s`, exit 0. Dev's 694 measured at `56d24de93`; +6 = `45a34face` (+2, TestResolverOwnSession) + `1c4f8556a` (+4, TestReportFamilyTuple: 3 defs, one parametrized ×2). Diff-attribution: only those 2 files moved in `56d24de93..1c4f8556a` within the selection. `/tmp/verify/a1-focused-head.log` |
| 2 | Unit A/B 156=156 IDENTICAL | 🟡 **CONFIRMED IN SUBSTANCE / literal count REFUTED at verified pair** | base 156 / head 155 lastfailed ids; single diff = `test_bearer_p5.py::…test_bootstrap_token_secret_str_no_leak_in_stdout` (base-only). Solo 3×: fails-base-only; 4-cell ENSEMBLE_DATA_DIR matrix proves pure gitignored-`data/ensemble.json` mode dependence (postgres→FAIL / sqlite→PASS, identical mapping both legs). Standing QUARANTINE row (2026-10-18, env-dependent). **0 branch-caused.** Legs: base 18548 collected/107F/49E/71S (857s), head 18835/106F/49E/71S (838s); INTERNALERROR aborter fired symmetrically (rc=1, 159 lines, file-local repro both legs). Ledger nit: row says `OperationalError`, observed `InsufficientPrivilege` (same mechanism). `/tmp/verify/a2-*` |
| 3 | Full A/B (--ignore packs/e2e) 175=175, 176th = documented flake | ✅ **CONFIRMED-WITH-RESTATEMENT** | Abort landed at the recorded fence node both legs (collection idx 5710 base / 5726 head; `pytest.exit` rc=1; byte-identical message incl. the `manual_test_pg_roundtrip.py` import-time `POSTGRES_DB=ensemble_test` mutation — symmetric blob). Executed prefix: base 176F/5416P/118S, head 176F/5432P/118S (+16 new Phase-1 nodes in-prefix, all passing). **Python set diff: 175 common + 1 base-only + 1 head-only — a same-family flake SWAP** (`TestDequeueAtomicClaim::test_dequeue_concurrent_only_one_worker_wins` [QUARANTINE row 55] ↔ `…drains_n_messages_with_n_workers` [row 53 family]); whole-file 5×/leg shows the family oscillates on both legs. Flake-candidate check: `skill_evolution::test_ab_resolution_threshold_met` WAS in-prefix (idx 4849/4865) and PASSED both legs; `plane_sync::test_service_concurrent_calls_dont_crash` never executes in this form (idx 22408/22481 > abort) → the "176th" attribution restated as the dequeue-family swap. Bonus: dev's Phase-0 unexplained 175↔176 (−1) RECOVERED — their base ledger dropped one id to a warning-path concatenation; normalized: 174 common + 1 flake = 176. `/tmp/verify/a3-*` |
| 4 | 12 mutation checks red-then-green incl. F1 | ✅ **CONFIRMED 12/12** (13 run; 10 exact-numeric; 1 understated by dev; 1 verified at narrower scope) | All in W3 (isolated detached-HEAD worktree), line-neutral edits, sha256 restore proofs, final status EMPTY + HEAD unmoved, combined pins 282/282 green. F1 mechanism pin LIVE and calibrated: `session=session` re-add → exactly `1 failed / 5 passed` (byte-for-byte the `45a34face` figure). M3 (D1-a resolver guard invert) → 7 red vs dev's 2 named (claim UNDERSTATES coverage). M2 (D4(i)) verified at `instance_ops:`-entry scope (5-6 red incl. every SOURCE-U anchor) — dev's "66/143 full-revert" number NOT independently reproduced (residual). Bonus M5 (D3 `== 1` → `>= 1`) red — beyond the claimed set. `/tmp/mut/` |
| 5 | Un-mocked fail-open constructions per branch | 🟡 **PARTIAL** | Real-seam coverage CONFIRMED for D3 helpers, D4(i) stamps (real enqueue → persisted type), D2(a) census (real scanner+gate), D2(b) mint (real bus+repo+Task row), D1 resolver (mock-free file, real dropped-table fail-back), D5(i) epoch clears (real resets + real inline lane), W1 `queued=True` producer. **Fail-open holes:** D2(c) C-union query `fetch_pending_for_targets` (`dependency_bus/repository.py:203-247`) has ZERO real-DB coverage anywhere in the repo (all 5 union tests mock the repo; M13 proves only the call-site is pinned); W1 `not_queued_reason` PRODUCERS (`instance_messaging.py:2440`,`:2683`) exercised by zero tests (renderer-only pin) — the false-marker defect W1 fixes could return silently. |
| 6 | E2E traps present (ATTESTATION_NUDGE_TEXT import, MethodType count_live_descendants, recursion_limit, delta asserts) | ✅ **CONFIRMED** (trap 4 scope-caveated) | `daemon/graph.py:4854` citation EXACT; 14 files import the constant module-level, zero try/except → rename breaks collection loudly; byte-literal canonical anchor exists in exactly one file (`test_attestation_nudge_inject.py:307-313`) — ledger note. MethodType bind present ×3 builders + PG variant, with in-code b08f40fe fail-open rationale. recursion_limit pin: `test_attestation_gate_bounds.py:480` ("must never be the only terminator"). Delta asserts: present family-wide; ABSENT from the 16 branch-new files (they use exactly-one/no-duplicate absolute pins — same spirit). |
| 7 | (mission item 4) Full-pool A/B — whole pool, both legs, scrubbed env | ❌ **FAIL-WITH-KNOWN-CAUSE: 6 deterministic branch-caused failures (1 root cause, test-side)** | BASE 29,662 nodes executed (28,949P/368F/53E/292S, failing-ID set **421**, 35m02s) vs HEAD 29,966 (29,244P/376F/54E/292S, set **430**, 47m04s). Set diff = 21 nodes: **415 common + 6 base-only + 15 head-only**. **6 branch-caused** = `tests/test_manager.py::TestSendMessage::test_send_message_success` + 5× `TestTitleGenerationTrigger::*` (3/3 HEAD solo, 0/3 BASE solo) — root cause: `a31615cc4` (D4(ii/iii) backstop) added `populate_existing=True` to `session.get(Instance, instance_id)` at `daemon/services/instance_messaging.py:2513`; the test file's 6 mock side-effects are 2-arg (`get_instance_side_effect` / `_get_side_effect` at ~:297/:1546/:1599/:1648/:1703/:1759) → `TypeError: unexpected keyword argument 'populate_existing'`. Production call is CORRECT (real `Session.get` accepts the kwarg) → **class = branch-caused stale-mock test debt**. Proof: `tests/test_manager.py` byte-identical base↔HEAD; kwarg absent at base (0 occurrences), present exactly once at HEAD. Fix = widen the 6 signatures to accept `**kwargs` (est. ~2 min to re-run the two affected shards). Latent same-class site (no break today, absorbed by `**values: Any`): `daemon/repositories/instance/repository.py:1799` `with_for_update=clear_denial_epochs`. |
| 8 | (mission item 8) Quarantine/flakes | ✅ plane_sync row STANDS (main-repo QUARANTINE.md:58 standing row + uncommitted Phase-0 session-state addendum row, ` M` verified); dequeue-family swap covered by rows 53/55; bearer_p5 present symmetrically in the pool common set (as predicted under the pinned scratch) → **2 NEW rows added** (context-hang containment set 39 nodes; session-state diff-noise set 13 nodes). The 6 branch-caused nodes are deliberately NOT quarantined — deterministic failures must not be hidden from the gate. |
| 8 | (mission item 8) Quarantine/flakes | ✅ plane_sync row STANDS (main-repo QUARANTINE.md:58 standing row + uncommitted Phase-0 session-state addendum row, ` M` verified by recon); dequeue-family swap covered by standing rows 53/55; bearer_p5 covered by standing row. | |

## Section 7 — END-OF-TRAIN FULL-POOL A/B (worker aea6979c) — GATE FAIL, 6 branch-caused

Design executed as frozen: fence-SATISFIED env (`POSTGRES_DB=ensemble_dev_test_only`, `ENSEMBLE_SELF_ENV=dev`, URL vars scrubbed, `PG_TEST_*` unset), `ENSEMBLE_DATA_DIR` pinned to a per-worktree scratch wiped at each leg start (neutralizes the item-2 `data/ensemble.json` mode asymmetry — both legs auto-detected **postgres** mode and wrote byte-identical `ensemble.json`), 26 pool shards + 1 isolated + 3 attestation shards per leg (≤3,253 nodes/shard, boundaries fixed by filename), `timeout 1750` per shard, pyproject per-test timeout untouched, exclusions identical both legs (packs, e2e, upgrade_executor INTERNALERROR file → own isolated shard, `[1x10k]` deselect, attestation family → dedicated `--override-ini='timeout=120'` leg), Python-only set adjudication (macOS `comm` is locale-unsafe), solo-3×-both-legs classification for every diff node.

| | BASE `074833b45` | HEAD `1c4f8556a` |
|---|---|---|
| Nodes executed | 29,662 | 29,966 |
| passed / failed / errors / skipped | 28,949 / 368 / 53 / 292 | 29,244 / 376 / 54 / 292 |
| Failing-ID set | 421 | 430 |
| Deselected | 968 | 968 |
| Leg wall (pool+isolated+attestation) | 35m02s | 47m04s |

**Set diff: 21 nodes = 415 common + 6 base-only + 15 head-only.**

### 🔴 CRITICAL — 6 deterministic branch-caused failures (one root cause)
`daemon/services/instance_messaging.py:2513` (D4(ii/iii) race backstop, commit `a31615cc4`) changed `session.get(Instance, instance_id)` → `session.get(Instance, instance_id, populate_existing=True)`. `tests/test_manager.py` mocks the session with 2-argument side effects → `TypeError: get_instance_side_effect() got an unexpected keyword argument 'populate_existing'`. Affected: `TestSendMessage::test_send_message_success` + `TestTitleGenerationTrigger::` ×5 (title_on_idle_to_running ×3, title_generation_skipped ×2). Evidence: 3/3 HEAD solo fail vs 0/3 BASE solo; `git diff base..HEAD -- tests/test_manager.py` EMPTY; `populate_existing` present 0× at base / 1× at HEAD; real `sqlalchemy.Session.get` accepts the kwarg (production correct). **Class: branch-caused stale-mock test debt, test-side fix** — widen the 6 signatures to accept `**kwargs` / `populate_existing=False`. Latent same-class site: `daemon/repositories/instance/repository.py:1799` (`with_for_update=clear_denial_epochs`) — absorbed by `**values: Any`, no break today.

### Remaining 15 diff nodes — all session-state, 0 branch-caused
13 pass solo 3× on both legs (9 × `tests/test_prompt_version_*` `[postgres]`/`[reject-postgres]` batch-order swap · `test_enqueue_shared::test_missing_instance_warns_but_creates_message` · `test_manager::test_send_message_instance_not_found` · dequeue/status family swap members) + 1 StaticPool node already covered by a standing row (`test_dependency_bus::…test_per_parent_lock_serializes_db_insert`, HEAD 1/3) + 1 base-anomaly solo (dequeue family, row 55).

### Containment (symmetric, documented)
pytest-timeout `timeout_method="thread"` calls `os._exit(1)` on one slow test → the shard dies with no summary and no `lastfailed`. Three files hang ONLY in full-shard context and pass solo: `tests/unit/test_scheduler_deferred_delivery_pin.py` (27), `tests/integration/test_job_driven_enqueue_work_id_facade.py` (3), `tests/integration/test_lcancheck_family_separation.py` (9, s3 — standing row). 39 node ids deselected IDENTICALLY on both legs (deselected 968 = 929 + 39) and every affected shard re-run on both legs — without this, 4 aborted shards would have fabricated ~111 false head-only nodes. No timeout was relaxed; no shard was killed.

### Supporting legs
- **Attestation dedicated leg: PERFECT IDENTITY** — 174P/53F/0E/2S both legs, 53-id failing set identical (0 base-only, 0 head-only), 90 s per leg, `--override-ini='timeout=120'` only.
- **Isolated `upgrade_executor` shard: identical signature both legs** — rc=1, 159 INTERNALERROR lines, 3× `RuntimeError: simulated detector internal error` (matches the item-2 file-local proof).
- **Census reconciled exactly** — whole-tree 29,733/30,965 base and 30,036/30,965 head; SUM(shards) == whole tree both legs; mission census 30,037 = 30,036 + 1 (`[1x10k]` cell, deselected identically).
- **Deviations (5, all symmetric, all documented)**: `-rfE` (ERROR ids were otherwise invisible); `-p a4_preimport` plugin (whole-tree collect aborts on an order-dependent `langgraph.checkpoint.base` ImportError); per-leg file-list resolution (BASE lacks the 14 branch-new test files); `python_files` glob filter on shard lists; the symmetric 39-node deselect.

### Gate verdict
**FAIL — fix required (test-side).** The train's production behaviour is clean: 415-node identical failing-ID core, zero production defects, all six failures from one stale-mock class. Recommendation: land the 6-signature widening **inside** this train, riding the already-serialized dev-2 W1-job-lane fix (per `cs.phase1.gates.pending` order: tester → dev-2 fix → tester delta-verify → giter finalize), with the two affected shards (`root-a`, `root-b`) re-run on both legs (est. ~2 min). Strict-6-sites scope; a blanket `**kwargs` sweep of `Session.get` mocks (incl. the latent `repository.py:1799` site) = separate test-debt commission.

**Answers to the worker's open questions (resolved within the verification mandate):** (1) fix INSIDE the train, not a separate commission — the fix is deterministic, root-caused, 6 lines, and the train already has an open fix-iteration slot; (2) strictly the 6 proven sites now (minimum blast radius to green the gate), blanket sweep as a follow-up commission with the latent `repository.py:1799` site as its seed.

## Gap list (carried from items 5-7 audits; report-only, nothing fixed)

**Critical:** none.

**Important:**
1. D2(c) C-union real-SQL + end-to-end settle-hold untested (`fetch_pending_for_targets` zero real-DB coverage; no test seeds a real pending watcher and asserts the leader does NOT settle through the C row). Two independent audits converged (mock-discipline C1-2; edge-probes P2). Recommend commissioning before release-gate finalize.
2. W1 `not_queued_reason` producers untested (renderer-only pin) — false-marker regression would return silently.
3. F2 structural pin constant-vs-constant (`test_source_stamp_matrix_d4i.py:214-220`) — reverting F2's production half stays GREEN; the stated invariant (no hardcoded report prefix outside constants.py) is unpinned. Fix shape: hard-code literals or `inspect.getsource` absence-assert.
4. Nudge tool-wiring existence guard vacuous (MagicMock auto-attr; `instance-not-found` refusal untested anywhere; `"delivered": true` assertion cannot distinguish live from truthy-mock).
5. `set_dependency_bus` singleton leak in `test_report_recipient_routing_d1.py:107-119` (no teardown restore; stopped-bus global reaches later files under xdist) + unpinned pipeline deferred-marker D1 site (`message_processing_pipeline.py:964`; fm11 tests still assert pre-D1 shape).

**Nice-to-have:** 5/11 D1 write sites + 3/6 backstop sites unasserted ("11+6 site walk COMPLETE" overstates); D4(i) 4b-branch pin cannot isolate branch 4b; stale doc comment `instance.py:1563-1564` (repo escape added by 3600a157d makes it false); unescaped LIKE concatenation `report_injection/repository.py:2142` (safe today, UUID-provenance-dependent); P1e tie-break arms (raw/row_id) unexercised; facade "returns service result unchanged" unasserted (C1-5); census log-schema constant pin (C1-6); duplicate pin `test_phase1_ledger_pins.py:214-221`; dead import `attestation_gate.py:88-89`; nudge-text byte-anchor lives in one file only; `_StubManager` bind can't catch `__init__`-time wiring; mocked spawn unconfirmed (`test_send_message_status_prose.py:195`); QUARANTINE row text nit (bearer_p5 exception class).

## Flake ledger status (post-full-pool)

- `TestDequeueAtomicClaim` family swap re-observed 2026-10-24 in BOTH the truncating full A/B and the full-pool run — standing rows 53/55 cover both nodes; no new row needed.
- `plane_sync` / `skill_evolution`: not oscillators in the truncating form; in the full pool `plane_sync` = 0 diff nodes, `skill_evolution` = 5 nodes failing on BOTH legs (symmetric).
- `bearer_p5`: present in the pool COMMON set (fail-both) under the pinned scratch — exactly as predicted; standing row applies.
- NEW row added (2026-10-24): context-hang containment set — 39 nodes across 3 files (scheduler_deferred_delivery_pin ×27, job_driven_enqueue_work_id_facade ×3, lcancheck s3 ×9) — shard-level deselect required under `timeout_method=thread`.
- NEW row added (2026-10-24): session-state diff-noise set — 13 nodes (prompt_version_* ×9, enqueue_shared ×1, test_manager::instance_not_found ×1, dequeue/status swap ×2) — all solo-PASS both legs.
- The 6 branch-caused `tests/test_manager.py` nodes are **not** quarantined: deterministic failures must stay visible to the gate until the test-side fix lands.

## Contamination record

W1 dirty-state at every checkpoint = exactly the 3 pre-existing foreign artifacts (install-audit.jsonl modified [foreign session + symmetric test-generated appends], 2 untracked .agents/tester files). W2 = clean ± symmetric install-audit.jsonl appends. W3 restored byte-identical (13/13 sha256, status EMPTY). No branch ever moved; no source file modified by any worker; port 8088 untouched throughout.


---

## ADDENDUM — 2026-10-25: DELTA-VERIFY of the final 3 completion commits @ `2ac01448824ae947e51751f8dd768060fec2c24d` (worker e4f74385)

Scope: scoped delta only — no full-pool re-run (the full-pool evidence above stands). The dev-2 fix slot landed exactly 3 commits on `1c4f8556a`: **A=`325d6213a`** (W1 job-lane — `not_queued_reason` threaded at both `enqueue_message_job` exits + producer pin), **B=`c55e4b6a7`** (6-signature mock widening in `tests/test_manager.py`), **C=`2ac014488`** (not-found fail-closed test contract, superseding T6b/C1-D7).

### Per-item verdicts — 6/6 PASS

| Item | Expected | Observed |
|---|---|---|
| 1. Git scope | HEAD `2ac014488…`; 11 commits on `dabe1f04d`; 3 on `1c4f8556a`; diff touches ONLY `daemon/services/instance_messaging.py`, `tests/unit/test_phase1_ledger_pins.py`, `tests/test_manager.py`; zero `.agents/**` | **exact match** (25/86/117 lines; no other path; W1 status = the 3 known foreign artifacts, byte-identical start→finish) |
| 2. The 6 `populate_existing`-class nodes solo @ HEAD | 6/6 GREEN | **6 passed / 0 failed** (0.28–0.78 s each) |
| 3. 7th node `test_send_message_instance_not_found` @ HEAD | GREEN under NEW contract | **1 passed** — `pytest.raises(ValueError, match="instance 'non-existent-instance' not found")` + no-orphan asserts (`MessageQueue`/`Task`/`Event` never constructed; `session.add`/`commit` never called); docstring names `a31615cc4` as the superseding commit. **AND base-production justification proven**: whole-file swap of `074833b45` `instance_messaging.py` into W3 (with HEAD test) → **RED `Failed: DID NOT RAISE <class 'ValueError'>` @ tests/test_manager.py:386** — base production's enqueue did not fail closed on a missing target. Restore sha256-verified both files. |
| 4. Shard `tests/test_manager.py` | 13P / 30S / 0F | **13 passed, 30 skipped, 0 failed** (twice, incl. `-rs`); all 30 skips = the documented pre-existing SQLite-migration quarantine family (identical reason string; spot-checked :411/:485/:1053). No new skip family. |
| 5. W1 producer pin | GREEN + one mutation | Pin file **12 passed / 0 failed** incl. `TestW1JobLaneNotQueuedReason::test_the_job_lane_attaches_the_reason_to_the_result_object`. Mutation: neutered A's marker/task-id-None exit (`:3243` — correctly identified as A's; the `:2873` site is PRE-EXISTING public lane, `:3565` is A's normal-exit defence-in-depth) → **RED** naming the job-lane node (`assert None == 'deferred_pause_marker'`) → restore → **GREEN 12 passed**. |
| 6. C's mutation claim | neuter raise → RED | Raise at `instance_messaging.py:2293-2297` neutered to fail-open `pass` → **RED `DID NOT RAISE ValueError`** → restore → **GREEN**. (Distinct from item 3's whole-file swap — two independent proofs, not double-counted.) |

Restore proofs: `instance_messaging.py` sha256 `1a42f47fc9877cb3e09e9f69808f6935dcf1a053dbd5716472a17439b44693bb` (= the KV-recorded production sha) and `test_manager.py` `2885b6d7…` — byte-identical after all 3 mutation pairs; W3 status EMPTY + HEAD `2ac014488…` detached; W2 read-only untouched.

### Methodology note (deviation, non-blocking)
The generic prescribed form `pytest <args> .` cannot run in this repo — the trailing `.` overrides `testpaths` and collects `test/packs/*` script-probes whose import-time `sys.exit(1)` is a collection INTERNALERROR (0 tests executed). Correct form: pass the node-id / file as the only path argument. Captured as skill `33a90a27` for future dispatches.

### 7/7 branch-caused resolution
All 6 deterministic `populate_existing` stale-mock failures from the end-of-train full-pool A/B are GREEN at `2ac014488` (item 2), and the 7th formerly-ambiguous node (`test_send_message_instance_not_found` — a session-state flake in the pool run) is now deterministically GREEN under C's re-contracted fail-closed assertion, with the contract change justified against base production (item 3). **Zero branch-caused failures remain.**

### FINAL END-OF-TRAIN VERDICT (restated)

**PASS — zero branch-caused failures remaining.** The full-pool A/B's FAIL-WITH-KNOWN-CAUSE (6 stale-mock nodes, one root cause) is resolved **test-side** by commit B; commit A closes the W1 producer fail-open gap flagged in this report's gap list (the `not_queued_reason` producers are now pinned by a mutation-sensitive real-seam test); commit C upgrades the not-found contract from fail-open to fail-closed with base-production justification. Production code in the range: 1 file, correct as shipped (real `Session.get` semantics). Residuals are the previously-ledgered items only: D2(c) real-SQL/settle-hold coverage (important gap — recommend commissioning), blanket `Session.get` mock sweep seeded by the latent `repository.py:1799` site, and the nice-to-have list. **Gate clear for giter finalize** (no push/merge performed by the tester).

Evidence: `/tmp/dv/` (12 logs + backups + sha table), workers e4f74385 (delta) on top of aea6979c (full-pool) et al.
