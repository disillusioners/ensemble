# Test Report: TOOL-PAIRING FULL-HISTORY HEAL — Official Tester Gates

Date: 2026-10-08 (18:16Z gate open → 19:2xZ certified close)
Branch: `fix/tool-pairing-full-history-heal` @ `86c1bc041` (commissioned tip) + 2 test-lane commits (`11aec6d0` packs/docs registration, `a597fe6a` G3 mock) → tested HEAD `a597fe6ae`. Base: `9be991d56` (v0.18.3 tip). Worktree: `/home/nea/ensemble-src-wt-pairing-heal` (Python 3.14.7, daemon-import fence verified; 3.13 rot not applicable).
Incident class: 03d7657f — 2013-bricking via mid-list unanswered tool_call (dup tool_call_id from 10-02 regen/failover) under strict-provider routing.
Worker instances: 92131d86 (discovery), ab6f400d (pack-creator), b1b8a0af (G3 mock author), 55011756/cb101f71 (tph44 ✝), fbf20aa2 (d1_seam), 020a5aeb (graph_retry), 48b7f088/c4486423 (injection), 6dd1ea6e (compaction), 5dbbae18 (llm_error_classifier), 51d5092c (instance_tools), 03c449af (dev_sh_static), 49787f30 (jq_full), 0b1d9463 (A/B base leg), 47414032 (boot smoke), ae32db7f (concurrency_atomic), 6d529781 (G3 re-chart). ✝ = rate-limit deaths.

## Summary

