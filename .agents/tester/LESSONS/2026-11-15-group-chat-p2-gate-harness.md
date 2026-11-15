# Group-Chat P2 gate — harness lessons (2026-11-15)

Bankable harness knowledge from the P2 gate (dual-side regression + 4 behavior workers). None of these
are product defects; all are gate-construction lessons worth reusing on P3/P4.

## 1. Fence-family coverage: use the sanctioned unset-`POSTGRES_DB` shape

Four files carry an inline session fence (`tests/unit/services/test_resource_traceback.py:68-88` and siblings):
`allowed_dbs = {ensemble_dev_test_only, ensemble_test_only}`; `POSTGRES_DB` unset/`""`/`ensemble_prod` → the fence
**self-pins** `ensemble_dev_test_only` and passes; any other value (e.g. the fenced worktree's `ensemble_dev`) →
`pytest.exit` rc=1, which kills an xdist worker (INTERNALERROR rc=3).

- The allowed canary name is **never dialed** — every engine in those files is SQLite (`sqlite:///{tmp_path}` / in-memory).
- Consequence: the standard 10-var fence pinning `ensemble_dev` makes 165 nodes **executed-nowhere** on both legs if
  you merely exclude them (P2 first pass did exactly that). Run them in a separate sanctioned pass (unset `POSTGRES_DB`),
  behind a contact-free DSN probe (`daemon.persistence` _build_ string, no `connect()`), and they close 165/165 both legs.
- Reusable rule: fence-family files belong in their own pack with the sanctioned shape, never inside a mass shard.

## 2. `SET search_path` in a connect listener is silently reverted

A psycopg3 `connect` listener doing `SET search_path=…` runs inside an implicit transaction; the pool's
`reset_on_return="rollback"` reverts it after the first checkout ⇒ DML lands in `public` and a "PASS" can be
measuring the wrong schema. Correct pattern (matches `daemon/persistence/factory.py:271/:311`):
`connect_args={"options": "-csearch_path=<schema>"}`, plus a **placement assert as the first gate**
(`create_all(checkfirst=False)` + assert the tables exist in the private schema, not public).
Observed in gc-p2-naming: first tier-1 "PASS" was invalid; discarded, 7 rows cleaned, residue 0.

## 3. Dual-side fence symmetry — scrub `ENSEMBLE_AUTH_*` on BOTH legs

The host shell carries `ENSEMBLE_SELF_ENV=live`, live `OPENAI_BASE_URL(_BACKUP)`, the 7-var `ENSEMBLE_AUTH_*`
family and `ENSEMBLE_UPGRADE_SCRIPTS_DIR`.

- Ambient auth makes `AuthConfig(enabled=True)` leak into `_parse({})` and turns router reads into 302s ⇒ ~193
  auth/oauth/bearer-shaped reds. If only the HEAD leg scrubs it, the BASE digest carries ~200 extra ids
  (favorable direction, but it buries the real signal and doubles the ledger).
- If you extend the fence asymmetrically the other way (scrub a var on HEAD only), you can mint head-only reds.
  Always verify any head-only id that is auth/upgrade/plugin/resources-shaped under **both** variants before ruling.

## 4. Git-subprocess pins cannot run in a gitless extraction

`git archive <base> | tar -x` is the cheap way to get a base leg without touching repo metadata — but any test that
shells `git` behaves differently there. Example: `tests/unit/repositories/test_source_gc_repository.py:766-771`
runs `git rev-list --first-parent --merges -1 HEAD` and asserts non-empty (:840); in the extraction stdout is `''`
⇒ 4 base-only reds. Classify **ENVIRONMENT-ATTRIBUTED (gitless)**; do NOT describe such a red as "fixed at HEAD".

## 5. pytest 9.0.2 print/junit divergence (re-confirmed)

Shard A printed "135 failed" with **zero** `^FAILED` summary lines; junitxml carried 132F+3E. Always build digests
from junitxml, and remember xunit2 has no `testcase@file` attribute — resolve paths by longest manifest-module
prefix match, or ids come out colon-mangled (`tests::unit::…`).

## 6. Registered pack script does not echo `RESULT:` on failure

`test/packs/concurrency_atomic_unit_test.sh` runs under `set -euo pipefail`; a pytest failure aborts before the
`RESULT:` echo. Classify by exit code + junitxml, never by grepping for `RESULT:`.

## 7. Node-count claims: derive from collection, not from the lane report

The lane reported "136/0" for its test bracket; the diff's actual test files collect **88 nodes**
(37+4+17+22 + the pre-existing 8-node boot-pin file). Always reconcile the claimed bracket against
`--collect-only` before quoting it in a gate.
