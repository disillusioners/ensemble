# TESTER GATE — v0.16.6 FIX COMMISSION (promote busy-daemon fix + 3-layer adoption protocol)

**Branch:** `feature/promote-quiesce-adoption-protocol` @ **`ec89bd85`** + ONE tester test-only gap-close **`3de1621c`** (adopt-unit suite layer-2 watchdog; diff vs ec89bd85 = `tests/test_adopt_unit.sh` ONLY)
**Base:** `792ff304` · **Date:** 2026-09-30 · **Dispatcher:** tester (this instance) · **Workers:** 21 instances, 24 todo nodes
**Deliverable contract:** per-pack P/F counts, pre-existing A/B isolation table, mock-reality verification notes, escalations (none)

---

## VERDICT: **GREEN — PROCEED TO FINALIZE**

Zero branch-caused test failures across the full gate set. The decisive acceptance e2e is green with a
proven non-tautology control witness. Every deviation from commission-cited baselines was attributed
byte-exactly (A/B base leg) or matched documented pre-existing sets. All pre-existing reds isolated and named.

### Reconciliation items for the leader (facts the gate record must carry)
1. **Commit count is 20, not 19** (`git rev-list --count 792ff304..ec89bd85` = 20; full list in W0 report — first commit `f66bf72c` likely the miscounted one).
2. **The cited base baseline "308P/19F" for test_release_journal.sh does NOT reproduce at 792ff304.** Measured ground truth: **274P/14F at base; 313P/14F/2S at branch** — the 14F set is **byte-identical** across both legs (one name embeds a dynamic epoch value; identical modulo that value). The 19F figure appears transposed from the supervision-gate era (base `b56a1a7e`). Implication: the branch **fixed zero, broke zero** of the pre-existing set and **adds ~41 passing tests**.
3. **`assert_not_contains` suite defect is INHERITED, not branch-caused** (missing at base too): `tests/test_release_journal.sh:203` calls a helper defined nowhere → one F2-gate assertion silently uncounted (neither P nor F) in every leg. → separate suite-fix commission recommended.
4. **Gate tree includes tester commit `3de1621c`** (test-only; precedent = supervision gate gap-close `94616384`).

---

## 1. Pack results (all HEAD-verified; dual-layer timeouts; env-poison-fenced)

