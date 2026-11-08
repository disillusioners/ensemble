# Phase 5 — The Persona Gate (JEV)

**File:** `v2-phase5-plan.md`  
**Depends on:** Phase 2 (`v2-phase2-plan.md`).  
**Flags / Decisions consumed:** DD-9, V2-9, V2-13 (`v4-federated-sketch.md` §3/§5).

---

## 1. Objective

Deliver the JEV-gated Persona Evolution subsystem, activating the dormant v1 persona authoring seam:
1. Implement `MemoirPersonaGateService` in `daemon/services/memoir_persona_gate.py` to evaluate whether reflection syntheses warrant persona evolution.
2. Implement the JEV (Judgment, Evaluation, Validation) classification pipeline:
   - Gated classes: `conflict > boundary_change > new_rule` force persona regeneration.
   - Suppressed classes: `reinforcement` and `none` are strictly discarded, preventing churn on conversational pleasantries.
3. Hook into the dormant `MemoirPersonaRepository` seam:
   - Calls `create_draft(agent_id='ari', body=proposed_body, source='ari')`.
   - Calls `submit(draft.id)` to transition the draft to `pending_approval`.
4. Enforce the constitutional human approval gate:
   - `approve(version_id, actor)` strictly requires a human session actor (`actor.auth_method == "session"`), enforced by `_require_human_lane` (`repository.py:231`).
   - The live running model NEVER freehand-edits or auto-approves its own durable persona (honoring the Hy-Memory production scar).
5. Ensure persona proposals are structured, storable objects carrying JEV classification metadata, conforming to future v4 cross-kingdom federation requirements (`v4-federated-sketch.md` §3).

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 5.1** | Two-stage JEV classification: Stage 1 rule-based heuristic filter (detects verbatim overlap and trivial diffs); Stage 2 LLM classifier evaluating behavioral impact. | Heuristic pre-filter discards trivial pleasantries without spending LLM tokens. Only genuine candidate shifts incur LLM classification cost. | Full LLM evaluation on every reflection tick; OR heuristic-only filtering. |
| **V2-D 5.2** | Strict JEV regeneration gating: ONLY `conflict`, `boundary_change`, or `new_rule` may generate a draft proposal. `reinforcement` and `none` NEVER trigger proposals. | Blessed v2 scope & roadmap: stops persona churn on repeated pleasantries while forcing updates when a genuine contradiction or boundary shift occurs. | Regenerating persona on any synthesis; OR static unchanging persona. |
| **V2-D 5.3** | Activate pre-staged dormant seam: `create_draft(source='ari')` followed by `submit()`. Status lands `pending_approval`. | v1 `MemoirPersonaRepository:163-207` already contains `create_draft` and `submit` with `source='ari'`. Reusing this tested state machine preserves v1 architecture. | Writing directly to `memoir_persona` pointer; OR creating an ad-hoc proposal table. |
| **V2-D 5.4** | Human session approval strictly required: `approve()` raises `HumanLaneRequiredError` unless `actor.auth_method == "session"`. | Constitutional doctrine DD-9: live model cannot edit its durable self. Only a human operator/user in an authenticated session can approve identity revisions. | Allowing the agent to approve its own draft; OR approving via automated test script tokens. |
| **V2-D 5.5** | Storable structured proposal document (`v4-federated-sketch.md` §3/§5): proposal carries original persona version, candidate diff, JEV classification tag, and reasoning. | Satisfies R3 backward constraint: in v4 federation, the proposal document is the cross-kingdom merge unit. Storing structured metadata prevents format obsolescence. | Freeform raw markdown strings with no classification metadata. |

---

## 3. Domain Detail & JEV Classification Specification

### 3.1 Classification Taxonomy
The JEV classifier categorizes candidate belief deltas against the current approved persona:
1. `conflict` (Highest priority): Directly contradicts an existing persona rule or behavioral directive (e.g. Ari previously believed X was forbidden, but Emperor explicitly commanded X). **-> FORCES REGENERATION.**
2. `boundary_change`: Expands or restricts Ari's operational domain, autonomy boundaries, or relationship stance (e.g. Ari is granted new decision-making authority over a subsystem). **-> FORCES REGENERATION.**
3. `new_rule`: Introduces a novel, enduring behavioral principle not previously covered in the persona document. **-> FORCES REGENERATION.**
4. `reinforcement`: Re-asserts or validates an existing persona rule without altering its boundaries or meaning. **-> SUPPRESSED (No proposal).**
5. `none`: Incidental operational context, transient state, or pleasantries with no enduring identity impact. **-> SUPPRESSED (No proposal).**

