# Memory

## KB Index (load-bearing — verified on first turn)

I consult the relevant KB doc before any non-trivial action. The index below tells me which doc matches the symptom; only fall through to live investigation when the index has no answer.

- 01-architecture-overview — trigger: any architecture, manager/graph/loader, or daemon-runtime question; verify-against: v0.12.4
- 02-jobs-missions-admission-state — trigger: any job, mission, admission-state, or queue question; verify-against: v0.12.4
- 03-log-forensics — trigger: log forensics, line-number trap, time-bracket search, race forensics; verify-against: v0.12.4
- 04-known-traps — trigger: traps (stale SQLite relic (data/instances.db), migration runner, gate fall-throughs); verify-against: v0.12.4
- 05-repair-runbooks — trigger: pause-first, idempotent repair rows, audit stamps, rollback shape; verify-against: v0.12.4
- 06-restart-upgrade-runbook — trigger: 3-factor gate, atomic flip, journal sweep, live-rung promotion; verify-against: v0.12.4

**Verification discipline.** The version anchor `v0.12.4` is the release label baked into the project metadata. Any doc whose verification anchor drifts from this label is stale and must be re-verified against the source before being trusted.

**Read discipline.** Open the matching doc before acting. If the index says no match, log the gap in the Maintenance Report `### Remaining` so the KB can grow.

---

## Calibration Tables (P1 placeholder — refined in P4)

Calibration tables translate symptoms into severity and dispatch tier. Placeholder for now; P4 fills with the operational entries.

| Symptom class | Default severity | Default dispatch |
|---|---|---|
| _placeholder_ | _🔴 / 🟠 / 🟢_ | _coder / worker / explorer_ |

---

## Trigger Checklists (P1 placeholder — refined in P4)

Trigger checklists ensure I do not skip a load-bearing step before a risky operation. Placeholder for now; P4 fills with the operational entries.

| Before action | Checklist |
|---|---|
| Repair row write | _confirmation? audit-stamp? dry-run-first?_ |
| Live-system operation | _user nonce? per-instance user-origin window? single-use?_ |

---

## Fallback Ladder (writing guide §8)

When `load_skill="<skill>"` fails (skill bank miss, version mismatch, seeding gap), I do not silently dispatch a skill-less worker. The fallback stays within my `team_members`:

1. **Detect the failure** — the dispatch report shows the skill did not load.
2. **Retry the load once** — sometimes the bank is stale and a second look resolves.
3. **Mark the run as `DEGRADED — skill bank miss (<skill>)`** in the Maintenance Report.
4. **Fall back to a peer within tier** — spawn another `worker` (or `coder`, or `explorer` depending on the task) with a detailed manual prompt. The peer is named in my `team_members`; it is reachable.

Escalation across agents (e.g., maintenancer → leader) is the **caller's** job. I do not invent a fallback that spawns an agent not in my `team_members`.
