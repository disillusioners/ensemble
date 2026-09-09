# Memory
## KB Index (load-bearing — verified on first turn)

I consult the relevant KB doc before any non-trivial action. The index below tells me which doc matches the symptom; only fall through to live investigation when the index has no answer.

- 01-architecture-overview — trigger: any architecture, manager/graph/loader, or daemon-runtime question; verify-against: v0.12.4
- 02-jobs-missions-admission-state — trigger: any job, mission, admission-state, or queue question (DEAD → QUEUED replay shape); verify-against: v0.12.4
- 03-log-forensics — trigger: log forensics, line-number trap, time-bracket search, race forensics; verify-against: v0.12.4
- 04-known-traps — trigger: traps (stale SQLite relic (data/instances.db), migration runner, gate fall-throughs); verify-against: v0.12.4
- 05-repair-runbooks — trigger: pause-first, idempotent repair rows, audit stamps, rollback shape; orphan ACTIVE sweep, DLQ replay; verify-against: v0.12.4
- 06-restart-upgrade-runbook — trigger: 3-factor gate, atomic flip, journal sweep, live-rung promotion; verify-against: v0.12.4

**Verification discipline.** The version anchor `v0.12.4` is the release label baked into the project metadata. Any doc whose verification anchor drifts from this label is stale and must be re-verified against the source before being trusted.

**Read discipline.** Open the matching doc before acting. If the index says no match, log the gap in the Maintenance Report `### Remaining` so the KB can grow.

---

## Skill Index (worker-loaded via `load_skill="<name>"`)

The seven skills listed below are the dispatch surface I send to workers. The canonical home for the full contract of each skill is the matching file in this agent's skills-template directory; I only keep a 1-line purpose here so the KB INDEX stays scoped.

- `kb-curator` (auto-load) — KB INDEX awareness + filesystem reads + first-turn RAG mirror
- `log-forensics` — time-bracket log forensics + redaction-aware reads
- `job-mission-repair` — diagnose + repair job/task/mission state via the controlled writer
- `ens-db-repair` — guarded execution of idempotent DO$$-only repair rows (3 gates)
- `restart-upgrade-ops` — pause-first quiesce + dry-run upgrade prep; live arms via 3-factor gate
- `bug-advisory` — read-only consultation; I diagnose and recommend, never execute
- `health-check` — MaintenanceJob registry + StaleTaskRecovery sweep (read-only)

I keep skill versions consistent: the `.md` frontmatter version is the source of truth; any manifest listing a skill must match it.

---

## Calibration Tables

Calibration tables translate symptoms into severity and dispatch tier. Entries below reflect operational use; new symptoms land here once they recur.

| Symptom class | Default severity | Default dispatch |
|---|---|---|
| Log redaction-broken: secrets visible in raw read | 🔴 | `worker` (load_skill="log-forensics") — re-redact + state scope |
| Audit stamp missing on a committed repair row | 🔴 | `worker` (load_skill="ens-db-repair") — self-surgery refusal + halt further writes |
| Orphan ACTIVE JobItem past `min_orphan_age` | 🟠 | `worker` (load_skill="job-mission-repair") — R6 recipe |
| DLQ replay requested (DEAD → QUEUED) | 🟠 | `worker` (load_skill="job-mission-repair") — R7 via `replay_from_dlq` |
| Stale task stuck in PROCESSING | 🟠 | `worker` (load_skill="job-mission-repair") — `StaleTaskRecovery` |
| Live-upgrade arm requested without user nonce | 🔴 | Halt; report `Blocked — missing user nonce`; do not arm |
| Health-check sweep overdue (≥15m cadence) | 🟢 | `worker` (load_skill="health-check") — diagnostic only |
| Bug triage — unknown symptom, no KB match | 🟢 | `worker` (load_skill="bug-advisory") — read-only |

---

## Trigger Checklists

Before every risky operation I run the matching checklist rows. Each row is a one-line pass/fail check; any failed row stops the operation until the upstream condition is met.

| Before action | Checklist |
|---|---|
| Repair row write (any `ens-db-repair` run) | confirmation present? audit-stamp configured? dry-run-first issued and reviewed? |
| Live-system operation (`system_upgrade` arm) | user nonce present? per-instance user-origin window open? single-use nonce? |
| Pause-first quiesce on a target instance | instance matched by id? current state `RUNNING`? cancellation-reason named? post-resume `is_paused=false`? |
| DLQ replay (DEAD → QUEUED) | task is in DEAD? caller has user confirmation? verified-idempotent? |
| KB doc citation | `last-verified-against` release tag equals the project's release label? |

---

## Fallback Ladder (writing guide §8)

When `load_skill="<skill>"` fails (skill bank miss, version mismatch, seeding gap), I do not silently dispatch a skill-less worker. The fallback stays within my `team_members`:

1. **Detect the failure** — the dispatch report shows the skill did not load.
2. **Retry the load once** — sometimes the bank is stale and a second look resolves.
3. **Mark the run as `DEGRADED — skill bank miss (<skill>)`** in the Maintenance Report.
4. **Fall back to a peer within tier** — spawn another `worker` (or `coder`, or `explorer` depending on the task) with a detailed manual prompt. The peer is named in my `team_members`; it is reachable.

Escalation across agents (e.g., maintenancer → leader) is the **caller's** job. I do not invent a fallback that spawns an agent not in my `team_members`.
