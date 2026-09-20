# LCA Advisory-Note Removal + A-Band Restore — MERGE GATE (2026-09-18)

**Branch:** `feature/lca-remove-advisory-note` @ `0a4fccb1` (base `858b1038`; delta = 3 commits: `6a695b8f` note removal, `1ad924d7` A-band restore via evaluation-time transcript scan, `0a4fccb1` pin tighten; 9 files +963/−1795; production surface = 3 daemon modules: `attestation_resolver_activation.py` +266/−22, `child_reports.py` +57/−339, `context_messages.py` +24/−58)
**Worktree:** `agents-ensemble-wt-lca-note-rm` (clean, drift-pinned 0a4fccb1 every session; main repo on foreign branch `feature/instance-tree-cross-project-children` — untouched, disclosed)

## VERDICT: ❌ FAIL — NOT READY (1 branch-caused regression; surgical 1-line fix path)

**The core feature is PROVEN — all 8 task jobs green except one collateral regression found by the delivery-plumbing family pack:**
- ✅ Evaluation-time A-band (child-lie deny→nudge→attest/revive→allow; D2 busy-descendants not suppressed) — live, judge-bundle-verified, base-proveable
- ✅ Same-turn window (both stamp writers; T+1 isolation) — live
- ✅ Zero note mint anywhere (static 0-MINT/0-DELIVERY + runtime census + daemon-stdout zero-hit)
- ✅ 17-pattern catalog byte-identical (blob + content sha256 `369342f5…`, triple-pinned)
- ✅ Legacy-checkpoint defense (old note activates; no re-mint; dual-source exactly-once)
- ✅ PG family 21/21; boot smoke enforce-default 39/0; ensure.md scoped 7/7
- ✅ Matrix 2258 executed: 0 unexplained reds; 1 foreign migration red (base-identical), 3 pre-existing (base-identical/documented), 1 flake family (retry-budget closed)
- ❌ **REGRESSION (blocker):** `6a695b8f` deleted the `from .context_messages import (…)` block wholesale; `_resolve_tree_root_id` had a SECOND caller in the same file (`child_reports.py:4319` in `_dispatch_post_commit_side_effects` — method body byte-identical base↔HEAD) → `NameError` silently caught (`:4324-4328`, DEBUG-only log) → lifecycle-hook `context_key` falls back to `instance_id` instead of tree-root/parent → `tests/unit/test_lifecycle_hook_completion.py` 3/13 red at HEAD, 13/13 green at base (file blob-identical both sides). Confirmed at DEBUG: `hook context_key resolution failed: name '_resolve_tree_root_id' is not defined`. Production impact: every child-completion lifecycle hook writes context under the child id, not the tree root (W7 contract broken). **Fix: restore `_resolve_tree_root_id` to the import block (1 line) + re-run delivery_1 pack + lifecycle file.** Not fixed by this gate (read-only on production).

Gate shape: 27 worker dispatches (1 discovery + 1 splitter + 12 pack runners incl. re-dispatch + 6 specialty lanes + 4 base-A/B + 1 bisect + probes), all `uv run python -m pytest` only, dual-layer timeouts on every pack, READ-ONLY on production (0 production code changes by the gate; test files/pack scripts only).

---

## Job 1 — Full attestation matrix @ 0a4fccb1: 2258 executed, 0 unexplained reds

Universe (whole-tree family per repo convention): Set A attestation family (75 files / 1217 collected) + Set B touched-module plumbing (`child_reports|context_messages` referencing, 65 files / 1071) − postgres (PG lane) − 2 foreign-lane untracked = **2258 pack-executable / 137 files / 10 packs**. Dev's "matrix 306" reconciled as their delta-scoped subset (~304 arithmetic: delta trio 110 + plumbing trio 148 + guards 37 + b4 9).