### 3.2 Proposal Lifecycle
```text
[Reflection Synthesis] ──> JEV Heuristic Filter
                                   │ (non-trivial delta)
                                   ▼
                         JEV LLM Classifier
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
[reinforcement / none]                  [conflict / boundary_change / new_rule]
        │                                               │
   (Discarded)                                          ▼
                                          create_draft(source='ari')
                                                        │
                                                        ▼
                                                     submit()
                                                        │
                                                        ▼
                                                [pending_approval]
                                                        │
                                           (Awaiting Human in Chat/UI)
                                                        │
                                                        ▼
                                           approve(actor=human_session)
                                                        │
                                                        ▼
                                                    [approved]
                                            (Pointer Row Updated)
```

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T5.1** | Implement `MemoirPersonaGateService` in `daemon/services/memoir_persona_gate.py` with two-stage diff evaluation. | Service evaluates candidate syntheses against active persona; returns JEV category and explanation. | `daemon/services/memoir_persona_gate.py` | P2 | R5 | AC1 |
| **T5.2** | Implement prompt and parser for JEV LLM classification with strict JSON extraction and fallback to `none`. | LLM reliably classifies edge cases; parse failure defaults safely to `none` (fail-safe against persona churn). | `daemon/services/memoir_persona_gate.py` | T5.1 | R3 | AC2 |
| **T5.3** | Wire persona proposal creation to `MemoirPersonaRepository`: invoke `create_draft(source='ari')` and `submit()` when JEV category ∈ `{conflict, boundary_change, new_rule}`. | Proposals land in `memoir_persona_versions` with status `pending_approval`; source recorded as `ari`. | `daemon/services/memoir_persona_gate.py`, `daemon/repositories/memoir/repository.py` | T5.2, P1 | — | AC3 |
| **T5.4** | Verify and test human session approval gate: `approve()` allows session actor; strictly rejects machine / tool / synthetic actors with `HumanLaneRequiredError`. | Session token approves draft and updates pointer row; machine token is rejected with 403. | `daemon/repositories/memoir/repository.py`, `daemon/routers/prompt_version.py` | T5.3 | R5 | AC4 |
| **T5.5** | Implement operator review surfaces: Add API endpoint / CLI helper to list pending persona proposals with JEV classification and diff view. | Operator can inspect pending proposals, review rationale, and trigger approval/rejection. | `daemon/routers/memoir.py` | T5.4 | — | AC5 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | JEV gating discipline: Trivial syntheses (pleasantries) classify as `none` and produce ZERO database writes; syntheses corroborating existing rules classify as `reinforcement` and produce ZERO writes; novel rules classify as `new_rule` and trigger proposal creation. |
| **AC2** | Draft creation state: When triggered, a new version is created in `memoir_persona_versions` with `version = current + 1`, `status = 'pending_approval'`, `source = 'ari'`; active persona pointer in `memoir_persona` remains UNCHANGED. |
| **AC3** | Human-lane enforcement: Attempting to call `approve()` with an agent or background worker actor raises `HumanLaneRequiredError`; calling with `actor.auth_method == "session"` succeeds, transitions status to `approved`, and updates pointer row. |
| **AC4** | Structured metadata round-trip (`v4-federated-sketch.md` §3): Proposal text and stored records carry the JEV category, justification string, and source synthesis citations. |
| **AC5 (Mutation)** | Bypassing JEV classification to allow `reinforcement` to propose flips AC1 RED; removing `_require_human_lane` from `approve()` flips AC3 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_jev_classifier_test.sh`: Tests heuristic filter, LLM classification prompt, JSON extraction, and category categorization.
- `test/packs/memoir_v2_persona_proposal_test.sh`: Tests draft creation, submission, status transitions, and pointer row isolation.
- `test/packs/memoir_v2_human_approval_gate_test.sh`: Tests human session authorization, actor validation, rejection of machine tokens, and pointer row updates.

---

## 7. Flag Consumption

- **DD-9:** Persona edits land PENDING; human-lane approval only.
- **V2-9:** The Persona Gate (JEV) architecture.
- **V2-13:** Structured proposal document format per `v4-federated-sketch.md` §3/§5.
