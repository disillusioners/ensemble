# Functional Verification Gate — fix/job-scoped-answer-resolution @ 829feb94d (2026-10-19)

**Commission:** job-scoped answer resolution targeted the mission ROOT instead of the asking CHILD → spurious 410 QUESTION_PACK_LOST with a false "daemon restart" claim (original incident: pack 007fcacc-… lived on CHILD f7c909b0; watcher called job_answer(work_id=<mission>) → 410 naming ROOT 432988b8).
**Branch / worktree:** `fix/job-scoped-answer-resolution` @ `829feb94d0bb6254d6ccbbf271b5f1e74632f4a4`, worktree `/home/nea/ensemble-src-wt-job-answer-asker-fix`. Base (merge-base): `630cebbbade10d82490b6130319c2fd133251b` (v0.25.4 bump; direct parent chain = 8 commits).
**Change set (8 files, +3319/−42):** NEW `daemon/services/answer_resolver.py` (+1043) wired into `daemon/routers/jobs_management.py` (+113/−10) + `daemon/tools/job_queue.py` (+133/−20); targeted lookups `daemon/repositories/instance/repository.py` `find_by_metadata_value` (+95) + `daemon/repositories/task/repository.py` `find_instances_with_answer_handle` (+73); `daemon/routers/answer_helper.py` (+80/−12, see Note 3); NEW suite `tests/job_queue/test_job_answer_target_resolution.py` (+1775); `CHANGELOG.md` (+7, convention 5th file).
**Runner:** worktree `.venv` Python 3.14.7 / pytest 9.0.2 / xdist 3.8.0 / pytest-timeout 2.4.0; `uv` absent from PATH. Base legs = scratch worktree `/home/nea/ensemble-src-wt-jaaf-base` (detached @ base) + borrowed venv binary with `PYTHONPATH=<base>` (editable-`.pth` static-path mechanics: PYTHONPATH wins deterministically; guard trio proves base tree in effect). Every pytest/script run: 12-var ambient scrub (`POSTGRES_*`, `DATABASE_URL`, `ENSEMBLE_*`, `SSL_*` — ambient verifiably points at **live prod** 10.44.0.2/ensemble_prod), dual-layer timeout (outer `timeout 300` + `PYTEST_TIMEOUT=280`/`--override-ini="timeout=280"`), no `-x`. DB-touching packs (P8/P9) scrub-then-source the fenced worktree `.env` with `POSTGRES_DB=ensemble_dev` assertion. Ports: 9797 (live prod daemon) untouched throughout; 8079/8088 never bound (P9 used in-process ASGI).

## VERDICT: 🟡 PASS-WITH-NOTES (all commission items green; 4 disclosed notes, none weakening the evidence)

---

## 1. Repro-at-base (commission item 1) — ✅ REPRODUCED

Pack `jaaf_p1_suite_base` — new suite file overlaid on pristine base (test file ONLY; sha256 `1a31b379…`; base guards: `import daemon` → base path; `import daemon.services.answer_resolver` → ModuleNotFoundError; `jobs_management` importable).

- **Base 630cefebb: 38 failed / 3 passed / 0 errors (41 items), exit 1, 9.4s, deterministic ×2 runs** (junit `failures="38" errors="0"`).
- Failure classes: **(i)** 9× `ModuleNotFoundError: daemon.services.answer_resolver` (function-local imports — prove the tree, not the symptom); **(ii)** 1× `AttributeError: find_by_metadata_value` (branch-new repo method); **(iii)** 28× **BEHAVIORAL**.
- **Original symptom shape among the 28:** 20× HTTP 410, 16× `QUESTION_PACK_LOST`, 17× false "daemon restart without a durable shadow" phrasing, 1× direct wrong-instance landing. Canonical excerpt: `410 QUESTION_PACK_LOST: No question pack for instance inst-37c… in memory or in instance_metadata — the pack was lost (daemon restart without a durable shadow)`; route form `{"detail":{"code":"QUESTION_PACK_LOST",…}} assert 410 == 200`.
- **ROOT-naming proven two ways:** (1) structural — suite seeds the pack on the CHILD and asserts `get_question_pack(root) is None`, so any 410 naming "instance X has no pack" necessarily names the ROOT (all 16 QUESTION_PACK_LOST nodes); (2) direct id mismatch — `assert 'inst-4f6134a…' == 'inst-5721c41…'` (actual=ROOT, expected=CHILD; tree contains only {root, child}).
- **Reviewer's 11F/30P reconciliation: CONFIRMED EXACTLY IN SCOPE** — the reviewer's 11 = precisely `TestJobAnswerLandsOnChildAsker` (3) + `TestJobRouteLandsOnChildAsker` (2) + `TestPackIdDisambiguatesMultipleAskers` (3) + `TestGenuinelyMissingPackIsAccurate410` (3); all 11 FAILED at base. Whole-suite 38F/3P delta (+27) is suite-coupling collateral (resolver/envelope-dependent classes ii & iii) — cannot pass at a full-base checkout by construction.
- **The 3 base PASSES are the unchanged-surface guards:** `test_instance_scoped_answer_still_works`, `test_instance_scoped_unknown_instance_still_404`, `test_tool_lane_reports_answer_target_terminal` — all also PASS at HEAD (see mapping e).
- Evidence: `/tmp/jaaf-p1/` (junit.xml, output.txt, junit_rerun.xml, output_rerun.txt, overlay_stash.py).

