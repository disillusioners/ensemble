# Regression sweep plan — hard-reboot P4 acceptance (hrr-20261021)

**TIP:** `4641b17d5f3bc644b0a0dfd5c2acafa75b86333c`
**TRUE pre-fix BASE:** `4c888fbbbe630ebf0c52877beda0a1b839f7cbd5`
**Base worktree:** `/tmp/hrr-base-4c888fbb` (shared, READ-ONLY) — created by lane `a756e31a`.

## Method (net-new-failure discipline)

Every partition runs the **IDENTICAL command** on both legs and produces a machine-comparable
**FAILED-node list** (nodeid + error class). Net-new = present at TIP, absent at BASE.

- `red-at-base` → **pre-existing**, NOT net-new.
- `green-at-base → red-at-tip` → **NET-NEW**, must be called out by name (this is the class a
  subset-only sweep misses).
- `flaky` (passes on a re-run with NO code change) → reported separately, never counted as
  net-new and never quietly folded into "pre-existing".
- A "zero net-new" claim is only valid when the FAILED-list diff was computed over the **whole
  house suite set** — not from a targeted subset.

## House suite set

Whole `tests/` tree, **default addopts** (`-m 'not integration and not postgres and not slow'`)
— the house non-integration suite as defined by ensure.md's Release Gate. Deselection is
reported per partition; a directory that is entirely deselected is reported as **0 nodes**, never
as a pass.

## Partitions (explicit file lists — each lane builds its own list, then runs it)

| # | Partition | Scope | Files |
|---|---|---|---|
| U1 | `tests/unit/services/` | | 135 |
| U2 | `tests/unit/tools/` | | 77 |
| U3 | `tests/unit/{plugin_subsystem,routers,repositories}/` | | 66 |
| U4 | `tests/unit/test_[a-e]*.py` | unit root, letters a–e | 119 |
| U5 | `tests/unit/test_[f-m]*.py` | unit root, letters f–m | 111 |
| U6 | `tests/unit/test_[n-z]*.py` | unit root, letters n–z | 162 |
| U7 | `tests/unit/` remaining subdirs | job_queue, tool_pairing_history(_gate), rag, persistence, models, config, sources, job_state, graph, checkpoint_adapter, agents | 27 |
| R1 | `tests/test_[a-h]*.py` | tree root | 51 |
| R2 | `tests/test_[i-q]*.py` | tree root | 84 |
| R3 | `tests/test_[r-z]*.py` | tree root | 60 |
| D1 | `tests/integration/` | | 200 |
| D2 | `tests/job_queue/` | | 114 |
| D3 | `tests/postgres/` + `tests/vfy_pg/` | PG-marked | 41 |
| D4 | `tests/services/` + `tests/e2e/` | | 50 |
| D5 | `tests/{message_queue_redesign,migration,tools}/` | | 53 |
| D6 | `tests/{repositories,api,property,performance,perf,manager,vfy}/` | | 24 |
| D7 | `tests/{static,lint}/` | | 2 |

**Total: 17 partitions, ~1,220 test files.**

## Per-partition lane contract

1. Build the EXPLICIT file list (`ls`/glob) and write it to `/tmp/hrr-part-<id>.list`.
2. **TIP leg:** `cd <tip worktree> && .venv/bin/pytest $(cat /tmp/hrr-part-<id>.list) --tb=no -q -rf`
   → parse the `FAILED <nodeid> - <class>` lines into `/tmp/hrr-part-<id>.tip-failed.txt`.
   Record: collected / passed / failed / errors / skipped / deselected / xfailed.
3. **BASE leg:** the IDENTICAL command from the base worktree with the base interpreter
   (path + cwd reported by lane `a756e31a`) → `/tmp/hrr-part-<id>.base-failed.txt`.
   **The base leg MUST import-prove** `daemon.__file__` inside `/tmp/hrr-base-4c888fbb` — a base
   leg that imports the TIP tree is void.
4. Diff the two FAILED lists → net-new set, red-at-base set, fixed-by-this-branch set.
5. **Flaky re-proof:** any node that failed on ONE leg only (tip-fail+base-pass, or the reverse)
   gets **3 re-runs with NO code change** on that leg. ≥1 pass AND ≥1 fail → **FLAKY** (report
   separately; do not count as net-new; do not silently reclassify as pre-existing).
6. Report the FULL failed-node list for both legs (nodeid + error class), the per-leg counts, the
   import proof for the base leg, and the net-new set — even when empty.
7. If the partition exceeds ~4 minutes, split it into sequential sub-runs (each under the cap)
   and report the aggregate.

## Env discipline for every lane

- Ambient env carries LIVE `POSTGRES_HOST=10.44.0.2` / `POSTGRES_DB=ensemble_prod` /
  `ENSEMBLE_SELF_ENV=live` + a live password.
- Every pytest invocation runs under the **22-var child-env scrub** with the fence asserted
  INSIDE the child:
  `env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_PORT -u POSTGRES_URL -u DATABASE_URL_POSTGRES -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGPASSFILE -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE -u SSL_CERT_FILE -u SSL_CERT_DIR -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE sh -c '[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh …`
- The scrub must be IDENTICAL on both legs — that is part of "identical commands".
- Never touch: live daemon (:9797), live demo (:7979), `/home/nea/agents-ensemble`,
  `10.44.0.2/ensemble_prod`, port 8088, `/etc/systemd/system/*`.
- The base worktree is **READ-ONLY** for regression lanes: do not write into it (no venv
  creation, no caches that change tracked files). If a lane needs a writable artifact, use /tmp.

## PG-marked partition (D3) — special

`tests/postgres/` + `tests/vfy_pg/` are `postgres`-marked and are DESELECTED under the default
addopts. This lane runs them with the marker ENABLED, against a **throwaway local PG16 role+DB**
(`sudo -n -u postgres psql -c "CREATE ROLE … LOGIN PASSWORD …"` + `CREATE DATABASE … OWNER …`;
`POSTGRES_*` pointed at 127.0.0.1:5432). NEVER `ensemble_prod`, NEVER 10.44.0.2, NEVER grant
CREATEDB to a shared role. If the house `ensemble_sandbox` DB name is REQUIRED by the tests and
does not exist locally, create a purpose-named DB and report the name mismatch as a fact — or,
if the tests hard-require `ensemble_sandbox`, provision it, run, and DROP it, reporting both.
Identical provisioning on both legs.
