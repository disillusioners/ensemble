# Maintenancer

> Centralized repair agent for the ensemble daemon — log forensics, controlled DB repair, knowledge-base consultation, gated live-system operations.

The **Maintenancer** is a peer agent that the leader dispatches when the user reports a problem that lives inside the ensemble daemon itself. It is the canonical destination for "ensemble is broken / slow / wrong" tickets that are not application bugs in user code.

---

## When the leader dispatches the Maintenancer

The leader routes a request to the Maintenancer when the symptom points at the daemon — its lifecycle, jobs, missions, logs, its own database, its upgrade pipeline, or its recovery machinery. Examples:

- The daemon is crashing, restarting unexpectedly, or failing to come up.
- A job or mission is stuck (orphan ACTIVE, DEAD with no replay, PROCESSING past the expected window).
- Log forensics is the next move — the user needs a time-bracketed root cause, not a guess.
- A `ensemble_prod` repair row is needed (idempotent DO$$ recovery from `repair_log.created_by`).
- A pause-first quiesce, restart, or live-rung upgrade needs preparation (the user still pulls the lever; the Maintenancer prepares the surface).
- A live `system_upgrade` operation is requested (3-factor nonce gate — see "Gated live operations" below).

The leader does **not** route to the Maintenancer for application-source bugs (developer), infrastructure / CI / deployment issues (devops), read-only codebase questions (wanderer), or one-off code-quality cleanup (tidier).

---

## What it can do

- Read the daemon's own logs (`system-log`) — time-bracket search, never line-window search.
- Read the daemon's own database (`ens-db`) — SELECT-only reads, schema inspection, pool status.
- Read and consult its six reference docs in the KB (see "KB index" below) before any non-trivial action.
- Mirror the KB into the ensemble knowledge base on first turn (best-effort; KB INDEX + filesystem reads are guaranteed-on even if the mirror is unavailable).
- Dispatch workers (`worker`, `coder`, `explorer`) loaded with one skill each (`load_skill="<one>"`), for example `log-forensics`, `ens-db-repair`, `job-mission-repair`.
- Stage a live `system_upgrade` operation when the user supplies a real nonce and a per-instance user-origin window is open (the 3-factor gate).

## What it cannot do

- Edit source code directly. Source mutations go through `coder`.
- Run raw-bash infrastructure work directly. Raw infra work goes through `worker` (worker retains incident-time break-glass access to `system-log` per OPEN B Option 1).
- Restart the daemon. The user pulls the restart lever; the Maintenancer proposes.
- Bypass the 3-factor nonce gate on `system_upgrade`. It cannot fabricate or echo a nonce the user did not send.
- Use the `db` category to reach the daemon's own database — the daemon's DB is reached through `ens-db`. The `db` category is for user-registered external connections only.

---

## Restart-required note (one-time Wave 3 deploy)

The Maintenancer is registered with the agent registry at import time (the registry singleton runs `discover()` once per process). The Wave 3 deploy window is the one-time restart that brings the new agent live; before that restart the leader's team does not include the Maintenancer and the privileged categories are not yet gated. After the Wave 3 restart the agent is reachable, the team table includes it, and the 3-factor gate is the only path that can arm `system_upgrade`.

The rollout runbook (`ROLLOUT.md`, sibling of this file) carries the wave structure, the pause-first quiesce procedure, the pool-config pre-flight, the smoke-spawn verification, and the rollback plan.

---

## KB index (six reference docs, by name)

The Maintenancer consults the relevant KB doc before any non-trivial action. The index lives in `memory.md` (load-bearing — guaranteed-on via the loader); the docs themselves live in the agent's knowledge directory:

- `01-architecture-overview` — daemon manager / graph / loader / registry / checkpoints / prompt loader / jobs admission.
- `02-jobs-missions-admission-state` — job/task/mission lifecycle, the four-value `AdmissionState`, `DEAD → QUEUED` replay shape.
- `03-log-forensics` — time-bracket search discipline, the line-number trap (line numbers are NOT chronological), race forensics.
- `04-known-traps` — known traps: stale `data/instances.db` SQLite relic, migration runner, gate fall-throughs, 3-factor gate, `report_injections.content` sentinel, `DROP NOT NULL`, `adopt_stale_txn`.
- `05-repair-runbooks` — pause-first quiesce, idempotent repair rows, audit stamps, rollback shape, orphan ACTIVE sweep, DLQ replay, MaintenanceJob registry.
- `06-restart-upgrade-runbook` — 3-factor gate, atomic flip, journal sweep, live-rung promotion, halt-for-human.

This list enumerates the docs by name (deep-links are intentionally omitted — see the prompt-writing guide, checklist #6 reasoning). Each doc carries its own `last-verified-against: v<semver>` header; if the version anchor drifts from the project's release label the doc is stale and must be re-verified against the source.

---

## Skills (seven, by name)

The Maintenancer dispatches these skills to workers via `send_message(..., load_skill="<one>")`. Exactly one skill per dispatch — never bundle multiple. The full contract for each skill lives in the matching file in the agent's skills-template directory; the one-line purpose here is for the dispatch surface only:

- `kb-curator` (auto-loaded) — KB INDEX awareness, filesystem reads, first-turn RAG mirror duty.
- `log-forensics` — time-bracket log forensics, redaction-aware reads.
- `job-mission-repair` — diagnose + repair job / task / mission state via the controlled writer.
- `ens-db-repair` — guarded execution of idempotent `DO$$`-only repair rows (three-factor gate: confirmation + audit-stamp + dry-run-first).
- `restart-upgrade-ops` — pause-first quiesce, dry-run upgrade preparation; live arms via the 3-factor gate only.
- `bug-advisory` — read-only consultation; diagnose and recommend, never execute.
- `health-check` — MaintenanceJob registry + StaleTaskRecovery sweep (read-only diagnostic).

Fallback when a skill fails to load: the dispatch report flags `DEGRADED — skill bank miss (<skill>)` and falls back to a peer within the team (`worker`, `coder`, or `explorer`). Fallbacks never cross team boundaries.

---

## Break-glass (incident-time raw-bash fallback)

The Maintenancer holds a tightly scoped allow-list — it does **not** have `bash` directly. If an incident requires raw-bash investigation on the host (process inspection, port binding, file-system layout), the leader dispatches a `worker` (which retains `system-log` as break-glass per OPEN B Option 1). The Maintenancer's job in this case is to read the worker's report, time-bracket the log evidence, and dispatch follow-up workers with one skill each.

If the Maintenancer itself errors out mid-incident, the leader re-spawns a fresh instance and resumes the dispatch — the Maintenancer is replayable; the maintenance report is the durable record.

---

## Rollout

See `ROLLOUT.md` (sibling of this file) for the three-wave deployment procedure, the pause-first quiesce recipe, the pool-config pre-flight, the smoke-spawn verification, and the rollback plan that completes the architect §2.4 list.

---

## Decision provenance

- Plan: `.agents/shared/planning/maintenancer-agent/plan-overview.md`
- Decisions: `.agents/shared/planning/maintenancer-agent/decisions.md` (D1–D20 made; OPEN A/B/C/D/E adjudicated)
- Architecture: `.agents/shared/planning/maintenancer-agent/architecture-recommendation.md`
- Build spec: `.agents/shared/planning/maintenancer-agent/detail-plan.md` (W1-P1∥P2∥P3 file-disjoint; W2-P4; W3-P5)
