# PG-Dialect Parity — critical-note id resolver (cncap lane, Phase B leg)

- **Date**: 2026-10-31 (UTC evening)
- **Worktree**: `agents-ensemble-wt-critical-notes-cap-fix`
- **HEAD verified before any work**: `c8252f916c2a76ec8e4af07315e8c3ff051140b0` (`c8252f916`, branch `feature/critical-notes-cap-fix`)
- **PG server**: `PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin23.6.0, compiled by Apple clang version 16.0.0 (clang-1600.0.26.6), 64-bit`
- **psql client**: 14.22 (Homebrew), `/opt/homebrew/opt/postgresql@14/bin/psql`

## Phase 0 — fencing + credentials (read-only)

- Worktree `.env` PG keys (password REDACTED, and it is EMPTY per repo trust-auth convention):
  `POSTGRES_HOST=localhost`, `POSTGRES_PORT=5432`, `POSTGRES_DB=ensemble_dev` (lane DB — never touched),
  `POSTGRES_USER=ensemble`, `POSTGRES_PASSWORD=<empty>`.
- Ambient poison confirmed present in host shell: `POSTGRES_DB=ensemble_prod` (+ `POSTGRES_HOST/PORT/USER/PASSWORD`).
  Every command scrubbed: `env -u POSTGRES_DB -u DATABASE_URL -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD`.
- Target assertions before EVERY connect: target name checked against `{ensemble_prod, ensemble_dev, ensemble_prod_test}` — HARD ABORT wired in both the teardown script and `/tmp/cncap_pg_parity.py` (which additionally asserts `SELECT current_database()` equals the scratch name post-connect, and that `daemon.__file__` resolves inside the worktree).
- Role capabilities probed: `ensemble super=false createrole=false createdb=true`.
- Owners (drives the conftest fence): `ensemble_dev`, `ensemble_prod`, `ensemble_test`, `ensemble_test_only` ALL `owner=ensemble`; `postgres` (maintenance) `owner=nguyenminhkha`.

## Phase 1 — scratch DB

- Created via autocommit psql to the `postgres` maintenance DB:
  **`ensemble_test_cncap_parity_43592`** (bash PID suffix; name recorded in `/tmp/cncap_scratch_db.txt`), `CREATE DATABASE` OK, `owner=ensemble`.
- **Fence interaction (expected, by design):** the repo's PG lane (tests/postgres/conftest.py G5 fence) REFUSES this unique-name DB because its owner role also owns the live DBs, and the `ensemble` role has no CREATEROLE to mint a distinct owner. Demo run (PG_TEST_DB=<scratch> + PG_TEST_ALLOWED_DBS declared) failed closed at session setup with **zero SQL against the scratch DB**:
  `RuntimeError: refused: the owner role 'ensemble' of the scratch database 'ensemble_test_cncap_parity_43592' also owns the live database 'ensemble_dev' — a scratch database must not be co-owned with production` (rc=1, 2 errors at setup, fence fixture `tests/postgres/conftest.py:211`).
  Therefore the repo lane ran against the conftest's sanctioned exact-name scratch `ensemble_test` (repo convention: multiple packs export `PG_TEST_DB=ensemble_test`; ownership-exempt `CO_OWNED_LEGACY_THROW_AWAYS`); the unique scratch DB hosted the Phase-3 harness, which carries its own target assertions.

## Phase 2 — repo's own PG lane for critical notes

- File (registered, present at base and HEAD): `tests/postgres/test_critical_notes_repository_pg.py` (2 tests, `SQLModelProjectRepository.update_critical_note` reference=None guard, via `pg_repository_factory`).
- **Conftest env names consumed** (read from `tests/postgres/conftest.py` FIRST, per brief):
  `PG_TEST_HOST`, `PG_TEST_PORT`, `PG_TEST_DB`, `PG_TEST_USER`, `PG_TEST_PASSWORD`, plus allowlist declaration `PG_TEST_ALLOWED_DBS` (comma-separated exact names).
- **Form A** (`-m postgres`):
  `timeout 300 env -u POSTGRES_DB -u DATABASE_URL … PG_TEST_HOST=localhost PG_TEST_PORT=5432 PG_TEST_DB=ensemble_test PG_TEST_USER=ensemble PG_TEST_PASSWORD= .venv/bin/python -m pytest tests/postgres/test_critical_notes_repository_pg.py --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly`
  → `2 passed in 0.91s`, **rc=0**. No failing nodes.
- **Form B** (same, minus `-m postgres`):
  → `2 passed in 0.75s`, **rc=0**. No failing nodes.
- (Marker trap check: the `postgres` marker auto-applies inside `tests/postgres/`; both forms selected the same 2 tests.)

