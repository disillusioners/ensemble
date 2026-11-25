# Central Maintenance System — Canonical Manual Trigger

Operator-facing note for firing ONE manual Central Maintenance run and reading its receipt.
This is the Phase 4 Task 4.6 deliverable (D-D manual trigger). Doc-only: no code, no DB, no
Ari prompt edit.

## Canonical path

The canonical manual trigger is a **scheduler ONE-SHOT at `run_at=now` targeting `maintenancer`**
— the SAME dispatch path as the weekly chain. Ari invokes it via the `task_schedule` tool surface
(`daemon/tools/scheduling.py:165-189`). Capability is verified in-repo: `TaskScheduleInput`
supports `recurrence="once"` with an ISO-8601 `when`, `agent_id` targeting, and past/near-now
one-shots (the adapter fires them immediately within the lateness cap or skips; read truth from
executions history). A manual row is a one-shot immediate with no chain block.

## Exact manual invocation

Copy this verbatim (from `phase4-plan.md` Task 4.6). Replace `<ts>` and `<now ISO 8601>` at call
time; keep the `trigger: manual` line inside `message` on its own line:

```
task_schedule(label="central-maintenance-manual-<ts>", message="trigger: manual\nExecute one manual Central Maintenance run now: dispatch the five lane children + report writer per the orchestrator workflow; parse the trigger token and pass trigger_type to the writer child.", when=<now ISO 8601>, recurrence="once", agent_id="maintenancer")
```

## Token transport (message-body trigger token)

`trigger_type` rides IN the message body as a **message-body trigger token** — zero daemon code,
no DB field (correct even though the writer child has NO DB surface):

1. The weekly chain row's `message` literal (authored by Phase 1, P1 T1.1) embeds
   `trigger: scheduled` on its own line.
2. The canonical manual invocation's `message` argument embeds `trigger: manual` (see the exact
   invocation above — Ari includes it).
3. The ORCHESTRATOR parses the `trigger:` token from its dispatch message at run start and passes
   `trigger_type` through to the `run-report-writer` child in the task envelope (alongside
   `run_token`).
4. The writer embeds exactly what it receives — it never reads the DB.

The row-config derivation (`config.chain` present ⇒ scheduled; absent + `creator_instance_id`
present ⇒ manual) is retained only as the DOCUMENTED rationale for why each message template
carries its token — it is not the runtime path. This transport is the leader ruling at
`decisions.md` APPROVER-2/B8.

## Receipt expectations

The receipt `trigger_type` equals the token the orchestrator parsed and passed through (so a run
from this invocation reports `trigger_type: manual`). The receipt carries:

- Header fields: `run_id`, `trigger_type` (`scheduled|manual`), `started_at`, `ended_at`,
  `overall_status` (plus rolled-up `recommendations`).
- The five PD-4a per-lane result blocks under `lanes:` — one each for `lane-disk-sweep`,
  `lane-pok-sweep`, `lane-skill-check`, `lane-abnormality-check`, `lane-metrics-watch`. The block
  schema is FROZEN and authoritative at `decisions.md` PD-4a — **cited by reference only; do not
  re-embed it here**. The disk lane reports `bytes_reclaimed` AND `inodes_reclaimed` (both).
- Three recommendation classes: `auto_fix_now` (class 1 — safe, bounded, in-lane),
  `needs_emperor_word` (class 2 — needs user decision), `route_to_lane` (class 3 — another
  lane/owner owns the fix). Definitions are in `decisions.md` PD-4a (by reference).

Artifacts land under `.agents/maintenancer/RESULTS/` (live-install daemon project root, never a
worktree) as:

- `run-<run_id>.md`
- `receipt-<run_id>.md`
- `INDEX.md` (append-only, dedup by run_id, lexicographic = chronological)

where `run_id = ckpt-YYYYMMDD_HHMMSSffffff-<hex8>`.

## REST-fallback caveat (documented limitation)

REST-created rows carry NO `creator_instance_id` (`daemon/services/scheduling_service.py:818-824`)
and REST callers may omit the token — REST-origin manual runs are OUTSIDE the canonical path
(documented, unsupported). Use the `task_schedule` invocation above instead.

## Fallback path (documented, NOT canonical)

As a fallback, a direct `send_message` to a maintenancer instance carrying the same
`trigger: manual` token line will fire a manual run. This is documented but NOT canonical — it
produces no schedule row and no executions audit, so prefer the ONE-SHOT `task_schedule` path.

## Operator notes from the staged-boot proof (P5B, 2026-11-22)

### List door on auth-OFF deployments (BY DESIGN)

`GET /api/maintenance-reports` returns **503 `AUTH_NOT_CONFIGURED`** on any auth-OFF deployment —
the shipped default. This is the BR-17 fail-closed door (`daemon/routers/maintenance_reports.py:158-171`):
`create_app` sets `app.state.auth_config` only when auth is enabled, and the door refuses to publish
run history on an open bind. An operator note, not a defect. The FE-facing view URLs still serve the
artifacts directly — `/views/maintenance-reports/run-<run_id>.md` and `receipt-<run_id>.md` return
HTTP 200 (the live-views markdown shell).

### Staged-boot prerequisites (the tier-2 rogue-containment guard)

A staged/frozen boot against an env-configured PostgreSQL DB needs ONE of:

- the tier-1 persisted `ensemble.json` in the boot's data dir (mode 600; the password field may stay
  empty — env wins for every field), or
- `ENSEMBLE_DB_AMBIENT_ALLOW=1` for a sanctioned first boot (the persisted `ensemble.json` makes
  later boots tier 1).

Without either, `daemon/ensemble_config.py::assert_db_resolution_sanctioned` refuses BEFORE any TCP
connection and the boot hard-exits **78** — a precise refusal, not a blind failure. The guard's own
message names the remedy. Scrub/keep the ambient env PER-VARIABLE (never the `env -u VAR*` glob
idiom — it silently unsets nothing) following the existing env-fence conventions, and pin
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly on any frozen-binary boot from a daemon-child
shell.

### Weekday semantics (the window is a LOCAL clock window)

The chain's `weekday` knob is evaluated on the **LOCAL** window date (the `timezone` knob's
calendar), never on the UTC date of the stored `run_at_utc`. A "Sunday 01:00–05:00
Asia/Ho_Chi_Minh" chain fires **Sunday local time**; because ICT is UTC+7, that instant's stored
`run_at_utc` lands on the PRIOR UTC date (Sunday 04:15 ICT = Saturday 21:15Z). Read from the UTC
date alone the row looks off-by-one-day — it is not.

## Cross-references and scope

- Trigger mechanics (one-shot chain, re-arm, lateness cap, window knobs) live in
  `phase1-plan.md` in the same planning directory (`.agents/shared/planning/maintenance-system/`) —
  cross-ref only. Phase 4 documents the invocation + receipt, not the one-shot internals.
- No Ari prompt edit is made — explicitly out of lane. This note is the operator-facing record.
