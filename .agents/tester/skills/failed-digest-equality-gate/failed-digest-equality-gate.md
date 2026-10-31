# Base-vs-Head FAILED-Digest Equality Gate (project canon)

Proves a candidate tree introduces **zero net-new test failures** vs a true pre-fix base, by running the identical suite on both legs and comparing the sorted FAILED node-id digests.

> Canonical in-repo version. Supersedes dynamic-skill `76e3e1a7-1a5a-4228-957f-f8e556e04213` (DB copy retained for searchability with a SUPERSEDED pointer; canonized 2026-10-31, commission tiered-gate-doctrine). Serves BOTH tiers — on Tier-1 (blast-radius) legs the identical discipline applies over the DECLARED set from `.agents/tester/skills/blast-radius-leg/`.

## When to use

Any "this branch/release adds no regressions" claim — Tier 1 (scoped set) or Tier 2 (whole tree). The whole-tree form is required for whole-tree net-new claims per the repo testing contract; a targeted-subset "zero net-new" claim is only acceptable when the subset IS the declared blast radius (Tier 1, derivation + slicing proof on record).

## Procedure

```sh
# 1. Two throwaway DETACHED worktrees. NEVER the shared dirty checkout.
git -C /home/nea/ensemble-src worktree add --detach <BASE_WT> <BASE_SHA>
git -C /home/nea/ensemble-src worktree add --detach <HEAD_WT> <HEAD_SHA>

# 2. Per-tree venv. Assert the import resolves INSIDE that tree (a shared .venv
#    pins the other branch via the editable .pth).
cd <WT> && /home/nea/.local/bin/uv sync
.venv/bin/python -c "import daemon; print(daemon.__file__)"   # must be inside <WT>

# 3. IDENTICAL command, BASE LEG FIRST, then HEAD LEG. Never concurrently —
#    load coupling pollutes the measurement (and shared-scratch-DB chunks race;
#    DF-4: 9 fabricated reds). Always under an explicit `timeout`.
cd <BASE_WT>
timeout 300 env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST \
  -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL \
  .venv/bin/pytest <SUITE> -q --tb=short -p no:randomly -n 8 > base.out 2>&1
# ...then the same in <HEAD_WT> -> head.out

# 4. Digest the SORTED FAILED node-id list.
grep -E '^FAILED ' base.out | sort | sed 's/^FAILED //' > base.nodeids.txt
grep -E '^FAILED ' head.out | sort | sed 's/^FAILED //' > head.nodeids.txt
sha256sum base.nodeids.txt head.nodeids.txt

# 5. Verify BOTH ways: cmp AND comm in both directions.
cmp base.nodeids.txt head.nodeids.txt                 # rc=0 == byte-identical
comm -23 base.nodeids.txt head.nodeids.txt   # base-only  -> FIXED at head
comm -13 base.nodeids.txt head.nodeids.txt   # head-only  -> NET-NEW reds

# 6. Attribute any collected-count delta PER FILE (a modified test file can change
#    node COUNT or only assertions — per-file counts are the attribution primitive).
.venv/bin/pytest tests/<dir>/<file>.py -q --collect-only -p no:randomly | grep -E '^[0-9]+(/[0-9]+)? tests collected'

# 7. 3x isolated SEQUENTIAL re-run of the shared reds (no -n) proves determinism
#    and rules out load-order flakes. Classification ladder: byte-identical to
#    base => pre-existing; order/load-sensitive => 3x per side; genuinely new =>
#    fix-and-rerun until net-new = empty, with a +N/-N fingerprint.

# 8. TEARDOWN (must not leave worktree metadata behind)
git -C /home/nea/ensemble-src worktree remove --force <BASE_WT>
git -C /home/nea/ensemble-src worktree remove --force <HEAD_WT>
git -C /home/nea/ensemble-src worktree prune
git -C /home/nea/ensemble-src worktree list | grep -c <slug>   # expect 0
```

## Digest form — the trap

**Canonical form: the sorted node-id list with the `FAILED ` prefix STRIPPED** (one per line, trailing newline). The raw `grep '^FAILED '` output is a DIFFERENT digest for the same run.

Always label which form a digest uses. Cross-gate comparisons are only valid within one form — comparing a raw-FAILED-lines digest against a nodeids-only digest fakes an "environment shifted" conclusion. Within-run base-vs-head equality holds under either form.

Known reference digests (`tests/job_queue/`, 15 reds):
- nodeids-only: `82557ce0b62d5f64ed5b78e7e267bbfae618ac70c55d13c42c1d7f27692e4043`
- raw FAILED lines: `fe9978b8586b2b870192d2f775dae2afc9233a1221aef21bd46098b11f068dbe`

## Gotchas

- **`env -u POSTGRES_*` is a SILENT NO-OP** — bash does not glob env names. Scrub per-variable, explicitly, or a "fenced" run can hit `ensemble_prod`.
- **Outer wrapper is `/bin/sh`** — bashisms (`${VAR^^}`, `${PIPESTATUS[0]}`) fail with "Bad substitution". Nest `bash -c '...'`.
- **`git worktree remove` needs the path argument** — `git -C <WT> worktree remove --force` errors with a usage message. Run it from the main repo with the full path.
- **Grep anchors**: a summary line is `2152/2155 tests collected (3 deselected)` for a directory and `77 tests collected in 0.27s` for a single file. Anchor `^[0-9]+(/[0-9]+)? tests collected` — a loose pattern like `error` also matches node-ids such as `test_guard_error_skips_fail_closed`.
- **`--collect-only` is byte-faithful** — safe to compare node lists across trees.

## Fences

Never touch `ensemble_prod` / `:9797` / `ensemble-main` / `current` / `releases/`. No daemon boots, no DB connections (suites are SQLite/mock), never kill processes, no commits/pushes beyond the two scratch worktrees, no HOME-shaped paths. Record the main checkout's `git status --porcelain=v1 | sha256sum` before AND after — it must be byte-identical.