## Phase 3 — ad-hoc resolver parity harness (`/tmp/cncap_pg_parity.py`)

Run: `cd <wt> && timeout 120 env -u … .venv/bin/python /tmp/cncap_pg_parity.py 2>&1 | tee /tmp/cncap_pg_parity.log` → **HARNESS_RC=0, 11/11 probes pass** (`"all_pass": true`).

Full harness JSON output (one line per probe, verbatim from `/tmp/cncap_pg_parity.log`):

```json
{"probe": "seed_bytes_raw_sql", "variant": null, "expected": {"stored_matrix": ["3F2B8A6E-1C4D-4E5F-9A0B-2C3D4E5F6A7B", "5e5f6a7b-8c9d-4e0f-1a2b-3c4d5e6f7a8b", "a1b2c3d4-e5f6-4789-abcd1-ef0123456789", "b7e4c2d0-9a8b-4c3d-8e7f-1a2b3c4d5e6f ", "d1e2f3a4b5c64d7e8f9a0b1c2d3e4f5a", "\u200bc9d8e7f6-a5b4-4231-90af-fedcba987654"], "stored_twin_ok": true}, "observed": {"stored_matrix": ["3F2B8A6E-1C4D-4E5F-9A0B-2C3D4E5F6A7B", "5e5f6a7b-8c9d-4e0f-1a2b-3c4d5e6f7a8b", "a1b2c3d4-e5f6-4789-abcd1-ef0123456789", "b7e4c2d0-9a8b-4c3d-8e7f-1a2b3c4d5e6f ", "d1e2f3a4b5c64d7e8f9a0b1c2d3e4f5a", "\u200bc9d8e7f6-a5b4-4231-90af-fedcba987654"], "stored_twin_ok": true}, "pass": true}
{"probe": "variant_remove_via_canonical", "variant": "uppercase", "expected": {"listed": true, "remove_canonical": true, "row_gone": true}, "observed": {"listed": true, "remove_canonical": true, "row_gone": true}, "pass": true}
{"probe": "variant_remove_via_canonical", "variant": "trailing", "expected": {"listed": true, "remove_canonical": true, "row_gone": true}, "observed": {"listed": true, "remove_canonical": true, "row_gone": true}, "pass": true}
{"probe": "variant_remove_via_canonical", "variant": "zwsp", "expected": {"listed": true, "remove_canonical": true, "row_gone": true}, "observed": {"listed": true, "remove_canonical": true, "row_gone": true}, "pass": true}
{"probe": "variant_remove_via_canonical", "variant": "unhyph", "expected": {"listed": true, "remove_canonical": true, "row_gone": true}, "observed": {"listed": true, "remove_canonical": true, "row_gone": true}, "pass": true}
{"probe": "malformed_unresolvable", "variant": "malformed(unhyph-33hex-request)", "expected": {"listed": true, "remove_canonical": false, "row_survives": true, "resolution": "MissingCriticalNoteId"}, "observed": {"listed": true, "remove_canonical": false, "row_survives": true, "resolution": "MissingCriticalNoteId"}, "pass": true}
{"probe": "malformed_exact_bytes_retry", "variant": "malformed(exact-bytes)", "expected": {"remove_canonical": true, "row_gone": true}, "observed": {"remove_canonical": true, "row_gone": true}, "pass": true}
{"probe": "junk_prefix_refused", "variant": "'x5e5f6a7b-8c9d-4e0f-1a2b-3c4d5e6f7a8b'", "expected": {"remove_canonical": false, "control_survives": true, "resolution": "MissingCriticalNoteId"}, "observed": {"remove_canonical": false, "control_survives": true, "resolution": "MissingCriticalNoteId"}, "pass": true}
{"probe": "junk_prefix_refused", "variant": "'zz-5e5f6a7b-8c9d-4e0f-1a2b-3c4d5e6f7a8b'", "expected": {"remove_canonical": false, "control_survives": true, "resolution": "MissingCriticalNoteId"}, "observed": {"remove_canonical": false, "control_survives": true, "resolution": "MissingCriticalNoteId"}, "pass": true}
{"probe": "control_remove_canonical", "variant": "control", "expected": {"remove_canonical": true, "row_gone": true}, "observed": {"remove_canonical": true, "row_gone": true}, "pass": true}
{"probe": "twin_ambiguous_refusal", "variant": "twin-representation-pair", "expected": {"resolution": "AmbiguousCriticalNoteId", "match_count": 2, "remove_canonical": false, "both_rows_untouched": true}, "observed": {"resolution": "AmbiguousCriticalNoteId", "match_count": 2, "remove_canonical": false, "both_rows_untouched": true}, "pass": true}
{"probe": "SUMMARY", "server_version": "PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin23.6.0, compiled by Apple clang version 16.0.0 (clang-1600.0.26.6), 64-bit", "scratch_db": "ensemble_test_cncap_parity_43592", "probes_run": 11, "all_pass": true}
```

