# LCA live-descendants gate verification — operational & technique lessons (2026-09-06)

Gate: RESULTS/2026-09-06-lca-live-descendants-verification.md (fix `8b083522`, verdict PASS). Extracted lessons:

## 1. `POSTGRES_*` family hazard — prod DB leaks through parts (🔴 repeat-offender)
Agent/dispatch shells inherit `POSTGRES_DB=ensemble_prod` (LIVE PROD) **plus the full `POSTGRES_*` parts family**. `daemon/repositories/factory.py` reads parts INDIVIDUALLY — unsetting only `POSTGRES_DB` is INSUFFICIENT isolation; a naive boot/test can still compose a prod DSN from `POSTGRES_HOST/PORT/USER/PASSWORD`. Rule: scrub `POSTGRES_DB POSTGRES_URL POSTGRES_HOST POSTGRES_PORT POSTGRES_USER POSTGRES_PASSWORD` wholesale, then re-export test-scoped values. Verified twice this gate (matrix + PG workers both found the live hazard).

## 2. Worktree editable-install is cwd-sensitive (🔴 silent wrong-tree testing)
`$WT/.venv` editable install registers `$WT` on sys.path, but invoking `$WT/.venv/bin/python` from ANOTHER cwd makes `sys.path[0]=''` resolve the current directory first — the MAIN worktree's `daemon/` wins. Symptom: tests silently run against the wrong tree. Rule: `cd $WT` before every python/pytest invocation in any worktree; verify with `python -c "import daemon; print(daemon.__file__)"` once per session.

## 3. PG-shadow technique for SQLite-default test files (🟢 reusable)
To run a SQLite-default test file (fixture `file_sqlite_engine` from `tests/support/conftest.py`) under real PostgreSQL WITHOUT repo changes:
1. Scratch module imports the test classes/functions verbatim from the original module.
2. Define a **module-level** fixture named `file_sqlite_engine` yielding a PG engine on a disposable DB (module scope shadows conftest — pytest fixture precedence).
3. **Dialect canary test is mandatory** (`engine.dialect.name == 'postgresql'`) — a silent SQLite fallback = false PG proof.
4. Per-test cleanup via **raw asyncpg TRUNCATE (RESTART IDENTITY CASCADE)** — SQLAlchemy NullPool sessions can silently retain rows even after TRUNCATE reports success.
5. Load `tests.support.conftest` via `pytest_plugins` in the scratch dir's conftest so imported tests' sibling fixtures resolve; run from `$WT` rootdir.
Proven: 23/23 live-descendants nodes ran identically under PG (24/24 with canary).

## 4. Local PG permission gap on `ensemble_test`
`public` schema in local `ensemble_test` was owned by another role; `ensemble` lacked CREATE → `InvalidSchemaName: no schema has been selected to create in` on first table create. One-time operator fix: `GRANT CREATE ON SCHEMA public TO ensemble`. NOT a code defect; will recur on fresh local PG setups.

## 5. Semantic pins verified this gate (for future drift checks)
- Deny predicate = NOT attested AND `pending_children=0` AND `queued_or_expected_wakeups=0` AND `live_descendants=0` (4-input AND, attestation_gate.py:397-421); R2-allow reason `allowed_legitimate_pending_wakeup` covers ALL three inputs (no separate live-descendants reason).
- Live set = statuses NOT IN {COMPLETED, TERMINATED, ERROR, FAILED} — a `waiting_children` child IS live (scenario (a) real-BFS value = 2, child + grandchild).
- Escalation flag is set on the deny-evaluation FOLLOWING the 3rd increment (`denied_count+1 > bound`, i.e. 4th un-attested zero-input eval) via atomic `reset_attestation_ledger_with_escalation` (end-state flag=True + count=0). R2-allow NEVER resets the counter; bare denies after escalation leave the flag True.
- Gate log row = 16 canonical fields + 2 additive extras (`next_denied_count`, `should_inject_nudge`).
- Branch default attestation mode = **enforce** (post `d6bd7e31`); boot line reads `mode=enforce ... deny_bound=3`.

## 6. Test-suite seam debt found (follow-up, non-blocking)
- `tests/support/conftest.py:155-176` reimplements `count_live_descendants` (shadow facade) — drift risk vs `manager.py:8601-8655`; recommend routing through the real facade.
- `test_no_repo_returns_zero` is self-referential (asserts its own inline reimplementation).
- Dev flagship test pins `live_descendants=1` as an override rather than building the real incident tree (closed externally by this gate's scenario (a)).

## 7. Misc
- MCP `plane` connection ERRORs at dev boot are benign/optional — do not misread as boot failure in error scans.
- Dev test-count claims can drift from actual (+23 claimed vs +26 actual; 3 additions landed in existing files) — always recount via `--collect-only` ground truth (was done; matrix total 340 exact).
