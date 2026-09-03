# Mission Class — Architecture Recommendation (Decision Package)

**Date:** 2026-09-02 · **Mode:** Standard Design, competitive fan-out (5 workers: 3 approach + 2 dimension) · **Evidence base:** read-only analysis pinned at `latest @e676ddea`; worker reports adjudicated against each other · **Status:** COMPLETE decision package for user approval — no implementation.

**Sibling docs:** `approach-comparison.md` (ranked options + 5-axis matrix + conflict adjudications) · `vocabulary-table.md` (full two-layer vocabulary, collisions, FE, migration classes) · `agent-contract-draft.md` (tool surface + guardrails + prompt edits).

---

## 1. HEADLINE RECOMMENDATION (approve or push back)

> **Mission becomes a first-class noun as a pure read-model projection keyed by `instance_id` (one mission, epoch-framed), surfaced to agents FIRST via three new tools with structural anti-trap guardrails, with the transport vocabulary cutover (mirror terminal wire-status `completed` → `settled`) sequenced mission-first — additive projection + tool migration land BEFORE the wire rename. No mission storage now: decision D stays deferred on its own trigger.**

This is **(b)+(d) hybrid** from the option set — "more things but separated concerns" (two nouns, two vocabularies, zero new writers), which is exactly the user's philosophy. The rename mechanics of option (a) survive as the final phase; the storage of option (c) is rejected as a category error.

## 2. What each layer owns (the noun split)

| Layer | Noun | Owns | Vocabulary | Writes |
|---|---|---|---|---|
| **Transport** | Job | "Was my submission handled?" — admission lifecycle, one row per public submission (mission proxies + mirror receipts) | `queued · active · settled · dead` (receipts) / task rows keep work-outcome `completed` | existing 23 writers — **frozen, unchanged** |
| **Work** | Mission | "Is the work done?" — outcome, epoch-aware, identity = `instance_id` | `pending · processing · paused · completed · failed · cancelled` | **none** — pure projection; truthmaker = `Instance.status` (+ `JobItem.terminal_reason` for the W4 DEAD hazard) |

Dependency direction: Mission **derives from** Transport + Instances, never writes. MissionResolver is a leaf (new `daemon/services/mission_resolver.py` reusing `_batch_instances`; the four Fix-C read surfaces are extended additively, not mutated).

## 3. Identity & Epoch verdict

**One mission per instance, keyed by `instance_id`. Mission_id == instance_id. One-mission-with-epochs — the leader's lean CONFIRMED under pressure-test.**

Decisive evidence (worker cross-adjudication):
- `spawn_instance` (manager.py:6246) creates **no JobItem** for internally-spawned children (JAFP/I4). A job-keyed mission identity (`mission_id == job_id`) would orphan every agent-spawned child — or force JobItem creation on internal paths, an I4 violation. **The instance is the only universal mission key.**
- `instances.parent_id` is permanent (survives terminate→revive); identity inherits that permanence with zero new mint surface (no D4 touchpoint, no census registration).
- Case-2 revive (send_message → COMPLETED instance → RUNNING) continues the same identity; per-revive new-mission ids would churn identity, break the FE `missions:N` badge de-dup (by instance), and force agents to correlate two ids per parent.

**Epochs:** an epoch = contiguous interval of `Instance.status` non-terminal. Opens on →RUNNING (spawn or revive); closes on →{completed, failed, cancelled}. `completed` is **revivable** (matches `InstanceStatus.COMPLETED`); `cancelled` (from TERMINATED) is **true-terminal**. **[SUPERSEDED by F7 — 2026-09-03: ALL canonical terminal values (`completed` / `cancelled` / `failed`) are revivable; no terminal value is true-terminal and there is no revive-class distinction (see `daemon/services/mission_resolver.py`). Pre-F7 wording retained for history.]** Read-aloud: "mission completed, epoch 3 processing" = truthful history + current liveness.

