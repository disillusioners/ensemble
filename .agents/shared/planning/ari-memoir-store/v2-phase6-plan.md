# Phase 6 — Keepsake Proposals & Wake-Refresh Seams

**File:** `v2-phase6-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`), Phase 2 (`v2-phase2-plan.md`), Phase 4 (`v2-phase4-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`, R2, DR-1, V2-11, V2-18 (§3-A6), V2-23 (§3-A4), V2-28 (C3), V2-Q1 (§2-Q1).

---

## 1. Objective

Deliver Keepsake Promotion Proposals and the Wake-Refresh Context Seam:
1. Implement `MemoirKeepsakeProposalService` in `daemon/services/memoir_keepsake_proposal_service.py` using the durable database substrate `memoir_proposal_ledger` (C3 / Leader ruling 2026-11-09 / V2-28) to stage candidate keepsakes from reflection (`COALESCE(poignancy, importance) >= 8` + heuristic filtering for enduring significance).
2. Surface keepsake proposals to both the Emperor and Empress via two complementary channels:
   - **Briefing Proposals Zone (§3-A6 / Register #14):** A dedicated, measured section (≤ 1000 chars) in the wake briefing (`[SYSTEM CONTEXT: Memoir]`). Explicit trim-rung order guarantees that proposals can never silently kill dynamic episodes.
   - **Gentle User Notification (§3-A4 / Register #17):** At most ONE gentle `notify_user` message per calendar day, tracked via `memoir_proposal_ledger` and consumed ONLY on an accepted send.
3. Enforce constitutional dual governance (R2): Pinning an episode requires explicit confirmation by the Emperor or Empress via the existing `memoir_pin` tool. Proposals are NEVER auto-pinned.
4. Implement the Wake-Refresh Context Seam via **Option B Amended (§2-Q1 / Register #10)** with explicit payload specification (SF6):
   - Adds `_maybe_memoir_refresh_message` in `daemon/services/context_messages.py` evaluated at both assembly sites (:2564–2570 and :2928–2934).
   - Emits a stamped context block carrying `context_kind="memoir_refresh"` and stable id `memoir_refresh:{instance_id}` via `_make_context_message`.
   - **No once-flag:** Re-evaluates on every qualifying wake; dedup across turns is structural via `add_messages` supersede-by-stable-id.
   - **`wake_source` Threading:** Thread a new `wake_source: str | None` parameter from dispatch (`instance_messaging.py:4005-4029`) through `assemble_context_messages` to the refresh builder. Gate: `agent_id == "ari"` → `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` → `wake_source == "scheduler"`. Do NOT gate on `original_source`.
   - **Payload Specification (SF6):** Fresh briefing render including persona (~2000 chars), keepsakes (~4000 tokens), `[PROPOSALS]` zone (≤ 1000 chars), and dynamic catch-up section; bounded strictly by DR-1 ceiling (24,000 chars) and absolute backstop (48,000 chars).
   - Preserves the L4 product ruling: scheduler wakes remain unstamped bare `MessageType.HUMAN`.
5. Add discrete kill-switch governance for wake refresh: `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` following the DR-3 truth table.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 6.1** | Dual proposal surfacing: Passive visibility via the wake briefing proposals section + active visibility via at most ONE gentle `notify_user` per calendar day. | Keeps proposals visible without cluttering interactive conversation or overwhelming chat channels. | Notification on every proposal (spam hazard); OR briefing-only. |
| **V2-D 6.2** | Notification ledger consumed ONLY on accepted send (§3-A4 / Register #17): `memoir_proposal_ledger` records consumption only when `notify_user` returns an accepted dispatch. | Refusals (e.g. background session unbound to chat) or rate-limit blocks must not starve legitimate notification later in the day. | Consuming slot on attempt or refusal. |
| **V2-D 6.3** | Keepsake Dual Governance (R2): Pinning strictly requires Emperor or Empress confirmation via `memoir_pin(id, reason, actor)`. Proposals are NEVER auto-pinned. | Constitutional ruling R2: keepsakes are sacred; only the Emperor or Empress holds pin authority. Auto-pinning violates the constitutional core. | Automatic promotion of high-poignancy episodes. |
| **V2-D 6.4** | Wake-refresh via Option B Amended (§2-Q1 / Register #10): Implement `_maybe_memoir_refresh_message` in `context_messages.py` at both sites (:2564-2570 and :2928-2934), using `context_kind="memoir_refresh"`, stable id `memoir_refresh:{instance_id}`, **no once-flag**, gated on threaded `wake_source == "scheduler"`. | L4 Landmine: Scheduler wakes are deliberately unstamped bare `MessageType.HUMAN` (`constants.py:1376-1399`). Touching the alarm template violates attestation source U. Threading `wake_source` is necessary because the signal is absent at the assembly seam today. | Option A (alarm template compose); OR gating on first-write-only `original_source`. |
| **V2-D 6.5** | Briefing Proposals Zone Trim-Rung Priority (§3-A6 / Register #14): Proposals zone is its OWN measured section (admission ≤ 1000 chars). Trim order: fit dynamic → trim dynamic → trim proposals (weakest first) → rung-2 withhold (>48k). | Persona (~2k tokens) and keepsakes (~4k tokens) already approach the 24k character ceiling. Bolting proposals into stable text would cause premature rung-1 overflow and wipe all dynamic episodes. | Mixing proposals into stable keepsakes zone. |
| **V2-D 6.6** | Durable Proposal Ledger Substrate (C3 / V2-28): Proposals and 1/day notifications are stored in `memoir_proposal_ledger` (created in Phase 1). | Restart-safe 1/day notification constraint is a blessed requirement; in-memory state is ruled out. | In-memory ledger; ad-hoc JSON metadata. |

---

## 3. Domain Detail & Seam Architecture

### 3.1 Keepsake Proposal Pipeline & Durable Ledger (C3 / §3-A4)
1. During reflection passes, `MemoirKeepsakeProposalService` scans newly synthesized beliefs and high-poignancy episodes (`COALESCE(poignancy, importance) >= 8` and `is_keepsake = False`).
2. Evaluates eligibility heuristic (enduring significance; skips already proposed/rejected within 30 days).
3. Staging & Surfacing:
   - Inserts candidate row into `memoir_proposal_ledger` with `notification_status = 'pending'`.
   - Appends proposal lines to `memoir_briefing.py` in the measured `[PROPOSALS]` section:
     `- [proposal] "<title>" (event_at=<date>, poignancy=<N>): <reason>`
     Lines follow the single-sourced `KEEPSAKE_LINE_TEMPLATE` formatting pattern.
   - Evaluates daily notification constraint against `memoir_proposal_ledger`:
     `SELECT COUNT(*) FROM memoir_proposal_ledger WHERE agent_id = :ari AND proposal_date = :today AND notification_status = 'sent'`.
   - If count is 0 and current session is bound to an active chat destination, invokes `notify_user`:
     `"Ari has reflected and proposed 1 new keepsake: '<title>'. Review via memoir briefing or pin directly."`
   - On successful dispatch, updates row in `memoir_proposal_ledger`: `notification_status = 'sent', notified_at = :now`. If `notify_user` returns a refusal (e.g. `notify_no_bound_chat_source`), `notification_status` is marked `'failed'`, preserving the daily slot for subsequent attempts.

### 3.2 Wake-Refresh Seam Integration & Payload Specification (SF6 / §2-Q1)

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
- Inner gate: Checks `agent_id == "ari"` -> `resolve_memoir_wake_refresh_knob()` -> `wake_source == "scheduler"`.
- **Payload Structure (SF6):**
  1. Header: `[SYSTEM CONTEXT: Memoir Refresh]`
  2. Persona section: Current active persona (~2000 chars)
  3. Keepsakes section: Fully rendered keepsakes (~4000 tokens)
  4. Proposals zone: Measured candidate proposals (≤ 1000 chars)
  5. Dynamic catch-up: Episodes written since instance spawn + active syntheses
  6. Total characters strictly bounded: ≤ 24,000 ceiling, 48,000 backstop.
- Message emission: Uses `_make_context_message` with `context_kind="memoir_refresh"`, stable id `memoir_refresh:{instance_id}`, and `source="memoir_refresh"`.
- Turn deduplication is structural: `add_messages` supersede-by-stable-id collapses N wake refreshes into exactly one current row in session history.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T6.1** | Implement `MemoirKeepsakeProposalService` in `daemon/services/memoir_keepsake_proposal_service.py` to stage candidates into `memoir_proposal_ledger` (C3). | Candidates with `COALESCE(poignancy, importance) >= 8` and enduring heuristic staged in `memoir_proposal_ledger`; deduplication prevents repeated proposals. | `daemon/services/memoir_keepsake_proposal_service.py`, `daemon/repositories/memoir/models.py` | P1, P2, P4 | — | AC1 |
| **T6.2** | Integrate proposals section into `daemon/services/memoir_briefing.py` as an own measured zone (≤ 1000 chars) with explicit trim-rung order. | Proposals render under `[PROPOSALS]`; trim-rung order trims dynamic first, then proposals, protecting dynamic zone from premature overflow. | `daemon/services/memoir_briefing.py` | T6.1 | R3 | AC2 |
| **T6.3** | Implement 1/day `notify_user` proposal notification gate in `MemoirKeepsakeProposalService` backed by `memoir_proposal_ledger` (C3 / Register #17). | First accepted notification of the day marks ledger `sent`; refusals or unbound sessions mark `failed` without burning the day's slot. | `daemon/services/memoir_keepsake_proposal_service.py`, `daemon/tools/notify_user.py` | T6.1 | R8 | AC3 |
| **T6.4** | Thread `wake_source` from dispatch in `instance_messaging.py` to `assemble_context_messages` and implement Option B refresh seam with SF6 payload spec in `context_messages.py`. | Scheduler alarm wakes receive `memoir_refresh:{instance_id}` context block carrying fresh briefing payload; scheduler user message stamps remain completely untouched. | `daemon/services/instance_messaging.py`, `daemon/services/context_messages.py` | T6.2 | R6 | AC4 |
| **T6.5** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` with DR-3 truth table. | Knob defaults to ON with warning; invalid values fail-closed; call-time updates take effect immediately. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | — | AC5 |
| **T6.6** | Test keepsake proposal surfacing, durable ledger consumption, trim-rung order, and wake-refresh context injection across session types. | Unit and integration test packs green; verify scheduler stamp purity, restart survival, and stable-id turn supersede. | `tests/unit/test_memoir_keepsake_proposals.py`, `tests/unit/test_memoir_wake_refresh.py` | T6.3, T6.4 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Proposal qualification: Episodes with effective poignancy < 8 are never proposed; episodes with effective poignancy ≥ 8 meeting heuristics appear in `memoir_proposal_ledger`; existing keepsakes are skipped. |
| **AC2** | Briefing proposals zone & trim order: Proposals appear under `[PROPOSALS]` (≤ 1000 chars); when total text approaches 24k ceiling, dynamic episodes trim first, then proposals trim (weakest first); rung-2 withhold (>48k) triggers only if stable zone breaches ceiling. |
| **AC3** | Durable ledger anti-spam gate (C3): Injecting proposals on day D emits notification; if notification returns `notify_no_bound_chat_source`, row is marked `failed` and slot is NOT consumed; once a notification succeeds (`sent`), subsequent proposals on day D are silent across daemon restarts. |
| **AC4** | Scheduler stamp purity & wake refresh payload (SF6): On self-alarm wake, alarm message source is asserted as `scheduler` with `MessageType.HUMAN` (unstamped); `memoir_refresh:{instance_id}` context block is present with `context_kind="memoir_refresh"` containing fresh persona, keepsakes, proposals, and dynamic episodes; successive wakes supersede to exactly 1 refresh row. |
| **AC5** | Wake-refresh kill-switch: Setting `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED="false"` cleanly suppresses the refresh context block without affecting regular briefing or alarm execution. |
| **AC6 (Mutation)** | Consuming notification slot on refusal flips AC3 RED; altering trim-rung order flips AC2 RED; changing alarm message body flips AC4 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_keepsake_proposals_test.sh`: Tests proposal qualification, deduplication, heuristic filtering, `memoir_proposal_ledger` staging, and measured zone trim rungs.
- `test/packs/memoir_v2_proposal_notification_test.sh`: Tests durable ledger consumption, restart survival of 1/day anti-spam constraint, and `notify_user` invocation.
- `test/packs/memoir_v2_wake_refresh_test.sh`: Tests `wake_source` threading, Option B context seam, fresh payload rendering (SF6), scheduler stamp purity (L4), stable-id supersession, and kill-switch control.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`: Discrete kill-switch for wake refresh.
- **R2:** Constitutional dual governance over keepsakes.
- **DR-1:** 24,000-char briefing ceiling and 48,000-char backstop.
- **V2-11:** Keepsake proposals governance and surfacing.
- **V2-18 / §3-A6:** Briefing proposals zone measured admission and trim-rung priority.
- **V2-23 / §3-A4:** Notification ledger consumed only on accepted send.
- **V2-28 / C3:** Durable `memoir_proposal_ledger` storage touchpoints.
- **V2-Q1 / §2-Q1:** Option B wake-refresh context block with `wake_source` threading, stable id, and SF6 payload specification.
