# Lesson: migration `.sql` trailing comments must never contain `;` — the runner's statement splitter shatters

**Date:** 2026-11-08 · **Commission:** source-gc (wi-ed230eb4) · **Found by:** G6 Tier-2 whole-tree leg (D2 `449d6ac0`)

## Root cause
`daemon/migrations/runner.py` strips **full-line** comments but leaves **trailing** comments intact, then splits the file into statements on `;`. A `;` inside a trailing comment therefore splits a statement mid-comment, and the comment tail becomes the next "statement" → `sqlite3.OperationalError: near "<word>": syntax error` → `MigrationError: Migration <file> failed` on **every fresh SQLite boot** that runs the full dir.

## The trap
- The defective file (`20261107_122648_create_source_gc_archive_tables.sql:92`) **documented the invariant in its own header** ("RUNTIME-SPLITTER NOTE … This file uses no semicolon in any comment") and then violated it one line at the bottom — header promises are not enforcement.
- **PG is unaffected** (schema via `create_all`; the runner is SQLite-only), so PG-gated packs, dev-PG fixtures and daemon boots against PG all stay green — only a full-dir SQLite run catches it. If your gate corpus never boots fresh SQLite with the full migration dir, this defect class is invisible.
- 5 whole-tree ids failed (migration-reality + legacy-ledger-shadow + prompt-version parity) while the lane's own 43 tests passed — the lane's tests never exercise the full dir.

## Durable rule
1. **Never place `;` inside any comment in `daemon/migrations/versions/*.sql`** — full-line or trailing. Prefer comment phrasing without semicolons entirely.
2. **Pin it:** migration-corpus test asserting no `;` occurs inside any comment (full-line or trailing) in any `.sql` under `daemon/migrations/versions/` — the invariant the headers already promise, mechanically enforced. (Routed to the source-gc fix cycle as the suggested companion pin.)
3. **Gate design:** any lane that adds a migration file must include a full-dir fresh-SQLite boot leg (e.g. `test_migration_dup_prefix_guard`'s fresh-boot shape) in its scoped corpus — not just the lane's own tests.

## Before/after
- Before: 5 deterministic net-new whole-tree reds at lane tip `7b6fa2cdf`; every fresh SQLite boot broken.
- Fix shape (1 line): remove the `;` from the trailing comment or promote it to a full-line comment above the statement — landed by the lane owner, then scoped Tier-1 re-gate (5 ids + fresh-boot proof).