| # | Pack | Result | Counts | Runtime | Notes |
|---|------|--------|--------|---------|-------|
| P1 | **ACCEPTANCE E2E** `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py` | ✅ PASS | 2/2 | 2.0s | Decisive gate. Tautology audit clean (§3) |
| P2 | `test_boot_report_recovery.py::TestBootSmokeRegression` | ✅ PASS | 3/3 | 8s | ⚠️ Coverage finding: no epoch-ordering pins (§6 F1) |
| P3 | **REAL dev-lane boot smoke** (port 8079, hardened wrapper) | ✅ PASS | all checks | 73s boot cycle | readyz `detail.inflight_turns: 0` (int); epoch proven (§4); 2 disclosures (§5) |
| P4 | `tests/test_stop_ownership.sh` | ✅ PASS | 43/43 | 157s | standing gate |
| P5 | `tests/test_supervision_stop_handback.sh` | ✅ PASS | 116/116 | 140s | standing gate |
| P6 | `tests/test_release_journal.sh` | ✅ PASS (0 new reds) | 313P/14F/2S | 267s | names byte-identical to base (§2); §16d arms passed in-suite |
| P7 | `tests/unit/services/test_boot_epoch.py` | ✅ PASS | 12/12 | 0.35s | mock-reality: real engines/SQL (§3) |
| P8 | `tests/test_health_probes.py` | ✅ PASS | 56/56 | 9.7s | 4 new HTTP-shape tests confirmed on real ASGI handler |
| P9 | `tests/unit/services/` FULL DIR | ✅ PASS (0 new reds) | 2164P/15F/0S | 205s | **exact baseline**; all 15 names = documented families |
| P10 | `tests/message_queue_redesign/` FULL DIR | ✅ PASS (0 new reds) | 465P/2F/13S | 48s | 2F verbatim-match documented pre-existing asset; flake not manifested; +1P = branch's new test |
| P11 | `tests/postgres/` readiness pair (real PG, throwaway :16930) | ✅ PASS | 10/10 (7+3) | 2.8s | epoch-bound SQL proven on real PG (3 named tests) |
| P1b | `tests/e2e/` FULL DIR (leg 2, documented bypass) | ✅ PASS (0 new reds) | 14P/4F/51S (+1E bypassed) | 14s | amnesty 2/2 in-dir; 4F count = baseline; all in pre-branch files |
| P12 | **§16d TERM drill** (standalone, real signal/lock) | ✅ PASS | 4/4 + neg-sanity | 15s | exit **143** + lock released + halt journaled (§4) |
| P13 | **stage.sh rider drill** (sandbox, stubbed builds) | ✅ PASS | 3/3 | ~13s | uv rider / staged_at / checksums (§4) |
| P14 | **mock-reality audit** (promote.sh parser vs real emitter) | ✅ PASS contract | 5 adversarial + 1 real-shape | — | non-blocking in every case; 4 latent gaps (§6 F3) |
| P15 | `test/packs/concurrency_atomic_unit_test.sh` (ensure.md Core) | ✅ PASS | 98P/0F/74S | 67s | baseline-exact |
| P16 | `tests/test_adopt_unit.sh` (coverage-gap close, added by tester) | ✅ PASS | 58/58 | 79s | quick-fix commit `3de1621c` (layer-2 watchdog) |
| P17 | `tests/test_supervision_e2e.sh` (added by tester) | ✅ PASS | 52/52 | 74s | LEGITIMATE healthy-host shape — fence did not trip; all 4 real-seat legs executed |
| P18 | `tests/test_supervision_journal.sh` (added by tester) | ✅ PASS | 39/39 | 30s | exact |
| P19 | `tests/test_supervision_twins.sh` (added by tester) | ✅ PASS | 84/84 | 22s | lib.sh↔python twin parity intact under branch's lib.sh edits |
| B6 | release_journal BASE leg @792ff304 (detached worktree) | ✅ attribution | 274P/14F | 215s | byte-compare source (§2); worktree removed cleanly |
| W0 | inventory + `bash -n` ×5 scripts | ✅ PASS | 5/5 syntax OK | — | changed-file/lanes/existence/§16d location |

**Totals:** 21 packs/drills, **0 TIMEOUT, 0 new branch-caused failures.**

## 2. Pre-existing A/B isolation table

| Component | Base 792ff304 | Branch ec89bd85 | Attribution | Basis |
|---|---|---|---|---|
| test_release_journal.sh | **274P/14F** | **313P/14F/2S** | **14/14 byte-identical set**; branch +41 tests all pass; 0 new / 0 fixed | B6 base leg (detached worktree, solo, fence-clean) — commission's 308P/19F NOT reproducible at base |
| tests/unit/services/ | (baseline 2164P/15F) | **2164P/15F EXACT** | exact count + all 15 names cluster-match QUARANTINE/PACKS families (proxy_phase1 ×7, b1_wc ×2, compaction ×3, context_injection ×1, service_tool ×1) | exact-match ⇒ B9 base leg **skipped as redundant** (blast-radius discipline) |
| tests/message_queue_redesign/ | (baseline 464P/2F+1 flake) | **465P/2F/13S** | 2F verbatim-match documented asset `RESULTS/assets/2026-09-27-maintenance-console/delta/pre-existing.txt:39-40` (test_usage_limit_worker_seam ×2); flake not manifested; +1P = branch's own new test | documented-name match ⇒ B10 base leg **skipped as redundant** |
| tests/e2e/ dir | (baseline 4F+1E pre-dev-A, commission-declared) | 4F/0E (+1E bypassed) | 4F all in files predating the branch (mtime 2026-09-21; commits 46cbdb5f/4e82c8c9/e8ff8861); count matches; "state stuck non-terminal" pattern consistent with no-backend environment | commission's own pre-existing declaration + file-provenance; 1E = module-level HTTP import in `test_context_injection_hybrid.py:50` (collection-blocker without dev daemon) |
| P6 missing-helper deviation | `assert_not_contains` MISSING at base | MISSING at branch | **inherited suite defect** (call at :203, no definition anywhere in either tree) — one F2-gate assertion silently uncounted in every future leg | B6 grep evidence |

