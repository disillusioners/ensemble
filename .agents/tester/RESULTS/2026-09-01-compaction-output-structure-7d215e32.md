# Test Report: Final Gate — Compaction OUTPUT-STRUCTURE (single-document carrier)

Date: 2026-09-01
Branch: `feature/compaction-output-structure` @ `7d215e32` (5 commits: a80767b9 engine · c714a6e8 FE · e720e3ce+b7db688f fix pass · 7d215e32 test strengthen)
Feature under test: every compaction lands ONE SystemMessage (`compaction-global-{iid}-{seq}`) — envelope → GLOBAL OVERVIEW → provenance-labeled SECTION DETAIL (original batch coords, conversation-time provenance) → boundary line — followed by verbatim tail with ORIGINAL ids; sentinel-based ordering; engine-stamped compacted_ids loss guard; fail-open merge ladder; FE fold affordance.
Round posture: **READ-ONLY verification — 0 code changes, 0 commits.** Worktree left on `feature/compaction-output-structure` @ `7d215e32` throughout (every worker rev-parse-bracketed; zero drift).
Evidence root: `/tmp/tester-evidence/compaction-output-structure/` (subdirs: suites/ red/ mock/ live/ adjudication/)

Worker instances: 8c15cbcf (recon) · 9bb2ae60 (pack + concurrency) · 196a4005 (green + model_config) · 99745ac8 (RED) · 5c452e42 (mock) · a4df066f (dispatcher) · ab67b7dc (executor + fired-watchers) · 53d0b313 (multimodal) · feb59696 (FE jest) · 2f266dfb (live-1) · 1cb2704f (live-2) · cc7d252f (live-3 FE) · e23d7cc2 (adjudication)

Excluded per task: 2 env-coupled pre-existing failures in `tests/unit/test_llm_allowed_models_precedence.py` — not present in any dispatched target (verified: pack file-list excludes it).

---

## VERDICT: ✅ SHIP — zero blockers; 3 should-fix follow-ups (🟠, all root-caused with citations, none land wrong data)

---

## Scope 1 — Review close (independent red/green): ✅ PASS

| Item | Result | Evidence |
|---|---|---|
| GREEN — tests/unit/test_compaction.py | **PASS 126/126** (0F/0E/0S), 8.84s | suites/test_compaction_20260901_7d215e32.txt (196a4005) |
| RED — old-impl replay discriminativeness | **GENUINE DISCRIMINATIVE** | red/ (99745ac8) |
| Mock quality — order-pinning / drop-guard / emergency×seam | GENUINE / GENUINE-at-helper / PARTIAL | mock/scope-1-mock-quality-audit.md (5c452e42) |

