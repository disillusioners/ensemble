# D6 — Mission Epoch Findings (REPORT ONLY)

**Feature:** `feature/fix-job-queue-timestamps-tz` (Phase 3) · **Date:** 2026-09-15
**Scope:** findings + recommendation ONLY — no implementation in this phase, per Leader decision D6.

## 1. What "epoch" means on the mission surface

A mission (an instance acting as a long-lived work unit) can be **revived**
after reaching a terminal status: a new message to a `COMPLETED` /
`TERMINATED` / `ERROR` / `FAILED` instance transitions it back to `RUNNING`
(`InstanceManager._revive_terminal_instance`, manager.py:~7210). Each
revive starts a new logical "epoch" of the same mission. Today the mission
surface **derives** epoch-ish timing from two columns that do NOT model
epochs:

- `MissionRecord.started_at` ← `Instance.last_activity_at`, falling back to
  `Instance.created_at` (mission_resolver.py:~852-861 pre-fix).
- `MissionRecord.epoch` ← a constant `1` (M1 projection; the resolver has
  no epoch counter — the M4(ii) `mission_events` log was never built).

## 2. Findings

### F1 — `last_activity_at` is a liveness rag, not an epoch boundary
`last_activity_at` is bumped by EVERY unrelated write: message enqueues,
revives, terminal mirrors (child_reports µs-twins), parent-cascades,
error-reporting bookkeeping (8+ writer sites — see the Phase-2 inventory).
It therefore cannot answer "when did this epoch start" — after a revive it
is refreshed, but so is it refreshed by a child completing. Post-Phase-3 it
carries naive-UTC digits (writers converted), which fixes the *format*
but not the *semantics*.

### F2 — `Instance.created_at` is the mission's BIRTH, not the epoch start
It is stamped exactly once (instance creation). A revived mission shows
`created_at` from its FIRST life; deriving `started_at` from it makes
epoch N report the epoch-1 start. This is the same conceptual bug as the
job-view D1 defect (work-start vs row-birth), one level up.

### F3 — No persistent epoch state exists anywhere
Verified: no `epoch` / `revive_count` column on `instances`; no
`mission_events` table; the attestation ledger resets are per-epoch in
*semantics* (`instance_messaging.py:~1895` resets
`attestation_denied_count` on fresh-episode revive) but store no epoch
number or boundary timestamp. Epoch boundaries are currently only
observable by diffing consecutive `updated_at` flips from
terminal→running in the events table — expensive and heuristic.

### F4 — The tz fix makes the naive fallback *format-correct* but the
mission surface still renders it via `to_utc_iso`'s assume-UTC policy.
Legacy `last_activity_at` rows (+07 digits, pre-fix writers) render 7h-off
until backfill — same class as the job-view legacy caveat, but for
missions the skew lands in `started_at`/`last_activity_at` fields consumed
by the missions panel and the `since` filter (see backfill-proposal.md).

## 3. Recommendation (for a future phase — NOT this one)

**Persist per-epoch open/close boundaries.** Concretely:

1. Add a lightweight `mission_epochs` table (or an `epoch` integer +
   `epoch_started_at` / `epoch_ended_at` pair on `instances`, bumped
   atomically in `_revive_terminal_instance`):
   - `epoch_started_at` stamped in the SAME transaction as the
     terminal→running flip (naive-UTC digits per the Phase-3 standard if
     the column is naive; aware ISO if TEXT).
   - `epoch_ended_at` stamped by the terminal finalize paths (the same
     µs-twin sites converted in Phase 3 commit 3 — they already write
     `updated_at` in that transaction; adding the epoch column write is
     one line at each site).
2. Source `MissionRecord.started_at` / `epoch` from that state;
   `last_activity_at` keeps its true meaning (liveness rag) and stops
   being consulted for epoch semantics — mirrors D1's job-view ruling.
3. The DDL-free fallback (if persistence must wait): derive
   `started_at` from the latest `Event` row with
   `kind=MESSAGE_RECEIVED` at priority-1 (fresh-episode user message —
   the same discriminator `instance_messaging.py` uses for the
   attestation reset). Cheaper than full epoch persistence, still
   heuristic.

**Why persist rather than derive:** the derive path (F3) requires
scanning events per mission-list page (N+1 against the missions panel)
and misclassifies agent-driven revives (no fresh user message — the
fresh-episode discriminator is priority-1-HUMAN only). The attestation
ledger ALREADY depends on fresh-episode semantics; giving it a durable
epoch key removes a latent drift class there too.

## 4. Interaction with the Phase-3 standard (for whoever implements)

- New epoch columns MUST follow the Phase-3 timestamp standard:
  aware-UTC application values, naive-UTC digits at naive-column binds,
  `now_utc_iso()` for TEXT columns, `to_utc_iso` on read.
- If epoch boundaries are stamped at the existing µs-twin sites, mint
  ONE aware instant and derive both shapes (twin µs-match preserved —
  same pattern as Phase 3 commit 3).
