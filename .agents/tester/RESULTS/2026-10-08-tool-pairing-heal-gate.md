# Test Report: TOOL-PAIRING FULL-HISTORY HEAL — Official Tester Gates

Date: 2026-10-08 (18:16Z gate open → 19:0xZ close)
Branch: `fix/tool-pairing-full-history-heal` @ `86c1bc041` (commissioned tip) + 2 test-lane commits (`11aec6d0` packs/docs registration, `a597fe6a` G3 mock) → tested HEAD `a597fe6ae`. Base: `9be991d56` (v0.18.3 tip). Worktree: `/home/nea/ensemble-src-wt-pairing-heal` (Python 3.14.7, daemon-import fence verified; 3.13 rot not applicable).
Incident class: 03d7657f — 2013-bricking via mid-list unanswered tool_call (dup tool_call_id from 10-02 regen/failover) under strict-provider routing.
Worker instances: 92131d86 (discovery), ab6f400d (pack-creator), b1b8a0af (G3 mock author), 55011756/cb101f71 (tph44 ✝), fbf20aa2 (d1_seam), 020a5aeb (graph_retry), 48b7f088/c4486423 (injection), 6dd1ea6e (compaction), 5dbbae18 (llm_error_classifier), 51d5092c (instance_tools), 03c449af (dev_sh_static), 49787f30 (jq_full), 0b1d9463 (A/B base leg), 47414032 (boot smoke), ae32db7f (concurrency_atomic), 6d529781 (G3 re-chart). ✝ = rate-limit deaths.

## Summary

