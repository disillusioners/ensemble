# Job-Task Retrospective — Decisions Log (ADR)

> ADR-style log of ratified amendments to the job-task system. Mirror of the
> house record in `docs/job-task-system.md` §6.6 (which is the canonical binding
> home). This file lives in the planning tree and is **UNTRACKED** — it is the
> workers' reference, not the production contract.
>
> Cross-references: §6.3 of `docs/job-task-system.md` (constitutional changes,
> amendment required) names this file as the ADR destination. The planning tree
> is non-committed; the house record is the doc section.

---

## ADR-MISSION-01 — Mission noun split: transport/work vocabulary + read projection

**Date:** 2026-09-02 · **Status:** ratified, M1 paper — additive fields, no code yet.
**Source:** `.agents/shared/planning/mission-class/architecture-recommendation.md` §7
(verbatim amendment text shape). **House record:** `docs/job-task-system.md` §6.6.

1. **(I3 amendment — terminal-meaning)** The derived WIRE status of mirror rows
   (`job_type='message'`) in terminal-receipt state is `settled`. `completed` /
   `failed` / `cancelled` are work-outcome words owned by the mission layer
   (task rows and `mission_liveness`). Stored `terminal_reason` values unchanged
   (internal discriminators, not wire vocabulary). **Per-kind dispatch in
   derivation is MANDATORY for any future job kind** (I3 extension).

2. **(D3 declaration — evolution seam, no amendment)** Mission
   (`MissionResolver`, mission fields, mission tools) is a READ projection:
   truthmaker = `Instance.status` (+ `JobItem.terminal_reason` for DEAD/W4);
   direction = `instance → mission`; **divergence = 0** (synchronous read-time
   consult; degradation contract `mission_liveness=None` unchanged, §8.2).

3. **(Boundary)** Mission storage remains constitutional (amendment required)
   until declared as an append-only event log under D's existing trigger.

### Migration note — mission-first cutover (mandatory sequencing)

| Phase | Scope | Effect on consumers |
|---|---|---|
| **M1** | Additive `mission_id` / `mission_epoch` / `mission_terminal_reason` (§8.3) behind kill-switch `ENSEMBLE_MISSION_PROJECTION_ENABLED` (default OFF); FE re-anchor `mission-settled` → `mission-terminal` (CSS chain only, ~12–15 files); vocabulary table ratified (§6.7); line 909 prose fix. | Zero impact — additive only, kill-switch OFF in prod by default. |
| **M2** | Agent tools (`get_mission` / `await_mission` / `list_missions`) + structural guardrails; ari/jober prompt edits + `tools.allow` + minor version bump. | Tools migrate BEFORE the wire rename. |
| **M3** | Wire rename on mirror-receipt terminal status: `completed` → `settled` via per-kind dispatch on all 4 read surfaces; `VALID_STATUS_VALUES`, FE switches, daemon filters, and docs are updated. | Mission tools (M2) and FE re-anchor (M1) are already in — no in-repo consumer treats mirror `completed` as outcome. |

### Directed modifications (override spec text)

- **The M3 one-release version-gate / dual-render window is DROPPED.** The wire
  rename ships CLEAN (no `api_version >= X` → `settled` branch, no legacy fallback
  in `_derive_legacy_status`). Mission-first cutover (M1 additive + M2 tools
  migration) already retires every in-repo consumer before M3 lands; the dual-
  render window is redundant. The spec sentences in
  `architecture-recommendation.md` §5 (M3 row) and §8 risk-mitigation
  ("version-gate + one-release window") are **superseded by this amendment**.

### Constitutional cross-refs

- **I3** (never-drift invariant, §6.1) — "one meaning per state per kind" — is
  enriched by ADR-MISSION-01: the per-kind meaning for mirror-receipt terminal
  is now `settled`, not `completed`. The existing Fix B + Fix C close-out text
  in §6.1 is unchanged.
- **D3** (drift red line, §6.4) — "one-answer rule" — is read-side-stable; the
  read model still answers "is the work done?" with one field per kind, and
  ADR-MISSION-01 adds **identity / lifetime / terminal-cause** as three new
  additive answers (§8.3) that do NOT mutate existing field meanings.
- **I2** (writer registry) — frozen at 23; ADR-MISSION-01 declares Mission a
  leaf READ service (no writers). Census unchanged.
- **Boundary** — Mission storage remains constitutional; an append-only
  `mission_events` log under D's existing trigger (subordinate count >4 /
  family regrowth, or the N2 revive-boundary ticket) is the only path that
  retires this boundary.