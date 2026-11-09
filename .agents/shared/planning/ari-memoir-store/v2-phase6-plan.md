# Phase 6 — Keepsake Proposals & Wake-Refresh Seams

**File:** `v2-phase6-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`), Phase 2 (`v2-phase2-plan.md`), Phase 4 (`v2-phase4-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`, R2, DR-1, V2-11, V2-18 (§3-A6), V2-23 (§3-A4), V2-28 (C3), V2-36 (W2), V2-Q1 (§2-Q1), W4.

---

## 1. Objective

Deliver Keepsake Promotion Proposals and the Wake-Refresh Context Seam:
1. Implement `MemoirKeepsakeProposalService` in `daemon/services/memoir_keepsake_proposal_service.py` using the durable database substrate `memoir_proposal_ledger` (C3 / Leader ruling 2026-11-09 / V2-28) to stage candidate keepsakes from reflection (`COALESCE(poignancy, importance) >= 8` + heuristic filtering for enduring significance).
2. Surface keepsake proposals to both the Emperor and Empress via two complementary channels:
   - **Briefing Proposals Zone (§3-A6 / Register #14):** A dedicated, measured section (≤ 1000 chars) in the wake briefing (`[SYSTEM CONTEXT: Memoir]`). Trim-rung order is cross-referenced strictly to V2-D 6.5's verbatim quote.
   - **Gentle User Notification Bridge (B4 / V2-40):** Reflection service bridges notification by enqueueing a message to the Kingdom Agent's (Ari's) instance lane via `manager.enqueue_message(ari_active_instance_id, message, source="system:memoir-proposal")` (RDRS lane-6 precedent, `daemon/services/report_delivery_recovery.py:126-135`). Ari's instance lane executes user notification with proper lineage/anchor. Tracked via `memoir_proposal_ledger` and consumed ONLY on an accepted enqueue.
3. Enforce constitutional dual governance (R2): Pinning an episode requires explicit confirmation by the Emperor or Empress via the existing `memoir_pin` tool. Proposals are NEVER auto-pinned; candidate episodes are evaluated at most ONCE in their lifetime (once-ever-per-episode constraint, W2 / V2-36).
4. Implement the Wake-Refresh Context Seam via **Option B Amended (§2-Q1 / Register #10)** with explicit payload specification (SF6):
   - Adds `_maybe_memoir_refresh_message` in `daemon/services/context_messages.py` evaluated at both assembly sites (:2564–2570 and :2928–2934).
   - Registers `memoir_refresh` in `_stable_id_for` closed list (`context_messages.py:679-822`), returning `memoir_refresh:{instance_id}` (N3).
   - Emits a stamped context block carrying `context_kind="memoir_refresh"` and stable id `memoir_refresh:{instance_id}` via `_make_context_message`.
   - **No once-flag:** Re-evaluates on every qualifying wake; dedup across turns is structural via `add_messages` supersede-by-stable-id.
   - **`wake_source` Threading:** Thread a new `wake_source: str | None` parameter from dispatch (`instance_messaging.py:4005-4029`) through `assemble_context_messages` to the refresh builder. Gate: `agent_id == "ari"` → `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` → `wake_source == "scheduler"`. Do NOT gate on `original_source`.
   - **Payload Specification (SF6 / W4):** Fresh briefing render including persona (~2000 tokens per `memoir_briefing.py:127-135`, W4), keepsakes (~4000 tokens), `[PROPOSALS]` zone (≤ 1000 chars, max 3 proposals, N2), and dynamic catch-up section; bounded strictly by DR-1 ceiling (24,000 chars) and absolute backstop (48,000 chars).
   - Preserves the L4 product ruling: scheduler wakes remain unstamped bare `MessageType.HUMAN`.
5. Add discrete kill-switch governance for wake refresh: `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` following the DR-3 truth table.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 6.1** | Dual proposal surfacing: Passive visibility via the wake briefing proposals section + active visibility via at most ONE gentle notification bridge per calendar day. | Keeps proposals visible without cluttering interactive conversation or overwhelming chat channels. | Notification on every proposal (spam hazard); OR briefing-only. |
| **V2-D 6.2** | Notification Bridge Enqueue & Ledger Consumption (B4 / V2-40 / Register #17): Reflection service is daemon-scoped and cannot invoke `notify_user` directly (`create_notify_user_tools` requires `current_instance_id`, `notify_user.py:460`). It enqueues to Ari's instance lane via `manager.enqueue_message(instance_id, message, source="system:memoir-proposal")` following RDRS Lane 6 precedent (`daemon/services/report_delivery_recovery.py:126-135`). `memoir_proposal_ledger` records consumption (`notification_status = 'enqueued'`) ONLY on accepted enqueue. Enqueue-then-mark crash race is bounded (ND-16). | Calling `notify_user` directly without instance anchor; unmanaged background notifications. | Reflection worker has no instance context. Bridging through Ari's lane preserves tool authorization, session binding, and attestation lineage. |
| **V2-D 6.3** | Keepsake Dual Governance (R2): Pinning strictly requires Emperor or Empress confirmation via `memoir_pin(id, reason, actor)`. Proposals are NEVER auto-pinned. | Constitutional ruling R2: keepsakes are sacred; only the Emperor or Empress holds pin authority. Auto-pinning violates the constitutional core. | Automatic promotion of high-poignancy episodes. |
| **V2-D 6.4** | Wake-refresh via Option B Amended (§2-Q1 / Register #10 / N3): Implement `_maybe_memoir_refresh_message` in `context_messages.py` at both sites (:2564-2570 and :2928-2934), using `context_kind="memoir_refresh"`, stable id `memoir_refresh:{instance_id}`, **no once-flag**, gated on threaded `wake_source == "scheduler"`. Register `memoir_refresh` in `_stable_id_for` closed list (:679-822, N3). | L4 Landmine: Scheduler wakes are deliberately unstamped bare `MessageType.HUMAN` (`constants.py:1376-1399`). Touching the alarm template violates attestation source U. Threading `wake_source` is necessary because the signal is absent at the assembly seam today. | Option A (alarm template compose); OR gating on first-write-only `original_source`. |
| **V2-D 6.5** | Canonical Trim-Rung Priority & Reachability Analysis (B5 / §3-A6 / Register #14 / B2'): Canonical rung order quoted verbatim from `v2-architecture-recommendation.md` §3-A6:
> with explicit rung order **fit dynamic → trim dynamic → trim proposals (drop weakest first) → rung-2 withhold**

Binding ruling: Zero interpretive restatement; canonical semantics live exclusively in the verbatim block quote above.
Measurement basis & reachability: In live code (`memoir_briefing.py:344`), `stable_chars` binds the stable zone alone:
- If `stable_chars > 48000`: rung-2 withhold triggers (returns `None`). Reachable ONLY by stable-zone overflow alone.
- If `stable_chars > 24000`: stable overage triggers (stable emitted in full; dynamic and proposals dropped; overage disclosed).
- Total-based proposals guard (explicit new rung): If `stable_chars <= 24000`, dynamic lines are fitted up to `MEMOIR_DYNAMIC_TOKEN_BUDGET` (`fit dynamic`). If total briefing characters (`stable_chars + dynamic_chars + proposals_chars`) exceed 24,000 chars, dynamic lines are trimmed oldest-first (`trim dynamic`), and if still exceeding 24,000, proposals are trimmed weakest-first (`trim proposals (drop weakest first)`). Reachability: because proposals admission is capped at ≤ 1000 chars, this total-based trim can fire only when `stable_chars + dynamic_chars` is within 1000 chars of 24,000.
Persona zone authority is 2000 tokens (`memoir_briefing.py:127-135`, W4). | Paraphrasing trim rungs; unstated measurement basis. | The verbatim §A6 block quote is the single canonical specification; eliminates paraphrase inversion defects. |
| **V2-D 6.6** | Durable Proposal Ledger Substrate (C3 / V2-28): Proposals and 1/day notifications are stored in `memoir_proposal_ledger` (created in Phase 1) with `UNIQUE(agent_id, episode_id)`. | Restart-safe 1/day notification constraint is a blessed requirement; in-memory state is ruled out. | In-memory ledger; ad-hoc JSON metadata. |
| **V2-D 6.7** | Proposal Once-Everness Per Episode (W2 / V2-36 / Leader ruling 2026-11-09): An episode is eligible for proposal at most ONCE in its lifetime (option c). If an episode already has a row in `memoir_proposal_ledger`, reflection skips it permanently. | Re-proposing after 30 days (option b); OR unlimited re-proposals. | No-spam doctrine and keepsake sacredness: a rejected or stale proposal must not re-nag. Re-proposal semantics can be added later if the Emperor asks. |

---

## 3. Domain Detail & Seam Architecture

### 3.1 Keepsake Proposal Pipeline, Ledger & Bridge Enqueue (C3 / §3-A4 / W2 / B4 / N2)
1. During reflection passes, `MemoirKeepsakeProposalService` scans newly synthesized beliefs and high-poignancy episodes (`COALESCE(poignancy, importance) >= 8` and `is_keepsake = False`).
2. Evaluates eligibility heuristic: enduring significance; skips ANY already-proposed episode regardless of outcome or age (ONCE-EVER-PER-EPISODE ruling, W2 / V2-36; enforced by `UNIQUE(agent_id, episode_id)` in `memoir_proposal_ledger`). A rejected or stale proposal must never re-nag.
3. Staging & Surfacing:
   - Inserts candidate row into `memoir_proposal_ledger` with `notification_status = 'pending'`.
   - **`[PROPOSALS]` Zone Selection Rule (N2):** Queries candidate proposals from `memoir_proposal_ledger` where `notification_status IN ('pending', 'enqueued')`, ordered by `effective_poignancy DESC, proposed_at DESC`, capped at maximum 3 proposals within the ≤ 1000-char proposals section.
   - Appends proposal lines to `memoir_briefing.py` in the measured `[PROPOSALS]` section:
     `- [proposal] "<title>" (event_at=<date>, poignancy=<N>): <reason>`
     Lines follow the single-sourced `KEEPSAKE_LINE_TEMPLATE` formatting pattern.
   - Evaluates daily notification constraint against `memoir_proposal_ledger`:
     `SELECT COUNT(*) FROM memoir_proposal_ledger WHERE agent_id = :ari AND proposal_date = :today AND notification_status = 'enqueued'`.
   - **Active Instance Resolution (ND-22):** Looks up Ari's active instance: queries `instances` table for `agent_id = 'ari' AND status = 'running' ORDER BY updated_at DESC LIMIT 1` (or `manager._instance_repository.get_active_instance_for_agent('ari')`). If count is 0 and active instance exists, bridges notification via `manager.enqueue_message(ari_active_instance_id, "[system:memoir-proposal] Ari has reflected and proposed 1 new keepsake: '<title>'. Review via briefing.", source="system:memoir-proposal")` (B4 / V2-40 / ND-6).
   - On successful enqueue, updates row in `memoir_proposal_ledger`: `notification_status = 'enqueued', notified_at = :now`. If enqueue fails or Ari has no active instance, status remains `'pending'`, preserving the daily slot for subsequent attempts. Enqueue-then-mark crash race is bounded (ND-16).

### 3.2 Wake-Refresh Seam Integration & Payload Specification (SF6 / §2-Q1 / W4 / N3)

In `daemon/services/instance_messaging.py`:
- At dispatch site (`:4005-4029`), identify incoming message source:
  ```python
  wake_source = getattr(incoming_message, "source", None)
  ```
- Forward `wake_source` to `assemble_context_messages`:
  ```python
  assemble_context_messages(..., wake_source=wake_source)
  ```

In `daemon/services/context_messages.py`:
- Defined at module scope:
  ```python
  CONTEXT_KIND_MEMOIR_REFRESH = "memoir_refresh"
  ```
- Registered in `_stable_id_for` closed kind-list (:679-822, N3):
  ```python
  if kind == "memoir_refresh":
      if not instance_id:
          raise ValueError("_stable_id_for('memoir_refresh') requires instance_id")
      return f"memoir_refresh:{instance_id}"
  ```
- Evaluated at both assembly sites (:2564–2570 refresh branch and :2928–2934 turn-1 path):
  ```python
  refresh_msg = await _maybe_memoir_refresh_message(
      instance_id=instance_id,
      manager=manager,
      instance_repository=instance_repository,
      wake_source=wake_source,
  )
  if refresh_msg is not None:
      persistent_msgs.append(refresh_msg)
  ```
- Inner gate: Checks `agent_id == "ari"` -> `resolve_memoir_wake_refresh_knob()` -> `wake_source == "scheduler"`. On regular session resumes or non-alarm human turns where `wake_source` is None or not `"scheduler"`, `_maybe_memoir_refresh_message` returns None (zero refresh blocks emitted; ND-12).
- **Payload Structure (SF6 / W4 / ND-20):**
  1. Header: `[SYSTEM CONTEXT: Memoir Refresh]`
  2. Persona section: Current active persona (~2000 tokens per `memoir_briefing.py:127-135`, W4)
  3. Keepsakes section: Fully rendered keepsakes (~4000 tokens)
  4. Proposals zone: Measured candidate proposals (≤ 1000 chars, max 3 proposals, N2) under header `### Proposals for Keepsake Review` (ND-20)
  5. Dynamic catch-up: Episodes written since instance spawn + active syntheses
  6. Total characters strictly bounded: ≤ 24,000 ceiling, 48,000 backstop.
- Message emission: Uses `_make_context_message` with `context_kind="memoir_refresh"`, stable id `memoir_refresh:{instance_id}`, and `source="memoir_refresh"`.
- Turn deduplication is structural: `add_messages` supersede-by-stable-id collapses N wake refreshes into exactly one current row in session history.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T6.1** | Implement `MemoirKeepsakeProposalService` in `daemon/services/memoir_keepsake_proposal_service.py` to stage candidates into `memoir_proposal_ledger` with once-ever constraint (C3 / W2). | Candidates with `COALESCE(poignancy, importance) >= 8` and enduring heuristic staged in `memoir_proposal_ledger`; once-ever constraint prevents re-proposing previously evaluated episodes. | `daemon/services/memoir_keepsake_proposal_service.py`, `daemon/services/memoir_reflection_service.py`, `daemon/repositories/memoir/models.py` | P1, P2, P4 | — | AC1 |
| **T6.2** | Integrate proposals section into `daemon/services/memoir_briefing.py` as an own measured zone (≤ 1000 chars, max 3 proposals, ordered by poignancy/recency, N2, header `### Proposals for Keepsake Review`, ND-20) with canonical trim-rung order cross-referenced to V2-D 6.5 (B5 / B2'). | Proposals render under header `### Proposals for Keepsake Review`; trim order cross-referenced strictly to V2-D 6.5; reachability verified. | `daemon/services/memoir_briefing.py` | T6.1 | R3 | AC2 |
| **T6.3** | Implement 1/day notification bridge via `manager.enqueue_message(instance_id, message, source="system:memoir-proposal")` to Ari's instance lane backed by `memoir_proposal_ledger` (B4 / V2-40 / Register #17). | First enqueue of the day marks ledger `enqueued`; failed enqueues leave status `pending` without burning the day's slot. | `daemon/services/memoir_keepsake_proposal_service.py`, `daemon/services/memoir_reflection_service.py`, `daemon/manager.py` | T6.1 | R8 | AC3 |
| **T6.4** | Thread `wake_source` from dispatch in `instance_messaging.py` to `assemble_context_messages`, register `memoir_refresh` in `_stable_id_for` (:679-822, N3), and implement Option B refresh seam with SF6 payload spec in `context_messages.py`. | Scheduler alarm wakes receive `memoir_refresh:{instance_id}` context block carrying fresh briefing payload (persona ~2000 tokens); `_stable_id_for` accepts kind without ValueError; scheduler user message stamps remain completely untouched. | `daemon/services/instance_messaging.py`, `daemon/services/context_messages.py` | T6.2 | R6 | AC4 |
| **T6.5** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` with DR-3 truth table. | Knob defaults to ON with warning; invalid values fail-closed; call-time updates take effect immediately. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | — | AC5 |
| **T6.6** | Test keepsake proposal surfacing, bridge enqueue, once-ever constraint, trim order cross-referenced to V2-D 6.5, `_stable_id_for` registration, and wake-refresh context injection across session types. | Unit and integration test packs green; verify scheduler stamp purity, restart survival, once-ever deduplication, and stable-id turn supersede. | `tests/unit/test_memoir_keepsake_proposals.py`, `tests/unit/test_memoir_wake_refresh.py` | T6.3, T6.4 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Proposal qualification & once-ever constraint (W2): Episodes with effective poignancy < 8 are never proposed; episodes with effective poignancy ≥ 8 meeting heuristics appear in `memoir_proposal_ledger`; existing keepsakes and any already-proposed episodes are permanently skipped (once-ever-per-episode constraint). |
| **AC2** | Briefing proposals zone & trim order (B5 / B2' / N2): Proposals appear under `[PROPOSALS]` (≤ 1000 chars, max 3 proposals ordered by poignancy/recency); trim order cross-referenced strictly to V2-D 6.5; rung-2 withhold (>48k) is reachable only by stable-zone overflow alone. |
| **AC3** | Durable ledger bridge enqueue gate (B4 / C3): Injecting proposals on day D enqueues message to Ari's lane with `source="system:memoir-proposal"`; if enqueue fails, row remains `pending` and slot is NOT consumed; once enqueue succeeds (`enqueued`), subsequent proposals on day D are silent across daemon restarts. |
| **AC4** | Scheduler stamp purity, `_stable_id_for` & wake refresh payload (SF6 / W4 / N3): On self-alarm wake, alarm message source is asserted as `scheduler` with `MessageType.HUMAN` (unstamped); `_stable_id_for('memoir_refresh', instance_id=...)` returns `memoir_refresh:{instance_id}` without ValueError; `memoir_refresh:{instance_id}` context block is present with `context_kind="memoir_refresh"` containing fresh persona (~2000 tokens), keepsakes, proposals, and dynamic episodes; successive wakes supersede to exactly 1 refresh row. |
| **AC5** | Wake-refresh kill-switch: Setting `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED="false"` cleanly suppresses the refresh context block without affecting regular briefing or alarm execution. |
| **AC6 (Mutation)** | Consuming notification slot on enqueue failure flips AC3 RED; altering canonical trim-rung order flips AC2 RED; changing alarm message body flips AC4 RED; re-proposing an existing episode flips AC1 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_keepsake_proposals_test.sh`: Tests proposal qualification, once-ever deduplication (W2), heuristic filtering, `memoir_proposal_ledger` staging, and measured zone trim rungs.
- `test/packs/memoir_v2_proposal_notification_test.sh`: Tests durable ledger consumption, restart survival of 1/day anti-spam constraint, and Ari instance lane bridge enqueue invocation (B4).
- `test/packs/memoir_v2_wake_refresh_test.sh`: Tests `wake_source` threading, Option B context seam, fresh payload rendering (SF6 / W4), scheduler stamp purity (L4), stable-id supersession, and kill-switch control.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`: Discrete kill-switch for wake refresh.
- **R2:** Constitutional dual governance over keepsakes.
- **DR-1:** 24,000-char briefing ceiling and 48,000-char backstop.
- **V2-11:** Keepsake proposals governance and surfacing.
- **V2-18 / §3-A6:** Briefing proposals zone measured admission and trim-rung priority.
- **V2-23 / §3-A4:** Notification ledger consumed only on accepted enqueue (B4 / V2-40).
- **V2-28 / C3:** Durable `memoir_proposal_ledger` storage touchpoints.
- **V2-36 / W2:** Proposal once-everness per episode.
- **V2-Q1 / §2-Q1:** Option B wake-refresh context block with `wake_source` threading, stable id, and SF6 payload specification.
- **W4:** Persona zone 2000 tokens authority.
