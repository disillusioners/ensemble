# A/B Regression Partition Run — Tier-2 Gate Recipe (project canon)

Run a file-partitioned regression comparison between a TIP worktree and a READ-ONLY BASE worktree, producing a defensible net-new-failure verdict.

> Canonical in-repo version. Supersedes dynamic-skill `fd1e01dc-37d5-4a0b-a05e-1933d18a006d` (DB copy retained for searchability; SUPERSEDED preamble requested via `skill_fix`, pending evolution-lane enablement — not yet applied; canonized 2026-10-31, commission tiered-gate-doctrine). Tier contract: `.agents/tester/rules/ensure.md` § Gate Tiers — Tier 2, budget ≤ 90 min advisory, fail-loud; projection guard applies before legs.

## When to use

Any commission asking "did this branch break tests that were green before?" — partition runs across `tests/**`, staged-release gates, or acceptance campaigns where a whole-tree A/B is too slow.

## The leg recipe (identical command, different CWD + interpreter form)

```
env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_PORT \
    -u POSTGRES_URL -u DATABASE_URL_POSTGRES -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER \
    -u PGPASSWORD -u PGPASSFILE -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE -u SSL_CERT_FILE \
    -u SSL_CERT_DIR -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE \
  sh -c '[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh \
  timeout 240 <pytest-cmd> <files> --tb=no -q -rf
```

