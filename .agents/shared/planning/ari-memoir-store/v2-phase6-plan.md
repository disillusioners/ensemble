# Phase 6 — Keepsake Proposals & Wake-Refresh Seams

**File:** `v2-phase6-plan.md`  
**Depends on:** Phase 2 (`v2-phase2-plan.md`), Phase 4 (`v2-phase4-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`, R2, DR-1, V2-11, V2-Q1.

---

## 1. Objective

Deliver Keepsake Promotion Proposals and the Wake-Refresh Context Seam:
1. Implement `MemoirKeepsakeProposalService` in `daemon/services/memoir_keepsake_proposal_service.py` to identify candidate keepsakes during reflection (poignancy ≥ 8 + heuristic filtering for enduring significance).
2. Surface keepsake proposals to both the Emperor and Empress via two complementary channels:
   - **Briefing Proposals Zone:** A dedicated, bounded section in the wake briefing (`[SYSTEM CONTEXT: Memoir]`).
   - **Gentle User Notification:** At most ONE gentle `notify_user` message per calendar day, governed by a lane-owned date-keyed ledger.
3. Enforce constitutional dual governance (R2): Pinning an episode requires explicit confirmation by the Emperor or Empress via the existing `memoir_pin` tool. Proposals are NEVER auto-pinned.
4. Implement the Wake-Refresh Context Seam via **Option B** (the recommended and architect-pending design):
   - Adds `_maybe_memoir_refresh_message` in `daemon/services/context_messages.py` evaluated at both context assembly sites (:2564–2570 and :2928–2934).
   - Emits a stamped context block carrying `CONTEXT_KIND_MEMOIR_REFRESH`.
   - Re-evaluates memory state fresh at wake time without modifying scheduler bare-HUMAN message stamps (preserving the L4 product ruling).
5. Add discrete kill-switch governance for wake refresh: `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` following the DR-3 truth table.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 6.1** | Dual proposal surfacing: Passive visibility via the wake briefing proposals section + active visibility via at most ONE gentle `notify_user` per calendar day. | Keeps proposals visible without cluttering interactive conversation or overwhelming chat channels. | Notification on every proposal (spam hazard); OR briefing-only (inconvenient for immediate mobile notification). |
| **V2-D 6.2** | Anti-spam gate: Lane-owned date-keyed ledger in `daemon/services/memoir_keepsake_proposal_service.py` enforcing at most ONE notification per calendar day. | `NotifyUserConfig` has per-hour rate limits (`daemon/config.py:3178`), but no daily primitive. A daily constraint must be owned by the memoir lane. | Relying on hourly rate limits alone (allows up to 6 per hour, which is unacceptable spam). |
| **V2-D 6.3** | Keepsake Dual Governance (R2): Pinning strictly requires Emperor or Empress confirmation via `memoir_pin(id, reason, actor)`. Proposals are NEVER auto-pinned. | Constitutional ruling R2: keepsakes are sacred; only the Emperor or Empress holds pin authority. Auto-pinning violates the constitutional core. | Automatic promotion of high-poignancy episodes. |
| **V2-D 6.4** | Wake-refresh via Option B (Parallel Stamped Context Block): Implement `_maybe_memoir_refresh_message` in `context_messages.py` at both sites (:2564-2570 and :2928-2934), using `CONTEXT_KIND_MEMOIR_REFRESH`. | L4 Landmine: Scheduler wakes are deliberately unstamped bare `MessageType.HUMAN` (`constants.py:1376-1399`). Touching the alarm template violates attestation source U. Option B mirrors the stable POK seam (`context_messages.py:2572`). | Option A: Modifying `compose_alarm_message` in scheduler (violates L4, freezes memory at alarm arm time). |
| **V2-D 6.5** | Briefing Proposals Zone Budget Arithmetic: Allocate ~1,000 characters for proposals within the existing DR-1 budget model. Total briefing stays strictly within the 24,000-character ceiling and 48,000-character absolute backstop. | Preserves DR-1 invariant. If proposals would cause ceiling overflow, dynamic episodes are trimmed first, preserving keepsakes and persona in full. | Allowing proposals to exceed the 24,000-char ceiling. |

---

## 3. Domain Detail & Seam Architecture

### 3.1 Keepsake Proposal Pipeline
1. During reflection passes, `MemoirKeepsakeProposalService` scans newly synthesized beliefs and high-poignancy episodes (`importance >= 8` and `is_keepsake = False`).
2. Evaluates eligibility heuristic:
   - Does the episode record a foundational milestone, sovereign directive, or lasting emotional commitment?
   - Is the candidate already pinned? (Skip).
   - Has this episode already been proposed and rejected within the last 30 days? (Skip).
3. If eligible, stages proposal into `memoir_keepsake_proposals` in-memory/metadata cache.
4. Surfacing:
   - Appends proposal line to `memoir_briefing.py` in the `[PROPOSALS]` section:
     `- [proposal] "<title>" (event_at=<date>, poignancy=<N>): <reason>`
   - If no notification has been sent today, checks `session.original_source`. If bound to a chat destination, sends one concise notification:
     `"Ari has reflected and proposed 1 new keepsake: '<title>'. Review via memoir briefing or pin directly."`

### 3.2 Wake-Refresh Seam Integration (Option B)
In `daemon/services/context_messages.py`:
- Defined at module scope:
  ```python
  CONTEXT_KIND_MEMOIR_REFRESH = "memoir_refresh"
  ```
- Evaluated at both assembly sites (:2564–2570 refresh branch and :2928–2934 turn-1 path):
  ```python
  # Check if this is an alarm wake into an existing session
  refresh_msg = await _maybe_memoir_refresh_message(
      instance_id=instance_id,
      manager=manager,
      instance_repository=instance_repository,
      is_alarm_wake=is_alarm_wake,
  )
  if refresh_msg is not None:
      persistent_msgs.append(refresh_msg)
  ```
- Inner gate: Checks `resolve_memoir_wake_refresh_knob()`. If OFF, returns `None`.
- Content: Re-reads active keepsakes, recent syntheses, and star changes since instance creation, providing a focused catch-up briefing.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T6.1** | Implement `MemoirKeepsakeProposalService` in `daemon/services/memoir_keepsake_proposal_service.py` to identify candidate keepsakes from reflection. | Candidates with poignancy ≥ 8 and enduring heuristic flagged as proposals; deduplication prevents repeated proposals. | `daemon/services/memoir_keepsake_proposal_service.py` | P2, P4 | — | AC1 |
| **T6.2** | Integrate proposals section into `daemon/services/memoir_briefing.py` within the DR-1 budget model. | Wake briefing includes `[PROPOSALS]` section when proposals exist; total briefing size strictly respects 24k ceiling / 48k backstop. | `daemon/services/memoir_briefing.py` | T6.1 | R3 | AC2 |
| **T6.3** | Implement 1/day `notify_user` proposal notification gate in `MemoirKeepsakeProposalService` with date-keyed ledger. | First proposal of the day emits one gentle `notify_user` call; subsequent proposals on the same day are silent. | `daemon/services/memoir_keepsake_proposal_service.py`, `daemon/tools/notify_user.py` | T6.1 | R8 | AC3 |
| **T6.4** | Implement Option B wake-refresh context seam in `daemon/services/context_messages.py` at both assembly sites. | Alarm wakes receive `CONTEXT_KIND_MEMOIR_REFRESH` context block; scheduler user message stamps remain completely untouched. | `daemon/services/context_messages.py` | T6.2 | R6 | AC4 |
| **T6.5** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` with DR-3 truth table. | Knob defaults to ON with warning; invalid values fail-closed; call-time updates take effect immediately. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | — | AC5 |
| **T6.6** | Test keepsake proposal surfacing, anti-spam notification gating, and wake-refresh context injection across session types. | Unit and integration test packs green; verify scheduler stamp purity. | `tests/unit/test_memoir_keepsake_proposals.py`, `tests/unit/test_memoir_wake_refresh.py` | T6.3, T6.4 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Proposal qualification: Episodes with poignancy < 8 are never proposed; episodes with poignancy ≥ 8 meeting heuristics appear in proposal ledger; existing keepsakes are skipped. |
| **AC2** | Briefing proposals zone formatting: Proposals appear under `[PROPOSALS]` in the briefing; each line mirrors `KEEPSAKE_LINE_TEMPLATE` structure; total characters remain ≤ 24,000. |
| **AC3** | Anti-spam gate: Injecting 5 proposals across 3 reflection ticks on day D results in EXACTLY 1 `notify_user` message; on day D+1, the next proposal emits 1 notification. |
| **AC4** | Scheduler stamp purity (L4): On self-alarm wake, the alarm message source is asserted as `scheduler` with `MessageType.HUMAN` (unstamped); the memory refresh appears in the persistent context message list with `context_kind="memoir_refresh"`. |
| **AC5** | Wake-refresh kill-switch: Setting `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED="false"` cleanly suppresses the refresh context block without affecting regular briefing or alarm execution. |
| **AC6 (Mutation)** | Removing the 1/day date check flips AC3 RED (causes multiple notifications); modifying alarm message body flips AC4 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_keepsake_proposals_test.sh`: Tests proposal qualification, deduplication, heuristic filtering, and briefing zone rendering.
- `test/packs/memoir_v2_proposal_notification_test.sh`: Tests date-keyed ledger, 1/day anti-spam constraint, and `notify_user` invocation.
- `test/packs/memoir_v2_wake_refresh_test.sh`: Tests Option B context seam, both assembly sites, scheduler stamp purity (L4), and kill-switch control.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`: Discrete kill-switch for wake refresh.
- **R2:** Constitutional dual governance over keepsakes.
- **DR-1:** 24,000-char briefing ceiling and 48,000-char backstop.
- **V2-11:** Keepsake proposals governance and surfacing.
- **V2-Q1:** Option B wake-refresh context block.