## 3. Mock-reality verification (gate item 7)

- **P1 e2e (tautology audit):** epoch = **faithful double** through the REAL SQL aggregate (`readiness.py:121-125` CASE arm; values actually change between tests: `now−125s` vs `now−1h`); heartbeat freeze = **real incident shape** (pinned DB value, no worker); **control witness PRESENT** — legacy no-epoch probe asserted `fresh is False` on the same 135s-stale beat vs 120s threshold (old code provably would have breached) + negative regression green. NOT a tautology.
- **P14 promote.sh parser (`_json_sub`/`_json_field`, lib.sh:247-307; consumer promote.sh:329-343):** fed the REAL parser 5 adversarial + 1 real-shape body — ready-int (advisory emitted, non-blocking), 503 `inflight_turns: null` (silent, tolerated), legacy absent-field (silent, tolerated), truncated JSON (graceful rc=1, non-blocking), braces-in-strings adversarial (silent truncation — non-blocking, latent GAP-1), no-composite-yet 503 (explicit-null tolerated). **No path blocks/crashes/wedges promote.** `pipeline_settled` no-journal → trivially settled rc=0 (2 sandbox variants). Python-side fixtures MATCH the real emitter; shell mocks in test_release_journal.sh diverge (top-level `reasons`, no `detail`) but degrade gracefully.
- **P7 boot-epoch units:** real in-memory engines; real `SELECT CURRENT_TIMESTAMP` bracketed by wall clock; real failure branch (genuine sqlite3.OperationalError through production except).
- **P8 HTTP shapes:** 4 new tests over the real ASGI handler pin int / int-0-null / 503-add-only / wire shape.
- **P11 epoch SQL on real PG:** 3 named tests prove `:boot_epoch`-bound SQL executed (all-frozen → `max_age NULL`; post-boot stale degrades; `inflight_turns == 1`).

## 4. Behavioral drills (gate items 5 & 6)

- **§16d (P12, independent):** real `kill -TERM` to stop-ensemble.sh mid-lock-acquire → **exit 143**, `rollback.lock.d` **released** by handler, journal halt event with phase `stop:lock` + ref `r-20260928-005506-f82e`; negative sanity: EXIT-only path exits **0** with empty history (rationale confirmed). Matches in-suite §16d arms (P6: all passed).
- **stage.sh rider (P13):** (1) no-uv → exit 78 + verbatim refusal/remedies; HOME-fallback used silently when PATH-uv absent (NOTE: divergence WARN fires only when BOTH resolve and byte-differ — verified with marker-proven staged binaries, PATH wins); (2) `staged_at` (manifest.json field) refreshed on re-stage (00:50:17Z → 00:52:01Z); (3) manifest byte-diff = exactly the one `staged_at` line; binary_sha256/launcher/config/agents-tree/frontend-tree + per-file manifests byte-identical; staged binary sha matches manifest both runs.

## 5. Incident disclosures (gate-process, not feature)

1. **Env-poison 3rd event (P3 boot smoke):** scrub-only wrapper INSUFFICIENT — `_auto_derive_env()` reads `~/agents-ensemble/.env` directly (bypasses process env) → first boot appended ONE `supervision_boot` advisory to LIVE `releases/state.json` (2026-09-30T00:30:46Z; `current/previous/in_flight/quarantined` untouched; left in place + disclosed — pruning is a second write). **Hardened + proven same drill: wrapper must EXPORT `ENSEMBLE_SELF_ENV=dev`** (explicit marker beats auto-derive). LESSONS: `LESSONS/2026-09-30-boot-smoke-env-poison-recurrence-unset-insufficient.md`. This falsifies the 2026-09-29 "unset is enough" remediation for the boot lane on multi-install hosts.
2. **Local PG auth drift recurrence (2nd in 2 days):** `ensemble` role password had drifted from `.env` `testpw`; re-carved identically (dev-local PG only, reversible). Owner should find what resets it. (P11 independently avoided system PG via throwaway initdb :16930 — cleaned fully.)
3. **Pre-existing host residue (not gate-caused):** 30 stale `ae-ownertest-C.*` procs + 3 empty `D.*` tmpdirs (2026-09-29); 17 stray `/tmp/upgrade-test.*` fixtures. Cleanup commission candidate.