### Observed parity matrix (PG)

| variant (stored bytes) | listed | remove via canonical | row gone | note |
|---|---|---|---|---|
| UPPERCASE | ✅ | ✅ True | ✅ | resolved via normalize arm |
| TRAILING-SPACE (`…6f `) | ✅ | ✅ True | ✅ | strip arm |
| U+200B-PREFIX (`\u200b…`) | ✅ | ✅ True | ✅ | invisible-char strip |
| UN-HYPHENATED (32 hex) | ✅ | ✅ True | ✅ | uuid.UUID reparses |
| MALFORMED (5-char 4th group, 33 hex) | ✅ | ❌ False (un-hyph representation request) | ❌ row SURVIVES | `MissingCriticalNoteId` — W1 asserted limitation |
| MALFORMED exact-bytes retry | — | ✅ True | ✅ | W1 sanctioned escape hatch (resolver arm 1) |
| CONTROL (canonical bytes) | ✅ | ✅ True | ✅ | exact-byte arm |
| junk-prefixed (`x<canon>`, `zz-<canon>`) | — | ❌ False (refused) | control row survives | `MissingCriticalNoteId`; matches nothing (W2) |
| representation TWIN (upper + canon, same project) + byte-exact request | ✅ both listed | ❌ False (refused) | BOTH rows untouched | `AmbiguousCriticalNoteId(match_count=2)` (W4/W5) |

**Parity verdict: matches the SQLite-leg expectation.** Resolver behavior on real PG is byte-for-byte the same tri-state contract: variants removable via canonical at HEAD; junk refused (nothing deleted); twin ambiguous (refusal, nothing selected); malformed = asserted limitation (unresolvable through representation matching; exact-bytes retry works by design); control removable. The matching logic is pure Python (`critical_note_ids.py`) and the scan is a project-scoped id-column SELECT — no dialect-divergent SQL on this path.

### Harness findings (side observations, no code changes made)

1. **`critical_notes.id` is a TABLE-GLOBAL PK** — the same id bytes cannot exist in two projects (`UniqueViolation` on the first seed attempt). A representation-twin pair therefore must live within ONE project (twin probe uses its own distinct canonical).
2. **Write-side normalization seam**: `CriticalNoteModel.__init__` normalizes `id` at construction, so legacy variant bytes can only be persisted by re-assigning `note.id` AFTER construction (ORM attr set bypasses the seam); stored bytes then verified via raw SQL (`SELECT id …`) before probing — `seed_bytes_raw_sql` probe pins the exact stored bytes (incl. trailing space, U+200B, uppercase).
3. Case-variant of a malformed id still resolves (normalize lowercases regardless of uuid success); the un-hyphenated 33-hex representation does NOT resolve (uuid rejects → no canonicalization; fingerprint needs exactly 32 hex on both sides). Exact-bytes retry is the sanctioned escape hatch (matches `critical_note_ids.py` docstring W1).

## Phase 4 — teardown + proof

- `pg_terminate_backend` on scratch backends: **0 terminated** (harness had already disconnected; `engine.dispose()` at exit).
- `DROP DATABASE ensemble_test_cncap_parity_43592 WITH (FORCE);` → **DROP OK** (script RAISES on failure — `ON_ERROR_STOP=1`, explicit `|| exit 93`).
- Verified gone: `SELECT count(*) FROM pg_database WHERE datname='<scratch>'` → **0**.
- Zero remaining connections: `SELECT count(*) FROM pg_stat_activity WHERE datname='<scratch>'` → **0**.
- Live-DB safety assertions:
  - No session in this task ever named `ensemble_prod` or `ensemble_dev` as a connect target: every psql connect was explicit `-d postgres` (maintenance) or `-d <scratch>`; the pytest lane targeted `ensemble_test` via `PG_TEST_DB`; the harness + fence assert `current_database()` before any SQL and HARD-ABORT otherwise (abort path proven live by the fence-refusal demo).
  - Connection census pre-work: `ensemble_prod -> 17`, `ensemble_dev -> 0`. Post-teardown census: `ensemble_prod -> 17 conns` (UNCHANGED — the live daemon's own pool), `ensemble_dev` absent (0 connections throughout).
  - Worktree untouched except this evidence file (`git status --porcelain` shows only the pre-existing untracked evidence files from sibling lanes + this file).

## Artifacts

- Harness script: `/tmp/cncap_pg_parity.py`
- Harness log: `/tmp/cncap_pg_parity.log`
- Scratch name record: `/tmp/cncap_scratch_db.txt` (DB dropped)
