# Test Report: TOOL-PAIRING INVALID_TOOL_CALLS UNION — Round-2 Official Gates

Date: 2026-10-09 (06:10Z gate open → 06:5xZ certified close)
Branch: `fix/tool-pairing-invalid-tool-calls` · commissioned tip `5869fcce0` · tested chain `1ef37932c (base, v0.18.4 tip) → 5869fcce0 → 375aed46 → 013ec310b → e69b03492` (3 fix commits + 3 test-lane commits; `git diff 5869fcce0..e69b03492 -- daemon/ scripts/ migrations/` = **EMPTY**)
Worktree: `/home/nea/ensemble-src-wt-pairing-heal-2` (Python 3.14.7, daemon-import fence verified, clean tree)
Incident class: v0.18.4 live gap (task 10816) — round-1 heal stripped a DB-repair synth TM answering an `invalid_tool_calls` call → W2 retry re-shipped the unanswered call → 2013 loop (empirically observed).
Full per-arc mock report: `RESULTS/2026-10-09-tool-pairing-original-symptom-v2-mock-test.md`.

## Verdict: ✅ READY (CERTIFIED) — all 5 round-2 gate items closed; zero branch-caused failures; sole RED set = KNOWN pre-existing 16/16 identity

### Summary
- **Executed: 14 packs, 2,856 tests + 1 static check — 0 branch-caused failures, 0 unattributed REDs.** No worker losses (rate-limit serialization discipline from round-1 LESSONS held: max-2 concurrency throughout).
- tph64: **64/64** (44 round-1 extend-only + 20 new; PASS 0.81s)
- 5/5 developer cross-module suites: graph_retry 19 · compaction 130 · d1_seam 9 · injection 30 · instance_tools 207 = **395/395**
- Broader net: llm_error_classifier 125 ✅ · dev_sh_static ✅ (Core #4) · concurrency_atomic 99/0/74 ✅ (Core #2/#3, exact baseline) · boot smoke **21/0, banner `Starting Ensemble v0.18.4`** (correct tree) ✅
- jq_full: 16F/1992P/38S → **strict 16/16 identity with the KNOWN pre-existing set (critical note d27a1ccc)** — mechanical `comm` diff: 0 new, 0 missing. Attribution carried by round-1 A/B + d27a1ccc; no new A/B leg needed (per commission expectation).
- Round-1 assets regression: gate_pins **8/8** (5 round-1 + 3 new perf-invalid) · G3-v1 mock **4/4** (byte-untouched)

### Gate item adjudication
| Item | Status | Evidence |
|---|---|---|
| 1. Official gates | ✅ | tph64 64/64 + 5/5 cross-module + broader net + boot smoke v0.18.4 + jq_full KNOWN-16 identity (table below) |
| 2. Original-symptom closure v2 | ✅ 4/4 (1.50s) | Arc (a): union gateway rejects raw invalid-only poison (2013-shaped); **v1-view negative control ACCEPTS the same payload — round-1 blind spot proven**; strip-simulation rejected; UNREACHABLE on branch (1 invoke, zero unanswered-invalid ids in every captured payload). Arc (b): W1 synth `partner-synth-{X}` adjacent, content exactly `PARTNER_SYNTH_INVALID_TEXT` (asserted ≠ PARTNER_SYNTH_TEXT); 1 invoke. Arc (c): exactly 2 invokes; synth survives W2 cycle BY IDENTITY (`is` + same id + adjacent position); uuid TM retained not stripped; retry accepted. Arc (d): verbatim live tuple (`call_8ed9e1771dca42348dfa7ca0` + TM uuid `1c2a9d4f-…`) mid-list ~602 msgs: probe CLEAN, both tuple members survive `is`-identity, zero removal, 1 invoke. Deviations documented (W2-only synth unreachable by construction — same idempotent heal helper; 2013-shape asserted on raised exception via 1 test-only quick-fix). |
| 3. Mock fidelity (both fields) | ✅ | Gateway unions `tool_calls` + `invalid_tool_calls` into one needed-set, id-keyed immediate adjacency, orphan rejection. Test docstring carries the two-tier evidence basis verbatim-framed per `_extract_tool_call_ids` docstring (daemon/tool_pairing_history.py:230-247): ROLE-level corpus-pinned (llm_error_classifier SIGNATURES) / ID-level EMPIRICAL OpenAI-wire assumption. |
| 4. Perf spot-check | ✅ | ~700-msg with invalid entries: 0.489ms (mixed-answered) / 0.155ms (flags-unanswered) / 1000-msg mixed: 0.662ms hard-bound 200ms — **exactly the reviewer's ~0.65ms/1000-msg reference class**. Zero mutation on clean; read-only even when flagging. (Pack runs dot-mode; timings evidenced from the authoring run + test-internal prints.) |
| 5. Round-1 regression extend-only | ✅ | 44/44 inside tph64; gate_pins 5/5 round-1 pins green; G3-v1 mock 4/4 byte-untouched. |

### A/B attribution (sole RED set)
| Pack | HEAD @ e69b03492 | Known baseline (d27a1ccc + round-1 A/B @ 9be991d56) | Classification |
|---|---|---|---|
| job_queue_full | 16F/1992P/38S (68.5s) | 16F/1992P/38S — identical node set | **16/16 KNOWN pre-existing, 0 new, 0 missing** (mechanical comm diff) |

Commission pre-authorized this expectation ("expect identical set"); no new A/B leg dispatched — the set-identity check IS the attribution. `latest` test debt unchanged, separate from this branch.

### Pack results
| Pack | Result | Runtime |
|---|---|---|
| tph64 (NEW) | ✅ 64/64 | 0.81s |
| graph_retry_integration | ✅ 19/19 | 0.56s |
| compaction | ✅ 130/130 | 17.27s |
| d1_seam_pairing | ✅ 9/9 | 0.34s |
| injection_pairing | ✅ 30/30 | 0.36s |
| instance_tools | ✅ 207/207 | 51.2s |
| llm_error_classifier | ✅ 125/125 | 1.08s |
| dev_sh_static (Core #4) | ✅ | <1s |
| job_queue_full | 🔶 KNOWN-16 identity (above) | 68.5s |
| pairing_heal_boot_smoke | ✅ 21/0 — banner **v0.18.4** | 25s |
| concurrency_atomic (Core #2/#3) | ✅ 99/0/74 (exact baseline) | 75s |
| tool_pairing_gate_pins (5 round-1 + 3 NEW perf-invalid) | ✅ 8/8 | 0.36s |
| tool_pairing_original_symptom_v2 (NEW — THE criterion) | ✅ 4/4 | 1.50s |
| tool_pairing_original_symptom v1 (regression) | ✅ 4/4 (byte-untouched) | 1.24s |

### Quick fixes applied
- 1 (test-only, <20 lines, G3-v2 arc-c: `CapturingGateway.raised_raw` to assert the 2013 shape on the raised exception). No production code touched.

### ensure.md Validation Results
Core #1 ✅ (no regressions in change-set packs) · Core #2/#3 ✅ (concurrency_atomic 99/0/74 exact) · Core #4 ✅ (dev.sh static). Release-Gate E2E: out of scope for branch gate (promote-time), per lane precedent.

### Code changes summary (test lane only)
- `375aed46` probe-perf-invalid pin (+408) · `013ec310b` tph64 pack + round-2 PACKS section (+75) · `e69b03492` G3-v2 mock 669L + pack + docs (+805). Untracked per-arc mock report folded into the final docs commit.

## Overall Status
- Regression net: ✅ (2,856 executed, 0 branch-caused failures, RED set = KNOWN-16 identity)
- Closure criterion (v2, incident-TRUE shape): ✅ 4/4 with v1-blind-spot negative control
- Perf: ✅ ms-class on invalid-mixed histories
- Extend-only regression: ✅
- **Testing Complete: ✅ READY (CERTIFIED 2026-10-09)** — merge-ready on `e69b03492`