- **TIP leg**: `cd <TIP>` then `.venv/bin/pytest` (daemon resolves via the worktree's editable `.pth`).
- **BASE leg**: `cd <BASE>` then `<TIP>/.venv/bin/python -m pytest` — `sys.path[0]=''` makes `import daemon` resolve to the BASE tree; deps come from the shared TIP venv.
- The fence is asserted **inside the child**, never in the parent (the parent legitimately carries `ensemble_prod`).
- **Write the scrub+fence ONCE, verbatim, into a runner script file** (e.g. `/tmp/<lane>/run_leg.sh`) and have both legs + every chunk invoke that file. Nesting the fence `sh -c '...'` inside another `sh -c '...'` gets mangled by the agent bash tool's `/bin/sh` wrapper ("Bad substitution" / unterminated quote), and hand-duplicating the string lets the two legs silently diverge. Cheap identity proof: grep both legs' runner scripts for `-u POSTGRES_DB` and `FENCE BREACH` — identical text, or STOP.

## Mandatory order

1. **Import proof FIRST** (base leg): `cd <BASE> && <TIP>/.venv/bin/python -c "import daemon; print(daemon.__file__)"` → MUST print `<BASE>/daemon/__init__.py`. Anything else = STOP.
2. **Venv gate**: from a NEUTRAL cwd (`cd /tmp`), the TIP venv's `import daemon` must print a path inside the TIP worktree — a cwd-relative import would mask a wrong `.pth`.
3. **Fence + scrub self-test (4-case matrix)** — proves each layer independently: **L1 = the `env -u` scrub, L2 = the in-child fence**. Use a marker child (`<cmd>` = `echo CHILD-RAN`). A single naive test proves NEITHER layer (the D7 harness shape burned ~4 diagnostic calls, 2026-10-21). Run all four; any failure = STOP and report:
   - **(A) in-child poison** (L2 fires): `sh -c 'POSTGRES_DB=ensemble_prod; export POSTGRES_DB; [ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh <cmd>` → rc != 0, `FENCE BREACH` emitted, `CHILD-RAN` ABSENT.
   - **(B) positive control**: full scrub + unpoisoned marker child → rc 0, `CHILD-RAN` present, no `FENCE BREACH`.
   - **(C) scrub-effectiveness probe** (L1): parent exports `POSTGRES_DB=ensemble_prod`; full-scrub child `sh -c 'echo "POSTGRES_DB=[${POSTGRES_DB:-<unset>}]"'` must print `[<unset>]`.
   - **(D) fence-independence regression** (L2 is real, not dead code): drop ONLY `-u POSTGRES_DB` from the scrub, parent carries the poison → the fence alone must emit `FENCE BREACH` + rc != 0.
   Never "fix" case A by prefixing the poison on the parent command line — that re-creates the original bug.
4. **Shared-scratch pre-flight (serial-only classification)** — before any chunk execution: `grep -rln 'dual_backend_engine\|auth_pg_scratch_helper\|scratch_pg' <partition> | grep -v __pycache__`. Any chunk whose file list contains a hit MUST run strictly ONE LEG AT A TIME (full base chunk, then full tip chunk), never concurrently — the shared scratch DB uses ONE scratch PG with FIXED table names (DROP+CREATE per fixture) and its guard skips only under `PYTEST_XDIST_WORKER`, so two INDEPENDENT pytest processes race on catalog creation. **DF-4 measurement (2026-10-22, HEAD 9959f9f4b vs BASE 60d7daaf4, tests/unit chunks 18/19/20/23): 4 fake NET-NEW + 5 fake BASE-only reds under parallel legs = 9 fabricated reds. The parallel asymmetry is VOID until re-run serially; identical failing sets on both legs serially = STABLE verdict.**
5. **Lists**: tip list = the partition selector; base list = tip list minus files absent at base. Report exclusions explicitly.
6. **Slicing proof** (ensure.md non-negotiable, all tiers): `--collect-only -q` over each leg's file list — executed == declared, node counts recorded in the gate report. A leg without a slicing proof is INVALID.
7. **Chunking**: split into ~13-file chunks (`split -l 13 -d`), same chunk partition on both legs; run sequentially; aggregate `^FAILED` lines. Keep each chunk well under the per-run timeout. Partitions containing a real-PG perf-matrix file: shard that file ALONE (own chunk); any raised per-shard timeout applies IDENTICALLY to both legs.
8. **Diff (three sets)**: `net-new = tip−base`, `pre-existing = ∩`, `fixed = base−tip`.
9. **Flaky re-proof**: every node in (net-new ∪ fixed) re-run 3× on the relevant leg, no code change; ≥1 pass AND ≥1 fail = FLAKY (own bucket). Even when the mandated set is empty, re-run the pre-existing reds 3× on BOTH legs to prove they are stable reds, not flakes masking a regression. Base-leg LOCATION artifacts — tests spawning `.venv/bin/pytest` with `cwd=<worktree root>` that fail in ANY base worktree — are classified environment/location artifact, mechanism-verified, and EXCLUDED from net-new counts; cross-reference the canon skill `.agents/tester/skills/ab-base-leg-provisioning-artifacts/` for the three known families.
10. **Base hygiene**: confirm the base worktree `git status --porcelain` is EMPTY before and after. `__pycache__/` and `.pytest_cache/` are gitignored. A transient row is usually a CONCURRENT lane's artifact — attribute by mtime before claiming it as yours.

## Gotchas that will bite you

- **`FAILED ` prefix**: node ids harvested from `-rf` summaries carry the literal prefix. Passing it to pytest = exit code **4 (usage error), no test ran**. Strip: `sed 's/^FAILED //'`.
- **rc semantics**: 0 = pass, 1 = failure, 4 = usage error (bad node id), 5 = nothing collected, 124 = timeout. Never treat a uniform rc=4 across a re-proof matrix as "flaky" — that is a harness bug.
- **Poison placement**: `POSTGRES_DB=ensemble_prod env -u POSTGRES_DB ...` strips the poison BEFORE the child can see it — prefix assignment + `-u` of the same var = unset in the child (rc 0, body runs, reads as "fence broken"). Poison goes INSIDE the child (set + export before the fence check) or stays ambient in the parent with the matching `-u` dropped.
- **`sh -c` nesting**: never nest the fence string inside another `sh -c '...'` — the agent tool's `/bin/sh` wrapper mangles the quoting. Ship the scrub+fence in the runner script file instead.
- **Zero-length list**: `$(cat file)` unquoted word-splits on spaces in param ids — use `mapfile -t` + `"${ARR[@]}"`.
- **Contention**: sibling partition lanes run concurrently against the SAME worktrees. Same-count results on both legs plus a stable-red 3× re-proof is the defense against load-induced noise.
- **Shared-scratch-DB concurrency (signature classifier)**: `ERROR at setup of ...` + `psycopg.errors.UniqueViolation` on `pg_type_typname_nsp_index` in a parallel-leg asymmetry is a shared-scratch-DB concurrency artifact, NEVER a branch regression.
