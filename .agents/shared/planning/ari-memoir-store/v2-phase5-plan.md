# Phase 5 — The Persona Gate (JEV)

**File:** `v2-phase5-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`), Phase 2 (`v2-phase2-plan.md`).  
**Flags / Decisions consumed:** DD-9, V2-9, V2-13 (`v4-federated-sketch.md` §3/§5), V2-20 (§2-Q4), V2-37 (B1), V2-38 (B2), V2-39 (B3), Suggestion #6.

---

## 1. Objective

Deliver the JEV-gated Persona Evolution subsystem, activating the dormant v1 persona authoring seam:
1. Implement `MemoirPersonaGateService` in `daemon/services/memoir_persona_gate.py` to evaluate whether reflection syntheses warrant persona evolution.
2. Implement the JEV (Judgment, Evaluation, Validation) classification pipeline:
   - Gated classes: `conflict > boundary_change > new_rule` force persona regeneration.
   - Suppressed classes: `reinforcement` and `none` are strictly discarded, preventing churn on conversational pleasantries.
3. **Failure Isolation & Candidate Parking in `memoir_jev_ledger` (B2 / Register #16 / V2-38):** On classifier failure (LLM down or parse error), do NOT default to `none` (silently lossy). Instead, PARK candidate record in `memoir_jev_ledger` (`synthesis_id`, `attempts`, `last_error`, `status='pending'`), retry next pass, and terminalize after 5 attempts as `status='terminal'`, `last_error='terminal:gate_unavailable'`.
4. **Persona Document Composition & Unbuildable Diff Parking (B3 / V2-39):** When JEV classifies `conflict`, `boundary_change`, or `new_rule`, compose `proposed_body` from active persona body + triggering synthesis claim via `invoke_raw_with_failover`, bounded strictly within 2000 tokens (`MEMOIR_PERSONA_TOKEN_BUDGET`, `memoir_briefing.py:133`). If diff exceeds 2000 tokens or is unbuildable/malformed, candidate is parked in `memoir_jev_ledger` (attempts incremented; terminal after 5 attempts as `terminal:unbuildable_diff`), NEVER silently dropped to `none`.
5. **Write Purity Pin (§2-Q4):** The JEV gate, draft creation (`create_draft(source="ari")`), and supersession pointers are exercised strictly from the reflection **SERVICE layer, never the tool module** (`daemon/tools/memoir.py` remains read/append pure).
6. Hook into the dormant `MemoirPersonaRepository` seam:
   - Calls `create_draft(agent_id='ari', body=proposed_body, source='ari')`.
   - Calls `submit(draft.id)` to transition the draft to `pending_approval`.
7. Enforce the constitutional human approval gate:
   - `approve(version_id, actor)` strictly requires a human session actor (`actor.auth_method == "session"`), enforced by `_require_human_lane` (`daemon/repositories/memoir/repository.py:231`), using `HumanLaneRequiredError` (DEFINED at `daemon/services/prompt_version_service.py:184`, RAISED at `:678-716`, and mapped to 403 in `daemon/routers/prompts.py:51-56` — Suggestion #6).
   - The live running model NEVER freehand-edits or auto-approves its own durable persona (honoring the Hy-Memory production scar).
8. **Minimal Human-Lane-Gated Persona Version HTTP Surface (B1 / Leader ruling 2026-11-09 / V2-37):** Implement minimal REST surface in `daemon/routers/memoir.py` under `/api/memoir/persona`: `GET /pending` (list pending versions only), `POST /{version_id}/approve` (moves persona pointer in `memoir_persona` to approved version), and `POST /{version_id}/reject` (terminal rejection). Enforces 403 `HUMAN_LANE_REQUIRED` outside human sessions. Strict scope discipline: list/approve/reject only. Note for test packs: hermetic auth tests scrub ambient `ENSEMBLE_AUTH_*` per project POK.
9. Ensure persona proposals are structured, storable objects carrying JEV classification metadata, conforming to future v4 cross-kingdom federation requirements (`v4-federated-sketch.md` §3).

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 5.1** | Two-stage JEV classification: Stage 1 rule-based heuristic filter (detects verbatim overlap and trivial diffs); Stage 2 LLM classifier evaluating behavioral impact. | Heuristic pre-filter discards trivial pleasantries without spending LLM tokens. Only genuine candidate shifts incur LLM classification cost. | Full LLM evaluation on every reflection tick; OR heuristic-only filtering. |
| **V2-D 5.2** | Strict JEV regeneration gating: ONLY `conflict`, `boundary_change`, or `new_rule` may generate a draft proposal. `reinforcement` and `none` NEVER trigger proposals. | Blessed v2 scope & roadmap: stops persona churn on repeated pleasantries while forcing updates when a genuine contradiction or boundary shift occurs. | Regenerating persona on any synthesis; OR static unchanging persona. |
| **V2-D 5.3** | Activate pre-staged dormant seam: `create_draft(source='ari')` followed by `submit()`. Status lands `pending_approval`. | v1 `MemoirPersonaRepository:163-207` already contains `create_draft` and `submit` with `source='ari'`. Reusing this tested state machine preserves v1 architecture. | Writing directly to `memoir_persona` pointer; OR creating an ad-hoc proposal table. |
| **V2-D 5.4** | Human session approval strictly required: `approve()` raises `HumanLaneRequiredError` unless `actor.auth_method == "session"`. Dated anchors (Suggestion #6): `HumanLaneRequiredError` is DEFINED at `daemon/services/prompt_version_service.py:184` and RAISED at `:678-716`; enforced at `daemon/repositories/memoir/repository.py:231`; mapped to 403 `HUMAN_LANE_REQUIRED` at `daemon/routers/prompts.py:51-56`. | Constitutional doctrine DD-9: live model cannot edit its durable self. Only a human operator/user in an authenticated session can approve identity revisions. | Allowing the agent to approve its own draft; OR approving via automated test script tokens. |
| **V2-D 5.5** | Storable structured proposal document (`v4-federated-sketch.md` §3/§5): proposal carries original persona version, candidate diff, JEV classification tag, and reasoning. | Satisfies R3 backward constraint: in v4 federation, the proposal document is the cross-kingdom merge unit. Storing structured metadata prevents format obsolescence. | Freeform raw markdown strings with no classification metadata. |
| **V2-D 5.6** | JEV candidate parking on classifier failure in `memoir_jev_ledger` (B2 / Register #16 / V2-38): On LLM down or parse failure, park candidate and retry next pass; terminalize after 5 attempts as `status='terminal'`, `last_error='terminal:gate_unavailable'`. | Defaulting to `none` silently drops candidate persona changes. Parking provides diagnostic visibility and prevents data loss. | Defaulting to `none` (silently lossy); OR crashing the reflection tick. |
| **V2-D 5.7** | Execution ordering & failure isolation: *Commit synthesis -> constellation line -> JEV gate -> keepsake proposal*. | The synthesis is the durable truth; failure in downstream consumers must never roll back or block the committed synthesis. | Combined mega-transaction across synthesis, JEV, and proposals. |
| **V2-D 5.8** | Minimal Human-Lane-Gated Persona REST Surface (B1 / Leader ruling 2026-11-09 / V2-37): Expose 3 endpoints under `daemon/routers/memoir.py`: `GET /api/memoir/persona/pending`, `POST /api/memoir/persona/{version_id}/approve`, `POST /api/memoir/persona/{version_id}/reject`. Follows `prompts.py:51-56/:617-640` pattern enforcing 403 `HUMAN_LANE_REQUIRED` outside human sessions. | Leaving pending persona versions unapprovable via HTTP; full CRUD prompt router clone. | Approver proved v1 lacks any HTTP surface to inspect or approve `pending_approval` persona versions. Minimal 3-endpoint surface unblocks human approval while maintaining strict scope discipline. |
| **V2-D 5.9** | Persona Document Composition & Unbuildable Diff Parking (B3 / V2-39): Compose `proposed_body` from active persona body + synthesis claim via LLM failover lane, bounded within 2000 tokens (`MEMOIR_PERSONA_TOKEN_BUDGET`, `memoir_briefing.py:133`). If diff exceeds 2000 tokens or is unbuildable/malformed, candidate is parked in `memoir_jev_ledger` (attempts incremented; terminal after 5 attempts as `terminal:unbuildable_diff`), NEVER silently dropped to `none`. | Unbounded persona composition; silent drop to `none` on composition failure. | Ensures persona document remains within constitutional briefing budget (2000 tokens). Candidate parking ensures unbuildable diffs are visible and retried rather than lost. |

---

## 3. Domain Detail & JEV Classification Specification

### 3.1 Classification Taxonomy
The JEV classifier categorizes candidate belief deltas against the current approved persona:
1. `conflict` (Highest priority): Directly contradicts an existing persona rule or behavioral directive (e.g. Ari previously believed X was forbidden, but Emperor explicitly commanded X). **-> FORCES REGENERATION.**
2. `boundary_change`: Expands or restricts Ari's operational domain, autonomy boundaries, or relationship stance (e.g. Ari is granted new decision-making authority over a subsystem). **-> FORCES REGENERATION.**
3. `new_rule`: Introduces a novel, enduring behavioral principle not previously covered in the persona document. **-> FORCES REGENERATION.**
4. `reinforcement`: Re-asserts or validates an existing persona rule without altering its boundaries or meaning. **-> SUPPRESSED (No proposal).**
5. `none`: Incidental operational context, transient state, or pleasantries with no enduring identity impact. **-> SUPPRESSED (No proposal).**

### 3.2 Proposal Lifecycle & Candidate Parking (B2, B3)
```text
[Reflection Synthesis Committed] ──> JEV Heuristic Filter
                                            │ (non-trivial delta)
                                            ▼
                                  JEV LLM Classifier
                                            │
              ┌─────────────────────────────┼─────────────────────────────┐
              ▼                             ▼                             ▼
   [reinforcement / none]          [Classifier Failure]       [conflict / boundary_change / new_rule]
              │                             │                             │
         (Discarded)                        ▼                             ▼
                                   [Park in memoir_jev_ledger]    Compose proposed_body (<= 2000 tokens)
                                   (Retry next pass;                      │
                                   terminal @ 5 attempts)          ┌──────┴──────┐
                                                                   ▼             ▼
                                                              [Unbuildable]  [Valid Diff]
                                                                   │             │
                                                                   ▼             ▼
                                                             [Park in ledger] create_draft(source='ari')
                                                                                 │
                                                                                 ▼
                                                                              submit()
                                                                                 │
                                                                                 ▼
                                                                         [pending_approval]
                                                                                 │
                                                                    (Human via /api/memoir/persona)
                                                                                 │
                                                                                 ▼
                                                                    approve(actor=human_session)
                                                                                 │
                                                                                 ▼
                                                                             [approved]
                                                                     (Pointer Row Updated)
```

### 3.3 Persona Document Composition Specification (B3)
- **Input:** Current approved persona body (`MemoirPersonaRepository.get_current(agent_id='ari').body`) + triggering `MemoirSynthesis` (`claim`, `verb`, `source_episode_ids`).
- **Prompt:** Instructs model to integrate the new belief/boundary/rule into the persona markdown document, preserving overall voice, Markdown headings, and structure.
- **Budget Ceiling:** The resulting text is metered using `daemon.loader.estimate_tokens` (cl100k tiktoken). Hard ceiling: `<= 2000 tokens` (`MEMOIR_PERSONA_TOKEN_BUDGET`, `memoir_briefing.py:133`).
- **Unbuildable Diff Handling:** If the generated document exceeds 2000 tokens, or contains syntax errors / empty output, candidate is parked in `memoir_jev_ledger` (`attempts += 1`, `status = 'pending'`, `last_error = 'diff_over_budget'`). If attempts reach 5, flips to `status = 'terminal'`, `last_error = 'terminal:unbuildable_diff'`.

### 3.4 Minimal Persona REST Surface Specification (B1)
Mounted under `daemon/routers/memoir.py` (`/api/memoir/persona`):
- `GET /api/memoir/persona/pending`: Scope `prompts:read`. Returns list of pending versions: `[{"id": "...", "version": N, "body": "...", "source": "ari", "submitted_at": "..."}]`. (Filtered to `status == 'pending_approval'`).
- `POST /api/memoir/persona/{version_id}/approve`: Scope `prompts:approve`. Enforces `request.state.auth_context.auth_method == "session"`, else 403 `HUMAN_LANE_REQUIRED`. Transitions status to `approved` and updates pointer row in `memoir_persona`.
- `POST /api/memoir/persona/{version_id}/reject`: Scope `prompts:approve`. Enforces human session. Transitions status to `rejected` (terminal state).
- Strict scope discipline: LIST/APPROVE/REJECT only — zero create, edit, or delete endpoints.
- Hermetic auth tests: Test suite cleans/scrubs `ENSEMBLE_AUTH_ISSUER`, `ENSEMBLE_AUTH_DISCOVERY_URL`, and `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` per project POK.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T5.1** | Implement `MemoirPersonaGateService` in `daemon/services/memoir_persona_gate.py` with two-stage diff evaluation. | Service evaluates candidate syntheses against active persona; returns JEV category and explanation. | `daemon/services/memoir_persona_gate.py` | P2 | R5 | AC1 |
| **T5.2** | Implement prompt and parser for JEV LLM classification with candidate parking in `memoir_jev_ledger` on failure (B2 / Register #16 / V2-38). | LLM classifies edge cases; parse failure or timeout parks candidate in `memoir_jev_ledger` and retries; terminalizes at 5 attempts as `terminal:gate_unavailable`. | `daemon/services/memoir_persona_gate.py`, `daemon/repositories/memoir/models.py` | T5.1, P1 | R3 | AC2 |
| **T5.3** | Implement persona document composition in `MemoirPersonaGateService` within 2000-token budget, with unbuildable diff parking in `memoir_jev_ledger` (B3 / V2-39). | Generates candidate `proposed_body` <= 2000 tokens; unbuildable or over-budget diffs parked in `memoir_jev_ledger` as `terminal:unbuildable_diff` after 5 attempts. | `daemon/services/memoir_persona_gate.py` | T5.2 | R3 | AC3 |
| **T5.4** | Wire persona proposal creation to `MemoirPersonaRepository`: invoke `create_draft(source='ari')` and `submit()` from reflection service when JEV category ∈ `{conflict, boundary_change, new_rule}` and diff is valid. | Proposals land in `memoir_persona_versions` with status `pending_approval`; source recorded as `ari`; tool module remains pure. | `daemon/services/memoir_persona_gate.py`, `daemon/repositories/memoir/repository.py` | T5.3 | — | AC4 |
| **T5.5** | Implement minimal REST surface in `daemon/routers/memoir.py` (`/api/memoir/persona`) for list pending, approve, and reject (B1 / V2-37). (Dated anchors: `prompts.py:51-56/:617-640`). | Exposes `GET /pending`, `POST /{id}/approve`, `POST /{id}/reject`; enforces 403 `HUMAN_LANE_REQUIRED` outside human session auth. | `daemon/routers/memoir.py`, `daemon/api.py` | T5.4 | R5 | AC5 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | JEV gating discipline: Trivial syntheses (pleasantries) classify as `none` and produce ZERO database writes; syntheses corroborating existing rules classify as `reinforcement` and produce ZERO writes; novel rules classify as `new_rule` and trigger proposal creation. |
| **AC2** | Candidate parking in `memoir_jev_ledger` (B2 / B3): A transient LLM timeout during JEV classification does NOT default to `none`; candidate is parked in `memoir_jev_ledger`; after 5 failed attempts, candidate is marked `terminal:gate_unavailable`; an unbuildable diff (>2000 tokens) is parked and terminalized after 5 attempts as `terminal:unbuildable_diff`. |
| **AC3** | Persona composition budget (B3): Composed `proposed_body` is strictly <= 2000 tokens (`MEMOIR_PERSONA_TOKEN_BUDGET`); valid diff successfully creates draft version in `memoir_persona_versions` with `version = current + 1`, `status = 'pending_approval'`, `source = 'ari'`. |
| **AC4** | Human-lane enforcement & write purity: Calling `approve()` with an agent or background worker actor raises `HumanLaneRequiredError` (`prompt_version_service.py:184` defined, `:678-716` raised, `repository.py:231` enforced); calling with `actor.auth_method == "session"` succeeds, transitions status to `approved`, and updates pointer row. `create_draft` invoked only from reflection service. |
| **AC5** | Minimal REST surface (B1 / V2-37): `GET /api/memoir/persona/pending` returns pending versions only; `POST /api/memoir/persona/{version_id}/approve` with human session approves draft and moves persona pointer; `POST /api/memoir/persona/{version_id}/reject` transitions to rejected; calling any endpoint without session token returns 403 `HUMAN_LANE_REQUIRED`; zero create/edit/delete routes exist. |
| **AC6 (Mutation)** | Defaulting to `none` on classifier exception flips AC2 RED; removing `_require_human_lane` from `approve()` flips AC4 RED; approving via bearer token flips AC5 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_jev_classifier_test.sh`: Tests heuristic filter, LLM classification prompt, JSON extraction, and candidate parking in `memoir_jev_ledger`.
- `test/packs/memoir_v2_persona_composition_test.sh`: Tests persona document composition within 2000-token ceiling, unbuildable diff detection, and parking.
- `test/packs/memoir_v2_persona_proposal_test.sh`: Tests draft creation, submission, status transitions, and pointer row isolation.
- `test/packs/memoir_v2_persona_router_test.sh`: Tests minimal REST surface (`/api/memoir/persona/pending`, `approve`, `reject`), session auth requirement (403 `HUMAN_LANE_REQUIRED`), and hermetic auth environment scrubbing per POK.

---

## 7. Flag Consumption

- **DD-9:** Persona edits land PENDING; human-lane approval only.
- **V2-9:** The Persona Gate (JEV) architecture.
- **V2-13:** Structured proposal document format per `v4-federated-sketch.md` §3/§5.
- **V2-20 / §2-Q4:** JEV candidate parking on failure.
- **V2-37 / B1:** Minimal human-lane-gated persona version HTTP surface.
- **V2-38 / B2:** Durable `memoir_jev_ledger` classification and retry ledger table.
- **V2-39 / B3:** Persona document composition and unbuildable diff parking.