| # | Pack | Files/Tests | Result | Runtime |
|---|---|---|---|---|
| 1 | lcan_matrix_1_unit_a | 21/489 | **PASS 489/489** | 3.10s |
| 2 | lcan_matrix_2_unit_b | 18/473 | **PASS 473/473** | 5.82s |
| 3 | lcan_matrix_3_migration | 1/18 | **EXPECTED-MATCH** — exactly the 1 documented foreign red, 17P | 0.24s |
| 4 | lcan_matrix_4_intf_fast | 6/13 | **PASS 13/13** | 175.2s |
| 5 | lcan_matrix_5_intf_mid | 4/8 | **PASS 8/8** | 209.3s |
| 6 | lcan_matrix_6_intf_s1 (real judge) | 2/4 | **PASS 4/4** | 182.2s |
| 7 | lcan_matrix_7_intf_s2 | 6/121 | **PASS 121/121** | 144.8s |
| 8 | lcan_matrix_8_intf_slow | 16/79 | **PASS 68P/2S/9desel** (=79) | 4.29s |
| 9 | lcan_matrix_delivery_1 | 21/497 | **FAIL 4** → 3 BRANCH-CAUSED (blocker, see §Regression) + 1 pre-existing (phase4, documented family) | 94.7s |
| 10 | lcan_matrix_delivery_2 | 42/556 | **FAIL 2** → both PRE-EXISTING base-identical | 14.2s |