- **Executed: 11 packs, 2,744 tests + 1 static check — 2,744 passed-equivalent, 0 branch-caused failures, 0 unattributed REDs.**
- Developer cross-module suites: **5/5 GREEN** (graph_retry 19, compaction 130, d1_seam 9, injection 30, instance_tools 207).
- Broader net: llm_error_classifier 125 ✅ · job_queue_full 2046 (16 RED → **A/B base-attributed 16/16 pre-existing**) · concurrency_atomic 99 ✅ (Core #2/#3) · dev_sh_static ✅ (Core #4) · boot smoke 21/0 ✅ (Core #2/#3 insulation).
- G3 original-symptom closure: **PASS 4/4 ×2** (author + independent re-chart).
- **NOT EXECUTED (environmental): tph44 (the branch's own 44-test suite) + G2/G4/G5 pins (authored nowhere).**
- ensure.md: Core #1 ✅ (no regressions in executed change-set packs) · Core #2/#3 ✅ (concurrency_atomic 99/0/74) · Core #4 ✅ (dev.sh:142). Release-Gate E2E: not in scope for a branch gate (LLM-cost lane; promote-time).
- Quick fixes: 1 (boot-smoke pack, +3/−2, TEST infra only). Quarantined: 0 new.

## Verdict: 🔶 PASS-WITH-GAPS

Everything that ran is green and every RED is base-attributed; two commissioned items are missing EVIDENCE (not failures) due to a provider rate-limit storm that killed 5 workers (ladder-exhausted per lane rules). The gaps are re-runnable now that the proxy cleared — recommend leader authorizes: (a) one tph44 pack run, (b) the G2/G4/G5 authoring dispatch. Neither gap is a RED; the gate does not certify until they land.

### Scope Decision

Full gate per commission (big/critical: daemon/graph.py +538 lines, new 829-line module, core dispatch path). No reduction taken beyond ensure.md's own Release-Gate exclusion.

## Gate item adjudication

| Item | Status | Evidence |
|---|---|---|
| G1 regression gates | 🔶 PARTIAL | 5/5 cross-module suites + broader net GREEN; jq_full 16 RED base-attributed (below). **tph44 never executed** (3× rate-limit worker deaths: original 55011756 + revive + replacement cb101f71; recovery ladder exhausted). |
| G2 system-interleave pin | ❌ NOT AUTHORED | 2× worker deaths (3dc47f7b, 1d8fe9e6) pre-authoring; no partial files landed (verified by A/B worker's read-only check of the worktree). |
| G3 original-symptom closure | ✅ VERIFIED | `tests/integration/test_tool_pairing_original_symptom.py` (577L) + pack; strict-gateway fake LLM validates call→result immediate adjacency (not count-pairing), 2013-shaped BadRequestError rejection; S1 proves pre-fix brick on genuinely-unhealed poison (W1 monkeypatched off at `daemon.graph.has_pairing_violations`, captured payloads still carry `g3-orphan-call-148` unanswered); S2 W1 pre-heal delivers clean payload, invoke=1, sentinel `partner-synth-g3-orphan-call-148`; S3 W2 heal-once + single retry, invoke=2, OK result; S4 bounded reraise, invoke=2, full `__cause__` chain, `_matches_pairing_invalid` non-transient. PASS ×2 (1.24s / 1.33s). Deviations documented in MOCK_TESTS.md (S2 pins the real synthesize-partner contract; S3 rejection is call-count-based — the documented W2 motivating case). |
| G4 probe performance | ❌ NOT AUTHORED | Same 2× worker deaths. |
| G5 idempotence | ❌ NOT AUTHORED | Same. |

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
| **tph44** | ❌ NOT RUN | — | environmental; ladder exhausted |

## jq_full RED — A/B base attribution (the only RED)

Base leg: scratch worktree `/tmp/tph-ab-base` @ `9be991d56` detached (venv fence verified, 3.14.7), byte-identical pack invocation (md5-matched script copy).
**Verdict: 16/16 PRE-EXISTING, 0 branch-caused, 0 base-only reds. Identical counts both sides (16F/1992P/38S; 72.2s vs 74.5s).**
Red set (all base-identical): terminal_write_census ×2 (unlisted site `daemon/manager.py:6010` + 14 stale `hooked_at` refs), watcher-repo `settled`-state fixtures ×3, tool-count 22→24 + `job_resume`-after-`job_answer` ordering ×2, `task_processor.py:1079` JSON-serialization MagicMock ×3, `fake_sync` 5-vs-6 arity ×2, py3.14 event-loop + hardcoded-macOS-path + PG boolean/int parity ×3.
Attribution context: this branch's diff does NOT touch manager.py / task_processor.py / job_queue_service.py / child_reports.py / job_recovery_service.py — the red surface is the v0.18.3 merge wave on `latest` (compaction R1+R2, live-views, upgrade-resilience cures) landing without jq_full fixture updates; last recorded jq_full run (2026-10-07 @ dc17a9142) predates it. **→ `latest` test debt, non-blocking for this branch, reported upward.**

### Gaps

1. **tph44** (tests/unit/tool_pairing_history/, 44 tests — the branch's own suite; G1's centerpiece). Not executed: 3 consecutive worker deaths to `openai: Rate limit reached` (my 3-4-concurrent dispatch waves saturated the shared proxy; ladder exhausted per Fan-In rules). The suite was collect-verified (44 nodes) and its subject surface is heavily covered by the green packs (G3 drives the real heal paths on real `create_agent_node`; d1_seam/injection/compaction/graph_retry all green), but no independent 44/44 evidence exists in this gate. **Ask: one fresh dispatch of `test/packs/tph44_unit_test.sh`.**
2. **G2/G4/G5 pins** not authored (same storm). Spec for all three remains valid (see worker-task definitions in this gate's dispatch history; API surface recon in discovery report §6). **Ask: one authoring dispatch.**
3. Boot-smoke pack fix (+3/−2) uncommitted at report time — folded into the docs commit below.

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

- Regression net: ✅ (2,744 executed, 0 branch-caused failures, RED base-attributed)
- G3 original-symptom closure: ✅
- tph44 + G2/G4/G5: ❌ pending (environmental)
- **Testing Complete: 🔶 NOT READY TO CERTIFY — PASS-WITH-GAPS; two follow-up dispatches close it**
