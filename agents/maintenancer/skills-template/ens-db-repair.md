---
version: 1.0.0
category: maintenance
auto_load: false
---

# Ens-DB Repair

Guarded execution of idempotent DO$$-only repair rows against the daemon's DB. Every repair row carries an audit stamp; the user confirms before any commit.

## Boundary

This is a guarded-execution skill — I follow the 3 gates (confirm, audit, dry-run). Consultation output is the `bug-advisory` skill's job.

## Contract

On dispatch I receive a target SQL (`DO $$` or `IF NOT EXISTS`), a target label, and a verification; I return a repair receipt.

- **Gate 1 — Confirm.** Real user message attests. I never invent; never echo.
- **Gate 2 — Audit stamp.** Every row carries a stamp (per-instance, never forged).
- **Gate 3 — Dry-run-first.** I surface the row count before asking for confirm.
- **DO$$-only idempotent shape.** Non-idempotent bulk transforms refused; only `DO $$ ... END $$` and `IF NOT EXISTS` DDL pass.
- **Nonce relay.** User nonce is relayed verbatim — never constructed client-side.

## Focus areas

1. **Receipt** — committed row returns stamp + nonce + verification.
2. **Self-surgery refusal** — never write a row that mutates the calling instance's own state.
3. **Pause-first** — quiesce precedes writes that touch per-instance state.
4. **Kill-switch awareness** — env-flip kill-switches that disable the writer at boot are honored.
5. **Dry-run preview** — surface row count before asking for confirm.

## Cross-references

- See this agent's Memory "KB Index" for matching knowledge docs.
- See the canonical Repair Runbooks doc for recipe anchors.

## Mandatory output format

```
## Ens-DB Repair Receipt
- target: <label>
- confirm: y|n
- audit-stamp: <stamp>
- dry-run rows: N
- nonce: <user-issued verbatim>
- sql-shape: DO$$ | IF-NOT-EXISTS | DML-upsert
- self-surgery-refused: y|n
- pause-first: y|n
- commit: y|n
- verification: <caller confirms>
- remaining-questions: <list | none>
```

A failed gate stops the run and is named in the report.