- **Executed: 13 packs, 2,793 tests + 1 static check — 2,793 passed-equivalent, 0 branch-caused failures, 0 unattributed REDs.**
- Developer cross-module suites: **5/5 GREEN** (graph_retry 19, compaction 130, d1_seam 9, injection 30, instance_tools 207).
- Broader net: llm_error_classifier 125 ✅ · job_queue_full 2046 (16 RED → **A/B base-attributed 16/16 pre-existing**) · concurrency_atomic 99 ✅ (Core #2/#3) · dev_sh_static ✅ (Core #4) · boot smoke 21/0 ✅ (Core #2/#3 insulation).
- G3 original-symptom closure: **PASS 4/4 ×2** (author + independent re-chart) · **tph44 branch suite: PASS 44/44** · **G2/G4/G5 pins: PASS 5/5 ×2** (leader-authorized gap closures).
- ensure.md: Core #1 ✅ (no regressions in executed change-set packs) · Core #2/#3 ✅ (concurrency_atomic 99/0/74) · Core #4 ✅ (dev.sh:142). Release-Gate E2E: not in scope for a branch gate (LLM-cost lane; promote-time).
- Quick fixes: 1 (boot-smoke pack, +3/−2, TEST infra only). Quarantined: 0 new.

## Verdict: ✅ READY (CERTIFIED) — all commissioned gate items closed; zero branch-caused failures; sole RED base-attributed

**Certification addendum (19:2xZ, leader-authorized gap dispatches):** tph44 executed — **PASS 44/44 (0.47s)** (36 full-history-heal + 8 W2-wiring, first clean attempt). G2/G4/G5 pins — **PASS 5/5 ×2** (author 0.32s + independent 0.31s; provenance verified: the pins dir was committed by `6455b66c2` — the rate-limit-killed authoring worker had completed+committed its work before its report died; pack wrapper + registration landed as `fb5ed07e5`; both test-lane only). Final chain: `86c1bc041 → 6455b66c2 → 11aec6d01 → a597fe6ae → 2ac61dd32 → fb5ed07e5` — `git diff 86c1bc041..fb5ed07e5 -- daemon/ scripts/ migrations/` = EMPTY.

Prior provisional state (PASS-WITH-GAPS) is superseded; the gaps section below is retained for the record.

### Scope Decision

Full gate per commission (big/critical: daemon/graph.py +538 lines, new 829-line module, core dispatch path). No reduction taken beyond ensure.md's own Release-Gate exclusion.

## Gate item adjudication

| Item | Status | Evidence |
|---|---|---|
| G1 regression gates | ✅ CLOSED | **tph44 PASS 44/44 (0.47s)** @ leader-authorized re-dispatch (36 full_history_heal + 8 w2_wiring); 5/5 cross-module suites + broader net GREEN; jq_full 16 RED base-attributed (below). |
| G2 system-interleave pin | ✅ PINNED | `tests/unit/tool_pairing_history_gate/test_interleave_pin.py`: poison `[Sys][AI(X)][Sys][TM(X)]` → violations True → heal synthesizes `partner-synth-call_x` (PARTNER_SYNTH_TEXT) at i_ai+1, misplaced TM removed, post-probe False; valid `[Sys][AI(a,b)][TM(a)][TM(b)]` → strict no-op (empty report, deep-equal). PASS ×2 (0.32s/0.31s). |
| G3 original-symptom closure | ✅ VERIFIED | `tests/integration/test_tool_pairing_original_symptom.py` (577L) + pack; strict-gateway fake LLM validates call→result immediate adjacency (not count-pairing), 2013-shaped BadRequestError rejection; S1 proves pre-fix brick on genuinely-unhealed poison (W1 monkeypatched off at `daemon.graph.has_pairing_violations`, captured payloads still carry `g3-orphan-call-148` unanswered); S2 W1 pre-heal delivers clean payload, invoke=1, sentinel `partner-synth-g3-orphan-call-148`; S3 W2 heal-once + single retry, invoke=2, OK result; S4 bounded reraise, invoke=2, full `__cause__` chain, `_matches_pairing_invalid` non-transient. PASS ×2 (1.24s / 1.33s). Deviations documented in MOCK_TESTS.md (S2 pins the real synthesize-partner contract; S3 rejection is call-count-based — the documented W2 motivating case). |
| G4 probe performance | ✅ PINNED | `test_probe_perf.py`: 678 alternating Sys/Human msgs → probe False in single-digit ms (≪10s budget); `msgs is original` + len 678 + deep-equal snapshot (zero-heal path, NO mutation). |
| G5 idempotence | ✅ PINNED | `test_idempotence.py`: double-heal no-op (heal-2 adds no synthesized/removed entries; after_second == after_first deep-equal); both synth prefixes (`partner-synth-`, `pairing-synth-`) recognized, parentless placeholders not re-flagged. |

## Pack results (tested HEAD a597fe6ae)

| Pack | Result | Runtime | Notes |
|---|---|---|---|
| graph_retry_integration | ✅ PASS 19/19 | 0.52s | stable across revive re-run |
| compaction (narrow) | ✅ PASS 130/130 | 17.8s | W3 emergency_truncate delegation surface |
| d1_seam_pairing | ✅ PASS 9/9 | 0.33s | |
| injection_pairing | ✅ PASS 30/30 | 0.40s | |
| instance_tools | ✅ PASS 207/207 | 50.2s | |
| llm_error_classifier | ✅ PASS 125/125 | 1.32s | pins ToolPairingInvalidError (:432) + 4 pairing signatures |
| job_queue_full | 🔶 FAIL 16/2046 → non-blocking | 72.2s | A/B below |
| dev_sh_static | ✅ PASS | 24.6ms | Core #4; `dev.sh:142` |
| pairing_heal_boot_smoke | ✅ PASS 21/0 | 25s | banner `Starting Ensemble v0.18.3`; livez+readyz 200; SIGTERM 1s; ports freed; after TEST-infra quick fix |
| concurrency_atomic | ✅ PASS 99/0/74 | 74s | Core #2/#3; +1 vs stale baseline (benign, diff-empty attribution) |
| tool_pairing_original_symptom (G3) | ✅ PASS 4/4 ×2 | 1.24s/1.33s | |
| **tph44** | ✅ PASS 44/44 (0.47s) | 2026-10-08 @ fb5ed07e5 | leader-authorized re-dispatch; 36 full_history_heal + 8 w2_wiring; environmental gap closed |
| **tool_pairing_gate_pins (G2/G4/G5)** | ✅ PASS 5/5 ×2 (0.32s/0.31s) | 2026-10-08 @ fb5ed07e5 | interleave pin + probe perf + idempotence; provenance `6455b66c2` (dir) + `fb5ed07e5` (pack+row) |

## jq_full RED — A/B base attribution (the only RED)

Base leg: scratch worktree `/tmp/tph-ab-base` @ `9be991d56` detached (venv fence verified, 3.14.7), byte-identical pack invocation (md5-matched script copy).
**Verdict: 16/16 PRE-EXISTING, 0 branch-caused, 0 base-only reds. Identical counts both sides (16F/1992P/38S; 72.2s vs 74.5s).**
Red set (all base-identical): terminal_write_census ×2 (unlisted site `daemon/manager.py:6010` + 14 stale `hooked_at` refs), watcher-repo `settled`-state fixtures ×3, tool-count 22→24 + `job_resume`-after-`job_answer` ordering ×2, `task_processor.py:1079` JSON-serialization MagicMock ×3, `fake_sync` 5-vs-6 arity ×2, py3.14 event-loop + hardcoded-macOS-path + PG boolean/int parity ×3.
Attribution context: this branch's diff does NOT touch manager.py / task_processor.py / job_queue_service.py / child_reports.py / job_recovery_service.py — the red surface is the v0.18.3 merge wave on `latest` (compaction R1+R2, live-views, upgrade-resilience cures) landing without jq_full fixture updates; last recorded jq_full run (2026-10-07 @ dc17a9142) predates it. **→ `latest` test debt, non-blocking for this branch, reported upward.**

### Gaps — RESOLVED (retained for the record)

1. **tph44** — resolved 19:2xZ by leader-authorized re-dispatch: **PASS 44/44 (0.47s)**.
2. **G2/G4/G5 pins** — resolved: pins had in fact been committed by `6455b66c2` (the "dead" authoring worker completed+committed before its report died to the rate limit — see LESSONS 2026-10-08-rate-limit-death-vs-work-loss.md); pack wrapper + registration landed as `fb5ed07e5`; **PASS 5/5 ×2** (author + independent).
3. Boot-smoke pack fix — committed in `2ac61dd32`.

## Quick fixes applied

- 47414032 (boot smoke): `test/packs/pairing_heal_boot_smoke_mock_test.sh` +3/−2 — PG16 unix-socket lock in `/var/run/postgresql/` (permission denied) → scratch-dir socket via `initdb -c unix_socket_directories` + `pg_ctl -o -k` (PG16 rejects `--unix-socket-directories` on initdb). Pack-infra only, zero production code.

## ensure.md Validation Results

- **Critical**: Core #1 ✅ (executed change-set packs all PASS) · Core #2/#3 ✅ (concurrency_atomic 99/0/74) · Core #4 ✅ (grep dev.sh:142)
- **Important/Nice-to-have**: not in this change set's scope (no converted-async call sites touched)
- **Release Gate**: deferred to promote-time per lane precedent (LLM-cost E2E lane)

## ensure.md Improvement Notices

None — no contradictions encountered this gate.

## Documentation Updated

- [x] PACKS.md — gate section OUTCOME + 12 final rows
- [x] MOCK_TESTS.md — G3 spec ACTIVE + Last Run (by b1b8a0af)
- [x] RESULTS/2026-10-08-tool-pairing-heal-gate.md — this file
- [x] test/packs/ — 11 packs registered (10 by ab6f400d @ 11aec6d0, G3 by b1b8a0af @ a597fe6a)

## Code Changes Summary (test lane only)

- `11aec6d0` — 10 gate pack scripts + PACKS.md registration (+941/−64)
- `a597fe6a` — G3 mock test (577L) + pack + docs (+705)
- docs commit (this report + PACKS.md final + boot-smoke pack fix) — see final commit below
- `git diff 86c1bc041..HEAD -- daemon/ scripts/ migrations/` = **EMPTY** (production untouched)

## Overall Status

- Regression net: ✅ (2,793 executed, 0 branch-caused failures, RED base-attributed)
- tph44 branch suite: ✅ 44/44 · G2/G4/G5 pins: ✅ 5/5 ×2 · G3 original-symptom closure: ✅ 4/4 ×2
- **Testing Complete: ✅ READY (CERTIFIED 2026-10-08)**
