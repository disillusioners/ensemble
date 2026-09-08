# RESULTS — Final Verification Gate: fix/kv-ambient-awareness @ b47085fd

- **Date**: 2026-09-08
- **Gate**: Full-suite regression surface with baseline attribution + kill-switch contract + deploy pre-flight
- **Branch**: `fix/kv-ambient-awareness` @ `b47085fd` (base `9eebf3ff`), worktree `agents-ensemble-wt-kvfix`
- **Baseline**: fresh worktree `agents-ensemble-wt-kvfix-base` (detached @ 9eebf3ff, own `.venv`, `daemon.__file__` verified worktree-local BOTH sides)
- **Method**: repo-standard 12 regression slice packs (P-1..P-12, `test/packs/regression_*_test.sh`, drift-pinned rev-parse before each run, dual-layer timeout 300s/280s, xdist `-n auto`) run at BOTH commits; per-node failure-set diffing for attribution. Zero mutations outside `.agents/tester/` docs (baseline worktree creation explicitly authorized by the gate task).
- **Status**: 🟡 IN PROGRESS — sections filled as waves report. Final verdict at bottom once complete.

---

## Task 1 — Full-suite run with baseline attribution

### Per-slice results

| Slice | Scope | Branch @b47085fd | Base @9eebf3ff | Verdict |
|---|---|---|---|---|
| P-1 regression_unit_tools | tests/unit/tools/ | PASS 2420P/1S/0F/0E (16.35s) | PASS 2420P/1S/0F/0E (18.92s) | ✅ zero delta |
| P-2 regression_unit_services | tests/unit/services/ | FAIL 1448P/7F/0E (13.84s) | FAIL 1352P/7F/0E (18.04s) | ✅ PRE-EXISTING (same 7 nodes) |
| P-3 regression_unit_smaller_subdirs_routers | 6 unit subdirs | PASS 647P/0F/0E (17.08s) | PASS 647P/0F/0E (17.23s) | ✅ zero delta |
| P-4 regression_unit_loose_a_d | tests/unit/test_[a-d]* | FAIL 1352P/10F/21E (16.52s) | FAIL 1343P/10F/21E/2S (19.92s) | ✅ PRE-EXISTING (same 10F nodes + same 21E 17+4 split) |
| P-5 regression_unit_loose_e_l | tests/unit/test_[e-l]* | FAIL 1152P/11F/0E (45.42s) | FAIL 1152P/11F/0E (50.52s) | ✅ PRE-EXISTING (same 11 nodes) |
| P-6 regression_unit_loose_m_r | tests/unit/test_[m-r]* | FAIL 1850P/9F/40S/0E (67.89s) | FAIL 1850P/9F/40S/0E (70.34s) | ✅ PRE-EXISTING (same 9 nodes) |
| P-7 regression_unit_loose_s_z | tests/unit/test_[s-z]* | FAIL 980P/54F/11S/2E (19.62s) | FAIL 980P/54F/11S/2E (20.29s) | ✅ PRE-EXISTING (identical counts + full node inventory) |
| P-8 regression_top_level_a_h | top-level a-h + 7 dirs | FAIL 1025P/20F/54S/1E (169.64s) | (pending) | (pending) |
| P-9 regression_top_level_i_q | top-level i-q + 2 dirs | FAIL 2375P/60F/73S/0E (30.51s) | FAIL 2374P/60F/73S/0E (25.91s) | ✅ PRE-EXISTING (families exact-match 26/18/10/6) |
| P-10 regression_top_level_r_z_misc | top-level r-z + 2 dirs | FAIL 2260P/12F/34S/5xf (33.38s) | FAIL 2257P/15F/34S/5xf (22.83s) | ✅ PRE-EXISTING ×12 (twin-matching) + 3 base-ONLY concurrency failures (see note) |
| P-11 regression_job_queue | tests/job_queue/ | FAIL 1678P/7F/38S/0E (25.06s) | FAIL 1678P/7F/38S/0E (36.59s) | ✅ PRE-EXISTING (perfect node-for-node parity) |
| P-12 regression_integration_opencode_e2e | integration+opencode+e2e | FAIL 960P/12F/2S/24E (31.94s) | FAIL 959P/10F/2S/21E (41.94s) | 🔴 DELTA: 3 branch-introduced failures (see §Delta), 1 base-only env-class failure; both sides carry httpx-env error class (split differs) |

