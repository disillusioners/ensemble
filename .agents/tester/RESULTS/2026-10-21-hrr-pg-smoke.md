# PG Smoke — hard-reboot MQ boot pass (dev-lane supplementary evidence)

- **Date**: 2026-10-21
- **Feature**: hard-reboot recovery (Phase 2 — `daemon/services/message_queue_boot_recovery.py` + the guarded UPDATE / selector in `daemon/repositories/message_queue/repository.py`)
- **Worktree**: `/home/nea/ensemble-src-wt-hard-reboot-recovery` (branch `feature/hard-reboot-recovery`)
- **Verdict**: 🟢 **PASS — the shipped selector + shipped guarded UPDATE behave exactly as specified on real PostgreSQL, inside ONE transaction that was ROLLED BACK (zero residue)**
- **Classification**: 🔵 **DEV-LANE SUPPLEMENTARY EVIDENCE — this artifact does NOT discharge AC6.**

---

## 0. Scope statement (read first)

This file records a **supplementary** dev-lane smoke: the shipped SQL executed against the fenced
dev PostgreSQL inside a single transaction that was rolled back.

**It does NOT discharge AC6.** The authoritative AC6 artifact is the **tester E2E lane's** to
produce: boot the daemon against a **freshly-rebooted host** with **PostgreSQL started by systemd
in parallel**, and observe the recovery inside ≤30s. That drill exercises the real systemd unit
ordering, the real `RestartSec` backoff and the real host reboot — none of which a transaction-scoped
smoke can stand in for. Nothing in this file should be read as evidence for AC6, and AC6 remains
open until the tester E2E lane lands its own artifact.

What this smoke DOES establish, on real PostgreSQL (not SQLite, not mocks):

1. the shipped selector and the shipped guarded UPDATE are valid SQL against the real schema;
2. the two skip-guards fire on real rows (TERMINAL instance, RUNNING task);
3. the reset is idempotent (a second pass is a no-op);
4. the transaction left **no residue** in the dev DB.

## 1. Fence assertion (the live install was never touched)

| Field | Value | Assertion |
|---|---|---|
| `POSTGRES_DB` | `ensemble_dev` | `!= ensemble_prod` ✅ |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `127.0.0.1` / `5432` | dev-local PG ✅ |
| `DAEMON_PORT` | `8079` | `!= 9797` (the live daemon's port) ✅ |
| `ENSEMBLE_SELF_ENV` | `dev` | the worktree `.env` marker ✅ |

The live install (`:9797` / `ensemble_prod` / the `ensemble-main` systemd unit) was **not** contacted,
not restarted and not written to. Every write below was inside one transaction that was rolled back.

## 2. Method

The smoke does **not** paraphrase the SQL: it extracts the shipped `UPDATE` string and the shipped
selector expression from `daemon/repositories/message_queue/repository.py` **via AST** (the same
module namespace the repository itself evaluates in) and runs those exact objects.

- fixtures: 3 instances (`running`, `terminated`, `running`-with-a-`running`-task) and 4
  `message_queue` rows (fresh-dead, NULL-`last_activity_at`, terminal-instance, running-task),
  all `status='processing'`, all inserted **inside the open transaction — no commit**;
- then: the shipped selector → the shipped per-row guarded UPDATE → the status assertions →
  a second pass (idempotency) → `ROLLBACK` → a residue count **outside** the rolled-back transaction.

## 3. Verbatim output

```
[fence] POSTGRES_DB=ensemble_dev DAEMON_PORT=8079 — live (:9797/ensemble_prod) untouched
[shipped] UPDATE sql:
   UPDATE message_queue SET status = 'ready', processing_started_at = NULL WHERE message_id = :mid AND status = 'processing' AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.instance_id = message_queue.instance_id AND i.status IN ('completed','error','failed','terminated')) AND NOT EXISTS (SELECT 1 FROM task t WHERE t.message_id = message_queue.message_id AND t.status IN ('running','paused'))
[selector] selected (4): ['hrr_smoke_m_fresh', 'hrr_smoke_m_null', 'hrr_smoke_m_task', 'hrr_smoke_m_term']
[update] flipped (2): ['hrr_smoke_m_fresh', 'hrr_smoke_m_null']
[assert] m_fresh=ready, m_null=ready, m_term=processing (TERMINAL guard), m_task=processing (RUNNING-task guard) — ALL AS EXPECTED
[assert] processing_started_at cleared on the flipped row — AS EXPECTED
[assert] second pass over the same rows = no-op (rowcount 0) — AS EXPECTED
[rollback] residue after ROLLBACK: message_queue=0 task=0 instances=0
[result] PG SMOKE PASS
```

Process exit code: `0`.

## 4. What the output shows, line by line

| Line | Reading |
|---|---|
| `[fence]` | the dev-lane fence held — `ensemble_dev` / `8079` |
| `[shipped] UPDATE sql` | the exact SQL text extracted from the repository source (not re-typed) |
| `[selector] selected (4)` | the shipped selector returns **all four** stranded rows (the selector's own scope — the skip-guards are enforced by the UPDATE, not by the SELECT) |
| `[update] flipped (2)` | the shipped guarded UPDATE flipped **only** the two eligible rows (`m_fresh`, `m_null`) — the TERMINAL-instance row and the RUNNING-task row were **not** touched |
| `[assert] … TERMINAL guard … RUNNING-task guard` | the two skip-guards fired on real rows |
| `[assert] processing_started_at cleared` | the reset clears `processing_started_at` (and nothing else — see the runbook's stale-error-text disclosure) |
| `[assert] second pass … no-op` | reboot-loop idempotency holds on real PG |
| `[rollback] residue … =0/0/0` | the ROLLBACK left **no** rows behind in `message_queue`, `task` or `instances` |

## 5. Reproduction

```
cd /home/nea/ensemble-src-wt-hard-reboot-recovery
set -a && . ./.env && set +a
.venv/bin/python /tmp/hrr_pg_smoke.py
```

The smoke script asserts its own fence before touching anything
(`POSTGRES_DB == "ensemble_dev"`, `DAEMON_PORT == "8079"`), so a mis-pointed run aborts before the
first write.

## 6. Related artifacts

- `tests/unit/test_message_queue_boot_recovery.py` — the SQLite-backed unit pack (M1–M7) covering the
  same selector / UPDATE semantics plus the de-tautologized M5 accounting assertions.
- `docs/runbooks/hard-reboot-recovery.md` — the operator-facing disclosures (manual MQ-row-edit
  revive hazard, R-03 no-manual-edit, stale error text, RTO qualifier).
- `.agents/shared/planning/hard-reboot-recovery/` — the plan of record (plan-overview, decisions,
  architecture-recommendation).