**⚠ Known limitation (adjudicated, must ship in docs):** the DB has **no terminal-transition timestamps** — full-fidelity epoch history (started_at/ended_at per past epoch) is NOT derivable at read time for pre-existing or multi-epoch instances. Current epoch + current liveness are precise; historical epochs are best-effort (job terminal stamps + revive evidence). **Durable epoch history is the ONE genuine gap that storage would fill** — and its honest cure is an append-only `mission_events` log preserving the single truthmaker, NOT a status-bearing missions table. This stays in M4, gated on D's trigger or the N2 revive-boundary ticket.

## 4. Vocabulary verdict (summary — full table in `vocabulary-table.md`)

**`settled` HOLDS** as the transport receipt-terminal word (payments/ledgers precedent; idiomatic; disjoint from every work/instance value). Condition discovered by audit: the FE already half-claims the word — `mission-settled` CSS class styles mission-terminal chips (mission-liveness-chip.component.scss:28, job.model.ts:188/223, ~12-15 files in the styling chain). **Prerequisite rename: `mission-settled` → `mission-terminal`** (bounded, FE styling chain only), giving `settled` exactly one owner: transport.

- Task-job `completed` **STAYS** — a task job IS its own mission (delivery ≡ work); its `completed` is the outcome. Convergent across all three approach workers.
- Stored `terminal_reason` values are **unchanged** — internal discriminators, not wire vocabulary; the Phase-4 StrEnum planning absorbs them untouched.
- Wire change is per-kind dispatch in `_derive_legacy_status`: `job_type='message'` + done → `settled`; `job_type='task'` + done → `completed` (unchanged). Centralize in one `derive_status(job_type, admission_state, terminal_reason)` helper.

## 5. Phased plan (revised from the suggested M1-M4 per design findings)

| Phase | Scope | Effort | Safety property |
|---|---|---|---|
| **M0 — Amendment & declaration** | ADR entry in job-task-retrospective `decisions.md`: I3 vocabulary amendment + D3 read-projection declaration (text shape in §7) | ~½ d | Constitutional paper before code |
| **M1 — Contract + vocab (read model)** | `MissionResolver` (identity, liveness mapping, best-effort epochs, W4-hazard preserved); additive JobResponse fields `mission_id`/`mission_epoch`/`mission_terminal_reason`; FE re-anchor `mission-settled`→`mission-terminal`; vocabulary table ratified in docs; kill-switch `ENSEMBLE_MISSION_PROJECTION_ENABLED` + one soak cycle before default-ON | ~1-2 d | Bit-for-bit `status` preserved; zero writers; additive only |
| **M2 — Agent tools** | `get_mission` / `await_mission` / `list_missions` + structural guardrails (`outcome` token, `mission_ref` cross-ref, doc one-liners, `watch_job(events='mission_terminal')`, `job_continue` mission-only gate); ari/jober prompt edits + `tools.allow` + minor version bump | ~1-1.5 d | Wrong-predicate trap becomes structurally hard |
| **M3 — Vocabulary cutover (the rename)** | Per-kind dispatch: mirror terminal wire-status `completed`→`settled` on all 4 read surfaces; version-gate / one-release dual-render window; deprecate `completed` for mirrors; update VALID_STATUS_VALUES, FE switches, docs | ~1 d + soak | AFTER consumers migrated — no agent left reading the old word |
| **M4 — Gated options (explicitly NOT now)** | (i) HTTP `GET /missions` — gate on operator demand (FE mission chips already cover operator visibility); (ii) storage-D as **append-only `mission_events`** — gate on D's original trigger (subordinate count >4 / family regrowth) or the N2 revive-boundary ticket | deferred | Each independently shippable; neither invalidated by M1-M3 |

**Why tools precede HTTP (revision of the suggested M2/M3 order):** ari/jober are the burning consumer class (the confusion is live in their prompts today — ari/soul.md L71-79, jober/soul.md L9/L54 key decisions on a single ambiguous `status`); operators already have FE mission chips. Tools-first retires the actual pain first.

## 6. Migration strategy (condensed — classes in `vocabulary-table.md` §5)