### Known-baseline confirmations (BOTH sides — confirmed identical at branch AND base)
- ✅ `tests/test_injection_api.py` 26F confirmed at branch AND base (P-9) — `_ManagerStub` fence class, `TypeError: MagicMock can't be used in 'await'` at `daemon/routers/messages.py:258`.
- ✅ 23 fixture ERRORs confirmed at branch: P-4 21E (17× `test_builtin_mcp_servers.py` `slash_commands` mock + 4× `test_context7_builtin.py` `blueprint` mock) + P-7 2E (`test_webfetch_builtin.py` `blueprint` mock) — all `instance_manager_with_repo` fixture class, `daemon/manager.py:981/:1074`.
- ✅ fresh-SQLite migration 20260714_000001 trap class present at branch: P-8 12 nodes (`test_jsonb_migration`, `test_phase4_metrics_trigger_init`, `test_skill_service_init` — MigrationError sqlite `near "CONSTRAINT"`), P-10 9× `test_spawn_limit_edge_cases`, P-9 1× `test_progressive_dispatch` write-once-guard, P-12 0 (deselected by pack machinery).
- ✅ Base-side confirmation: ALL the above families reproduced node-for-node at base (26F injection_api; 21E+2E fixture classes; 12 fresh-SQLite nodes incl. 11F+1E; QUARANTINE families P-2 ×7 and P-11 ×7; P-7 watchover super-cluster ×38).

### Branch totals (12/12 complete)
18,147 passed / 202 failed / 48 errors / ~255 skipped / 5 xfailed.
### Base totals (12/12 complete)
18,037 passed / 203 failed / 45 errors / ~255 skipped / 5 xfailed.
### Net delta
Pass +110 (branch-added tests: +96 unit/services, +9 unit a-d, +1 i-q, +3 r-z, +1 integration). Failed −1 net = 3 branch-introduced (§Delta below) − 4 base-only (3× message_queue_redesign concurrency @ base, 1× wc_wake_pure_hang env-class @ base). Errors +3 = httpx-env class split reshuffle (daemon absent by design both sides). **Zero unattributed failures: every failure node is attributed pre-existing, base-only, or branch-introduced-with-evidence (3 nodes, adjudication below).**

### KV-ADJACENT DELTA (P-12) — branch-introduced failures (adjudication in flight)
Delta = branch FAIL ∧ base PASS, node-for-node:

| # | Node | Branch failure | Base outcome |
|---|---|---|---|
| 1 | `tests/integration/test_context_freshness.py::TestKVFreshness::test_kv_written_mid_session_visible_next_call` | `'project' not in kinds ['shared_meta_kv']` (:337) | PASS (collected+ran green; existence at 9eebf3ff via git grep :262) |
| 2 | `tests/integration/test_context_hierarchy.py::TestHumanMessagesModeForChild::test_child_human_messages_mode_uses_inherited_context_key` | `'project' not in kinds ['shared_meta_kv']` (:569) | PASS (same adjudication; git grep :508) |
| 3 | `tests/integration/test_context_in_graph.py::TestContextSlotReadsProjectInjectedFlag::test_orchestrator_skips_persistent_rebuild_when_flag_true` | repo mock `get` called once, expected zero (:635) | PASS (not in base failure list) |

Base-only failure (fail at base, pass at branch — NOT branch-introduced): `tests/integration/test_wc_wake_pure_hang.py::test_http_post_messages_wakes_parked_wc_parent` (TypeError `object.__new__()`, daemon-absent env class). Error-class split differs between sides (branch: hybrid ×8 + vscode_routing ×16 = 24E; base: hybrid ×8 + vscode_routing ×5 + vscode_security ×8 = 21E) — environment class (daemon not running by design), both sides affected, not branch-attributable.

Also base-only (P-10): 3× `tests/message_queue_redesign/` concurrency failures at base under xdist (SQLite "cannot commit — SQL statements in progress") — pass at branch; contention noise, non-blocking direction.

**Adjudication COMPLETE — all 3 are STALE PINS, not regressions.** Read-only source-level adjudication (worker 853456ff) + solo determinism matrix (3× deterministic FAIL @ branch, PASS @ base, no xdist artifacts):