### RED check detail (4 legs)
- **LEG A mechanism**: proof test `TestB3NonContiguousSectionCoords::test_non_contiguous_old_impl_replay_b3_regression` (test_compaction.py:4370–4582) embeds the a80767b9 `_per_batch_section_meta` verbatim (collapsing `s_idx` accumulator) and pins 3 bug signatures (§ 4370); mirror test (:4078–4246) drives real `compact_state` with 10 body→span binding assertions (:4186–4243) incl. the B3 pin `"messages #41–#60" in header_2` (:4204).
- **LEG B green**: proof test unmodified → 1 passed (0.59s) — OLD replay genuinely violates all 3 pins.
- **LEG C hardened mutation (the discriminator)**: embedded OLD replay patched to NEW original-coords logic in a /tmp copy → **1 failed (0.52s) on the exact PROOF assertion** (`assert violations` → `assert []` at :4567; failure output shows correct ORIGINAL coords rendered). NOT a tautology.
- **LEG D independent replay**: OLD fn extracted from `git show a80767b9:daemon/compaction.py` via AST (not the dev's embedded copy); side-by-side: section-2 survivor OLD=(21,40) vs NEW=(41,60) vs hand-computed original (41,60) — OLD wrong exactly as the strengthened assertion predicts. Driver exit 0.

### Mock-quality verdicts
- **Order-pinning real-graph: GENUINE** — `test_order_pinning_real_graph` (:2752–2923, file-backed SQLite, real `aupdate_state`+`aget_state` read-back), `test_created_at_preserved_on_preserved_tail` (:4744–4920), `test_chained_compaction_leaves_exactly_one_doc` (:2637–2731). Each FAILS without the sentinel (landed order `[INJ…, doc, T1..T5]`, tail ids, doc-count assertions).
- **Engine-drop guard: GENUINE at helper level** — `test_missing_preserved_tail_id_raises` (:3031) fires production `CompactionAborted` at compaction.py:407-418; B2 emergency seam passes real guard (:3921-3977). Gap: site-level disjointness asserts (graph.py:3551, compact_executor.py:1625-1629, instance_messaging.py:1228-1232) and helper assert at compaction.py:399-401 are NOT covered by any test.
- **Emergency×seam: PARTIAL** — helper-level pin real; CLE path entered via real `create_agent_node` (test_injection_graph.py:376-531) but `_StubGraph.aupdate_state` bypasses the real reducer; no test observes the LANDED channel on the emergency path. (Live e2e covers on-demand landing only.)
- 7 coverage-debt flags recorded (replacement_messages-only assertions, inspect.getsource pins, e2e canaries lacking channel read-back, no trailing-system-count pin, uncovered site asserts, StubGraph reducer bypass, no checkpoint-reread-after-abort). All test-quality debt — no production defect.

---

## Scope 2 — Full suites: ✅ PASS — every target EXACT

| Target | Command (all `timeout`-wrapped, rev-parse-bracketed, `SSL_CERT_*` unset) | Result | Expected | Worker |
|---|---|---|---|---|
| compaction_unit_test pack | `timeout 300 bash test/packs/compaction_unit_test.sh` | **322/322** (9.41s) | ≥320 (290+32) | 9bb2ae60 |
| test_compaction.py | `timeout 120 .venv/bin/pytest tests/unit/test_compaction.py --tb=short -q` | **126/126** (8.84s) | 126 | 196a4005 |
| test_compaction_model_config.py | same pattern | **31/31** (1.00s) | 31 | 196a4005 |
| test_command_dispatcher.py | same pattern | **76/76** (2.16s) | 76 | a4df066f |
| executor family (3 files, 1 invocation) | same pattern | **74/74** (8.10s; 64+3+7) | 74 | ab67b7dc |
| fired-watchers (bonus, compaction-adjacent) | same pattern | **13/13** (1.02s) | 13 | ab67b7dc |
| test_compaction_multimodal.py | same pattern | **30/30** (0.97s) | 30 | 53d0b313 |
| FE full suite | `CI=1 timeout 240 npm test -- --no-cache` | **65 suites / 2361 tests, 0 fail** (10.298s) | 65/2361 | feb59696 |
| concurrency_atomic_unit_test (ensure.md Core) | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` | **98P/74S/0F** baseline-exact (7.84s) | 98/74/0 | 9bb2ae60 |

Zero failures, zero skips-beyond-baseline, zero errors across all targets. Executor family composition shifted 65+3+6 → 64+3+7 (total unchanged 74). Known exclusion (allowed_models) confirmed absent from the pack.

### ensure.md Core status (blast-radius scoped)
- ✅ Critical: no regressions in changed packs (all above PASS)
- ✅ Critical: concurrency pack PASS (98/74/0)
- ✅ Critical: sync-DB-on-loop covered by concurrency pack PASS
- ✅ Critical: `dev.sh --timeout-graceful-shutdown 10` (dev.sh:102, static check)
- Release Gate (4 E2E workflow tests): NOT run — release-scoped, not this feature-branch gate's blast radius (consistent with prior gate precedent). Live compaction e2e below provides fresh end-to-end evidence.
- Contradictions: none (all validations ran as packs with dual-layer timeouts).

---

## Scope 3 — LIVE re-compact e2e: ✅ PASS (the payoff check)

Chain: daemon `./dev.sh` (uvicorn PIDs 31765/31767, port 8079) → fresh instance `e0e7a609-80f9-4f0f-8a03-a762c70e5eba` (agent worker) → 57 user+assistant messages over ~60 min conversation time, planted facts T1/T2 early → `/compact` on quiescent instance → verification → comprehension turn → FE fold-card in real browser.

| Check | Result | Evidence (live/) |
|---|---|---|
| /compact on quiescent instance | **success** — mode=summary, sections 1/1, forced=true, 39s wall | command_final_p2.json |
| Final order (CHECKPOINT ground truth via AsyncSqliteSaver.aget_state) | **[injected head 21][ONE doc][tail 14, ORIGINAL ids, original relative order]** — doc exactly once (ckpt pos 21); 0 truncation-marker messages; trailing-system run = 0 (W6 pathology DEAD); no inverted layout | checkpoint_order_p2.json, checkpoint_probe_p2.py |
| Tail verbatim ORIGINAL ids | ✅ id-set diff: only NEW id = the doc; everything else pre-existing ids | pre/post_compact_*_p2.json |
| Doc body — envelope | ✅ `[CONTEXT COMPACTION — mode=summary … compacted_at=2026-09-01T16:17:52 … preserved verbatim: 14 … self_id=…]` | doc_body_p2.txt |
| Doc body — GLOBAL OVERVIEW | ✅ substantive; 600-token cap fired with in-body notice (designed degrade); carries all planted facts | doc_body_p2.txt |
| Doc body — SECTION DETAIL | ✅ `### SECTION 1/1 — messages #1–#39` (ORIGINAL coords; B3 binding live-confirmed). ⚠️ conversation-time clause ABSENT → see Follow-up F1 | doc_body_p2.txt |
| Doc body — boundary line | ✅ `── END OF COMPACTED CONTEXT` INSIDE the doc (not a separate message) | doc_body_p2.txt |
| Subsequent-turn comprehension (GLOBAL-first proof) | ✅ **"Codename BLUE-FALCON-9, magic number 7391, and widget records go in PostgreSQL (not SQLite)."** — all 3 facts recalled from the doc ALONE (source messages compacted away) | comprehension_reply_p2.json |
| FE fold card (real Chromium + playwright) | ✅ collapsed preview + expander ("Show/Hide compacted context"); expand reveals envelope/GLOBAL/SECTION/boundary; **exactly 1 card**; refetch 5s apart → identical SHA256 (no re-materialization, no ghosts); 13 tail messages render in order | fe_fold_{collapsed,expanded}_p3.png, dom_*.json, refetch_integrity_p3.json |
| sections_kept/sections_total copy | ✅ "1/1 sections" in envelope (expanded view); preview deliberately shows GLOBAL body only (component.ts:562-593) | fe_fold_expanded_p3.png |
| Partial-budget compaction | SKIPPED (budget/risk — permitted; suite evidence covers partial path: TestPartialSummaryWS34 + executor family green) | — |
| Emergency path live | Not driven (hard to force live — permitted; suite evidence: TestB2EmergencyTruncationSeam + reactive injection tests green) | — |

Strict contiguous prefix/suffix order assertion: NOT met literally — protected `[SYSTEM CONTEXT]` injections inside the compacted span are relocated into the head group ahead of the doc. This IS the spec design (`[injected…][doc][tail…]` — architecture-recommendation.md §4/§5); per-segment relative order preserved. My dispatch's stricter check was inapplicable, not a product deviation.

### Adjudicated findings (all 🟠 should-fix, none blocker; full citations in adjudication/Q1-Q3-verdicts.md)

**F1 — Conversation-time provenance never wired (W3 unrealized in production).**
Clause implemented + unit-tested (compaction.py:724-725; tests assert presence :3511-3544 and omission :3546-3575) but `build_compaction_doc(msg_timestamps=…)` is called from all 3 production sites (compaction.py:2901, :3059, :3205) WITHOUT the argument; `CompactionContext` (:1084-1116) lacks the field; all 4 constructors omit it. Live header confirms omission. IMPORTANT: the W3 BUG (generation timestamps) is also absent — clauses are omitted, never falsified. Fix: thread timestamps (4 call sites + dataclass field). No wrong data lands → not a blocker.

**F2 — Doc id `compaction-global--1` (empty instance-id segment) + latent seq collision.**
`CompactionContext.instance_id=""` default (compaction.py:1106); all 4 call sites omit it → id renders `compaction-global--1` (compaction.py:908). Seq parser (:156-165) on the empty-iid form yields max_seq=0 → a SECOND compaction on the same instance mints the SAME id. Channel stays consistent (sentinel rewrite removes the old doc with the span) and FE merge-by-id replaces content in place (message-merge.util.ts:126-133) — no data loss, but the `{iid}-{seq}` uniqueness contract is broken and FE fold-card body silently swaps on re-compact. Fix: pass instance_id at call sites (in scope at each).

**F3 — `tokens_saved=-9845` is a measurement artifact (real savings ≈ +1,292).**
Asymmetric injection accounting: 21 injected `[SYSTEM CONTEXT]` messages excluded from `tokens_before` (compaction.py:1705-1707, :1732) but re-attached into `tokens_after` (:1992, :2105). tiktoken reconstruction: +1,292 real savings − 10,755 injected ≈ −9,463 ≈ reported −9,845. Already parked as known N1 (compact_executor.py:1315-1317). Misleading headline stat only; compaction is effective.

### Live-run operational notes
- LLM HA failover observed 3× (localhost:4123 → llm.daoduc.org) — benign, added latency.
- Empty-assistant placeholders precede real replies — poll `content != ""`, not role presence.
- Command-state polling: field is `data.command.phase` (not `data.state`).
- Compaction elapsed_ms=328833 in response ≠ 39s wall (measured from command creation) — reporting quirk, noted.
- `ebf542de` (user evidence instance) untouched. Port 8088 untouched (empty throughout).

---

## Environment end state
- Worktree: `feature/compaction-output-structure` @ `7d215e32` — UNCHANGED all round (read-only gate; only pre-existing `.agents/` scratch dirty entries, untouched).
- Left RUNNING for the user: daemon on 8079 (PIDs 31765/31767), FE dev server on 4199 (PID 37364). Instance `e0e7a609` compacted + comprehension-verified (status settling to completed).
- 0 code changes, 0 commits, 0 test modifications.

## Documentation Updated
- [x] RESULTS/2026-09-01-compaction-output-structure-7d215e32.md (this file)
- [x] PACKS.md — compaction_unit_test last-run → 322/322 @ 7d215e32
- [x] LESSONS/2026-09-01-compaction-output-structure-gate.md — F1/F2/F3 root causes + live-run gotchas
- [ ] QUARANTINE.md — no new flaky tests (none observed; all runs deterministic)

## Action Needed (follow-ups — 🟠 should-fix, post- or pre-merge at leader's discretion)
- [ ] F1: wire msg_timestamps through CompactionContext + 4 call sites (W3 conversation-time provenance)
- [ ] F2: pass instance_id at the 4 CompactionContext call sites (fixes id format + seq collision)
- [ ] F3: symmetric injection accounting for tokens_before/after (parked N1)
- [ ] Test-coverage debt (🟢 nice-to-have, from mock audit): site disjointness-assert coverage; emergency-path landed-channel pin; trailing-system-count pin; channel read-back in e2e canaries

### Overall Status
- Scope 1 (red/green + mock quality): ✅ PASS
- Scope 2 (all suites exact): ✅ PASS
- Scope 3 (live e2e payoff): ✅ PASS
- ensure.md Core: ✅ PASS
- **VERDICT: SHIP — no blockers. 3 should-fix follow-ups (F1/F2/F3), all root-caused, none land wrong data, all small-surface fixes.**
