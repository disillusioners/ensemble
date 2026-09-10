# PG Shadow-Module search_path + /tmp-Staged Pack PROJECT_DIR (2026-09-10 gate)

Two verifier-infra bug classes from the RESUME_ROUTER dup-fix PG gate (branch `feature/fix-resume-router-report-dup` @ `004b4a26`). Neither touched source; both cost a re-dispatch cycle and one of them SILENTLY MASKS PG runs.

## 1. Connection-scoped `SET search_path` silently masks PG shadow runs (the dangerous one)

**Shape:** shadow module's `engine` fixture did `with eng.begin(): c.exec_driver_sql("SET search_path TO my_schema, public")` then `SQLModel.metadata.create_all(eng)` + per-test `DROP SCHEMA my_schema CASCADE`.

**Failure mode:** `SET` inside one connection-transaction does NOT apply to other pool connections. `create_all` and test bodies got fresh connections → tables landed in `public`, the per-test `DROP SCHEMA my_schema` was a NO-OP, and tests collided cross-test on repeated primary keys (`UniqueViolation: message_queue_pkey`, e.g. repeated `rmsg-1`). W5 saw 19/37 fail this way before the fix; a single-node canary smoke does NOT catch it (first test passes — collisions need ≥2 tests sharing keys).

**Fix (durable recipe):** put search_path in the libpq connect options so EVERY connection resolves the test schema first:

```python
import urllib.parse
opts = urllib.parse.quote(f"-c search_path={schema},public")
eng = create_engine(f"{base_url}?options={opts}", poolclass=NullPool)
```

**Canary discipline:** a 1-real-node smoke proves import+schema+seed, NOT isolation. For shadow modules, smoke at least 2 tests that reuse the same seed keys, or run the full file once before declaring the harness ready.

## 2. `/tmp`-staged pack scripts must anchor PROJECT_DIR explicitly

**Shape:** pack scripts copied the in-repo convention `PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../../" && pwd)"` — correct only for scripts living 4 levels deep INSIDE the repo. Staged at `/tmp/resdup-gate/packs/`, the traversal clamps at `/` → `cd /` → drift-pin `git rev-parse` dies (exit 128) under `set -e` BEFORE the script's own ABORT branch (declared exit 5) fires.

**Fix (durable recipe):** `PROJECT_DIR="${PROJECT_DIR:-/abs/path/to/repo}"` — env-overridable hardcoded default; drift-pin semantics preserved.

**Dispatch lesson:** when re-delivering a pack whose wrapper is defective but whose TARGET is well-defined, the ad-hoc direct invocation (target + caller-side drift pin + dual-layer timeout inline) is the accepted recovery — 3 of 6 packs recovered that way this gate, all first-try PASS. Reserve script repair for packs whose scripts carry irreplaceable lifecycle (DB create/drop traps, env guards) — the 3 PG packs.

## 3. Reviewer-PG unblock recipe (validated)

Fresh `CREATE DATABASE <name> OWNER ensemble` on the shared local cluster → role CAN create tables on `public` (probe table create/drop proves it). Caveat: PG14 `public` stays bootstrap-superuser-OWNED, so `DROP SCHEMA public` is still forbidden — prefer dedicated test-owned schemas for per-test resets. Disposable DBs on the LIVE cluster are safe with: wholesale `POSTGRES_*` scrub + URL-contains-`ensemble_prod` abort guards + admin ops via `ensemble_test` admin DB + `DROP ... WITH (FORCE)` in EXIT traps.