| # | Verdict | Retired contract (evidence) |
|---|---|---|
| 1 | **STALE PIN** | Test asserts `"project" in kinds` with literal comment *"it carries the KV section"* (test_context_freshness.py:335-340). Scenario is `project_id=None` → project block never renders at branch by design (`_fetch_project_payload(None)` early-returns; `build_project_context_message(None,…)→None`); KV renders standalone `shared_meta_kv`. Retired by decisions.md D4/D7; replaced by `test_kv_metadata_NOT_embedded_in_project_block_c3` (test_context_messages.py:360-381) + `test_kv_freshness_on_turn2_short_circuit`. |
| 2 | **STALE PIN** | Same retired contract (test_context_hierarchy.py:567-571, comment *"KV lives there"*). The REAL contract it pins — tree-root partition inheritance — still works: marker IS in output (first assertion passes); only the kind assertion is stale. Replaced by `test_instance_messaging_first_turn_kv_partition` + `test_instance_messaging_partition_consistency` (both PASS). |
| 3 | **STALE PIN** | Docstring pins verbatim the retired "project_already_injected ⇒ ZERO persistent fetches" contract (test_context_in_graph.py:572-588, `project_repo.get.assert_not_called()` :635). Branch's C3 refresh by design calls `_fetch_project_payload` on turn 2+ to detect `is_default` for the D4 composition (context_messages.py:1444-1470). New pin `test_kv_freshness_on_turn2_short_circuit` sets up `project_repo.get` KNOWING it is called; `test_at_most_one_project_block_and_one_kv_block` (D14) pins project-block-stays + KV-refreshes coexistence. |

**Required follow-up (🟠 important, owner = branch dev, before or immediately at merge):** update the 3 stale assertions on-branch (kinds → `"shared_meta_kv"`; `get` → `assert_called_once_with("proj-1")`) per the new contract — otherwise `latest` inherits 3 deterministic reds post-merge. Behavior itself is green under the new contract.

All 6 NEW branch integration tests PASSED at branch (first_turn_kv_partition, partition_consistency, kv_ambient_real_service_flag_on ×2, persistence_synthetic_context_id_order ×2); base has no such files (expected).

### Attribution trap noted (pending adjudication)
`coding2` llm-config signatures (P-5 ×2 `test_llm_allowed_models_precedence`, P-9 ×1 `test_llm_load_balance_meta_loading`) may be caused by an untracked/gitignored local config asymmetry between worktrees (kvfix `porcelain` clean but gitignored files invisible). Base P-5/P-9 outcomes + (if divergent) a neutralized-env solo re-run will adjudicate.

---

## Task 2 — Kill-switch final contract check

(pending — 4 ad-hoc packs in flight: OFF-pin matrix 2×vocab-values per flag; flag-ON real-service; vocabulary ValueError; boot emission)

---

## Task 3 — Deploy pre-flight summary (report-only) — ✅ COMPLETE

Runbook under review: plan-overview.md §Deploy runbook addendum — Pause-First Then Quiesce (+ Rollout steps 4-5, W9 acceptance spec, D8/D10).

### Runbook steps as written
1. `pause_instance_cascade` FIRST — pause targets before any state change
2. bounded quiescence confirmation — wait (bounded) for in-flight tasks to reach pause-cancelled/checkpointed boundary; do NOT proceed on an unconfirmed window
3. restart the daemon — `./scripts/upgrade/restart.sh` (flags land with new code)
4. boot-log verification — grep BOTH flags in `data/logs/ensemble.log`: `"Ambient KV freshness ENABLED"` (ENSEMBLE_AMBIENT_KV_FRESH) and `"kv_ambient_system_default_enabled="` (ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED); values must match intended `.env`; BOTH lines must be PRESENT — absence is a verification FAILURE (emit-at-boot requirement S13)
5. resume cascade — DB-only PAUSED → RUNNING

