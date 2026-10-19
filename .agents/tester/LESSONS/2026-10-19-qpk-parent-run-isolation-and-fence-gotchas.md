# LESSONS — FAIL@parent proofs, env fences, and PG seeding gotchas (qpk-rehydrate commission, 2026-10-19)

## 1. Isolated parent-run recipe for FAIL@parent regression proofs (no second venv needed)

Problem: proving a new test file is RED at the parent commit without disturbing the feature worktree, main checkout, or building a second venv (worktree `.venv` editable-install pins imports to the worktree).

Recipe (verified working, d1377f596 vs 333b2c225):
```bash
TMP=$(mktemp -d /tmp/<slug>-parent.XXXXXX)
git -C <worktree> archive <parent-sha> | tar -x -C $TMP     # no checkout touched
cp <worktree>/tests/<new_test_file>.py $TMP/tests/           # HEAD tests, parent code
cp <worktree>/.env $TMP/.env                                  # cwd-relative env loaders behave the same
SITE=$(<worktree>/.venv/bin/python -c "import site; print(site.getsitepackages()[0])")
cd $TMP && PYTHONPATH=$TMP:$SITE <worktree>/.venv/bin/pytest tests/<file> -v --tb=short -p no:cacheprovider
```
- `PYTHONPATH=$TMP:$SITE` beats the venv's editable `.pth`: repo code resolves from `$TMP` (parent), third-party deps from the venv site-packages.
- GATE IT: assert `import daemon; print(daemon.__file__)` starts with `$TMP` BEFORE pytest — if it resolves to the worktree, the isolation failed; STOP, do not improvise.
- `git archive` (not `worktree add`) avoids worktree-census/giter-fence friction and needs no venv of its own.
- Cleanup: `rm -rf $TMP` only.

## 2. `/bin/sh` env-fence no-op (occ#6 class, re-confirmed)

A fence step written as `set -a; source ./.env; set +a` silently does NOTHING under `/bin/sh` (`source` undefined → error or fallback shell), after which echoed "fence" values are the AMBIENT poison (`POSTGRES_HOST=10.44.0.2`, `POSTGRES_DB=ensemble_prod`). Harmless here because the assert compared against the dev shape and the worker re-fenced with POSIX `. ./.env`. Rule: always write fences POSIX-portable: `set -a; . ./.env; set +a`.

## 3. SQLAlchemy named params × PG `::jsonb` cast collision

`text("... :payload::jsonb ...")` → `psycopg.errors.SyntaxError` (colon ambiguity). Fix: `CAST(:payload AS jsonb)`. Portable across SQLAlchemy/psycopg versions. Hit while seeding `instances.metadata` in the Gate-2 harness.

## 4. Dispatch-path bookkeeping

- Inventory tables can carry path drift: `test_discard_on_startup_dependency_bus_race.py` actually lives under `tests/unit/services/` (was tabled without `services/`). Workers should verify existence and report corrections; dispatchers should treat inventory paths as unverified until first run.
- Developer-claimed parent-failure counts can be imprecise (claimed 4F/1P; measured 6F/1P). Always re-measure; report the measured number.
- New test files landing with a fix MUST get a PACKS.md entry in the same commission — `test_question_pack_boot_rehydrate.py` arrived unregistered (drift caught by inventory worker, fixed by tester at aggregation).
