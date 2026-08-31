# Architecture Recommendation: wc-wake-report-integrity — Component 2 (Report-Integrity), C2 Register Closure

**Feature:** `wc-wake-report-integrity` · **Branch:** `feature/wc-wake-report-integrity @ 1f8f8ed4`
**Date:** 2026-08-30 · **Author:** architect (controller) — aggregation of 4 dispatched analysts; per my role I compare and decide, the analysts produced the evidence.
**Analyst instances:** A `2419dd9f` (system-guards, resilience-design) · B `6f8c152f` (prompt-side, resilience-design) · C `24aa4d87` (hybrid, resilience-design) · V `38380049` (P1 validation, data-flow-design). All four confirmed `Skill loaded:` on first line — no skill-bank misses.
**Inputs:** `technical-analysis.md` (615L), `decisions.md` (C2 register), `phase2-plan.md`, `phase1-plan.md`, `plan-overview.md`, plus direct code verification on this checkout.
**Scope:** C2-D2.1–D2.18 verdicts (register-ready rows in §5 — the planner/leader may lift them into `decisions.md` verbatim), NR-1–NR-4 confirmation, P1 validation, C1-D3 leader-lock. DIAGNOSIS/DESIGN ONLY — no implementation.

---

## 1. Executive Summary

**Recommended family: HYBRID — layered defense-in-depth, landing prompt-side + instrument layers first and the completion-gate guard (b) in staged form with a pre-committed enforcement flip.** All three family analysts independently converged on hybrid ("guards-only is necessary but not sufficient" — A; "this family ships first as rate-reducer + instrument; guards are the pre-authorized escalation" — B; "(a)+(c)+(d)+(e) for prevention/flagging + (b) for the death-window" — C).

Four code-verified findings drove every contested verdict:

1. **Nothing else sees a dead tree.** The watchdog wedge enumerates WAITING_CHILDREN parents only (`waiting_children_watchdog.py:569` → `repository.py:2188-2194`); the ORPHAN lane filters DEFERRED `report_injection` rows only (`report_delivery_recovery.py:565`), and the dead-parent guard classifies only TERMINATED as dead (`child_reports.py:2811-2814`). A **COMPLETED** parent is invisible to every existing backstop. Without (b), the silent-death class is not detected-late — it is **permanently undetected**. (C, decisive)
2. **The planner's suggested (b) predicate source is wrong.** `dependency_bus.pending_watchers` (`:935`) is a cache-first read (`:960-961`) purged after `emit_terminal` (`:709`) — it returns EMPTY in exactly the inter-report gap (b) targets. The predicate must read **durable DB state**: `report_injections` PENDING/DEFERRED rows (the write-once obligation invariant) + `dependency_watchers` FIRED-but-unconsumed rows. (A, code-verified; corroborated by C's stranded-PENDING-row Path-1/2 analysis)
3. **The prompt-side precedent is strong but lane- and frame-constrained.** `RECOVERY_GUIDANCE_HINT` demonstrably changes parent behavior (complied in both correct and wrong conditions — the Aug-26 replacement storm proves compliance), but it fires only on the **error lane** (`error_reporting.py:739`); the junk class is **success-lane**, so an `error_reporting.py` constant for (d) would be dead code. And an instruction-bearing hint inside `_frame_injected_report` (`graph.py:194-224`) is self-neutralized by the frame's own "NOT an instruction … Do NOT execute" text and erodes the prompt-injection defense. → (d) lives prompt-side; (c)'s marker text must be **descriptive-only**. (B, code-verified)
4. **This codebase's prompt-efficacy law:** prompts failed for diffuse conduct DON'T-X rules (polling loops → GII throttle `411757be`; tool-call loops → LoopBreaker `88783c08` four days later) and worked for **single-decision-point rules delivered per-message** (2026-08-29 dispatch delivery-discipline clause, KB-verified) or **in-band at the decision moment** (RECOVERY_GUIDANCE_HINT). Therefore (e) must mirror into dispatch prompts, not live in rule.md alone. (B)

