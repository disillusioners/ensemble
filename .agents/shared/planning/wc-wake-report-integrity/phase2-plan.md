# Phase 2 Plan: wc-wake-report-integrity — Component 2 (Report-Integrity)

Date: 2026-08-30
Author: planner[v2] via plan-creation worker
Status: RECONCILED 2026-08-30 — **Seq-AB operative** (D2.12 LOCKED); Phase 2a CLOSED (C2 register LOCKED via `architecture-recommendation.md` §5); Phase 2b pending the approver delta-confirmation gate. See the reconciliation banner below.
Branch: `feature/wc-wake-report-integrity @ 1f8f8ed4`
Companion: `technical-analysis.md` (decision-space structurer, 615 lines)
Companion: `decisions.md` (this feature's consolidated decision register)
Companion: `phase1-plan.md` (Component 1: WC-target waking; written in parallel by another worker — see §1)
Ruling: `architecture-recommendation.md` (2026-08-30) — THE ruling doc for this plan (§5 verdicts, §3 waves, §6 NR adjustments, §7 P1 corrections, §8 C1-D3 lock)

> **RECONCILIATION BANNER — 2026-08-30 (C2 register CLOSED via `architecture-recommendation.md` §5; all 18 D2.x LOCKED).** **D2.12 = Seq-AB is THE operative sequencing**: a two-wave hybrid — **Wave 1 (days)**: NR-1–NR-4 + (e) opening-variant cardinal (D2.11 constraint set) + (d) prompt-side scrutiny guidance (D2.10: 12 parent agent defs + writing-guide mandatory line + dispatch-prompt mirror) + (c) passive DESCRIPTIVE-ONLY marker (D2.9); **Wave 2 (weeks)**: staged (b) via B.S.1-i/ii/iii with the durable-row predicate (D2.7) + companions B.S.3–B.S.6 (+ B.S.7/B.S.8), with a **pre-committed enforcement flip** (stage-ii log soak ≤2 weeks → flip on first deploy, withheld on false-fires; immediate flip on any silent-death incident — D2.5/D2.5-FLIP). **Seq-A (§4.1) and Seq-C (§4.3) are SUPERSEDED** — their text is retained for audit only. **Pure Seq-B day-1 enforcement is SUPERSEDED** by the staged B.S.1 landing. §4.2 remains operative as the Wave-2 (b) specification, as corrected below (D2.2/D2.7/D2.9/D2.10).

---

## 1. Objective

Close the **silent full-tree-death class** (the premature-report / completion chain — 2 occurrences in this session — the 2026-08-30 premature-report incident; original commit ref `43070f6f` unreachable from this checkout) via a **decision-gated** implementation:

- **Phase 2a** records every OPEN decision in `decisions.md` with an architect owner and a closed-by date.
- **Phase 2b** lands implementation behind kill-switches + fail-OPEN defaults, sized to the chosen family, and verifiable by red-green unit tests **plus** an incident-repro integration test that survives every branch.

A single sentence that, when true, marks Phase 2 complete: *No child instance can persist a zero-tool-call no-work report through the completion gate to a parent that declared itself waiting on that child, and any regression of the same class surfaces in the existing test suite before it reaches production.*

This plan covers **Component 2 (report-integrity) only**. Component 1 (WC-target waking, #8) is locked in `phase1-plan.md` and is referenced here only for cross-coupling notes.

---

## 2. Split Validation (P1 / P2 Hypothesis)

*(Reconciliation 2026-08-30: see header banner — Seq-AB operative. The coupling-table row below now carries the D2.7 LOCKED durable-row predicate, replacing the pre-ruling `dependency_bus.pending_watchers` suggestion.)*

**Caller hypothesis:** P1 = #8 WC-target waking (in `phase1-plan.md`); P2 = report-integrity / premature-completion class.

### Verdict: **Hypothesis confirmed with one refinement.**

The 11-hop premature-completion chain (technical-analysis §"11-Hop Premature-Completion Chain") intersects WC-parking **only at hop-10/11** — the parent's silent COMPLETED stamp while still WAITING_CHILDREN on a terminal child. WC parking itself is not on the chain; what is on the chain is the **silent-death propagation** that survives WC parking.

| Coupling concern | Severity | Notes |
|---|---|---|
| **RECONCILED (D2.7 LOCKED, 2026-08-30):** candidate (b)'s declared-waiting predicate reads **durable DB state** — NOT `dependency_bus.pending_watchers`, NOT `instances.status = WAITING_CHILDREN` (deprecated as control-flow per technical-analysis §"Technical Debt" item 1). **PRIMARY:** `report_injections` rows for the parent with `state IN ('PENDING','DEFERRED')` whose child is terminal (write-once obligation invariant; promote `count_pending_for_parent` `daemon/repositories/report_injection/repository.py:1042` + child-terminal JOIN + same-tx adaptation). **CORROBORATING:** `dependency_watchers` rows FIRED ∧ `enqueued_at IS NULL`. `pending_watchers` never fires here — cache-first read (`dependency_bus.py:960-961`) purged post-`emit_terminal` (`:709`) → EMPTY in exactly the inter-report gap | High | Mitigated in §4.2 (b) branch spec (B.S.1-i predicate; B.S.7 ordering) |
| WC-parked parents have **no live carrier Task** by design (PROCESS_REPORT lane creates the carrier only at child completion); any wedge predicate `ANDed with "no live carrier"` MUST ALSO be `ANDed with "zero non-terminal children"` (KB "Spurious enqueue wake" warning; verified 2026-08-29) | High | Carried into candidate (b)'s predicate spec |
| Candidate (b) re-opening a parent's turn via notice injection MUST survive LangGraph checkpoint round-trip; the existing synthetic system msg seam (`GET /messages` injects `is_synthetic=true`) is the proven delivery vehicle | Medium | Already supported by `additional_kwargs["source"]` injection-marker pattern |
| `enqueue_message` (the WC wake primitive per KB "Design rule — who wakes the target?") is **not** on the report-integrity chain — coupling is loose, not tight | Low | Cross-cite only |

### P2 Decomposition — **RECOMMENDED STRUCTURE (advisory)**

I recommend P2 split into **P2a (decision gate + no-regret instruments) → P2b (chosen-family implementation)**. This is **not** itself an OPEN decision — the analyst's charter pre-decided that the architect chooses the family, and that choice requires registered decisions before implementation. The decomposition is structural to that flow.

| Sub-phase | Title | Cardinality of work | When it runs |
|---|---|---|---|
| **P2a** | Architect decision gate + no-regret instruments | ~5–7 days | Immediately on plan approval |
| **P2b** | Chosen-family implementation | Varies by family (analysis Axis 1: 3 days for (c)+(d) alone → 3–4 weeks for (a)+(b)+(c)+(d)) | After every C2-D2.x decision is LOCKED in `decisions.md` |

Rationale (firm): the architect must close the 18 OPEN decisions before any candidate implementation begins — otherwise we ship a guard that the architect would have specified differently. The only work that is **safe to start before the gate closes** is the no-regret instrument set (incident-repro test scaffold, observability counters, exclusion-list normalization) that every family needs regardless of choice.

---

## 3. Phase 2a — Architect Decision Gate (Firm Dates)

### 3.1 Inputs

| Input | Path | Purpose |
|---|---|---|
| Technical analysis | `.agents/shared/planning/wc-wake-report-integrity/technical-analysis.md` | Per-candidate mechanism / seam / blast-radius; the 11-hop chain; the 18-item decision seed (D2.1–D2.18) |
| Decision register | `.agents/shared/planning/wc-wake-report-integrity/decisions.md` | C1 + C2 + FLAG-1 register; architect closes C2 items, leader closes C1-D3 |
| WC wake context | Shared context `wc-wake-report-integrity-…` (2026-08-30 18:25:00) | Spurious-wake hazard, `enqueue_message` as the wake primitive, parked-parent-wake test corollary |
| Phase 1 plan | `.agents/shared/planning/wc-wake-report-integrity/phase1-plan.md` | Cross-reference for C1 coupling (esp. C1-D3 WC routing in job_inject) |
| Precedent kill-switch | `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` (env-driven kill-switch, governor feature merged bb759ee3) | Pattern for (b)'s and (a)'s config gating |
| **Architect ruling (C2 closure)** | `.agents/shared/planning/wc-wake-report-integrity/architecture-recommendation.md` | THE ruling doc for this plan's reconciliation: §5 verdicts (D2.1–D2.18 LOCKED, lifted into `decisions.md`), §3 waves (Seq-AB), §6 NR adjustments, §7 P1 corrections, §8 C1-D3 leader-lock |

### 3.2 Process (the only phase with firm dates)

| Day | Activity | Owner | Output |
|---|---|---|---|
| Day 1 | Architect reads `technical-analysis.md` end-to-end + `decisions.md` C2 register; opens a fan-out thread tagged `c2-architect-fanout` | architect | Decision-ready state |
| Day 2 | Walk through D2.1 (fix family) and D2.12 (sequencing) — these are the gating decisions for everything else | architect + planner observer | LOCKED D2.1, D2.12 in `decisions.md` |
| Day 3 | Per-candidate cluster: D2.2/D2.3/D2.4 (candidate a), D2.5/D2.6/D2.7/D2.8 (candidate b), D2.9/D2.10/D2.11 (c/d/e) | architect | LOCKED D2.2–D2.11 in `decisions.md` |
| Day 4 | Cross-cutting: D2.13 (evidence bar), D2.14 (test depth), D2.15 (exclusion list), D2.17 (repair-with-LLM backstop), D2.18 (carrier-vs-completion) | architect | LOCKED D2.13–D2.18 in `decisions.md` |
| Day 5 | Reconciliation: verify every chosen candidate has a kill-switch name + fail-mode default + at least one integration-test surface; D2.16 (FLAG-1) closed | architect + planner | All C2-D2.x LOCKED or DEFERRED-with-rationale; P2b unblocked |

**Exit criterion for Phase 2a:** every row in `decisions.md` C2 section has a non-OPEN status (LOCKED / DEFERRED / WITHDRAWN). D2.16 (defer-admission latency) resolves the FLAG-1 in or out of scope.

### 3.3 Pre-implementation no-regret items (parallelizable with the fan-out)

These items are **safe to start immediately** because every candidate branch (Seq-A / Seq-B / Seq-C) benefits from them and none of them commits to a family choice.

| # | Item | Files touched | Acceptance | Why every branch needs it |
|---|---|---|---|---|
| **NR-1** | Incident-repro integration test scaffold (parent declared-waiting → child emits zero-tool no-work opener → child COMPLETED → parent COMPLETED while child terminal-and-unnoticed → assert this DOES NOT happen) | new `tests/integration/test_report_integrity_repro.py`; fixtures under `tests/integration/fixtures/report_integrity/` | Red on current code; test shape matches the analysis §"11-Hop Premature-Completion Chain" hop-1 → hop-11 sequence. **§6 adjustment (2026-08-30):** coherence = asserts the class is **DETECTABLE** (NR-3 counter fires + parent-state shape), NOT that a candidate prevents it; pre-fix red, post-NR-3 green; (b)-prevention assertions are separate Wave-2 tests. | Every candidate fix must turn this test green. Without the scaffold, each candidate reinvents the repro. The scaffold itself **assumes the class, not the fix** — it asserts that the silent-death scenario is detectable, not that any particular candidate prevents it. |
| **NR-2** | Excluded-agent normalization (**D2.15 LOCKED / §6 adjustment, 2026-08-30**): lift **BOTH** the `{"wanderer","explorer"}` literal at `daemon/config.py:1427` (report_repair default_factory) AND the consumer read `report_repair_cfg.repair_excluded_agents` (`child_reports.py:1561`) into **ONE shared constant** in `daemon/constants.py`; evaluate `watcher` (empty `tools.allow`) for inclusion at landing. Generic per-agent opt-out mechanism: DEFERRED (OQ-1) — re-open trigger: a third text-only-by-design agent appearing. | new `daemon/constants.py` entry + `child_reports.py:1561` consumer-site update (+ `config.py:1427` literal removal) | Existing tests green; new constant imported by every future candidate (a)/(b)/(c) | (a) and (c) MUST consult the exclusion list. A constant is the cheapest seam; future per-agent opt-out (D2.15) builds on this. |
| **NR-3** | Junk-rate counter (a single Prometheus-style metric `report_integrity_junk_report_total` incremented inside `_get_last_assistant_message_raw` at `child_reports.py:1479-1559` when the returned message has `tool_calls == []` AND the history is short (≤2 non-synthetic messages per `_is_likely_truncated_report` short-circuit at `:1312-1313`)). Default ON, no-op log if no metric sink attached. **Increment placement (§6 adjustment, 2026-08-30): increment BEFORE the `skip_repair` short-circuit (`:1545-1546`) AND the `report_repair.enabled` short-circuit (`:1552-1553`) so ALL terminal completions count, not only repair-eligible ones.** | `child_reports.py:1479-1559`; new metric definition | Metric emits in unit test with mocked LLM zero-tool response; counter is **observability only** — does not change report content | Seq-A and Seq-C both want observability before deciding to escalate; Seq-B also benefits as a confirmation that the gate is dropping junk. |
| **NR-4** | Truncation-guard short-circuit audit — **CONCLUSION RECORDED (§6 adjustment, 2026-08-30): keep the short-circuit NARROW.** (c) must fire on exactly the input where it short-circuits (`child_reports.py:1312-1313` — test pins this); widening would break legitimate 1-message reports and duplicate (c)'s signal. Remaining work: record the audit memo in `decisions.md` linked to a new ticket. | `child_reports.py:1275-1326`; one-line addition to `decisions.md` | Audit memo linked from `decisions.md` C2 register | Without the audit, (c) ships and silently inherits the same gap. |

NR-1 through NR-4 are the only work that starts before Day 5 of the fan-out. They have NO blocking dependency on a family choice.

---

## 4. Phase 2b — Implementation Branches (Decision-Gated)

Each branch below is **pre-planned at implementation-ready level**: tasks, file seams, test surfaces, acceptance criteria, and inter-branch dependencies. **No branch starts until its corresponding C2-D2.x decisions are LOCKED** in `decisions.md`.

The branches are named after the analysis's three sequencing options: **Seq-A (prompt-observe-guard)**, **Seq-B (guard-first)**, **Seq-C (instrument-first)**. The architect's D2.12 choice selects which branch (or hybrid) executes. Branches are not mutually exclusive — Seq-B and Seq-C can run concurrently if D2.12 selects a hybrid.

### 4.0 Operative Wave Composition — Seq-AB (D2.12 LOCKED 2026-08-30; ruling §3)

**Wave 1 (days — no gate changes):** NR-1 (repro scaffold; asserts detectability) · NR-2 (lift BOTH exclusion literals into one constant) · NR-3 (junk-rate counter, incremented BEFORE both repair short-circuits) · NR-4 (conclusion recorded: keep the truncation short-circuit narrow) · (e) opening-variant cardinal per the D2.11 constraint set, mirrored into dispatch prompts · (d) scrutiny guidance in the **12 parent agent defs** + `docs/agent-prompt-writing-guide.md` mandatory line + dispatch-prompt mirror (D2.10) · (c) passive marker, **DESCRIPTIVE-ONLY text** (D2.9), `SANITY_FLAG_VERSION`-versioned, exclusion-list-aware. Tasks: A.1–A.3 (as corrected) + the NR rows. The open-ended A.4/A.5 observation gates are superseded by the pre-committed Wave-2 flip.

**Wave 2 (weeks — staged gate change):** **B.S.1-i** predicate function (no behavior change; durable-row source per D2.7; unit tests incl. the FIRED-but-unenqueued fixture + NR-1 repro extension) → **B.S.1-ii** predicate-attached log at the COMPLETED stamp sites (`child_reports.py:2361/2366/2546/2551/2706/2711` + `job_feedback_observer.py:2822` + `_update_parent_on_child_complete` `:931/:1045-1101/:1132-1137`); **soak ≤2 weeks** → **B.S.1-iii** enforcement ON (inject adjudication notice, never block) with the **pre-committed flip**: first deploy after soak unless the log shows false-fires; **immediate on any silent-death incident** (D2.5/D2.5-FLIP); kill-switch stays as the revert path. **Companions:** B.S.3 fail-OPEN suite · B.S.4 reconciler bridge (seam `child_reports.py:2396`) · B.S.5 wedge skip-if-(b)-guarding + shared per-parent cooldown · B.S.6 gate-ordering test · B.S.7 inline same-tx LAST-gate ordering · B.S.8 kill-switch registry test. **(a) lands in NO wave** (D2.2 — reserved escalation variant only).

---

### 4.1 Branch Seq-A — Prompt-side first, observe, then guard

> **SUPERSEDED by Seq-AB (D2.12 LOCKED 2026-08-30) — retained for audit only.** The Wave-1-relevant tasks (A.1–A.3, as corrected) survive inside Wave 1 (§4.0); the open-ended A.4 observation window and the conditional A.5 escalation gate are replaced by the **pre-committed** Wave-2 enforcement flip.

**Architect pre-condition (D2.12 = Seq-A or Seq-A hybrid).** D2.1 may constrain family to prompt-side only (no (a) or (b) system guards) OR may permit (a)/(b) as the escalation layer after observation.

#### Seq-A Tasks

| # | Task | Files touched | Depends on | Acceptance |
|---|---|---|---|---|
| A.1 | Land **(e) Child work-discipline cardinal** — **D2.11 LOCKED: (e) IN, belt-and-braces, Wave 1.** Extend the existing closing cardinal (`agents/worker/rule.md:166-175` "REPORT DELIVERY") with the **opening variant**. **Constraint set (D2.11, binding):** (1) binds the opening pattern only (task-dispatched turn ending in future-intent text + zero tool calls); must NOT prohibit end-turn-after-`send_message`, final text-only reports after real work, question-to-parent turns (distinguish future-intent vs request-for-input), one-message acks, explorer-style synthesis (excluded agents anyway); (2) names the consequence (detected as junk); (3) gives the compliant alternative (begin work with a tool call, deliver the report, or ask); (4) single-decision-point phrasing ("before ending any turn"); (5) mirrored into dispatch prompts. **Recipients:** work-turn agents — worker, tester, coder, developer[v2], tidier[v2], planner[v2], reviewer[v2], architect, approver[v2], wanderer, governor. **Exempt:** explorer (text-only by design), `_mother`/`_baby_template`, watcher/image-reader/kb-writer class. | `agents/worker/rule.md`; `agents/developer/rule.md`; per-recipient defs per the D2.11 set; dispatch-prompt mirror | D2.11 LOCKED | Text-presence unit tests + registry-completeness test (every work-turn agent carries the cardinal — D2.14); existing worker persona tests green |
| A.2 | Land **(d) Parent-scrutiny prompt guidance** — **D2.10 LOCKED: PROMPT-SIDE home.** Edit set: the **12 parent agent defs** (leader, project-manager, developer[v2], architect, approver[v2], planner[v2], reviewer[v2], tidier[v2], coder, tester, wanderer, governor — verified via `meta.json team_members`) + a mandatory line in `docs/agent-prompt-writing-guide.md` + the **dispatch-prompt mirror** (the empirically strongest channel). Seed text: "if a child's report shows zero tool-call evidence AND no concrete output artifact, verify by calling `send_message` to the child OR escalating to the user." Condition on the visible `[REPORT SANITY: …]` marker pattern (D2.9 symbiosis); the marker's directive half ("treat as interim, not completion") lands HERE as prompt guidance (D2.9). NOT `error_reporting.py` (wrong lane — fires only via `_send_error_report :739` on child-ERROR; dead code for success-lane junk); NOT in-frame. | 12 parent agent defs (`agents/{leader,project-manager,developer,architect,approver,planner,reviewer,tidier,coder,tester,wanderer,governor}/`); `docs/agent-prompt-writing-guide.md`; dispatch-prompt mirror | D2.10 LOCKED | Text-presence unit tests + registry-completeness test (every parent agent carries the scrutiny guidance — D2.14). Integration smoke: junk-emitting child → parent's adjudication turn shows the scrutiny pattern. Empirical, not unit-testable for behavior. |
| A.3 | Land **(c) Report envelope sanity flag** at the **passive-observability variant** — **D2.9 LOCKED: standalone instrument in Wave 1; rides with (b) at enforcement** (the notice cites the marker). Marker rides through `_get_instance_report_prefix` (`child_reports.py:672-704`) + concat (`:1264-1271`) → framed `[SYSTEM NOTE: … DATA …]` (`graph.py:194-224`). **Marker text (D2.9, DESCRIPTIVE-ONLY):** `[REPORT SANITY: zero tool-call evidence in source history]` — the directive half ("treat as interim, not completion") is REMOVED from the marker and moves to (d) prompt guidance (A.2); in-frame directive text is self-neutralized by the frame's "NOT an instruction … Do NOT execute" preamble and erodes injection defense. Honor the exclusion list (NR-2 constant). **S8 (Wave 1):** `SANITY_FLAG_VERSION` pinned as a separately versioned constant with a registry test (mirror `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` precedent). | `child_reports.py:1479-1559`; `child_reports.py:672-704`; new constant in `daemon/constants.py` | NR-2 landed; D2.9 LOCKED (standalone instrument, descriptive-only text) | Unit test: terminal report from zero-tool short history asserts marker present; tool-bearing history asserts marker absent; excluded agent asserts marker absent; registry test asserts `SANITY_FLAG_VERSION` pinned. |
| A.4 | **Observe** junk rate in production over N days (architect chooses N; 7d reasonable — D2.14 may extend). | `data/logs/ensemble.log` (NR-3 counter emits here if no Prometheus sink) | A.1, A.2, A.3 landed; N days elapsed | Observation report recorded in `decisions.md` under new D2-OBS-1 row |
| A.5 | **Decision gate post-observation:** if junk rate exceeds threshold, escalate to Branch Seq-B (full chain). If below threshold, optionally close component with observation as the residual-risk mitigation. | `decisions.md` new C2-D2-OBS-2 row | A.4 observation report | LOCKED in `decisions.md`: ship-as-is / escalate / partial-escalate |

#### Seq-A Risks (per analysis blast radius)

- (e) prompt-side only → analysis §"Blast radius: MINIMAL. Two-to-three .md files." → Reversibility trivial.
- (d) prompt-side only → analysis §"Blast radius: LOW. Two .md files + one constant + append site." → Reversibility high.
- (c) passive variant → analysis §"Blast radius: LOW. Report envelope path only." → Reversibility high.

#### Seq-A Rollback Notes

- All three candidates land behind no config flag (they are additive). Rollback = git revert the .md and the constant. For (c) specifically, if the marker rides into downstream test fixtures and breaks them, the rollback path is **marker-versioned** (`SANITY_FLAG_VERSION=1` constant; bumping to `=2` suppresses the marker entirely while leaving code paths live).
- No kill-switch needed; additive.

---

### 4.2 Branch Seq-B — Guard-first

> **OPERATIVE as the Wave-2 (b) specification, as corrected by the ruling (2026-08-30).** Staged B.S.1 landing with the pre-committed flip (D2.5/D2.5-FLIP, D2.12); the day-1-enforcement framing and the original (a)/(d) scopes below are superseded — see the corrected B.1/B.2/B.4 rows and §4.0.

**Architect pre-condition (D2.12 = Seq-B or Seq-B hybrid).** D2.5 must LOCK (b) as in-scope. D2.6 (fail-mode) MUST default to **fail-OPEN** (per analysis Axis 3 + Architectural harmonies §1). D2.7 must LOCK the declared-waiting source. D2.8 must LOCK the gate ordering.

#### Seq-B Tasks

| # | Task | Files touched | Depends on | Acceptance |
|---|---|---|---|---|
| B.1 | Land **(b) Terminal-child-aware waiting** behind the kill-switch `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=0` (default OFF at code-land; enforcement flips per B.S.1-iii after the stage-ii log soak — pre-committed flip per D2.5/D2.5-FLIP). Per D2.7 (LOCKED) the predicate source is the DURABLE rows (§2 coupling row; B.S.1-i); per B.S.7 it runs INLINE, same-tx, AFTER the bus and tasks gates. | `daemon/services/child_reports.py:2361/2366/2546/2551/2706/2711`; `daemon/services/job_feedback_observer.py:2822`; `daemon/services/child_reports.py:931/:1045-1101/:1132-1137`; `daemon/config.py` (new env binding) [Correction 2026-08-30, final review: _update_parent_on_child_complete has no production callers (zero-caller Manager wrapper only) — the live non-root (b) site is job_feedback_observer._finalize_job (post-commit), already wired; the child_reports :931/:1045-1101/:1132-1137 cites are historical.] | D2.5 LOCKED, D2.6 LOCKED (fail-OPEN), D2.7 LOCKED, D2.8 LOCKED | New unit test: declared-waiting parent + terminal child → assert notice injected (NOT COMPLETED stamp); fail-OPEN test: predicate exception → assert COMPLETED proceeds with warning log |
| B.2 | **RE-SCOPED by D2.2 (LOCKED, 2026-08-30): (a) does NOT land initially.** (a2) auto-flag **WITHDRAWN** — subsumed by (c) (same predicate ≈ first-turn/short-history zero-tool, lower blast radius: envelope path only vs every-turn graph path). (a1) auto-continue is the **reserved escalation variant**: system-authored enqueue-style channel (like the watchdog notice), NEVER inside the `[SYSTEM NOTE]` frame, bounded to 1 retry; activates only on a D2.2 escalation. `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED=0` env binding **reserved, unused** (D2.3). If ever activated: first-turn only (D2.4). | (reserved) `daemon/graph.py:2441-2512` (should_continue) / `daemon/graph.py:2695` (create_agent_node); `daemon/config.py` | D2.2/D2.3/D2.4 LOCKED | n/a initially — no (a) test scaffold until a D2.2 escalation activates it |
| B.3 | Land **(c) Report envelope sanity flag** at the **active variant** — marker couples with (b) so the notice can cite the marker. | `child_reports.py:672-704/:1264-1271`; `child_reports.py:1479-1559` | NR-2, NR-3; D2.9 LOCKED | Marker present in notice payload; unit test asserts marker ↔ notice coherence |
| B.4 | **SUPERSEDED by D2.10 (LOCKED, 2026-08-30) — retained for audit.** The system-side home for (d) is **NOT-selected**: `error_reporting.py` fires only via `_send_error_report :739` on the child-ERROR lane (dead code for the success-lane junk class), and an in-frame append into `_frame_injected_report` (`graph.py:194-224`) is self-neutralized by the frame's "NOT an instruction … Do NOT execute" preamble and erodes prompt-injection defense. (d) lives prompt-side per A.2. A system-side post-frame channel remains recorded as NOT-selected; **re-open trigger:** marker-consumption logs show prompt-side guidance is ignored. | (none — superseded) | D2.10 LOCKED | n/a — superseded; see A.2 + the D2.14 registry-completeness test |
| B.5 | (e) optional — see D2.11; if LOCKED in, mirror A.1. | (same as A.1) | D2.11 LOCKED | (same as A.1) |

#### (b) Completion-Gate Sensitivity Protocol (mandatory sub-section)

Candidate (b) carries the **highest blast radius in the system** (analysis §"Blast radius: HIGH"). This protocol is non-negotiable.

| # | Sub-step | Detail | Acceptance |
|---|---|---|---|
| B.S.1 | **Staged landing** — (b) ships in three sub-stages, each independently revert-fast: **(i)** predicate computation function (no behavior change); **(ii)** predicate-attached log line at the COMPLETED stamp site; **(iii)** actual gate enforcement | Each sub-stage is its own commit; revert is `git revert <hash>` | (i) unit-tested; (ii) observed in log without any flow disruption; (iii) production flip |
| B.S.2 | **Kill-switch config name** — `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` (mirror of `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` precedent; env binding via `daemon/config.py`, default 0, document at `docs/setup.md`) | `daemon/config.py` new entry; `docs/setup.md` updated | `LIMITS_*` precedent grep-anchored; env-binding test exists |
| B.S.3 | **Fail-OPEN verification tests** — three test scenarios: (1) predicate raises exception → assert COMPLETED proceeds with warning log; (2) predicate returns malformed value → assert COMPLETED proceeds; (3) predicate times out (5s budget) → assert COMPLETED proceeds | `tests/unit/services/test_child_reports.py` extensions; new `tests/unit/services/test_b_fail_open.py` | All three scenarios green; documentation in `tests/unit/services/test_b_fail_open.py` module docstring |
| B.S.4 | **Turn-reconciler compatibility check** (analysis Open Question 6) — if (b) injects a notice that re-opens the parent's turn, the new turn must thread through `reconcile_turn_mirror(work_id)` (authoritative per Critical Note). The inline check at `child_reports.py:2396` is the seam where any change must thread through. Verify by extending `tests/unit/services/test_turn_reconciler.py` (or its modern equivalent) with the (b)-re-open scenario. | `tests/` mirror-table test; one comment in `child_reports.py:2396` referencing `D2.reconciler-bridge` | Test green; comment exists |
| B.S.5 | **Watchdog wedge-predicate alignment** (analysis Open Question 5) — verify the wedge predicate at `waiting_children_watchdog.py:861-893/:896-903/:911-927` is consistent with (b)'s declared-waiting predicate. If (b) blocks, the wedge never needs to fire for the same instance; update the wedge to skip instances that (b) is actively guarding. | `waiting_children_watchdog.py:861-893` predicate extension; one test asserting no double-fire | Test green; wedge predicate doc updated |
| B.S.6 | **Defense-in-depth gate ordering** (D2.8) — three gates read the same "can parent complete?" question: bus-pending COUNT (fail-CLOSED, same-tx, `child_reports.py:2117-2127`); task-pending COUNT (fail-OPEN, `child_reports.py:2008/:2663`); (b) declared-waiting (fail-OPEN). Default ordering per analysis: **bus > tasks > (b)**. Honor D2.8 if architect picks a different ordering. | `child_reports.py` ordered check; one test asserting the order is preserved | Test green; comment in `child_reports.py:2117-2127` references `D2.8` |
| **B.S.7** | **Inline same-tx evaluation, LAST gate** (ruling S7; D2.8) | The (b) predicate runs INLINE in the SAME TRANSACTION as the completion stamp, AFTER both prior gates: bus pending-count (fail-CLOSED, same-tx, `child_reports.py:2117-2127`) > tasks pending-count (fail-OPEN, `:2008/:2663`) > (b) (fail-OPEN, LAST) — evaluated ONLY when both prior counts are zero. Implementation template: the TOCTOU-hardened inline-COUNT pattern at `child_reports.py:2065-2089`. | B.S.6 ordering test extended: (b) evaluates last and only on both-counts-zero; template shape matches `:2065-2089` |
| **B.S.8** | **Versioned-constant registry** (ruling S8 — Wave 2 share) | Kill-switch env names pinned as separately versioned constants with registry tests asserting the exact env names + default-0 binding: `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` and reserved `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED` (mirror `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` precedent). Wave-1 share (`SANITY_FLAG_VERSION` registry test) is scheduled in A.3. | `daemon/constants.py` + `daemon/config.py` registry test green — renaming/deleting either constant fails the test |

#### Seq-B Risks

- (b) is **HIGH blast radius** per analysis — touches turn-reconciler, PROCESS_REPORT lane, ReportDeliveryRecovery, pause/resume, watchdog, question-pause, watchover, defense-in-depth gates. Mitigated by B.S.1 (staged landing), B.S.2 (kill-switch), B.S.3 (fail-OPEN).
- (a) is **MEDIUM blast radius** per analysis — every turn traverses the graph path. Mitigated by exclusion list (NR-2 + D2.15) and config-gated default OFF.
- (c) + (d) together: **LOW blast radius** — additive marker + additive hint.

#### Seq-B Rollback Notes

| Candidate | Kill-switch | Revert-fast | Notes |
|---|---|---|---|
| (a) | `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED=0` | Yes | Env-binding, no daemon restart required if process reads env on each call; otherwise restart |
| (b) | `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=0` | Yes (with restart) | Mirror of `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` precedent |
| (c) | (none needed) — additive marker; if it breaks downstream test fixtures, bump `SANITY_FLAG_VERSION` constant to suppress while keeping code live | N/A | Marker-versioned |
| (d) | (none needed) — additive hint; revert = git revert | N/A | Prompt-file reverts |
| (e) | (none needed) — prompt .md files | Trivial | git revert |

---

### 4.3 Branch Seq-C — Cheapest-instrument-first (envelope flag as canary)

> **SUPERSEDED by Seq-AB (D2.12 LOCKED 2026-08-30) — retained for audit only.** Pure-instrument sequencing was rejected: it leaves the silent-death window open for the whole observation period with permanent-loss exposure (ruling §5 D2.12).

**Architect pre-condition (D2.12 = Seq-C or Seq-C hybrid).** Lowest blast, fastest data, but **silent-death window remains open during observation** (per analysis). Acceptable only if D2.13 (evidence bar) closes "yes, 2 occurrences + generalisability is enough; we can afford the observation window."

#### Seq-C Tasks

| # | Task | Files touched | Depends on | Acceptance |
|---|---|---|---|---|
| C.1 | Land **(c) Report envelope sanity flag** as **pure instrumentation** — no consumer yet, marker rides to parent but parent has no hint to react to it | `child_reports.py:1479-1559`; `child_reports.py:672-704/:1264-1271` | NR-2, NR-3 | Unit test asserts marker present; integration test asserts marker visible in parent's view but no action taken |
| C.2 | **Observe** flag frequency in production over N days | `data/logs/ensemble.log` (NR-3 + flag-specific counter) | C.1 | Observation report |
| C.3 | Decision gate post-observation — if flag fires more than expected, escalate to Branch Seq-B (full chain). If rare, ship **(d) alone** as parent-side consumption. | `decisions.md` new C2-D2-OBS-1 row | C.2 | LOCKED |

#### Seq-C Risks

- Silent-death window **remains open during observation** (analysis §Seq-C "Weaknesses"). If observation period (e.g. 7 days) does not surface enough events to make a decision, escalate per C.3.
- (c) does NOT block or fix; only observes. Risk: observation period coincides with low-junk-rate period and architects under-react.

#### Seq-C Rollback Notes

- (c) rollback = git revert + bump `SANITY_FLAG_VERSION=0`. Trivial.

---

## 5. Coupling Map (Phase 2 ↔ Phase 1 + Phase 2a ↔ Phase 2b)

| | Phase 2a (decision gate) | Phase 2b Seq-A | Phase 2b Seq-B | Phase 2b Seq-C |
|---|---|---|---|---|
| **Phase 1 (WC waking, phase1-plan.md)** | Independent — no decision overlap. **Loose coupling (D2.7 LOCKED, 2026-08-30):** candidate (b)'s declared-waiting predicate reads DURABLE rows — `report_injections` PENDING/DEFERRED-with-terminal-child (primary; `count_pending_for_parent` `report_injection/repository.py:1042` + child-terminal JOIN, same-tx) + `dependency_watchers` FIRED ∧ `enqueued_at IS NULL` (corroborating) — the same durable queue/report layer Phase 1's `enqueue_message` wake path populates. NOT `dependency_bus.pending_watchers` (cache-first `:960-961`, purged post-`emit_terminal` `:709` → EMPTY in the inter-report gap). | Independent | Loose (durable-row layer shared with Phase 1 wake primitive) | Independent |
| **Phase 2a (this plan)** | — | Tight (A.4 observation report feeds back into `decisions.md`) | Tight (B.S.1–B.S.6 protocol is gated on D2.5–D2.8 LOCKED) | Tight (C.3 escalation decision) |
| **Phase 2b Seq-A** | Tight | — | Independent (mutually exclusive in practice; hybrid not recommended) | Independent |
| **Phase 2b Seq-B** | Tight | Independent | — | Independent (hybrid = C.1 then B.1–B.4 is plausible) |
| **Phase 2b Seq-C** | Tight | Independent | Loose (hybrid C → B) | — |

**Cross-phase critical-path risk:** Phase 1 must ship **before** candidate (b)'s predicate can be verified end-to-end (the predicate reads the same durable queue/report layer the wake primitive populates). Mitigation: Phase 1 ships first (per the `phase1-plan.md` ordering); Phase 2b Seq-B's (b) task B.1 includes an integration test that exercises both paths together.

---

## 6. Risks

### 6.1 Per-Branch Risks (lifted from analysis blast-radius assessments)

| Branch | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| Seq-A | (e) cardinal not effective — LLM ignores the rule | Low | Medium | Cardinal text quality + (c) marker as backstop; observe in A.4 |
| Seq-A | (d) parent-scrutinizes legitimate reports | Low | Medium | Hint text quality + (c)-marker-conditioned trigger (only fires when marker present) |
| Seq-A | (c) marker confuses downstream consumers | Low | Low | Marker-versioned; rollout in NR-3 metric first; existing tests asserted before merge |
| **Seq-B** | **(b) BLOCKS legitimate parent completion** | **High** | Medium | **Fail-OPEN default (B.S.3) + kill-switch (B.S.2) + staged landing (B.S.1)** |
| Seq-B | (b) false-positive on excluded agents | High | Low | Exclusion list (NR-2) consulted; D2.15 extends |
| Seq-B | (a) auto-continue forces extra LLM call on legitimate single-message ack | Medium | Low | Exclusion list + D2.4 (first-turn only, not generalized) |
| Seq-B | (a)/(b) interaction with turn-reconciler | High | Medium | B.S.4 reconciler-bridge test |
| Seq-B | (a)/(b) interaction with watchdog wedge predicate (double-fire) | Medium | Medium | B.S.5 wedge-predicate alignment |
| Seq-C | Observation window coincides with low-junk-rate period, architects under-react | High | Medium | C.3 escalation rule explicit; observation period capped at 14 days |

### 6.2 Cross-Cutting Risks (per-bundle)

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| **CCR-1** | **Completion-gate interplay risk** (caller-flagged) — any combination of (a)/(b)/(c)/(d) that touches the completion gate can interact with the existing defense-in-depth gates (`_bus_count_pending_for_target_sync` `job_feedback_observer.py:344-424` and `_count_pending_tasks_for_instance_sync` `:426+`) in unexpected ways | High | Medium | B.S.6 (gate ordering protocol); D2.8 LOCKED before any candidate implementation; one integration test asserting all three gates fire in order |
| **CCR-2** | **Evidence sufficiency question** — 2 occurrences (the 2026-08-30 premature-report incident — technical-analysis §1) is the basis for the entire (b) gate change | High | Low (gating) | D2.13 (evidence bar) — architect closes before B.1; if closed "require one more incident," branch Seq-B defers |
| **CCR-3** | **Carrier-vs-completion signal confusion** — (a) uses AIMessage tool_calls, (b) uses carrier completion, (c) uses content-only; an architect picking the wrong signal for a candidate can produce a guard that doesn't fire on the actual incident | High | Medium | D2.18 LOCKED before any candidate implementation; one test per signal type |
| **CCR-4** | **Excluded-agent list growth** — today `{"wanderer","explorer"}` is hand-maintained; new candidates multiply consumers of this list | Medium | High | NR-2 (constant) + D2.15 (generic opt-out mechanism) |
| **CCR-5** | **The inter-report gap** (analysis §"Technical Debt" item 2 + KB) — between A's report consumed and B's terminal event, both pending counts legitimately 0. The completion gate is silent during this window | High | Medium | (b)'s predicate MUST include "A's report was junk" OR "B's terminal event arrived after A's report was consumed"; D2.7 LOCKED before B.1; new test asserting the gap is covered |
| **CCR-6** | **Truncation-guard short-circuit** (analysis §"Technical Debt" item 5) — `_is_likely_truncated_report` returns False when `len(messages) < 2` (`:1312-1313`); this is exactly the junk-detection gap | High | Medium | NR-4 audit memo + (c) MUST NOT replicate this guard's short-circuit; one test asserting (c) fires on the same input where truncation-guard short-circuits |

### 6.3 Risks That Would Invalidate This Plan

- **(b) closed OUT-of-scope** (D2.5 = "defer behind observability") → branch Seq-B does not execute; Seq-A or Seq-C is the entire Phase 2b. Plan remains valid.
- **D2.13 closed "require one more incident"** → branch Seq-B deferred indefinitely; Seq-A or Seq-C is Phase 2b. Plan remains valid.
- **D2.16 (FLAG-1) closed IN-scope** → this plan grows; FLAG-1 scope note added in §8 becomes a separate sub-phase.

No decision closure invalidates the plan structure. All closures redirect within the documented branches.

---

## 7. Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|---|---|---|
| 1 | All 18 C2-D2.x decisions LOCKED with rationale | `decisions.md` row-by-row audit | 18 / 18 LOCKED (or DEFERRED-with-rationale) |
| 2 | Incident-repro integration test red on current code | `pytest tests/integration/test_report_integrity_repro.py` before P2b | Test fails as expected |
| 3 | Incident-repro integration test green after chosen family lands | `pytest tests/integration/test_report_integrity_repro.py` after P2b | Test passes |
| 4 | Fail-OPEN verification tests green (Seq-B only) | `pytest tests/unit/services/test_b_fail_open.py` | All 3 scenarios green |
| 5 | Turn-reconciler compatibility test green (Seq-B only) | `pytest tests/<reconciler>` with (b)-re-open scenario | Test passes |
| 6 | Watchdog wedge-predicate alignment test green (Seq-B only) | `pytest tests/unit/services/test_waiting_children_watchdog.py` with double-fire assertion | Test passes |
| 7 | Defense-in-depth gate-ordering test green (Seq-B only) | `pytest tests/<gate-ordering>` asserting bus > tasks > (b) | Test passes |
| 8 | Junk-rate counter emits on zero-tool terminal report | `pytest tests/unit/services/test_child_reports.py` with mocked LLM zero-tool response | Counter incremented |
| 9 | Truncation-guard short-circuit audit memo linked from `decisions.md` | grep `audit memo` in `decisions.md` | Memo present |
| 10 | No false-positive spike in excluded-agent flows post-rollout | NR-3 metric; comparison to pre-rollout baseline | Spike < 1σ over 7-day window |
| 11 | `git log` shows per-candidate revert-fast commits for Seq-B's (b) | `git log --grep=stage-` | 3 commits (B.S.1 stage i, ii, iii) |
| 12 | Documentation updated: `docs/setup.md` lists new kill-switch env bindings | grep `WC_REPORT_INTEGRITY_*` in `docs/setup.md` | Both env names present |

---

## 8. Out of Scope / Flagged

| # | Item | Reason | Reference |
|---|---|---|---|
| OOS-1 | **P1 scope (Component 1 — WC-target waking, #8)** | Lives in `phase1-plan.md`; only cross-coupled to P2 via candidate (b)'s declared-waiting predicate (loose coupling). | `.agents/shared/planning/wc-wake-report-integrity/phase1-plan.md` (written in parallel; cross-reference once present) |
| OOS-2 | **The full set of follow-on decisions from analysis Open Questions 1–7** | Some are P2 decisions (D2.18 carrier-vs-completion, D2.17 repair-with-LLM backstop, D2.15 exclusion list); others (carrier-Task race, repair-with-LLM timing) are deferred backlog for future hardening, not blockers for Phase 2. | technical-analysis §"Open Questions" |
| OOS-3 | **Watchdog cadence tuning (`config.py:858-865`, `config.py:1023`)** | Tempting to lower cadence as a "force-fire (b) faster" mitigation, but cadence change is orthogonal to the gate logic and ships in a separate stability pass. | KB + Critical Note "stability remaining shelf" |
| **FLAG-1** | **Defer-admission latency investigation** (94-min pickup-while-idle) | Flagged but **not committed** to this component. Composes of drift reconciler (300s), ReportDeliveryRecoveryService (300s + 10-min-age + 100-batch), observer requeue (900s ±90 jitter), PAUSED-target claim deferral, WorkerPool claim loop, by-design `wait_for_idle` deferral. Distinct class from premature-completion. | technical-analysis §"Flag-Only — Defer-Admission Latency"; `docs/retry-architecture.md:256` |
| OOS-4 | **`wait_for_idle` by-design deferral** | By design — operators have agreed. Not a defect. | `docs/retry-architecture.md` |

---

## 9. Implementation-Ready File Map (Per Branch)

This is the cross-reference the implementer (developer worker in Phase 2b) uses. Every file:line cited is from technical-analysis.md §"References" and is verified.

### Candidate (a) — Premature-first-turn guard

| File | Lines | Touch type | Branch |
|---|---|---|---|
| `daemon/graph.py` | 2441-2512 (`should_continue`) | Add guard predicate (B.2 / A only via hybrid) | Seq-B (B.2) |
| `daemon/graph.py` | 2695 (`create_agent_node`) | Alternative seam for (a1) auto-continue | Seq-B (B.2) |
| `daemon/graph.py` | 939 (LoopDetector scan :982-1122) | Reuse existing tool-call signature scan | Seq-B (B.2) |
| `daemon/config.py` | new entry `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED` | Env binding | Seq-B (B.2) |
| `tests/unit/graph/test_should_continue.py` | new | Fresh-instance zero-tool first-turn fixture | Seq-B (B.2) |

### Candidate (b) — Terminal-child-aware waiting

| File | Lines | Touch type | Branch |
|---|---|---|---|
| `daemon/services/child_reports.py` | 2361/2366/2546/2551/2706/2711 | Pre-stamp check | Seq-B (B.1) |
| `daemon/services/child_reports.py` | 931/1045-1101/1132-1137 (`_update_parent_on_child_complete`) [Correction 2026-08-30, final review: _update_parent_on_child_complete has no production callers (zero-caller Manager wrapper only) — the live non-root (b) site is job_feedback_observer._finalize_job (post-commit), already wired; the child_reports :931/:1045-1101/:1132-1137 cites are historical.] | Pre-stamp check | Seq-B (B.1) |
| `daemon/services/child_reports.py` | 2396 (inline check) | Turn-reconciler bridge seam (B.S.4) | Seq-B (B.S.4) |
| `daemon/services/job_feedback_observer.py` | 2822 (`_finalize_job_db_sync`) | Pre-stamp check | Seq-B (B.1) |
| `daemon/services/waiting_children_watchdog.py` | 263-295 (`_build_wedge_notice`) | Mirror text shape for (b)'s notice | Seq-B (B.1) |
| `daemon/services/waiting_children_watchdog.py` | 861-893/896-903/911-927 (wedge predicate) | Skip-if-(b)-guarding predicate | Seq-B (B.S.5) |
| `daemon/repositories/report_injection/repository.py` | 1042 (`count_pending_for_parent` — PENDING ∪ DEFERRED delivery-owed rows) | D2.7 PRIMARY declared-waiting source: promote + child-terminal JOIN + same-tx adaptation | Seq-B (B.S.1-i) |
| `daemon/repositories/dependency_bus/repository.py` (+ `models.py`) | FIRED ∧ `enqueued_at IS NULL` (documented at `models.py:128`) | D2.7 CORROBORATING declared-waiting signal (FIRED-but-unenqueued fixture) | Seq-B (B.S.1-i) |
| `daemon/services/dependency_bus.py` | 709 (post-`emit_terminal` purge), 960-961 (cache-first read) | Rationale-only citation — why `pending_watchers` is NOT the predicate source (D2.7); no code touch | — |
| `daemon/config.py` | new entry `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` | Env binding (kill-switch) | Seq-B (B.2) |
| `docs/setup.md` | append | Document new kill-switch | Seq-B (B.S.2) |
| `tests/unit/services/test_child_reports.py` | extend | Completion-time injection test | Seq-B (B.1) |
| `tests/test_dependency_bus.py` | extend | Watcher-pending-state fixture | Seq-B (B.1) |
| `tests/integration/test_completion_gate_block.py` | new | Incident-repro + fail-OPEN + defense-in-depth | Seq-B (B.1, B.S.3, B.S.6) |

### Candidate (c) — Report envelope sanity flag

| File | Lines | Touch type | Branch |
|---|---|---|---|
| `daemon/services/child_reports.py` | 1479-1559 (`_get_last_assistant_message_raw`) | Inspect `tool_calls` + history length | Seq-A (A.3) / Seq-B (B.3) / Seq-C (C.1) |
| `daemon/services/child_reports.py` | 672-704 (`_get_instance_report_prefix`) | Marker integration | All branches |
| `daemon/services/child_reports.py` | 1264-1271 (envelope concat) | Marker append | All branches |
| `daemon/services/child_reports.py` | 1561 (excluded-agents consumer; literal `config.py:1427`) | Consult NR-2 constant | All branches |
| `daemon/services/child_reports.py` | 1275-1326 (`_is_likely_truncated_report`) | NR-4 audit memo link | All branches |
| `daemon/constants.py` | new `SANITY_FLAG_VERSION` constant | Versioning for rollback | All branches |
| `daemon/constants.py` | new excluded-agent constant (NR-2) | Exclusion list lift | All branches |
| `tests/unit/services/test_child_reports.py` | extend | Envelope marker test | All branches |

Note (D2.9 LOCKED, 2026-08-30): the marker text is **DESCRIPTIVE-ONLY** — `[REPORT SANITY: zero tool-call evidence in source history]`. The directive half ("treat as interim, not completion") moved to (d) prompt guidance (A.2); the (b) enforcement notice cites the marker at Wave 2.

### Candidate (d) — Parent-scrutiny prompt hint

| File | Lines | Touch type | Branch |
|---|---|---|---|
| 12 parent agent defs (`agents/{leader,project-manager,developer,architect,approver,planner,reviewer,tidier,coder,tester,wanderer,governor}/`) | per-agent `rule.md` / `workflow.md` | Scrutiny guidance — the D2.10 LOCKED prompt-side home | Wave 1 (A.2) |
| `docs/agent-prompt-writing-guide.md` | new mandatory line | Scrutiny-guidance rule for new/edited agents (rot mitigation; D2.14 registry-completeness test) | Wave 1 (A.2) |
| dispatch prompts (per-agent dispatch templates) | mirror line | The empirically strongest channel (2026-08-29 delivery-discipline precedent) | Wave 1 (A.2) |
| `agents/leader/workflow.md` | 667-679 (waiting discipline) | Edit scrutiny guidance | Seq-A (A.2) |
| `agents/leader/rule.md` | 59/151 | Edit scrutiny cardinal | Seq-A (A.2) |
| `agents/developer/rule.md` | 32-33/74/178 | Mirror scrutiny guidance | Seq-A (A.2) |
| registry-completeness test (new) | extend | Every work-turn agent carries the (e) cardinal; every parent agent carries the (d) scrutiny guidance (D2.14) | Wave 1 (A.1/A.2) |

**REMOVED from scope (D2.10 LOCKED, 2026-08-30):** the `error_reporting.py` `PARENT_SCRUTINY_HINT` constant + `_frame_injected_report` append rows + the parallel `PARENT_SCRUTINY_HINT` test — wrong lane (fires only via `_send_error_report :739` on child-ERROR; dead code for success-lane junk) and in-frame directive text is self-neutralized (`graph.py:194-224`). A system-side post-frame channel is recorded as **NOT-selected**; re-open trigger: marker-consumption logs show prompt-side guidance is ignored.

### Candidate (e) — Child work-discipline cardinal

| File | Lines | Touch type | Branch |
|---|---|---|---|
| `agents/worker/rule.md` | 166-175 | Extend cardinal | Seq-A (A.1) |
| `agents/developer/rule.md` | 32-33/74/178 | Mirror | Seq-A (A.1) |
| `agents/worker/soul.md` | 3/20/102/136 | Persona mirror (optional) | Seq-A (A.1) |

---

## 10. References

- `architecture-recommendation.md` (this directory) — **THE ruling doc**: §5 C2 verdicts (register-ready, lifted into `decisions.md`), §3 waves (Seq-AB), §6 NR adjustments, §7 P1 corrections, §8 C1-D3 lock
- `daemon/services/child_reports.py` — completion + report + envelope paths (full file:line citations in technical-analysis §"References")
- `daemon/graph.py` — should_continue, _frame_injected_report, build_instance_graph wiring
- `daemon/services/job_feedback_observer.py` — _finalize_job_db_sync, _bus_count_pending_for_target_sync, _count_pending_tasks_for_instance_sync
- `daemon/services/dependency_bus.py` — pending_watchers (symbol index only — NOT the D2.7 predicate source; see the `:709` purge + `:960-961` cache-first rationale), emit_terminal, fire_for_terminated_target
- `daemon/services/waiting_children_watchdog.py` — wedge predicate, _build_wedge_notice
- `daemon/services/error_reporting.py` — RECOVERY_GUIDANCE_HINT pattern (precedent only — NOT the (d) home per D2.10)
- `daemon/repositories/instance/repository.py` — parents_with_non_terminal_children, hang predicate exclusions
- `daemon/config.py` — kill-switch env binding pattern (precedent: LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED at `daemon/config.py:486` — `governor_recursion_guard_enabled`; `:858/:973` are the drift-reconcile sweep, not the guard)
- `agents/worker/rule.md` — existing REPORT DELIVERY cardinal
- `agents/leader/workflow.md` — existing waiting discipline
- 2026-08-30 premature-report incident — incident pattern evidence (technical-analysis §1; original commit ref `43070f6f` unreachable from this checkout)
- Commit `2026-08-29` (agent-architecture blueprint) — dispatch-prompt delivery-discipline pattern (precedent for prompt-side efficacy)

---

**End of Phase 2 plan.** Implementation-ready on architect-closure of the C2-D2.x register. See `decisions.md` for the decision register and `phase1-plan.md` for Component 1.