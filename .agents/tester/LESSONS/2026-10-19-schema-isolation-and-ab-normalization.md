# 2026-10-19 — job-scoped-answer gate: schema-isolation failure class + A/B normalization mechanics

Four reusable test-infra lessons from the `fix/job-scoped-answer-resolution` functional gate (full report: `RESULTS/2026-10-19-job-scoped-answer-resolution-functional-gate.md`).

## 1. SQLAlchemy schema-isolation: `default_schema_name` is cached BEFORE the connect-event hook

**Root cause:** SQLAlchemy resolves and caches `dialect.default_schema_name` (from the server's default = `public`) during engine/dialect initialization — BEFORE any `connect`-event listener runs. A `SET search_path TO <throwaway>` in a connect hook therefore does NOT change what `create_all(checkfirst=True)` sees: it checks `public.<table>`, finds it existing, and silently skips creating the throwaway-schema copy. All subsequent fixture INSERTs resolve unqualified table names through the cached default → they land in `public`.

**Impact here:** 515 fixture instances + 10 tasks written to `public` of ensemble_dev on the first P8 run. Reversed surgically (fingerprint + created-at window + explicit id list; counts reconciled 549→34, 10→0).

**Fix pattern (now in `/tmp/jaaf-p8/pg_branch_check.py`, adopt for any schema-isolated fixture):**
1. connect-event listener executes `SET search_path TO <schema>` on the raw DBAPI connection, AND
2. `create_all(checkfirst=False, ...)` inside the throwaway schema (no cross-schema existence checks), AND
3. **pre-seed zero-count guard**: before ANY fixture write, assert the target tables are empty (or absent) in the schema you think you're writing to — abort on failure. This guard is what makes the class impossible to repeat silently; it proved itself by aborting an intermediate run with zero writes.

## 2. A/B failure-set diffs require per-node LOCATION attribution, not just set equality

`test_f1_killswitch_tz_matrix.py` child-spawns the RELATIVE path `.venv/bin/pytest` with `cwd=worktree-root`. Any base leg run from a venv-less scratch worktree reds 3 nodes that pass at HEAD — a location artifact, not a code signal. Rules: (a) attribute each diff node (branch / location / divergent) with a mechanism + probe; (b) bridge-probe cheaply (symlink `<base>/.venv/bin/pytest → real venv pytest`, re-run the 3 nodes at base → 3/3 PASS proves location); (c) prefer `CREATE DATABASE` isolation over schema fallback when the role allows — `CREATE DATABASE` was denied (42501) here, forcing the schema path that bit us in lesson 1. Future worktree legs running that file: pre-provision `.venv/bin/pytest`.

## 3. Small mechanics that silently corrupt gate evidence

- `grep -E '^(FAILED|ERROR)'` over pytest `-q` output captures log-record lines that begin with `ERROR` at column 0 (captured-log format `ERROR    daemon.services…`) — over-counted the failure set 15→18. Use `grep -E '^FAILED'` only; junit `failures=`/`errors=` attrs are the cross-check.
- `cmd | tee file` in `/bin/sh` (dash): `$?` reports tee's 0, not pytest's code. Redirect (`> file 2>&1`) then `echo $? >> file`, or wrap in `bash -c` and read `${PIPESTATUS[0]}`.
- Collect-count is NOT a resolution proof: `--collect-only` reported 41/41 with BOTH the correct and the wrong `PYTHONPATH` (collection walked the cwd tree). The discriminator is the guard trio (`import daemon` path + expected ModuleNotFoundError for branch-new module + control import).
- Editable installs are static-path `.pth` files (not MetaPathFinders) → `PYTHONPATH=<base>` beats them deterministically; that is the only reason borrowing the fix worktree's venv binary for base legs is sound.

## 4. Dev-run claims: verify the selection, not just the counts

None of the dev's sweep shapes (`-k` expressions / file lists) existed in any of the 8 commit bodies — only bare counts. The gate derived its own 18-file selection and ran it A/B. Counts claims (2096P/15F) were off by +1 for an innocent reason (HEAD commit itself adds a test) — only a self-derived A/B catches that class cleanly.