**Hang characterization (dev's "13 base-hangs" claim):** all 13 proxy files probed at HEAD under proper per-test timeouts — **13/13 PASS, ZERO hangs** (3 needed the 150s tier: idle_orphan 123s, revive 78s, live_descendants 115s). They are invocation-shape artifacts, not hangs. Base sample (3 files): revive + live_descendants pass↔pass; idle_orphan `test_b08f40fe…` FAIL→(retry budget at base: 1F/2P)→**FLAKY** (live-judge verdict-variance class, same family as the stage3-quarantined `revive_after_escalation` flake). "13 hangs" final classification: 12 invocation-shape + 1 flake — none branch-caused.

**The 1 foreign red:** `tests/migration/test_attestation_migration.py::…test_no_boolean_int_literal_default` (offender `20260915_120000_critical_notes_lifecycle.sql 'BOOLEAN NOT NULL DEFAULT 0'`) — **base-identical at 858b1038** (same node, same offender string, verbatim). Foreign critical-notes lineage; OPEN owner follow-up.

**Pack-10 reds (2):** `tests/job_queue/test_in_progress_guard.py::{test_completed_with_no_waiting_runs_normal_path, test_waiting_for_none_treated_as_zero}` — **base-identical** (byte-equal signature: `assert <MagicMock per_kind_status_for()> == 'completed'` + 3 await-warnings at `job_feedback_observer.py:2957/3012/4426`). Awaited-Mock test-debt class → QUARANTINE rows.

## Job 2 — Child-lie E2E (THE acceptance): PASS 3/3 ×2 runs — independently constructed

`tests/integration/test_lcan_childlie_e2e.py` (spec-driven; dev's feature tests unread; real compiled graph, judge captured at the module seam):
- **S1 deny→attest-allow:** child report seeded in production drain shape (`source="internal_report:<uuid>"`); leader relays promise genuinely → eval-time A-band fires (`fired=True band=deny a_advisory_present=True a_notes=1 a_kwargs_seen=True marker_hit=False length_trigger=False`), judge ×1 sees A-section evidence (`terms=ending turn,will write` excerpt in bundle), **deny+nudge** (counter 0→1, stable-id nudge, graph NOT ended) → attest → `bypass_reason=meta_bypass`, judge ×0, **ledger counter resets 1→0**, graph ENDS.
- **S2 revive variant:** deny+nudge → leader revives child → real completion → judge verdict=complete → **rescue allow** (graph ends, no attestation, counter untouched).
- **S3 D2 row:** busy descendant present (`busy_descendants=1 live_descendants=1`) → A-band STILL fires (`terms_fired=a_suspicion`, judge exactly-once) → allow_hint + durable completion-check hint. **NOT busy-suppressed.**
- Globals: zero `context_kind=child_report_check` in channels/queue; message_queue 0 rows; relay B-marker-free (≥150 words).
- **Base-proof @ 858b1038: all 3 FAIL exactly on A-band assertions** (base bundle `a_advisory_present=False a_notes=0`; S3 `judge.bundles==0`, never fires on busy tree) — test detects the delta.

## Job 3 — Same-turn window LIVE: PASS 4/4 ×3 runs

`tests/integration/test_lcan_sameturn_window.py`: **S1** live-drain writer (full graph.ainvoke, `graph.py:~6960` stamp) — report drained at turn-T start visible to gate at turn-T end (`a_notes=1 terms=c_quiet,a_suspicion`, full deny→attest→allow cycle, judge exactly 2 = budget). **S2** fallback enqueue writer (`instance_messaging.py:525` stamp shape) — identical activation, `kwargs_surface_seen=True`, judge ×1. **S3** T+1 isolation — T eval: `a_notes=0` (c_quiet only); T+1 eval: `a_notes=1`; row-level fingerprints distinct; `a_suspicion` absent at T, present at T+1. Globals: zero mint; catalog 17. **Base-proof: 3/3 fail at base** on the same assertions. (S3 resolver-level with documented justification: LangGraph per-thread drain-slot semantics; S1/S2 cover full-graph writers.)

## Job 4 — No-note verification: GREEN

Static sweep (33 sites classified, full-sentence reads): **0 MINT, 0 DELIVERY**, 5 intentional legacy-READ keeps (`attestation_resolver_activation.py:459-484,627-660` + enum), 28 comment/enum/import sites. `internal_report:` stamp writers = delivery-agnostic source-stamps on HumanMessages/queue rows + routing filters — zero context-kind collateral. Catalog: **byte-identical base↔head↔worktree** (git blob `16b9d4a5…`, sha256 `369342f5…`, `git diff` empty). Runtime census `tests/unit/test_lcan_nonote_census.py` (12 tests: catalog pin, byte-pin, no-mint invariants, legacy detector dual-surface) + delta-touched contracts — pack `lcan_nonote_unit_test.sh` **47/47 ×2 runs (<1s)**.

## Job 5 — Legacy-checkpoint defense: PASS (4× runs)

`tests/integration/test_lcan_legacy_checkpoint.py` (993 lines; pre-removal note shape derived from `git show 858b1038:child_reports.py` mint block): **S1** old note (exact pre-removal shape, stable-id + kwargs surface) → A-band activates, deny+nudge, counter+1, attest→allow. **S2 no-re-mint:** legacy note read once, zero new notes, nudge body carries no "Child Report Check". **S3 mixed:** legacy + fresh report → 2 evidence rows, single trigger, **judge exactly-once** (counter-wrapped pin). Base characterization: S1/S2 identical at base; S3 sees 1 row at base vs 2 at HEAD — the dual-source coverage IS the upgrade contribution (expected delta signature).

## Job 6 — PG family: PASS 21/21

`lca3_pg_attestation_integration_test.sh` @ disposable PG14 :15433 (self-contained; prod env scrubbed by pack): **21 passed in 3.32s**, 7.1s wall. Teardown verified: no listener :15433, datadir removed, 0 residual PIDs (all visible postgres PIDs foreign: homebrew :5432, ensemble_prod, unrelated user DBs). `tests/postgres/` contains exactly 1 attestation file — no coverage gap.

## Job 7 — Boot smoke: PASS 39/0 in 14s

`lcan_boot_smoke_test.sh` (new; disposable uvicorn :15800, PG :15810, mock-LLM :15820 — all >10000, lsof-verified free): enforce-default boot (all 5 `ENSEMBLE_LEADER_ATTESTATION_*` unset → `mode=enforce attestation_enabled=true`), 0 Traceback/CRITICAL/ValueError; **child-lie through the REAL fused path**: `JUDGE#1→not_complete` (deny+nudge, `a_advisory_present=True a_notes=1`) → attest-path → `judge_verdict=complete resolver_outcome=allow` → instance `completed`; **zero "Child Report Check" / CONTEXT_KIND_CHILD_REPORT_CHECK occurrences in entire daemon stdout**; clean SIGTERM, ports freed, 0 orphans. (2 disclosed test-code fixes: mock role-detection widened for the renamed `developer` agent; order-independent eval-row assertions.)

## ensure.md (scoped): 7/7 PASS — all with verbatim evidence

Delta-touched 3-file pack 110/110 ×2 (0.56s) · `concurrency_atomic_unit_test` **RESULT: PASS** 98P/74S/0F (7.61s) · thread-identity 27/27 · dev.sh `--timeout-graceful-shutdown 10` (lines 99+102) · async-await static: 6 hits all docstring/log-text, 0 bare call sites · flag-policy: `git diff 858b1038..0a4fccb1 -- daemon/ | grep -E "^\+.*(environ|getenv)"` EMPTY · dead-code: 29 `child_report_check` refs all classified intentional; 0 orphan `_stable_id_for` callers. **0 contradictions, 0 Improvement Notices.**

## The regression (blocker) — full anatomy

- **Bisect:** 858b1038 13/13 PASS → **6a695b8f 10/13 (3 red)** → 1ad924d7 10/13 → 0a4fccb1 10/13. Breaking commit = `6a695b8f` alone.
- **Mechanism:** deleted import block `from .context_messages import (CONTEXT_KIND_CHILD_REPORT_CHECK, _make_context_message, _resolve_tree_root_id, _stable_id_for)` (base `child_reports.py:42-46`) — "by-package" removal assuming all four names mint-side-only. **Wrong for `_resolve_tree_root_id`: second caller at HEAD `:4319`** inside `_dispatch_post_commit_side_effects` (method body diff-zero vs base). NameError at `:4319` → caught `:4324` → DEBUG-only log `:4326` → **silent fallback `context_key = instance_id` `:4328`**.
- **Symptoms:** hook context written under `child-001` instead of tree-root (`root-x`/`root-abc`) or parent (`parent-001`); context dirs created at wrong path. Tests: `test_lifecycle_hook_completion.py::{TestOutcomeGating::test_hook_dispatched_for_regular_child_completed :168, TestContextKeyFallback::test_tree_root_id_is_used :306, TestContextKeyFallback::test_resolve_returns_none_falls_back_to_parent_id :346}`.
- **Not a contract change:** test file blob `29d3a11b…` identical base↔HEAD; D-CTD-7/D-CTD-8 document no lifecycle-hook contract change; the dev's own comment at HEAD `:48` ("still used by an unrelated path") misidentifies the caller — the only production caller outside `context_messages.py` is this file's dispatch.
- **Scope note:** the dev's 306-run never included this file (Set B delivery family) — exactly the coverage the whole-tree family discipline exists for.
- **Fix (dev's, 1 line):** re-add `_resolve_tree_root_id` to `child_reports.py` imports; then re-run `lcan_matrix_delivery_1_test.sh` + the file solo; expect 497/497 and 13/13. (`_make_context_message`/`_stable_id_for`/`CONTEXT_KIND…` stay importable where still needed — verified no other collateral callers by the no-note sweep.)

## Scope Decision
Full attestation family + touched-module delivery plumbing (Set A 75 files + Set B 65) — warranted: merge gate on gate-critical machinery with a −339-LoC production deletion. Not run: non-family repo suites (out of blast radius). Boundary ruling: `test_attestation_wakeups_helper.py` (filename-only match) sanity-run by finalizer.

## Gaps / Notes
- 🔴 Blocker: the `6a695b8f` import-block regression (above) — gate FAIL until fixed + delivery_1 re-run.
- 🟠 Foreign migration red (`20260915_120000_critical_notes_lifecycle.sql`) OPEN at base and HEAD — critical-notes lineage owner.
- 🟢 Quarantine additions: `test_in_progress_guard` ×2 (base-identical awaited-Mock debt), `test_b08f40fe_idle_orphan…` (judge-verdict flake family, 2P/1F at base).
- 🟢 Dev-run metadata drift: "306 matrix" = delta-scoped subset (whole-tree = 2258); "13 base-hangs" = 12 invocation-shape + 1 flake, zero real hangs at HEAD.
- 🟢 Live anecdote ×2: the PROD delivery-time advisory (pre-removal daemon) fired false-positive Child Report Checks on THIS gate's own worker reports (matched "Awaiting issue?" table header; matched the test fixture string "I will write RESULTS. Ending turn" quoted as evidence) — content-level adjudication cleared both. Exactly the noise class this removal eliminates.
- 🟢 Probe discipline: `-m integration` on unmarked files deselects 100% (vacuous probe) — flag dropped with manifest evidence; boot-smoke mock needed role-key widening for the renamed developer agent.

### Overall Status
- Matrix: ✅ (0 unexplained reds) · E2E: ✅ · Same-turn: ✅ · No-note: ✅ · Legacy: ✅ · PG: ✅ · Boot: ✅ · ensure.md: ✅ 7/7
- **Regression: ❌ 1 branch-caused (6a695b8f `_resolve_tree_root_id` import collateral)**
- **Testing Complete: ❌ NOT READY — apply the 1-line import fix, re-run delivery_1 + lifecycle file, then re-gate (fast: everything else already green)**

*Evidence commit: `6ca25181cbd9a1e913426dbf9ff60d70b67406cd` (short `6ca25181`, parent `0a4fccb1`, on `feature/lca-remove-advisory-note`, NOT pushed) — 23 files +5618/−0, ONLY tests/ + test/packs/ + .agents/tester/ paths; `git diff 0a4fccb1..HEAD --stat` verified; daemon/frontend/docs untouched. Boundary sanity: `test_attestation_wakeups_helper.py` 11/11 PASS in 0.59s.*