## 2. HEAD results (commission item 2) — ✅ ALL GREEN, A/B DIFFS SELF-VERIFIED

| Pack | Leg | Items | Result | Time | Evidence |
|---|---|---|---|---|---|
| `jaaf_p4_suite_head` | new suite @ HEAD | 41 | **41/41 PASS**, exit 0 | 12.4s | `/tmp/jaaf-p4/` (junit + nodes.txt: all 41 node ids) |
| `jaaf_p6_dir_head` | full `tests/job_queue/` @ HEAD | 2150 | **2097P / 15F / 0E / 38S**, exit 1 | 95.5s | `/tmp/jaaf-p6/` (failures_failed_only.txt = 15) |
| `jaaf_p2_dir_base` | full `tests/job_queue/` @ base (pristine) | 2109 | 2053P / 18F / 0E / 38S, exit 1 | 81.0s | `/tmp/jaaf-p2/` (18 raw / 15 corrected) |
| `jaaf_p5_sweep_head` | 18-file answer/question sweep @ HEAD | 220 | 219P / 1F / 0E / 0S | 18.8s | `/tmp/jaaf-p5/` |
| `jaaf_p3_sweep_base` | same sweep @ base | 220 | 219P / 1F / 0E / 0S | 17.4s | `/tmp/jaaf-p3/` |

- **Full-dir failure-set diff (self-verified, not trusted from dev):** raw diff = **3 nodes**, all `test_f1_killswitch_tz_matrix.py::TestRecoveryServiceBothStateParity::*` — **LOCATION-attributed, not code**: the test child-spawns relative `.venv/bin/pytest` (absent in the venv-less scratch worktree); `git log base..HEAD -- <file>` EMPTY; symlink-bridge probe at base → **3/3 PASS**; already ledgered (QUARANTINE.md 2026-10-04 row). **Branch-attributable diff = EMPTY (md5 `b6a407d078de6cd95e8ecec776c95945` both legs, 15↔15).**
- **Sweep diff: EMPTY** (md5 `a51a73474738b37917a33997ac967a37` both legs; the lone F = the quarantined order-pin; all 18 per-file tallies identical).
- Arithmetic: HEAD 2150 − base 2109 = 41 = exactly the absent new suite; skips identical (38↔38).
- **Sweep shape disclosure:** dev's "259P/1F" selection was unrecoverable from git (zero `-k`/file-lists in all 8 commit bodies) — the gate derived its own: name-pattern `*answer*|*question*` files minus the new suite = 18 explicit files (list in PACKS.md row). Numbers above are for THAT selection.
- Dev-claim reconciliation: "15F in tests/job_queue/" CONFIRMED; "13 unit" NOT reproduced (sweep shows 1F only); HEAD total 2150 = dev's 2149 +1 (HEAD commit `829feb94d` itself adds 1 test) — no unexplained deltas.

## 3. PG-branch behavioral pass (commission item 3) — ✅ PASS

Pack `jaaf_p8_pg_branch` — `find_by_metadata_value` + `find_instances_with_answer_handle` against BOTH engines with identical fixtures (real `_seed_instance`/`_seed_task` shapes; decoys exercising every WHERE arm; 515 instances / 10 tasks per engine, asserted equal).