1. **Mission-first cutover** (W1's M4 framing, merged with W4's word): additive projection (M1) + tool migration (M2) land before any wire rename (M3). At M3 time, no in-repo consumer treats mirror `completed` as outcome.
2. **One-release version-gate** at M3: `api_version >= X` → `settled`; older → legacy values. Reversible (derivation-only; `terminal_reason` column untouched).
3. **Matcher breakage by class** (W1 grep: ~200 hits): daemon filters (jobs_crud.py:45/500/527, constants.py:164/251, manager.py:5159/5588), FE switches (job.model.ts + 5 components + e2e), docs (4 files), agent consumers (via M2). SSE `'completed'` **event type** is a distinct vocabulary — NO edit.
4. **DB stamps:** none migrate. `terminal_reason='completed'` remains the stored discriminator; read-model maps absorb the rename; Phase-4 StrEnum/versioning unaffected (sequence M3 before or after Phase 4 — if Phase 4 renames the discriminator itself, that is a SEPARATE amendment).

## 7. Constitutional compliance & amendment text shape

**Census/writers: unchanged (23, frozen) in every shipped phase.** M1-M3 are read-model + vocabulary only. (Worker note: this also avoids W3's blind-spot hazard — a stored mission-status family would be *invisible* to the admission_state census, the 05-24 pattern.)

Amendment (ADR-style entry, job-task-retrospective `decisions.md`):

```
ADR-MISSION-01 — Mission noun split: transport/work vocabulary + read projection
1. (I3 amendment — terminal-meaning) The derived WIRE status of mirror rows
   (job_type='message') in terminal-receipt state is 'settled'. 'completed'/
   'failed'/'cancelled' are work-outcome words owned by the mission layer
   (task rows and mission_liveness). Stored terminal_reason values unchanged
   (internal discriminators, not wire vocabulary). PER-KIND DISPATCH IN
   DERIVATION IS MANDATORY FOR ANY FUTURE JOB KIND (I3 extension).
2. (D3 declaration — evolution seam, no amendment) Mission (MissionResolver,
   mission fields, mission tools) is a READ projection: truthmaker =
   Instance.status (+ JobItem.terminal_reason for DEAD/W4); direction =
   instance→mission; divergence = 0 (synchronous read-time consult;
   degradation contract mission_liveness=None unchanged, §8.2).
3. (Boundary) Mission storage remains constitutional (amendment required)
   until declared as an append-only event log under D's existing trigger.
```

## 8. Risks

- 🟡 **M3 wire rename breaks external matchers** — mitigated by mission-first cutover + version-gate + one-release window; residual: out-of-repo consumers (none known; API docs versioned).
- 🟡 **Epoch history is best-effort** until M4(ii) — documented limitation, not a correctness risk (current liveness precise).
- 🟢 **`settled` half-claim** — resolved by bounded FE re-anchor prerequisite in M1 (styling chain only, ~12-15 files, spec-comment updates).
- 🟢 **Agent habit drift** — M2 guardrails make the wrong predicate structurally hard (naming asymmetry + `outcome` token + mandatory `mission_ref`).
- 🟢 **D3 stays green** — one answer per question maintained; MissionResolver declares truthmaker/direction/bound.

## 9. Decisions Pending (user)

1. **Approve the headline** — (b)+(d) hybrid, mission-first cutover, no storage now.
2. **`settled` word sign-off** — including the FE `mission-settled`→`mission-terminal` re-anchor prerequisite.
3. **M4 gates** — confirm HTTP `/missions` waits for operator demand; confirm storage-D stays on its trigger (not pulled by this program).
4. **Soak discipline** — kill-switch flip (default OFF → soak → ON) for M1, per repo kill-switch precedent.

## 10. Open Questions

- Exact ari/jober internal reliance on `instance_id` keying (assumed present via parent_id/list_instances; convention, not breaking).
- Whether Phase 4 (`terminal_reason` StrEnum) lands before or after M3 — sequencing note only, both orders work.

## 11. Gaps

**None — fan-in total (5/5 worker reports received and adjudicated).** Cross-report conflicts resolved in `approach-comparison.md` §3 (identity, naming mechanics, HTTP timing, epoch derivability).