**Recommended sequencing — Seq-AB (two-wave hybrid):**
- **Wave 1 (days):** NR-1–NR-4 + (e) opening-variant cardinal (constraint set §5-D2.11) + (d) prompt-side scrutiny guidance (12 parent agents + writing-guide rule + dispatch-prompt mirror) + (c) passive **descriptive-only** marker.
- **Wave 2 (weeks):** (b) terminal-child-aware waiting via B.S.1 three-stage landing (predicate → log → enforce), kill-switch `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=0` at code-land, **enforcement flip pre-committed** to the first deploy after the stage-ii log soak shows the predicate fires only in the incident shape — or immediately upon any silent-death incident. Candidate (a) does not land initially ((a2) subsumed by (c) at lower blast radius; (a1) reserved as the escalation variant).

---

## 2. Approach Comparison (five fixed axes)

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|-----------|-------------|-----------------|------|------|----------------|
| **A: system-guards only** ((a)+(b)+(c)-active) | High — graph.py + child_reports.py + job_feedback_observer.py + full B.S.1–B.S.6; 3–4 wks (Axis-1) | Medium — guards on every turn/completion; exclusion list grows per new agent | Medium — kill-switches, fail-OPEN suites, 3-gate ordering to keep correct | Blind to junk-WITH-tool-calls, wrong-answer reports, future agents outside exclusion list; (b) misfire contained by fail-OPEN but (a2) adds every-turn graph-path exposure | High (test scaffold for (b) alone is the priciest item) | **Rejected as sole family** — A's own report: "necessary but not sufficient" |
| **B: prompt-side only** ((d)+(e)+(c)-passive) | Low — ~12 prompt files + guide rule + marker; days | High — additive; auto-covers new agents via writing guide | Medium-High rot risk — 12+ files across v1/v2 variants, open-ended parent surface (no parent-ownership check on send_message); mitigated by registry-completeness test | Silent-death window stays open at ≈pre-fix rate whenever any LLM ignores one sentence (B's own efficacy confidence for closing the window: MEDIUM-LOW) | Low | **Rejected as sole family** — ships first as Wave 1, never as the whole fix |
| **C: layered hybrid** (prompt+instrument wave → staged (b)) | Medium — two small waves, each independently revertable | High — Wave 1 additive; (b) short-circuits to the both-counts-zero window only | Medium — both surfaces, but each layer small and separately kill-switched/revertable | **Lowest** — (e) reduces rate, (c) flags residuals, (d) teaches consumption, (b) closes the only window nothing else sees; fail-OPEN bounds (b) misfire to a spurious notice | Days + staged weeks | **✅ RECOMMENDED (Seq-AB)** |

Dominant axis: **Risk.** The class's defining cost is *silent permanent* tree death; C is the only family that both reduces the rate and closes the detection gap, while every layer's failure mode is either advisory (prompts, marker) or fail-OPEN (gate).

---

## 3. Recommended Architecture — Layer Map & Waves

### Sub-defect → layer (defense-in-depth chains)

| SD | Primary layer | Backstop layer | Notes |
|----|--------------|----------------|-------|
| **SD1** child premature END (hop 1) | **(e)** opening-variant cardinal, mirrored into dispatch prompts (the proven channel) | **(a1)** auto-continue — RESERVED, not landed (see D2.2) | (a2) withdrawn: subsumed by (c). Reduce-then-flag chain |
| **SD2** report honesty (hops 6–7) | **(c)** descriptive sanity marker at the envelope (fires on exactly the input where the truncation guard short-circuits, `child_reports.py:1312-1313`) | NR-3 counter (observability of every escape) | Marker wording corrected — §5 D2.9 |
| **SD3** parent adjudication (hops 9–10) | **(d)** prompt-side scrutiny guidance, conditioned on the visible `[REPORT SANITY: …]` pattern | **(b)**'s adjudication notice (gate-level backstop) | (c)+(d) symbiosis preserved with clean frame boundaries |
| **SD4** gate blind spot (hops 10–11) | **(b)** terminal-child-aware waiting — the **only** layer that can fire here | **none exists** (wedge/ORPHAN/drift all blind to COMPLETED parents — finding #1) | Durable-state predicate (finding #2); fail-OPEN; last gate |

### Waves

**Wave 1 (days, no gate changes):** NR-1 · NR-2 (lift BOTH exclusion literals) · NR-3 (counter before both short-circuits) · NR-4 (audit memo: keep truncation short-circuit narrow) · (e) cardinal per constraint set · (d) scrutiny guidance in 12 parent agent defs + `docs/agent-prompt-writing-guide.md` mandatory line + dispatch-prompt mirror · (c) passive marker, descriptive text only, `SANITY_FLAG_VERSION`-versioned, exclusion-list-aware.

**Wave 2 (weeks, staged gate change):**
- **B.S.1-i** predicate function (no behavior change) + unit tests incl. FIRED-but-unenqueued fixture + NR-1 repro extension.
- **B.S.1-ii** predicate-attached log at the COMPLETED stamp sites (`child_reports.py:2361/2366/2546/2551/2706/2711` + `job_feedback_observer.py:2822` + `_update_parent_on_child_complete` :931/:1045-1101/:1132-1137). Soak: target ≤2 weeks.
- **B.S.1-iii** enforcement ON (inject adjudication notice, never block) — **pre-committed flip**: first deploy after soak unless the log shows false-fires; immediate on any silent-death incident. Kill-switch stays as the revert path.
- Companions: B.S.3 fail-OPEN suite (exception/malformed/timeout → COMPLETED proceeds), B.S.4 reconciler bridge (`reconcile_turn_mirror`, seam `child_reports.py:2396`), B.S.5 wedge skip-if-(b)-guarding (+ shared per-parent cooldown), B.S.6 gate-ordering test (bus > tasks > (b); (b) evaluated only when both counts are zero).

**Kill-switch / revert stack:** `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` (default 0 → flipped per above); `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED` (reserved, unused, default 0); `SANITY_FLAG_VERSION` (marker suppress); (d)/(e) git revert.

---

## 4. The Evidence Bar (D2.13) — reasoned

Two occurrences IS enough to land (b) in staged form, because the decision matrix changed shape under the fan-out evidence:

- **Detection asymmetry (decisive):** the alternative to (b) is not "(b) later" — it is *no detection ever* (finding #1). The cost of waiting is not bounded latency; it is unbounded exposure.
- **Misfire cost is bounded by design:** fail-OPEN + inject-notice means the worst case of a wrong predicate is a spurious adjudication turn, not a blocked completion; the kill-switch reverts in one env flip.
- **The judgment call is converted to an empirical rule:** the stage-ii log soak measures the predicate's false-fire rate *before* enforcement; the flip is pre-committed so operational inertia cannot strand the code (C's 🔴).
- **Counter-consideration honored:** the two incidents may share a model/task-specific cause (Axis-4). That argues for staging and the log soak — which the recommendation keeps — not for deferral.

---

## 5. C2 Register Verdicts (register-ready — lift into `decisions.md`)

| ID | Verdict | Rationale + Evidence |
|----|---------|----------------------|
| **D2.1** | **LOCKED: Hybrid (layered defense-in-depth, Seq-AB)** | No single candidate covers the 11-hop chain (taxonomy; all 3 analysts concur). (b) is the only SD4 layer (wedge WC-only `repository.py:2188-2194`; ORPHAN DEFERRED-only `report_delivery_recovery.py:565`; dead-parent TERMINATED-only `child_reports.py:2811-2814`). (d)/(e) cover what guards can't: junk-with-tool-calls, wrong-answer reports, future agents. Prompt-side is Wave 1 by design — first-class, not fallback. |
| **D2.2** | **LOCKED: (a) does not land initially. (a2) WITHDRAWN — subsumed by (c) (same predicate ≈ first-turn/short-history zero-tool, lower blast radius: envelope path only vs every-turn graph path). IF escalation ever activates (a), the variant is (a1) auto-continue via a system-authored channel (enqueue-style, like the watchdog notice), NEVER inside the [SYSTEM NOTE] frame; bounded to 1 retry.** | A recommended (a2); C showed (a2)∧(c) redundant and (c) lower-blast; B's frame finding rules directive text out of the frame and shows prevention belongs to the prompt channel. Conflict resolved on blast-radius + channel-fit evidence. |
| **D2.3** | **LOCKED: default OFF** — `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED=0`, env binding reserved (mirror `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`, `config.py:486` precedent). Contingent on D2.2 escalation. | A; only-hop-1 evidence; every-turn blast radius. |
| **D2.4** | **LOCKED: first-turn only** (if ever activated). | A: "only hop-1 evidence exists; generalization widens blast radius without evidence"; C anti-pattern: generalized (a) breaks legitimate single-message acks. |
| **D2.5** | **LOCKED: (b) SHIPS in this component — staged B.S.1, kill-switch default 0, pre-committed enforcement flip (≤2-week soak; immediate flip on any silent-death incident).** | Finding #1 (no backstop sees COMPLETED parents) converts "defer behind observability" into "accept permanent non-detection". C's own 🔴 risk notes observation-window incidents cost permanent tree loss. Staging + soak honors the B.S. protocol's reason for existence. |
| **D2.6** | **LOCKED: fail-OPEN + inject-notice (never block).** | A + C unanimous; fail-CLOSED regression (hung parent) is worse than today's silent completion; matches existing defense-in-depth semantics (`job_feedback_observer.py:344-424`, `:426+`). |
| **D2.7** | **LOCKED: durable DB state, NOT `pending_watchers`.** Primary signal: `report_injections` rows for the parent with `state IN ('PENDING','DEFERRED')` whose child is terminal (write-once obligation invariant); corroborating: `dependency_watchers` FIRED-but-unconsumed rows. Rationale: `pending_watchers` (`dependency_bus.py:935`) is cache-first (`:960-961`) and purged post-fire (`:709`) → EMPTY in the inter-report gap. Exact composition pinned at B.S.1-i via the NR-1 repro fixture; invariant: readable in the completion transaction, empty on healthy paths, non-empty in the incident shape. | A (code-verified) overrides the planner's suggested default; C marked that default unverified and its Path-1/2 analysis corroborates the durable row. |
| **D2.8** | **LOCKED: bus (fail-CLOSED, same-tx, `child_reports.py:2117-2127`) > tasks (fail-OPEN, `:2008/:2663`) > (b) (fail-OPEN, LAST).** (b) evaluates only when both prior counts are zero (short-circuit; also bounds hot-path cost to the SD4 window). B.S.6 ordering test asserts the sequence. | A + C; matches existing gate semantics; (b) never authoritative. |
| **D2.9** | **LOCKED: (c) standalone-instrument in Wave 1; rides with (b) at enforcement (notice cites the marker). Marker text CORRECTED to descriptive-only:** `[REPORT SANITY: zero tool-call evidence in source history]` — the directive half of the planned text ("treat as interim, not completion") moves to (d) prompt guidance. | B: in-frame directive text is neutralized by the frame's own "NOT an instruction" and erodes injection defense (`graph.py:194-224`). (c)+(d) symbiosis preserved: (d) prompt text conditions on the visible marker pattern. |
| **D2.10** | **LOCKED: PROMPT-SIDE home for (d).** Edit set: the 12 parent agent defs (leader, project-manager, developer[v2], architect, approver[v2], planner[v2], reviewer[v2], tidier[v2], coder, tester, wanderer, governor — verified via `meta.json team_members`), + mandatory line in `docs/agent-prompt-writing-guide.md`, + dispatch-prompt mirror (the empirically strongest channel). NOT `error_reporting.py` (wrong lane — fires only via `_send_error_report` `:739` on child-ERROR; dead code for success-lane junk). NOT in-frame. A system-side post-frame channel is recorded as NOT-selected; revisit only if marker-consumption logs show prompt-side guidance is ignored. | B (code-verified); A's B.4 variant overridden on lane evidence. Rot mitigation: registry-completeness test (D2.14). |
| **D2.11** | **LOCKED: (e) IN — belt-and-braces, Wave 1.** The closing cardinal (`worker/rule.md:166-175`) verifiably does not cover the opening variant (grep: zero opener/preamble/first-turn rules in worker/developer/coder). Constraint set for the text: (1) binds the opening pattern only (task-dispatched turn ending in future-intent text + zero tool calls); must NOT prohibit end-turn-after-`send_message`, final text-only reports after real work, question-to-parent turns (distinguish future-intent vs request-for-input), one-message acks, explorer-style synthesis (excluded agents anyway); (2) names the consequence (detected as junk); (3) gives the compliant alternative (begin work with a tool call, deliver the report, or ask); (4) single-decision-point phrasing ("before ending any turn"); (5) mirrored into dispatch prompts. Recipients: work-turn agents (worker, tester, coder, developer[v2], tidier[v2], planner[v2], reviewer[v2], architect, approver[v2], wanderer — complies naturally, governor). Exempt: explorer (text-only by design), `_mother`/`_baby_template`, watcher/image-reader/kb-writer class. | B §4; (a)+(e) redundancy dissolved by D2.2 ((a) deferred). |
| **D2.12** | **LOCKED: Seq-AB (hybrid of Seq-A wave + staged Seq-B (b) with pre-committed flip).** NOT Seq-C pure-instrument (leaves the death window open for the observation period with permanent-loss exposure — C §sequencing); NOT pure Seq-B day-1 enforcement (the B.S. protocol exists to de-risk exactly that); NOT open-ended C→B (operational-inertia risk — C's 🔴; fixed by pre-committing the flip). | C's comparison table + both 🔴 risk findings; A's posture (ii) honored via staging. |
| **D2.13** | **LOCKED: enough — with the empirical rule.** Land (b) staged now (rationale §4). Operational triggers: stage-ii log false-fires ⇒ hold flip + fix predicate; any silent-death incident ⇒ immediate flip; NR-3 ≥1 junk event in the 14-day window ⇒ confirms the class is live (supports flip); sustained 0 events does NOT argue against flipping (the predicate simply never fires on healthy traffic — flipping costs nothing observable). | §4; A (posture ii), C (empirical rule), B (14-day volume-normalized window; 7d extended on n=2/session base rate). |
| **D2.14** | **LOCKED:** (c): unit (marker present on zero-tool short-history terminal report; absent on tool-bearing; absent for excluded agents) + integration visibility in parent view. (d)/(e): text-presence unit tests + **registry-completeness test** (every work-turn agent carries the cardinal; every parent agent carries scrutiny guidance) — no behavior tests (not unit-testable; empirical only). (b): unit predicate tests (fires in incident shape incl. FIRED-but-unenqueued fixture; dormant on healthy paths) + B.S.3 fail-OPEN 3-scenario suite + NR-1 incident-repro integration + B.S.4 reconciler bridge + B.S.5 double-fire + B.S.6 ordering. (a): n/a initially. | A's per-candidate scaffold costs + B's registry-completeness innovation (closes the rot hole). |
| **D2.15** | **LOCKED: NR-2 constant now** (`daemon/constants.py`), lifting BOTH the `config.py:1427` literal AND the consumer read `report_repair_cfg.repair_excluded_agents` (`child_reports.py:1561`) into one shared constant; evaluate `watcher` (empty `tools.allow`) for inclusion at landing. Generic per-agent opt-out mechanism: DEFERRED (OQ-1) with re-open trigger = a third text-only-by-design agent appearing. | C (grep: only two consumers in `daemon/`); A (watcher flag). |
| **D2.16** | **LOCKED: OUT OF SCOPE — DEFERRED.** FLAG-1 stays FLAGGED for a future stability-pass feature; composing mechanisms (drift reconciler 300s, RDRS lanes, observer requeue 900s±90, PAUSED claim deferral, WorkerPool claim loop, by-design wait_for_idle) are a separate class from report integrity. Folding grows scope ~50% and dilutes focus. | C; planner's flag-only framing. |
| **D2.17** | **LOCKED: NO coupling.** `_repair_report_with_llm` is truncation-only (junk never invokes it — the `:1312-1313` short-circuit excludes the junk class); repaired junk is still junk for gate purposes; pre-parent repair costs ~19s/completion (W3 precedent, `:1542-1545`); marker-conditioned repair is self-contradictory (marker says "flag", repair would "fix" by hallucinating). Leave untouched. | C §D2.17; A concurs implicitly ((c) is the honest-flag alternative to repair). |
| **D2.18** | **LOCKED: two signals, two roles.** Work signal = **last assistant message's `tool_calls` empty** (read at report-build; drives (c) marker + NR-3). Timing signal = **carrier-Task completion** (when the gate runs; drives (b)'s evaluation point). (b) is content-blind — it checks delivery/declaration state only (durable rows), never re-adjudicates content; this keeps the gate cheap and minimizes its false-positive surface. | A's canonical-signal verdict + C's separation; resolves the planner's OQ-1. |

### OQ dispositions (advisory items, resolved in passing)

| OQ | Disposition |
|----|-------------|
| OQ-1 excluded-agent contract | Deferred with re-open trigger (D2.15). |
| OQ-2 inter-report gap | Resolved by the durable predicate (D2.7) — the gap is exactly the FIRED-but-unenqueued / stranded-PENDING window the durable rows capture. |
| OQ-3 repair timing | Moot — no coupling (D2.17). |
| OQ-4 wedge vs (b) overlap | Resolved — no redundancy: wedge sees only WC-status parents; (b) fires at the completion stamp. B.S.5 skip-if-guarding still applies for the WC-state overlap + shared cooldown. |
| OQ-5 turn-reconciler | B.S.4 as planned; seam `child_reports.py:2396`. |
| OQ-6 terminal semantics | Notice text varies by child terminal status (COMPLETED/FAILED/ERROR/TERMINATED → different adjudication playbooks); template parameterized at B.S.1-i. |

---

## 6. No-Regret Items NR-1–NR-4 — CONFIRMED with adjustments (land regardless of family)

| # | Verdict | Adjustment |
|---|---------|-----------|
| **NR-1** incident-repro scaffold | **CONFIRM** | Coherence clarified: asserts the class is *detectable* (NR-3 counter fires + parent-state shape), not that a candidate prevents it; pre-fix red, post-NR-3 green. (b)-prevention assertions are separate Wave-2 tests. |
| **NR-2** exclusion-list constant | **CONFIRM** | Lift BOTH the `config.py:1427` literal and the `child_reports.py:1561` consumer read; single constant; evaluate `watcher` for inclusion. |
| **NR-3** junk-rate counter | **CONFIRM** | Increment BEFORE the `skip_repair` short-circuit (`:1545-1546`) AND the `report_repair.enabled` short-circuit (`:1552-1553`) so ALL terminal completions count, not only repair-eligible ones. |
| **NR-4** truncation-guard audit | **CONFIRM** | Conclusion recorded now: keep the short-circuit NARROW; (c) must fire on exactly the input where it short-circuits (test pins this); widening would break legitimate 1-message reports and duplicate (c)'s signal. |

---

## 7. P1 Validation (secondary mandate) — choke point CONFIRMED for all active traffic; 4 corrections

**Confirmed (verdict-grade, code-enumerated):**
- **C1-D1 choke-point totality:** `graph.astream :3530` is the ONLY production graph-driver site; every active lane funnels through `enqueue_message[_job]` → `WorkerPool.claim` → `ProcessMessageProcessor` → `MessageProcessingPipeline.execute` (`pipeline.py:387`) → `_do_process` (`:399`) → `_process_message_with_tracking` → `:3530`. Verified lanes: HTTP (`messages.py:396`), agent-tool (`instance.py:2904`), watchdog (`waiting_children_watchdog.py:680`), `job_continue` (`job_queue.py:1057`), `invoke_agent_and_wait` (`utils.py:642`), report delivery (`child_reports.py:445`).
- **PROCESS_REPORT lane covered** (registered as second alias of `ProcessMessageProcessor`, `task_processor.py:1077-1098` → same pipeline); child-report-into-poisoned-parent healed today by in-graph site 2 (`graph.py:3145`); T6 seam adds belt-and-suspenders.
- **Watchover drain / question-pause / resume helpers are POST-graph** (run after `astream` returns) — not entry bypasses; correctly out of choke-point scope.
- **T-chain:** T1→T6 (id-format dependency), T2-atomic-with-T3/T4 (set-pin at `test_instance_tools.py:131`), T5-then-T6 input order `[placeholders]+[persistent]+[leftovers]+[user]` all CONFIRMED. T5 needs NO dependency on T2.
- **P1↔P2 seam:** T1–T10 touch no `child_reports.py` lines — no conflict with Wave-1/Wave-2 sites.

**Corrections to phase1-plan (apply before implementation):**
1. 🟡 **Latent bypass — `Manager.send_message` (`:6245` def, `:6258` delegation) → `InstanceMessagingService.send_message` (`:1007`) → `graph.ainvoke` (`:1060`).** Zero production callers (exhaustive grep; only test files + a docs example) but it bypasses `_build_graph_input`, the choke point, AND the in-graph pairing guard. **Recommended: DELETE both methods in P1** (with test-fixture migration to `enqueue_message`; verify `daemon/api.py:124` re-export is unaffected — it is, distinct name), or record explicit accepted-residual-risk in C1-D1. This was plan-overview insight #4 ("address at the shared seam or defer explicitly") — phase1-plan T6 text did not actually address it; now pinned.
2. 🟡 **`:3407` silent-resume branch sets `graph_input = None`** — T5/T6 seam must SKIP prepend/insert on None (in-graph guard covers that path). Add to T6 mechanics.
3. 🟡 **Add explicit T7 → T10 dependency** — under C1-D3 Option A the pure-hang integration test should also exercise the `job_inject` → enqueue wake lane (third wake surface).
4. 🟢 **Anchor fix:** `task_processor.py:1085-1091` is the processor dispatch table, not a "fallback inject" site; correct the §5.1/§6 references (actual path: `ProcessMessageProcessor.process` → `pipeline.execute :387` → `_do_process :399-400`).

---

## 8. C1-D3 Leader-Lock — recorded

C1-D3 = **Option A** (job_inject WC-targets → `manager.enqueue_message`, consistent treatment; `has_instance_busy` pre-check included) recorded as **LOCKED by leader** in `decisions.md` immediately below the OPEN row (original retained for audit). Rationale carried: primitive-selection rule, durability, three-caller consistency, and the T2 forcing fact (eligibility check + docs rewritten either way). See the register for the full row.

---

## 9. Risks (residual, post-recommendation)

- 🔴 **(b) predicate false-fire on a durable-row shape not in the incident fixture** (e.g., a legitimately-consumed report whose row lingers). Mitigation: stage-ii log soak exists precisely to surface this; flip withheld on false-fires; kill-switch revert. This is the single assumption that would flip D2.5 — tested before enforcement, monitored after.
- 🟡 **Prompt edit-set rot** (12+ files, v1/v2 variants, open parent surface). Mitigation: writing-guide mandatory line + registry-completeness test (D2.14); revisit the system-side post-frame channel if consumption logs show ignore-rates.
- 🟡 **(e) compliance is probabilistic** (conduct-rule precedent). Mitigation: dispatch-prompt mirror (proven channel); NR-3 tripwire; (c)+(b) backstops.
- 🟢 **(c) marker downstream fixture breakage** — `SANITY_FLAG_VERSION` suppression path.
- 🟢 **Live RUNNING instances retain pre-edit prompts until graph rebuild** — propagation note, not a defect.

## 10. Confidence

**High** for D2.1/D2.5/D2.6/D2.7/D2.9/D2.10/D2.13/D2.18 (each rests on code-verified, multiply-attributed findings). **Medium-High** for D2.12 timing (soak length is a judgment; pre-commitment bounds it). **Medium** for (e)/(d) efficacy (empirical-only, by nature). Flip-risk assumption: if the stage-ii soak shows the durable-row predicate false-fires on healthy traffic shapes, D2.5's enforcement flip is withheld and the predicate is narrowed — the staged design absorbs this without architectural change.

**Evidence caveat:** commit `43070f6f` was not reachable from this checkout (cited via planning docs — B's unverified item); incident counts rest on the planner's citations. Immaterial to the verdicts: the class's reachability is corroborated by two independent occurrences and the structural blindness finding stands on current code.

---

**End of recommendation.** Register rows in §5 are formatted for direct lift into `decisions.md`; the C1-D3 lock is already applied there.
