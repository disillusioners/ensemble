# Gate 2 — PIN FAMILIES GREEN (rogue-containment-p2 tester gate)

- Run date: 2026-11-15 (UTC). Runner: tester worker (pack execution lane).
- Branch: `feature/rogue-port-containment` @ `a7aec8582` (HEAD verified pre-run — exactly the commissioned tip).
- Pack: `rogue_p2_pin_families_unit_test` (ad-hoc, 3 legs, one invocation per leg, each wrapped `timeout 120`).
- **AGGREGATE: PASS — 141 passed / 0 failed / 0 timeout across 3 legs.**

## Environment anomaly (disclosure — load-bearing for leg provenance)

The commissioned worktree `/home/nea/ensemble-src-wt-rogue-contain` was **deleted by an external actor mid-run** (between LEG AH completion ~00:50Z and LEG HY start ~00:51Z):

- LEG BF and LEG AH ran inside the commissioned worktree against its own `.venv` (cpython-3.14) — both green.
- LEG HY first attempt crashed at pytest plugin-load (`PackageNotFoundError: No package metadata was found for pytest_asyncio`) — the venv was being deleted under the running process; directory confirmed gone at ~00:52Z. Zero tests executed on that attempt; **no pin signal lost** (crash was pre-collection).
- `git worktree list` shows the registration as `prunable`; the **branch is intact** at `a7aec8582`; `origin/latest` unmoved at `bf4bfde6e`; branch **not yet merged** at time of check (~00:53Z). Note: the lane's own KV cleanup ruling said "delete at merge cleanup" — the deletion preceded the merge.
- LEG HY was completed on a **byte-identical reconstruction**: `git archive a7aec8582` extracted to `/tmp/rogue-p2-gate2-hy-repro-427990` + `uv sync --frozen` (the task's sanctioned venv-repair path). Zero git mutations; zero interference with the pending finalize. The worktree's `tests/` files were clean vs HEAD at run time (foreign dirty set was `.agents/`-only), so archive bytes == the bytes BF/AH executed.

## LEG BF — prod-DSN fence + DB resolution guard

- Invocation: `timeout 120 env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PGHOST -u PGPORT -u PGUSER -u PGPASSWORD .venv/bin/pytest tests/unit/test_prod_dsn_fence.py tests/unit/test_db_resolution_guard.py -q --tb=short -p no:randomly`
- Where: commissioned worktree `.venv`.
- Exit code: 0 — **RESULT: PASS**
- Count: **44 passed** — claim 44 = 26 `test_prod_dsn_fence.py` + 18 `test_db_resolution_guard.py` (census-exact match).
- Wall time: 7s (pytest 1.12s).
- Failures: none.
- Raw output: rescued as `leg-bf-raw.txt` (orig `/tmp/rogue_p2_leg_bf_426848.txt`).

## LEG AH — port ownership + pins + holder identity

- Invocation: same env scrub; `timeout 120 .venv/bin/pytest tests/unit/test_port_ownership.py tests/unit/test_port_ownership_pins.py tests/unit/test_port_holder_identity.py -q --tb=short -p no:randomly`
- Where: commissioned worktree `.venv`.
- Exit code: 0 — **RESULT: PASS**
- Count: **68 passed** (commission said "record actual"). Census split: `test_port_ownership.py` 26 + `test_port_ownership_pins.py` 31 + `test_port_holder_identity.py` 11 = 68. Reconciliation note: pins(31) + holder(11) = **42 exactly** — the commission's claimed "42-passed shape verified at a7aec8582" maps to the pins+holder sub-family; `test_port_ownership.py` adds 26 beyond it.
- Wall time: 11s (pytest 5.98s).
- Failures: none.
- Raw output: rescued as `leg-ah-raw.txt` (orig `/tmp/rogue_p2_leg_ah_427021.txt`).

## LEG HY — pkill overlay guard + pins

- Invocation: same env scrub; `timeout 120 .venv/bin/pytest tests/unit/test_pkill_overlay_guard.py tests/unit/test_pkill_overlay_pins.py -q --tb=short -p no:randomly`
- Where: attempt 1 — commissioned worktree (crashed pre-collection; env deletion in flight; see anomaly; raw: `leg-hy-crash-raw.txt`). Attempt 2 — byte-identical frozen-tip archive + `uv sync --frozen` venv, pytest 9.0.2.
- Exit code: 0 — **RESULT: PASS**
- Count: **29 passed** — claim 29 = 16 `test_pkill_overlay_guard.py` + 13 `test_pkill_overlay_pins.py` (census-exact match).
- Wall time: 12s (pytest 2.76s).
- Failures: none.
- Raw output: rescued as `leg-hy-retry-raw.txt` (orig `/tmp/rogue_p2_leg_hy_retry_429901.txt`).

## Collect-only census (per-file counts; no test execution)

```
18  tests/unit/test_db_resolution_guard.py      \_ LEG BF = 44
26  tests/unit/test_prod_dsn_fence.py           /
26  tests/unit/test_port_ownership.py           \
31  tests/unit/test_port_ownership_pins.py       |_ LEG AH = 68 (pins+holder = 42)
11  tests/unit/test_port_holder_identity.py     /
16  tests/unit/test_pkill_overlay_guard.py      \_ LEG HY = 29
13  tests/unit/test_pkill_overlay_pins.py       /
```

## COMMIT STATUS: BLOCKED (mechanical; evidence preserved)

The instructed commit target (the worktree) was deleted mid-run, and committing to `feature/rogue-port-containment` NOW would mutate the branch tip while giter's finalize merge (drift-guard pins `latest==bf4bfde6e` + `v0.30.11==29f368431`) is pending — deliberately not done to avoid interfering with the in-flight landing. This evidence pack is rescued at `/tmp/rogue-p2-gate2-evidence-rescue-20261115T0053Z/` (this file mirrors the intended path `.agents/tester/RESULTS/2026-11-15-rogue-p2-pin-families.md`). Ready-to-apply once the dispatcher re-homes a tree:

```
git add .agents/tester/RESULTS/2026-11-15-rogue-p2-pin-families.md && git commit --only -m "test: rogue-p2 gate 2 — pin families green evidence" -- .agents/tester/RESULTS/2026-11-15-rogue-p2-pin-families.md
```

## Fence compliance

- Per-variable env scrub applied exactly as mandated on every invocation (`POSTGRES_*` ×5, `DATABASE_URL`, `PG*` ×5); no ambient `POSTGRES_*`/`DATABASE_URL`/`PORT` vars were visible in the runner shell regardless.
- No other packs/tests run; never `pytest tests/`; no pkill; nothing killed; no live-port contact (9797/9798/7979/8079 untouched — pins are unit-level with their own tmp fences; no PG/boot was required by any pin, as commissioned).
- Foreign dirty files (`.agents/tidier/notes.md`, `boot_rogue_mirror.sh`, `scratch-config.yaml`) untouched throughout.
- QUARANTINE.md check: not triggered — zero pin failures (gate rule applies only on FAIL).
