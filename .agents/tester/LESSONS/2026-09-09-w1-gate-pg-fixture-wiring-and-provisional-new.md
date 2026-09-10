# W1 Gate 2026-09-09 — PG fixture wiring, provisional-NEW classification, misc traps

Context: W1 maintenancer-agent full regression (`feature/maintenancer-agent` @ `c24e399d`, base `a6b0ac0f`). RESULTS/2026-09-09-w1-maintenancer-full-regression.md.

## L1 — Verify PG-conversion expectations against FIXTURE WIRING, not skip messages

The dispatch expectation "4 PG-conditional skips convert to passes with ENSEMBLE_TEST_PG_URL" was structurally satisfiable for only **2/4**. Two tests in `tests/unit/tools/test_ens_db_repair_idempotent.py` gate on `engine.url.get_backend_name().startswith("postgres")` but their `engine` fixture (:33-51) is hardcoded `sqlite:///` and never reads `ENSEMBLE_TEST_PG_URL` — they skip "PG-only" **even when PG is available**. The sibling file `test_ens_db_tools_select_only.py` wires correctly via `shared_engine` → `_maybe_pg_engine()`.

**Rule**: before promising skip→pass conversions, grep the TEST file (not just conftest) for the env var: `grep -n "ENSEMBLE_TEST_PG_URL" <test file>` — a skip message mentioning PG is NOT evidence the fixture can ever produce a PG engine. Dead PG pins = silently-vacuous acceptance tests (the worst kind: green-looking coverage).

## L2 — HEAD-side "NEW/unexpected" failure classification is provisional until base-adjudicated

`test_wanderer_agent.py` ×2 failed at HEAD with symptoms on the exact surface W1 touched (wanderer meta) and were classified NEW by the HEAD pack. Base adjudication: FAIL@base too — base tools.allow had **16** entries (incl. `db`,`infra`,`blueprint`); W1's only change removed `system-log` (→15), *shrinking* a pre-existing mismatch. The HEAD worker's instinct to caveat with a meta diff was right but used the wrong anchor commit (91651cb2 = P3 head, not base a6b0ac0f) — FIX-NOW-batch-era diffs are NOT the branch delta.

**Rule**: (a) "NEW" classification requires either a base run or a `git diff <base>..<head> -- <file>` showing the branch touches the offending token; (b) the diff anchor must be the TRUE base, never an intra-branch commit; (c) count-based meta assertions can pre-exist mismatched and still change count across the branch.

## L3 — Misc environment traps (this gate)

- `AgentRegistry(agents_dir)` does NOT auto-wrap `str`→`Path` (registry.py:508; `discover()` raises `AttributeError` at :521 on str). Always pass `Path('agents').resolve()`.
- Fresh worktree `.venv`s (bare `uv sync`) lack `psycopg2` — SQLAlchemy's default PG driver; PG-gated tests `ModuleNotFoundError` until `uv pip install psycopg2-binary` (venv-only, disclose it). Worktree venv ships `psycopg` v3 only.
- `POSTGRES_DB=ensemble_prod` sits in the interactive shell env — any PG test run MUST provision a disposable instance (local `initdb`/`pg_ctl` on port 154xx works when docker is down; ~30s incl. teardown).
- `tests/unit/tools/test_prompt_section_reference_integrity.py` (1287 tests) is the bulk of the "permanent integrity gate" — path tokens in ANY new agent prompt file trip `test_no_bare_md_filename_tokens_in_prompts[<file>.md]` with the file named in the parametrize. Cheap pre-flight for prompt-shipping branches: run just `-k "bare_md"` first.