## 6. Findings ledger (non-blocking; routed)

- **F1 🟠 (dev/test-debt):** `TestBootSmokeRegression` pins InstanceManager init-body only — NO epoch-capture ordering pins (api.py :399→:410→:439). Runtime proof exists (P3 log-timestamp table); needs a `TestBootOrder`-pattern source-order assert.
- **F2 🟠 (suite defect, inherited):** `assert_not_contains` undefined (test_release_journal.sh:203) — F2-gate assertion silently uncounted every leg. Separate commission.
- **F3 🟢 (hardening ledger, P14):** `_json_sub` lacks string-context awareness (braces-in-strings silently drop advisory); no shell-consumer e2e fixture for the M7 advisory path; dormant f-string watch-item; `_json_field` substring false-positive risk. Zero impact today.
- **F4 🟢 (test-debt):** `test_context_injection_hybrid.py` module-level HTTP import blocks whole-dir collection without a dev daemon — hoist resolution to test-time.
- **F5 🟢 (cosmetic):** daemon `__version__` still "0.16.5" on branch (expected pre-tag); P3 observed it in /livez.
- **F6 🟢 (commission-text):** P13 found my task's rider scenario-(b) parenthetical diverged from code (silent fallback vs WARN); code-correct behavior verified.

## 7. ensure.md status (Core, blast-radius scoped)

- Critical: changed packs PASS ✅ (all scoped packs green; 0 new reds) · concurrency pack 98P/0F/74S ✅ · thread-identity tests ✅ (same pack) · dev.sh `--timeout-graceful-shutdown 10` ✅ (dev.sh:99/102, W0 static).
- Important: await-callers + original deadlock scenario ✅ (concurrency pack).
- Release Gate (real-LLM E2E workflows): **NOT triggered** — W0 execution-lane intersection: classic dispatch/claim lane EMPTY (task_processor/message_job_handler/claim-paths untouched); intersection is readiness/recovery/boot lanes, whose e2e obligation (amnesty e2e) ran and passed. Scope reduction reported per rule.

## 8. Scope decisions & gaps

- **Added beyond commission:** P16–P19 (four suites pinning CHANGED scripts — full-dir-per-merge lesson; W0 flagged adopt-unit gap; runbook ruling acknowledged, dedicated suite run anyway, all green).
- **Skipped as redundant (documented):** B9/B10 base legs — exact-baseline/unit count + documented-name matches made base legs non-informative (blast-radius discipline; basis in §2). No `[incomplete]` nodes; no worker failed twice; no TESTER_CANT_OPTIMIZE escalations.
- All packs dual-layer timeout; all runs env-poison-fenced (zero-survivor echo-verified); 9797/7979/8088 untouched throughout; `~/agents-ensemble*` written only by the disclosed incident (§5.1).

## 9. Code changes by tester (committed)

- `3de1621c` — `test: adopt-unit suite — add layer-2 internal watchdog (240s self-interrupt, dual-layer timeout)` (15 lines, tests/test_adopt_unit.sh only; suite passed WITH the fix — tested state = committed state).

## 10. Documentation updated

- RESULTS/2026-09-29-v0166-fix-commission-gate.md (this file) · PACKS.md (commission pack table) · LESSONS/2026-09-30-boot-smoke-env-poison-recurrence-unset-insufficient.md · QUARANTINE.md unchanged (no new quarantines — all reds pre-existing/named).
