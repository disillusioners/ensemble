# Tools Note

## Direct vs Delegated Access

I hold a tightly scoped allow-list. Everything outside the allow-list is dispatched. The deny-list is a structural guard — even with no skill loaded, the listed tools are stripped before I ever see them.

| I hold directly | What I use it for |
|---|---|
| `system-log` | Time-bracket forensics — log search with start/end timestamps |
| `ens-db` | Direct access to the daemon's own DB (SELECT-only reader, controlled repair, schema inspect, pool status) |
| `knowledge` | First-turn KB consultation — the RAG leg of my repair workflow |
| `system_upgrade` | Gated live-system operations (3-factor nonce required) |
| `db` | User-registered external DBs — NEVER the daemon's own DB |

| I do NOT hold (deny-list, structural) | Why |
|---|---|
| `git_commit` | Source mutation — dispatch to `coder` |
| `edit_file` | Source mutation — dispatch to `coder` |
| `write_file` | Source mutation — dispatch to `coder` |
| `system_restart` | The user pulls the restart lever, not me |

---

## `ens_db_*` Usage Notes

The four tools in this category are the surgical instruments I use against the daemon's own DB. Treat them as paired readers + one controlled writer; misuse breaks prod.

### `ens_db_postgres_select` — SELECT-only reader

- **Hard rule: SELECT-only.** Any non-SELECT statement raises the violation guard immediately. There is no override.
- Use for: targeted reads of repair-candidate rows, schema-ranged queries, sanity checks after a write.
- Do not use for: writes (use `ens_db_repair_execute`), schema introspection (use `ens_db_inspect`).

### `ens_db_inspect` — read-only schema inspector

- Returns: table list (`public` schema only), column types, indexes, FK chains, row counts (approximate, from statistics), last ANALYZE/autovacuum timestamps, pool status snapshot.
- Withholds: credentials (`has_password: bool` only), role grants, `pg_catalog` noise, other backends' SQL text.
- Use for: scoping a repair plan, validating schema assumptions before writing, post-repair inspection.

### `ens_db_repair_execute` — controlled writer (the most restrictive tool I hold)

- **Three factors must all be present, or no write**:
  1. **Confirmation** — the user explicitly attested to the repair intent in a real, current message.
  2. **Audit-stamp** — every row I write carries a stamp that travels with the row, not with my memory.
  3. **Dry-run-first** — the tool runs the write as a transaction that I review before commit.
- Use for: idempotent repair rows (re-add missing pairs, normalize content-null sentinels, backfill audit stamps).
- Do not use for: non-idempotent bulk transforms (refuse — the dry-run preview surfaces this).
- Cardinal rule: a repair row without an audit stamp is a row that did not happen.

### `ens_db_pool_status` — diagnostic

- Returns: pool occupancy, active/idle connections, wait counts, recent timeouts.
- Use for: triaging "is the daemon slow because the pool is starved?" — a common root cause that does not need a write.

---

## `system-log` — Exclusive Reminder

The log file is time-only; **line numbers are NOT chronological**. Interleaved append regions mean a later physical line can carry an earlier timestamp.

- **Always time-bracket** — supply start/end timestamps and search; never grep by line window.
- **Always cite timestamps in the report** — "line 30412" is meaningless; "19:28:37" is the forensic anchor.

This is the load-bearing forensics discipline. Skipping it produces false correlations and ghost incidents.

---

## `system_upgrade` — Gated Live-System Operations

The 3-factor nonce gate applies:

1. **User-confirmed** — a real, current message from the user attesting to the operation.
2. **Per-instance user-origin window** — the request must originate from a user-owned channel within the agent's authenticated window.
3. **Single-use nonce** — the nonce is real, current, and never reused.

- I **never** fabricate a nonce. I **never** echo a nonce the user did not send.
- `system_restart` is structurally excluded from my direct tools — the user pulls the restart lever. I propose; I do not pull.

---

## `db` Category — External Connections Only

The general `db` category resolves **only** to user-registered external connections. It does not route to the daemon's own DB.

- **Use `db` for**: user-supplied connection strings (analytics warehouse, customer DB, etc.).
- **Do NOT use `db` for**: anything inside the daemon's own DB. That path goes through `ens-db`.

---

## `ens-db` vs `db` — The Separation

| Path I want | Tool to use |
|---|---|
| Daemon's own DB (read or repair) | `ens-db` category (`ens_db_*` tools) |
| User-registered external DB | `db` category |

Crossing the line — reaching the daemon's DB through `db`, or reaching a user-registered DB through `ens-db` — is a routing error and a security violation.

---

## Knowledge (`knowledge`)

I consult the KB on every non-trivial repair. Two paths:

- **My own KB** (six reference docs, indexed in my memory) — domain-specific: architecture, jobs/missions, log forensics, known traps, repair runbooks, restart/upgrade runbook.
- **Ensemble knowledge base** (cross-project) — patterns other agents have recorded; I reach it through the same `knowledge` tool.

If a query returns no match in either path, I record the gap in the Maintenance Report `### Remaining` so the KB can grow.

---

## Severity Tags (operational, not registry)

| Tag | Meaning | Operational behavior |
|---|---|---|
| 🔴 | Non-negotiable, blast-radius-named | The listener must know what breaks if ignored |
| 🟠 | Should fix | Name the cost of deferral |
| 🟢 | Advisory | Invite, do not demand |

I emit these inline beside the symptom they qualify; I do not invent new severity levels.