- **5/5 geometries × 2 methods × 2 engines MATCH:** child-holder {C1}; root-holder {R2}; two-holders {C3a,C3b}; zero-holders ∅; 501-instance subtree → exactly {#317} (chunk boundary 500+1 exercised; executed-statement counts reconcile; timings 2.5–12.1ms — PK-anchored, no pathological scan).
- Dialect arms confirmed by compiled SQL + event-hook capture: PG `metadata ->> %(key)s` vs SQLite `json_extract(metadata, ?)`; handle query identical shape across engines.
- Isolation: throwaway schema `jaaf_pgbranch_d430353a` (CREATE DATABASE denied 42501 → schema fallback per instruction); escape-guarded `DROP … CASCADE`; pg_namespace count 0 after; public delta +0.
- Evidence: `/tmp/jaaf-p8/pg_branch_check.py` (re-runnable, aborts unless `POSTGRES_DB=ensemble_dev`), full log in worker report.

## 4. Live route smoke (commission item 4) — ✅ PASS

Pack `jaaf_p9_route_smoke` — in-process real app (`create_app()` + TestClient ASGI, banner `Starting Ensemble v0.25.4`), real route `POST /api/jobs/{work_id}/answer`, real `ensemble_dev`, FAKE_HOME-fenced, mktemp + escape guards, `start_sources` no-op fence. 31.7s.

- **S1 child delivery + resume:** POST with `question_pack_id` → **200**, `answer_target.instance_id` = CHILD, `route=subtree_pack_id`, `status=answered`, `resume_route=answer_gate_existing_turn`, `resume_info.resumed=true`; DB: child `paused→running`, handle task `paused/awaiting_answer→resumed`, RAM pack `pending→answered`, durable shadow cleared. (Mission root also `paused→running` via pre-existing cascade `_resume_target_and_tree` semantics — unchanged by fix, observed + disclosed.)
- **S2 idempotency:** same POST → **200** `status=already_delivered`, `resume_route=already_delivered`; server log `CAS lost … transitioned=False (already_delivered no-op)`; holder still found via RAM-pack lane (N-1 behavior live).
- **S3 ambiguity:** no pack id + two askers → **400** `INVALID_REQUEST` with `candidate_ids:[childA, childB]` and message instructing `question_pack_id`.
- **S4 tool lane:** REAL factory `create_job_tools_if_available(manager, …)` → 24 tools; `job_answer` invoked with pack id → success envelope `answer_target.instance_id=childA, route=subtree_pack_id, resume_info.resumed=true` — same shared resolver as the route.
- Cleanup: 5 instances / 5 tasks / 3 packs deleted by exact-id predicates (plus recovered first-run orphans via unique run-tag), residue count **0** across instances/task/event/message_queue/checkpoints/checkpoint_blobs/checkpoint_writes.
- Evidence: `/tmp/jaaf-p9/` (smoke.py, run-smoke.sh, evidence.md, run2-full.log — full verbatim HTTP bodies).

## 5. QUARANTINE.md intake (commission item 5) — ✅ DONE

ensure.md:7 grep-verified (read directly): *"Pre-existing failures must be quarantined, not left to permanently red the gate."* Intake row added to Active (top): 15-node dir set A/B-identical md5-proven at 630cefebb↔829feb94d with per-family cross-refs (mirror-finalize trio = 4th independent attribution; order-pin rows 2026-10-04/09-25/09-27/10-14; tool literal sequence byte-identical base↔HEAD — branch adds no tool def); tz_matrix ×3 location-artifact re-confirmed (2026-10-04 row, bridge probe 3/3 PASS); sweep 1F≡1F. **Zero new members, zero un-quarantine candidates** (order-pin still red at HEAD). Claim reconciliation recorded (dev 15F confirmed; "13 unit" not reproduced; reviewer "19+1" ≈ 18 raw base + order-pin double-count).

## 6. Scenario → node mapping (commission item 6) — all green at HEAD

| Commission scenario | Green node ids (tests/job_queue/test_job_answer_target_resolution.py) | Live corroboration |
|---|---|---|
| (a) child+job-scoped delivered+resumed | `TestJobAnswerLandsOnChildAsker::test_tool_answer_reaches_child_asker_not_mission_root`, `::test_tool_answer_resumes_child_from_paused`, `TestJobRouteLandsOnChildAsker::test_route_answer_reaches_child_asker`, `::test_route_without_pack_id_resolves_via_answer_handle` (+ pre-pause 17/18, stale-shadow 16/21, root-reachable 22/23, fast-path 29/30/31, idempotent 34/35/36, scan-signals 39/40) | S1+S2+S4 smoke |
| (b) root-no-pack: no false 410 | `TestStaleAnsweredRootPackDoesNotShortCircuit::*`, `TestStalePendingShadowIsNotAuthoritative::*`, `TestMissionRootIsReachableWhenTheWorkResolvesToAChild::test_route_answers_the_root_when_the_work_is_a_child`, `::…without_a_pack_id`, `TestAskerIsRootFastPath::test_answered_pack_on_the_work_instance_resolves_via_fast_path` | — |
| (c) two children, pack-id disambiguation | `TestPackIdDisambiguatesMultipleAskers::test_pack_id_selects_the_right_child`, `::test_ambiguous_without_pack_id_is_refused`, `::test_route_ambiguity_is_400_with_candidates`, `TestAmbiguousCandidatesAreRefused::*` (19/20), `TestTruncatedSearchIsRefused::*` (41), `TestLargeSubtreeWithSuppliedPackId::*` (24/25) | S3+S4 smoke |
| (d) genuine-missing accurate 410 | `TestGenuinelyMissingPackIsAccurate410::test_missing_pack_is_410_without_restart_claim`, `::test_missing_pack_details_carry_resolution_fields`, `::test_route_missing_pack_is_410`, `TestNoAskerInSubtreeIs404::*` (12) | — (base leg shows the OLD false-restart 410 these tests kill) |
| (e) instance-scoped unchanged | `TestInstanceScopedRouteUnchanged::test_instance_scoped_answer_still_works` (13), `::test_instance_scoped_unknown_instance_still_404` (15), `::test_instance_scoped_missing_pack_still_410` (14) | 13 & 15 also PASS at BASE (P1's 3-pass set); 14 differs at base ONLY by the false-restart phrasing the fix's accurate-410 contract removes (see Note 3) |

(Mock discipline honored: suite is behavioral by design — real repos/resolver/engines; P9 mocks nothing but fixture rows; no web automation — FE untouched, instance-scoped route behavior pinned.)

## 7. ensure.md Core validation — ✅ PASS (Release Gate ruled NOT warranted)

- Core #1 (no regressions in changed packs): scoped packs all green modulo quarantine → PASS.
- Core #2/#3/#6 (concurrency/deadlock/loop-thread): `concurrency_atomic_unit_test` **PASS 99P/0F/74S, 98s, baseline-exact** (13 files, named in worker report).
- Core #4 (`dev.sh --timeout-graceful-shutdown 10`): static PASS (`dev.sh:142`; file untouched by branch).
- Core #5 (await-callers): N/A — zero async-signature changes in `daemon/` diff.
- Core #7 (no dead code): production diff is pure additions (no removed def/class/import); resolver consumed by both wired surfaces.
- Release Gate: **NOT run — ruled not warranted** (isolated fix verification, no release/architecture change; boot-orphan-sweep precedent). No ensure.md contradictions: every validation ran as a pack with dual-layer 5-min timeouts, scoped by blast radius; no `-x` anywhere.

## Notes (disclosed, none weakening the evidence)

1. **P8 first-run isolation failure (reversed):** SQLAlchemy caches `dialect.default_schema_name='public'` before the connect-event hook → `create_all(checkfirst=True)` skipped the throwaway schema and 515 fixture instances + 10 tasks landed in `public` of **ensemble_dev**. Read-only verification, then surgical exact-set deletion (fingerprint + time-window + explicit ids; counts reconciled 549→34, 10→0), leftover schema dropped, script fixed (connect-event `SET search_path` + `checkfirst=False` + pre-seed zero-count abort guard — the guard proved itself by aborting an intermediate run with zero writes). Canonical PASS run fully isolated (public delta +0). LESSONS filed.
2. **P9 pre-fence near-miss (read-only):** two ad-hoc `SELECT`s briefly hit **ensemble_prod** via ambient env before the unset-and-assert fence was scripted — zero writes, zero mutation; fence then held for all executed scenarios. Env-poison class; disclosed per convention.
3. **Brief-vs-diff discrepancy (benign):** commission brief said instance-scoped route "instances.py + answer_helper.py intentionally untouched", but the diff shows `answer_helper.py` +80/−12 (shared-resolver refactor). Behavior is pinned unchanged: nodes 13–15 green at HEAD; 13/15 also green at base; the ONLY observable delta is node 14's 410 message losing the false "daemon restart" phrasing — exactly the fix's accurate-410 contract. `instances.py` untouched (not in diff).
4. **P9 auto-dispatch observations (fixtures, not defects):** background resume of synthetic instances predictably fails (no checkpoint) — route correctness (200 + DB transitions) is the gated assertion; cascade resumed a waiting sibling (`silent_resume`, pre-existing semantics); dev backlog empty; no LLM calls fired.

## Conventions honored

No test/script resolved live-shaped paths (FAKE_HOME + escape guards; in-process ASGI bound nothing); every "N passed" claim above names its files (18-file sweep list in PACKS.md; 13 concurrency files in worker report; full-dir = 114 modules, junit-verified); base scratch worktree removed at gate close; 9797/8088 untouched; worktree `.env` fence verified (`ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`).

**Evidence tree:** `/tmp/jaaf-p1/` … `/tmp/jaaf-p9/` (junit, outputs, failure sets, scripts, verbatim transcripts). Curated copies: `.agents/tester/EVIDENCE/2026-10-19-job-scoped-answer-gate/`.

## Gaps

None — all 7 commission items executed and green. Deferred (out of gate scope, already ledgered): the 15 pre-existing family members remain quarantined test-debt (fix commissions on latest); tz_matrix location artifact (future worktree legs: pre-provision `.venv/bin/pytest`).
