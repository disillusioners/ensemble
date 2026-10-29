# FM3 — F4-A surface final merged-tree verification (2026-10-29)

Worktree: /home/nea/ensemble-src-wt-tdbp-fmf4a (detached @ 6947cc2b862e24481e0ce14ac82b5f0744aca3a5 —
"test(dbpool): expected-collected reconciliation to the merged tree (row1 10→23 per FOLD-MAP NOTE-6 ...)")
Scratch worktree; ZERO commits; final porcelain = ONLY this untracked raw dir.
Fence on EVERY command: env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST
-u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)" ENSEMBLE_SELF_ENV=dev
/home/nea/ensemble-src/.venv/bin/python -m pytest ...

## Digest table — daemon/services/readiness.py

| state | sha256 |
|---|---|
| pristine (pre-plant baseline == post-restore, ×3) | f46e0342fe6ef1550c157aac8ecca3bfc8ea0047050c88dde343ff51d3837c52 |
| PLANT-1 (pre-check deleted from both probe bodies, 2 hunks −4 lines) | 48a598a2404cf6edacdd0784621303cc9a79f63fe8d24e1d5ecf7c5e25f0834f |
| PLANT-EXTRA (threshold re-hard-coded to MANAGER_POOL_CONFIGURED_CAPACITY, 1 hunk ±1) | c590a07a4bf907eafcac54b9a7a5913011c2835f27ca6196ce020f7160d6c8c9 |

## Results

- P1 baseline: 16 passed (test_dbpool_probe_bound.py)
- P1 RED (plant-1): 3 failed / 13 passed — 2 static body pins + the BEHAVIORAL pin
  ("engine.connect() must not be reached at capacity")
- P1 restore: sha256 == pristine; 16 passed
- P1-EXTRA RED: 2 failed / 14 passed — the D-57 structural pin (static-constant comparison)
  + BONUS behavioral reproduction of the self-inflicted outage
  (TimeoutError "manager pool at effective capacity (25/32)" on the 22-tier pool @ 25)
- P1-EXTRA restore: sha256 == pristine; 16 passed
- P2 StaticPool repro ×1: 1 passed
- P3 the 11 (CLASSIFICATION.md rows 1-11) ×1: 11 passed in 5.68s
- P4 owning tests (6 ids) ×1: 6 passed; F4-f PG legs (bonus): 2 passed over the house
  scratch ensemble_test (conftest hard-allowlist fence)
- P4 independent driver: 15/15 checks PASS, rc=0 — /tmp/tdbp-fm3/fm4a_driver.py (copy here)

## Files

P1-baseline.out, P1-plant.diff, P1-red.out, P1-restore-green.out,
P1-extra-plant.diff, P1-extra-red.out, P1-extra-restore-green.out,
P2-staticpool-repro.out, P3-eleven.out, P4-owning-tests.out, P4-driver.out,
P4-f4f-pg.out, readiness.sha256.{pre,post-plant,post-plant-extra}, fm4a_driver.py