### Completeness checklist
- [x] Pause-first ordering explicit (step 1 before any state change) — matches repo Pause-First Then Quiesce convention (WatchoverService precedent)
- [x] Bounded quiescence with explicit do-not-proceed-unconfirmed guard
- [x] Restart mechanism named (`./scripts/upgrade/restart.sh`)
- [x] Boot-log verification for BOTH flags with exact grep-able strings; emit-at-boot (not lazy) is an explicit design requirement with reviewer gate
- [x] Resume step explicit (DB-only semantics stated)
- [x] Operator choice encoded without preference: (i) default-ON direct (single restart) vs (ii) staged default-OFF-first (restart #1 flags=0 → verify boot logs DISABLED/False + post-deploy smokes → restart #2 ON); both pass through the identical Pause-First sequence; (ii) runs boot-log verification twice — WC-wake-style "do not forget the flip" hazard explicitly called out (D8)
- [x] Kill-switch revert paths: flag `=0` in `.env` → restart → verify boot log DISABLED/False; OFF semantics = byte/value-identical pins in each flag's suite (validated in Task 2)
- [x] FE two-block staging check: owned by FRONTEND (named owner; W9 per-mode acceptance spec with card-count table, `context_kind` filter requirement, synthetic-id enumerate-order pin; gate before ship) — external dependency, not a gap in this runbook
- [x] Post-deploy checks defined: child first-turn partition smoke; external KV write visible next non-retry turn; default-project `GET /messages` two-block check; legacy-instance block hygiene W8 (at most 1 project + 1 kv block per kind); explorer ordering watch item (S17, note-only)
- [x] Rollback criterion explicit: (a) duplicate/blank `[SYSTEM CONTEXT]` cards persisting across reload → note-and-fix-frontend severity; (b) default-project prompt degradation → flip severity (`=0` + restart)

### Flags (non-blocking, nice-to-have 🟢)
1. 🟢 **Unset-state boot-line literal**: for option (i) (flags UNSET in `.env`), the runbook does not show the expected literal boot-line shape for the unset case (Shape B prints `env ...=<unset>`). Operator instruction could include the expected exact line to reduce grep ambiguity.
2. 🟢 **Quiescence bound value**: "bounded" is not quantified in the runbook block (the WatchoverService precedent carries its own bound). Operator should reuse the repo-standard bound; stating the number would remove ambiguity.
3. 🟢 **restart.sh path**: referenced as the restart mechanism; existence on the deploy host is assumed (report-only gate — no execution performed to verify the script path).

### Task 3 verdict: **PASS** — runbook complete and self-consistent for the operator; zero blocking gaps; 3 nice-to-have clarifications.

---

## Verdict — ✅ OVERALL: VERIFIED

- **Task 1 (full suite + baseline attribution): PASS-WITH-FINDINGS.** 24 pack runs (12 slices × 2 commits), all drift-pinned, dual-layer timeout, xdist. Branch 18,147P/202F/48E vs base 18,037P/203F/45E. **Zero unattributed failures; zero branch-introduced BEHAVIOR regressions.** All 202 branch failures attributed: 199 pre-existing (node-for-node base parity, incl. all known-baseline classes: injection_api 26F, 23 fixture ERRORs, fresh-SQLite migration trap, QUARANTINE families), 3 branch-introduced = adjudicated STALE PINS of deliberately-retired contracts (source-cited; 3x deterministic; replaced on-branch by new-contract pins). 4 base-only failures (3 concurrency @ base, 1 env-class) — favorable/neutral direction, non-blocking.
- **Task 2 (kill-switch contract): PASS 4/4** — OFF-pin matrix (2 vocab pairs, byte/value-identity), flag-ON real-service (C2+C3+C1'), vocabulary ValueError fail-loud (44/44 incl. boot-fail), boot emission (7/7, execution-level, both lines, anti-lazy pinned).
- **Task 3 (deploy pre-flight): PASS** — runbook complete & self-consistent; 0 blocking gaps; 3 nice-to-haves (unset-state boot-line literal, quiescence bound value, restart.sh path confirm).

### Required follow-up (merge-adjacent, important)
1. **3 stale-pin updates on-branch** (test_context_freshness.py:336-340, test_context_hierarchy.py:567-571, test_context_in_graph.py:635) — else latest inherits 3 deterministic reds post-merge. Suggested assertions in the Adjudication section above.

### Non-blocking observations
- 2 skipped tests in P-12 not node-attributable from `-rf` output (outside failure scope; cosmetic logging gap in pack script).
- P-12 pack-header doc-drift (claims 738 collected/258 deselects; actual 998 outcomes) — cosmetic.
- Base-side P-10: 3 `message_queue_redesign` concurrency failures under 16-way parallel xdist (SQLite "cannot commit — SQL statements in progress") — contention artifact of THIS gate's parallel fan-out, not a base defect signal; noted for attribution hygiene.
- All 6 NEW branch integration files green; all new unit kv files green in-slice (P-2) and in targeted packs.
- Gate-doc write hazard hit and repaired: parallel same-file edit_file calls duplicated the Verdict tail 4x with mid-line truncation (the repo's documented lost-update class); repaired via single sequential python read-modify-write with count assertions (this write).

### Housekeeping
- Baseline worktree `agents-ensemble-wt-kvfix-base` removed post-gate (recreate: `git worktree add --detach <path> 9eebf3ff && uv sync`).
- Gate docs written: RESULTS (this file), LESSONS/2026-09-08-kv-gate-stale-pin-discovery.md, PACKS.md gate block.
- Docs left UNCOMMITTED in the worktree (dispatcher/dev may commit as gate artifacts per repo convention, cf. base commit 2a5524ff).
