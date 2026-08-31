# Plan Overview: wc-wake-report-integrity

**Feature:** `wc-wake-report-integrity`
**Branch:** `feature/wc-wake-report-integrity` @ `1f8f8ed4` (checkout verified; all anchors re-verified on this base by 3 explorer passes)
**Date:** 2026-08-30
**Author:** planner[v2] — dispatcher aggregation of worker outputs (this file synthesizes; the specialist files below are the sources of truth)
**Status:** Review APPROVED-WITH-CONDITIONS — conditions C1–C3 applied 2026-08-30. C2 register CLOSED (18 LOCKED verdicts, `architecture-recommendation.md` §5; Seq-AB operative); C1-D3 LOCKED Option A; C1-D7 added. **Implementation gated on the approver delta-confirmation.**

---

## File Map (sources of truth)

| File | Author channel | Content |
|---|---|---|
| `phase1-plan.md` | worker (plan-creation; ran DIY — skill not injected) | Component 1 (#8 WC-target waking): locked D1–D6 + riders + W5, D3 recommendation (§4), verified current-state map (§5), 11-task dependency-ordered breakdown T1→T10 incl. T6b (§6), test inventory (§7), risks + 4 open questions (§8) |
| `technical-analysis.md` | worker (technical-analysis) | Component 2 decision space: 11-hop premature-completion chain with file:line evidence; failure taxonomy (SD1–SD4); five candidates (a)–(e) with equal rigor (mechanism/seam/failure-modes/false-positives/testability/blast-radius/reversibility/evidence); interaction matrix; 5 decision axes; 3 sequencing options; 18-item OPEN decision seed |
| `phase2-plan.md` | worker (plan-creation) | Component 2 plan: split validation, Phase 2a (architect decision gate, 5-day process, no-regret items NR-1–NR-4), Phase 2b pre-planned branches Seq-A/B/C at implementation-ready level incl. candidate (b) Completion-Gate Sensitivity Protocol (B.S.1–B.S.6), coupling map, risks, success criteria, per-branch file map |
| `decisions.md` | worker (plan-creation) + reconciliation passes | Consolidated namespaced register: **C1** (D1–D7, R1/R2, W5 — leader-owned; all LOCKED/ACCEPTED), **C2** (D2.1–D2.18 — CLOSED 2026-08-30, LOCKED rows lifted verbatim from the ruling; D2.5-FLIP policy row; OPEN rows retained for audit), **FLAG-1** (defer-admission latency — stays FLAGGED per D2.16) |
| `architecture-recommendation.md` | architect (controller; 4 dispatched analysts) | **RULING DOC** — C2 register closure (§5's 18 LOCKED verdicts), Seq-AB waves (§3), evidence bar (§4), NR-1–NR-4 adjustments (§6), P1 validation + 4 corrections (§7), C1-D3 leader-lock (§8) |

## Objective

Two components, one feature:

1. **Component 1 (#8)** — make both public message lanes (HTTP `POST /messages` + agent-tool `send_message`) route WAITING_CHILDREN targets via `enqueue_message` (durable MessageQueue row + PENDING Task → WC→RUNNING flip → `notify_work`), replacing the stranding RAM-FIFO parking lot; plus the D1 enqueue-entry pairing guard (closes the poisoned-tail→2013 exposure for ALL enqueue traffic), D2 leftover-drain ordering, riders R1/R2, and the W5 ordering trade-off. Design locked (D1/D2/D4/D5/D6, D3 = Option A — leader-locked 2026-08-30).
2. **Component 2** — close the silent full-tree-death class (premature zero-tool-call report → parent/grandparent complete while "waiting" on terminal children; 2 occurrences, cf. the 2026-08-30 premature-report incident (technical-analysis §1)). Deliberately **decision-gated**: the fix family (system guards vs prompt hints vs hybrid) is OPEN — the decision space is structured, not pre-decided.

## Phase Structure (split validation: confirmed, with one refinement)

| Phase | Name | Objective | State |
|---|---|---|---|
| **P1** | WC-target waking (#8) | Implementation-ready: T1 R1 ids → T2 `INJECTION_ELIGIBLE_STATUSES` shrink (the pivot) → T3/T4 lanes in parallel → T5 D2 drain (+ S4 seam) → T6 D1 seam guard + R2 (+ S5 skip-None) → **T6b `:1060` bypass deletion** → T7 D3 (LOCKED Option A) → T8–T10 docs/tests/verification | Ready; gated on approver delta-confirmation |
| **P2a** | Architect decision gate | **CLOSED 2026-08-30** — 18 verdicts LOCKED via `architecture-recommendation.md` §5 (hybrid, Seq-AB); NR-1–NR-4 confirmed with adjustments (§6) | Done |
| **P2b** | Seq-AB implementation (operative) | **Wave 1 (days):** NR-1–NR-4 + (e) opening-variant cardinal (D2.11 constraint set) + (d) prompt-side scrutiny (12 parent agents + writing-guide + dispatch-prompt mirror, D2.10) + (c) passive **descriptive-only** marker (D2.9). **Wave 2 (weeks):** staged (b) via B.S.1-i/ii/iii — durable-row predicate (D2.7: `report_injections` PENDING/DEFERRED + `dependency_watchers` FIRED ∧ unenqueued; NOT `pending_watchers`), fail-OPEN inject-notice (D2.6), inline same-tx last gate after bus > tasks (D2.8/S7), pre-committed enforcement flip (owner operator, ≤2wk soak, immediate-on-incident). (a) withdrawn/reserved (D2.2). Seq-A/Seq-C superseded — audit only | Gated on approver delta-confirmation |

**Split rationale** (phase2-plan §2): the two components couple only loosely — the 11-hop chain intersects WC-parking at hop-10/11 only. The Wave-2 (b) predicate reads **durable rows** (`report_injections` PENDING/DEFERRED with terminal child + `dependency_watchers` FIRED ∧ `enqueued_at IS NULL` — D2.7 LOCKED; `pending_watchers` is cache-first and empty exactly in the inter-report gap), while P1's wake primitive writes **MessageQueue + Task rows** — the coupling is shared DB state and shared test fixtures, not a shared bus. **Cross-phase critical path: P1 ships before (b) can be verified end-to-end (T10's three-surface pure-hang test — HTTP, agent-tool, `job_inject` — is the joint exercise; S6).**

## Decision Register Summary (see `decisions.md` for full rows)

- **LOCKED (leader, recorded):** C1-D1 (entry-seam guard), C1-D2 (drain ordering), C1-D4 (WC 202→200; RUNNING keeps 202), C1-D5 (marker loss + durable provenance), C1-D6 (queued PROCESS_MESSAGE counts busy), C1-D7 (`:1060` legacy bypass deletion → T6b), C1-R1 (`pairing-synth-{tc_id}` ids), C1-R2 (CLE regression test), **C1-D3 = Option A** (job_inject WC → `enqueue_message`; leader-locked 2026-08-30, `architecture-recommendation.md` §8).
- **LOCKED (architect, 2026-08-30, §5):** C2-D2.1–D2.18 all closed — hybrid Seq-AB (D2.1/D2.12); (b) ships staged with kill-switch default 0 + fail-OPEN (D2.5/D2.6); durable-row predicate (D2.7); gate ordering bus > tasks > (b) (D2.8); marker descriptive-only (D2.9); (d) prompt-side 12-agent set (D2.10); (e) in with constraint set (D2.11); evidence bar met with empirical rule (D2.13); test depth incl. registry-completeness (D2.14); NR-2 dual-lift (D2.15); FLAG-1 out-of-scope (D2.16); repair decoupled (D2.17); two-signals-two-roles (D2.18). **D2.5-FLIP policy:** owner user/operator; ≤2-week stage-ii soak then flip on first deploy (withheld on false-fires); immediate flip on any silent-death incident.
- **ACCEPTED (recorded):** W5 claim-order race (two turns possible; `claim_pending_task` ORDER BY `created_at ASC`, repository.py:1486; S9 terminal-after-turn-1 edge pinned in T9).
- **FLAGGED (deferred, D2.16):** FLAG-1 defer-admission latency — separate stability-pass feature.

## Research Insights That Shaped the Plan

1. **The WC parking lot strands messages by design** — `set_injection` (manager.py:2377) is a RAM append that never wakes a parked parent; entries die on restart and TTL-purge at ~1h (`_cleanup_stale_injections`, status-agnostic, source-blind). `enqueue_message` is the only house wake primitive ("who wakes the target?" rule).
2. **The enqueue dispatch path has NO pairing guard today** — `_build_graph_input` (instance_messaging.py:176-243) feeds `graph.astream` (:3530) with a poisoned checkpoint tail unguarded; a plain enqueue turn draining nothing reaches the LLM as `AIMessage(tc)→HumanMessage` → gateway 2013 → permanent-error replay loop. The D1 seam closes this for ALL lanes at one choke point.
3. **The "one-tx wake primitive" is rows+flip+event in one tx; `notify_work` is post-commit** (:1711-1712) — plan accounts for the ordering.
4. **Legacy direct path surprise:** `send_message`'s direct `ainvoke` (instance_messaging.py:1060) bypasses `_build_graph_input` — phase1-plan addresses it at the shared seam or defers explicitly. **Resolved: deleted via D7/T6b.**
5. **The report-integrity class is four sub-defects, not one** (SD1 child premature END / SD2 report honesty / SD3 parent adjudication / SD4 gate blind spot); **no single candidate covers the full 11-hop chain** — hence the decision-gated P2 rather than a pre-picked guard.
6. **The completion gate has a confirmed structural blind spot** (inter-report gap: both pending counts legitimately 0 between one child's consumed report and the next child's terminal event) — candidate (b)'s target zone, and the reason the caller flagged completion-gate sensitivity.
7. **D5 is natively true** — the enqueue-path wake `HumanMessage` (:3475) already carries no markers; D5 work is verification/tests, not code. Bonus: WC+images starts working (today's 202 path silently drops them).

## Top Risks (full registers in phase1-plan §8 / phase2-plan §6)

| Risk | Severity | Mitigation |
|---|---|---|
| D1 seam placement wrong (misses paths or duplicates CLE synthesis) | High | Single choke point before astream; R1 ids make seam placeholders idempotent vs in-graph sites; R2 pins the CLE-mirror convention |
| HTTP contract break 202→200 for WC (FE consumers) | Medium | T4 doc updates; FE-latency arc interplay documented (phase1-plan §6-T4/§8-T8); WC slow-path actually disappears |
| W5 two-turn consequence (user msg + report now race) | Medium | Accepted + recorded; T9 updates both the two-turn assertions and the FIFO-leftover single-turn invariant |
| Candidate (b) blocks legitimate parent completion (completion gate = most-sensitive area) | High | B.S.1–B.S.8 protocol: staged landing, kill-switch env, fail-OPEN default + verification tests, durable-row predicate (D2.7 — not the never-fires `pending_watchers`), turn-reconciler bridge, watchdog wedge alignment, gate ordering (bus > tasks > (b), short-circuit on both-zero), pre-committed flip (owner operator, ≤2wk soak, immediate-on-incident) |
| Evidence sufficiency: 2 occurrences vs gate-change blast radius | High | D2.13 explicitly decides the bar; kill-switch + fail-OPEN default lowers the cost of shipping |
| Silent multi-edit write failures (repo convention) | — | Workers verified writes via byte-count + marker greps; decisions.md reconciliation verified again by planner (143 lines, 7 sections, single lineage) |

## Rollback Strategy (summary)

- **P1:** kill-switch semantics RESOLVED (Q2, leader 2026-08-30 — `decisions.md` C1-Q2): config flag, OFF during soak, flip per D2.5-FLIP policy; task-ordered commits make partial reverts surgical; no DB/schema changes in P1.
- **P2b:** every system-side candidate behind env kill-switches (`WC_REPORT_INTEGRITY_A/B_*` mirroring `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`); (c) marker-versioned via `SANITY_FLAG_VERSION`; prompt-side = trivial git reverts.

## Reading Order

- **Developer/leader (P1, post-approver-gate):** `phase1-plan.md` (all of it; D3 LOCKED Option A; T6b deletion included) → execute T1 → T2 → (T3,T4) → T5 → T6 → T6b → T7 → T8 → T9 → T10.
- **P2 implementers (Wave 1 first, post-approver-gate):** `architecture-recommendation.md` (ruling) → `phase2-plan.md` §4.0 + §4.2 (operative Seq-AB waves + corrected Wave-2 spec) → `decisions.md` (register of record).
- **Everyone:** `decisions.md` is the register of record — status flips go there; `architecture-recommendation.md` §5 is the closure authority.
