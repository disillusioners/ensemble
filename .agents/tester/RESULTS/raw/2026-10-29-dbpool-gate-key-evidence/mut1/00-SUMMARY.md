# J4 part 1 — MUTATION RE-PROOF legs (dbpool-idle-txn FUNCTIONAL GATE)

Worker: J4 mutation re-proof (part 1 of 2)
Tree: `/home/nea/ensemble-src-wt-tdbp-mut1` (detached @ `c89e3470c64756f6df989a2868ac9d68b484980e`)
Scratch PG: 127.0.0.1:5446, db/role `dbpool_mut1` — torn down at end.
Interpreter: `/home/nea/ensemble-src/.venv/bin/python` (cd = worktree, load-bearing).
Fence: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME=/tmp/tdbp-mut1/fakehome`

## Digest table (sha256)

| Leg | Target file | pre-plant | post-plant | post-restore | restore == pre |
|---|---|---|---|---|---|
| LEG 1 | `daemon/manager.py` | `78bb883fc3eb7e67b2a1ca420abf80c6596e21ab53ae0d459cb523bff4933100` | `2e51e1642f4ff92b1d56094b4d3edb797177123e179869245a853fa9543ebed9` | `78bb883fc3eb7e67b2a1ca420abf80c6596e21ab53ae0d459cb523bff4933100` | MATCH |
| LEG 2 | `daemon/services/mission_record_service.py` | `d9ca54b441fbd1eca3116ee9601770c14fe1f67c868fb24cb3af4c53e4b5c609` | `f198e43b419edab80f6a496292f83a9ace751f99efc645d4d00439c00c8a9492` | `d9ca54b441fbd1eca3116ee9601770c14fe1f67c868fb24cb3af4c53e4b5c609` | MATCH |
| LEG 3 | `daemon/repositories/factory.py` | `0c1e25fed664fb6e651def494749766d9a3899704251291eaa46ccea6ed1ccc4` | `c4d69b07fa12f45f5a05a00c0cd25792cb654c4d3e5d3fb58ae853b546983598` | `0c1e25fed664fb6e651def494749766d9a3899704251291eaa46ccea6ed1ccc4` | MATCH |

## LEG 1 — A1 carrier-dimension pin

Mutation (contract-exact): the strict consumer `live_carrier_exists is False` → `not live_carrier_exists`
at BOTH reconcile variants — `daemon/manager.py:11226` (sync `_reconcile_deferred_report`) and
`daemon/manager.py:12049` (async `_reconcile_deferred_report_async`). 2 sites replaced.

RED — PG behavioural leg `tests/integration/test_dbpool_deferred_report_reconcile_pg.py::TestF8CarrierDimensionNoSecondCarrier`
(2 collected) → **2 failed**:
- route A: `AssertionError: F8 carrier dimension (route A): a SECOND PROCESS_REPORT carrier was enqueued for message msg-7810712c (carriers=2) — the (c) branch consumed an UNKNOWN carrier flag as 'no live carrier'` (`assert 2 == 1`)
- route B: `AssertionError: F8 carrier dimension (route B): a SECOND PROCESS_REPORT carrier was enqueued for message msg-f37d2897 (carriers=2) — …` (`assert 2 == 1`)

RED — static twin `tests/unit/tools/test_dbpool_fix_site_static.py::TestF8StrictCarrierFlagConsumer`
(3 collected) → **3 failed** (covers the SYNC variant):
- `test_both_variants_compare_the_flag_by_identity` — `_reconcile_deferred_report does not compare live_carrier_exists at all`
- `test_the_loose_form_is_absent_from_both_variants` — `uses the LOOSE carrier form not live_carrier_exists at line(s) [11226]`
- `test_the_strict_comparison_guards_the_c_revival_branch` — `the (c) branch condition is not live_carrier_exists and (not db_dead_parent) and self._is_parent_alive(parent_row)`

GREEN after restore: PG leg 2 passed; static twin 3 passed.

NOTE (accuracy): the PG behavioural leg drives the ASYNC twin only (`_reconcile_deferred_report_async`).
The SYNC variant's strict consumer is covered by the static twin, not by the PG leg.

## LEG 2 — T-M7 broad-catch pin

Mutation (contract-exact): `daemon/services/mission_record_service.py:557`
`except MissionEpochConflictError:` → `except IntegrityError:` (1 site; `IntegrityError` already imported at :130).

RED — `tests/unit/tools/test_dbpool_mission_mint.py::TestTm7NonConflictIntegrityErrorPropagates`
(1 collected) → **1 failed**: `Failed: DID NOT RAISE <class 'sqlalchemy.exc.IntegrityError'>` at
`tests/unit/tools/test_dbpool_mission_mint.py:347`.
Full pack (8 collected) → `1 failed, 7 passed` (only the T-M7 node reds).

GREEN after restore: full pack `8 passed`.

## LEG 3 — W-4 flag flip

Mutation (contract-exact): `daemon/repositories/factory.py:277`
`MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED = False` → `= True` (1 site).

RED — `tests/unit/tools/test_dbpool_honesty_blocks.py::TestConstantsHonestyBlock::test_idle_in_txn_backstop_defined_not_applied_structure`
(1 collected) → **1 failed**: `assert True is False` (at :156).
Full pack (8 collected) → `1 failed, 7 passed`.
Second guard also trips — direct render probe under the mutation:
`options = -c timezone=UTC -c idle_in_transaction_session_timeout=60000` (clause present = True).
Independent second owner REDs too — `tests/unit/repositories/test_pg_engine_utc_session.py` → **2 failed, 8 passed**
(`test_create_postgres_engine_binds_utc_session_options` — `assert '-c timezone=...timeout=60000' == '-c timezone=UTC'`;
`test_f2c_idle_in_txn_clause_absent_while_disabled` — `assert True is False`).

GREEN after restore: honesty pack `8 passed`; sibling pack `10 passed`.

## Final state

`git status --porcelain` → EMPTY (no tracked changes, no untracked entries surfaced).
`git diff --stat` → empty. `git diff --cached --stat` → empty. HEAD unchanged at `c89e3470c`.
