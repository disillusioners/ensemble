# Direction pins + implementation line map — revive-wedge re-gate (leg 02)

Date: 2026-11-28
Tip: `3a04a78b41344b8f3e7986d3b97672edc843d513`
`daemon/services/child_reports.py` sha256 at tip: `717c9fc4cf00dbb0801346b30316f71763d7aad08a187288b26ea61b508a22a5`

Sources: worker `b6afe9ff` (pin inventory, read-only) and worker `036b667b` (base-leg setup, read-only).

## 1. Coverage matrix — Direction 1 (REVIVE TERMINALIZES) BEFORE the added pin

Canonical signature: child completes → REVIVED from COMPLETED → completes again.
At the pre-fix base: log `completion report skipped: reason=idempotency_skip` **AND** the instance row
stays `running`. At the tip: CAS UPDATE to COMPLETED, a NEW report minted, and the synchronous
`report_injections` row minted and reaching `completed`.

| Candidate pin | (a) log line | (b) row status | (c) new report minted | (d) `report_injections` persisted |
|---|---|---|---|---|
| `test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child` (L2733-2743) | N | Y | Y (negative form) | N |
| `test_child_reports.py::TestReviveWedgeDecoupling::test_the_suppress_outcome_reaches_the_b4_gate_on_a_completed_row` (L2790-2811) | N | Y | N | N (bus emission is MOCKED; `report_injections` never queried) |
| `test_child_reports.py::TestReviveWedgeDuplicateFreedom::test_an_explicit_revive_then_a_new_anchor_mints_a_second_report` (L2905-2912) | N | Y | Y | N |
| `test_stale_running_sweep_rider_pins.py::TestReviveWedgeNarrow::test_new_anchor_with_prior_delivered_report_mints_a_second_row` (L760-781) | N | Y | Y | N |
| `test_stale_running_sweep_rider_pins.py::TestReviveWedgeNarrow::test_same_anchor_re_supplied_with_prior_delivered_report_does_not_mint` (L784+) | N | Y | Y (negative) | N |
| `test_stale_running_sweep_pins.py::TestReviveWedgeDriveOutcome::test_revive_wedge_victim_lands_in_healed` | N | Y | N | N |
| `tests/postgres/test_child_reports_narrow_guard_pg.py::test_new_anchor_report_is_minted_and_child_terminalizes` | N | Y | Y | N |

**VERDICT (pre-addition): NO existing pin covers all four.** Two gaps are universal:
- **(a)** ZERO tests anywhere in `tests/` assert the literal `completion report skipped: reason=idempotency_skip`.
- **(d)** NO test asserts that a `report_injections` row is PERSISTED and reaches `completed`.

⇒ the commission's ADD instruction applies. See §4.

## 2. Direction 2 (DUPLICATE DELIVERY STAYS STOPPED — the `58fc594eb` regression)

A second report under a DIFFERENT anchor must STILL be suppressed by ARM B. Pins are **behavioural**
(asserting a suppressed second delivery / row count), not predicate-level:

| Pin | Lines | Scenario | Behavioural |
|---|---|---|---|
| `tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child` | L2733-2743 | prior READY/PROCESSING under anchor-A, incoming anchor-B ⇒ no second row + `idempotency_skip` | YES |
| `tests/unit/services/test_stale_running_sweep_rider_pins.py::TestNit1MintSitePrefixGuard::test_prior_pending_report_under_a_different_anchor_blocks_a_second` | ~L759-764 | prior READY under a different anchor, drive path ⇒ exactly ONE report row | YES |
| `tests/unit/services/test_child_reports.py::TestReviveWedgeDuplicateFreedom::test_a_second_invocation_without_a_revive_mints_no_second_row` | L2846-2850 | prior COMPLETED under anchor-A, NO revive, incoming anchor-B ⇒ no second row + `idempotency_skip` | YES |

Predicate-level only (NOT behavioural, NOT a substitute): `tests/unit/test_ready_message_completion_report.py::TestMintGuardTwinLockstep::test_both_twins_evaluate_the_one_shared_two_arm_predicate` (L479).

## 3. ARM A (same-anchor COMPLETED duplicate still suppressed)

| Pin | Lines | Behavioural |
|---|---|---|
| `tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child` (row `i`) | L2733-2743 | YES |
| `tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_an_arm_a_only_suppression_emits_exactly_one_arm_a_record` | ~L2983-3023 | YES (outcome `idempotency_skip` + exactly one arm-a record) |
| `tests/unit/services/test_stale_running_sweep_rider_pins.py::TestReviveWedgeNarrow::test_same_anchor_re_supplied_with_prior_delivered_report_does_not_mint` | ~L784+ | YES (`len(sources)==1`) |

## 4. The ADDED Direction-1 pin

Node id: `tests/unit/services/test_child_reports.py::TestReviveWedgeDirection1Canonical::test_revive_then_new_anchor_terminalizes_and_persists_report_injection`

Placement: **UNIT** — `tests/unit/services/test_child_reports.py`, SQLite in-memory, no PG seam required.
Author worker: `e26d9a48`.

Scenario: 1st lifecycle (`anchor-1`) ⇒ report row lands COMPLETED ⇒ explicit revive (child back to RUNNING)
⇒ 2nd lifecycle under a NEW anchor (`anchor-2`).

Assertions mapped to the canonical signature:

| Part | Assertion |
|---|---|
| (a) | the log line `completion report skipped: reason=idempotency_skip` is NOT emitted (captured via `caplog` on `daemon.services.child_reports`) |
| (b) | `second.outcome == "regular_child_completed"` AND the child instance row reaches `InstanceStatus.COMPLETED.value` (not stuck `running`) |
| (c) | the report sources are exactly `[internal_report:{child}:anchor-1, internal_report:{child}:anchor-2]` — the 1st lifecycle's row remains AND a new one is minted |
| (d) | a `ReportInjection` row is PERSISTED for that child (mint path) — the suppress path mints NO report_message/report_injections, so the row's presence is the discriminator |

Runs at the tip: the new node alone **1 passed**; the whole file **66 passed** (no pre-existing failures,
no collateral damage). Raw: `pin-direction1-new-node.out`, `pin-direction1-full-file.out`.

`report_injections` mint-path finding (author): in the regular path the report_message + Task +
ReportInjection (`state=PENDING`, `deferred_reason=SYNC_MINT`) are flushed inside a SAVEPOINT-wrapped
transaction and committed; in the suppress path (`suppress_mint=True`) the code CASes, commits and returns
`idempotency_skip` WITHOUT minting either. ⇒ the row's PRESENCE (not its terminal state) is what
discriminates tip from pre-fix base. The commission's wording "row reaches `completed`" is under
active refinement (see the (d) follow-up leg) — the unit fixture's synchronous mint lands the row at
`PENDING`/`SYNC_MINT`.

## 5. Implementation line map AT THE TIP (re-derived; the commission's numbers shifted)

1. Two-arm predicate, `_mint_guard_suppress_predicate` **L120-137**:
   - ARM A (same-anchor): `source == f"internal_report:{instance_id}:{completed_message_id}"` AND status ∈ {READY, PROCESSING, COMPLETED}
   - ARM B (cross-anchor prefix): `source.startswith(f"internal_report:{instance_id}:", autoescape=True)` AND status ∈ {READY, PROCESSING} ← **COMPLETED deliberately narrowed OUT**
2. Live consumer **L5256-5271**: on a guard hit → `should_send = False`, `skip_reason = "idempotency_skip"`, `suppress_mint = True`.
3. Gate **L5296**: `if not should_send and not suppress_mint:` → logs `... completion report skipped: reason={skip_reason}` and returns early. The mint-guard case (`suppress_mint=True`) **falls through** to the CAS.
4. CAS **L5520-5536**: `UPDATE instance SET status=COMPLETED, updated_at, last_activity_at, version=version+1`.
5. Post-CAS **L5578-5590**: `_emit_mint_suppression_record(...)` → returns `outcome="idempotency_skip"`.
6. Second-report mint **L7241-7286**: `if outcome == "idempotency_skip":` → COMPLETED gate (L7259-7261) → `_emit_terminal_for_child_instance_via_bus(status="completed")`.

## 6. The PRE-FIX base, statically proven (`5cc5912f7`)

Base `daemon/services/child_reports.py` sha256: `ba5d62cdec2c39659bd582304498e14aea6b043f2aaf91731d40bce6f774403d`
(identical to the on-disk file in the base worktree ⇒ checkout == commit content).

| Fact | Base `5cc5912f7` | Tip `3a04a78b` |
|---|---|---|
| `_mint_guard_suppress_predicate` | **ABSENT** (`grep -c` = 0) | present, L120-137 |
| `suppress_mint` occurrences | **0** | 14 |
| Mint-site guard | inline WIDE single-arm prefix guard, in **two copies** (live L4675-4696, dead-code twin L1400-1419) | shared two-arm predicate |
| Cross-anchor status set | `[READY, PROCESSING, **COMPLETED**]` ← the wide prefix guard | `[READY, PROCESSING]` ← COMPLETED narrowed OUT |
| Early-return gate | `if not should_send:` — unconditional early return | `if not should_send and not suppress_mint:` — the mint-guard case falls through |
| CAS on the guard-hit path | **NOT reached** — the early return (L4700) precedes the CAS (L4902) in the same function (`_process_child_completion_db_sync` spans base L3636-5794) ⇒ the instance row stays `running` | reached ⇒ row becomes `completed` |

⇒ **canonical base signature confirmed statically**: log `completion report skipped: reason=idempotency_skip`
AND the instance row remains `running`. The base leg is NOT vacuous.

## 7. Base-leg import-resolution proof (the vacuity trap)

The tip venv is an **editable path-append install**: `.venv/lib/python3.14/site-packages/_editable_impl_ensemble.pth`
contains the single line `/home/nea/ensemble-src-wt-revive-wedge` (38 bytes, no trailing newline). There is
no `__editable___*_finder.py`.

| Invocation from the base tree | `daemon.__file__` resolves to |
|---|---|
| `cd base && tipvenv/bin/python -c "import daemon"` | **BASE** (`sys.path[0] = ''`) |
| `cd base && PYTHONPATH=base tipvenv/bin/python …` | **BASE** |
| `cd base && PYTHONSAFEPATH=1 tipvenv/bin/python …` | **TIP — VACUITY TRAP** |
| `cd base && tipvenv/bin/python -P …` | **TIP — VACUITY TRAP** |
| `cd /tmp && PYTHONPATH=base tipvenv/bin/python …` | **BASE** (CWD-independent) |
| `cd base && PYTHONSAFEPATH=1 PYTHONPATH=base tipvenv/bin/python …` | **BASE** (survives `-P`) |
| negative control: `cd tip && tipvenv/bin/python …` | TIP — proves resolution is CWD-driven, not accidental |

**`-P` / `PYTHONSAFEPATH=1` removes CWD from `sys.path` and hands resolution back to the `.pth`-injected
TIP tree** — any base-leg invocation using it would run the FIXED code and report a FALSE GREEN.

Deliverable command form for the base leg:

```bash
cd /home/nea/ensemble-src-wt-revive-gate-base && \
PYTHONPATH=/home/nea/ensemble-src-wt-revive-gate-base \
/home/nea/ensemble-src-wt-revive-wedge/.venv/bin/python -m pytest <args>
```

Stronger variant (pytest's own `prepend` import mode inserts the base repo root at `sys.path[0]`, because
`tests/__init__.py`, `tests/unit/__init__.py` and `tests/unit/services/__init__.py` all exist):
`[PROBE] sys.path[0:5] = ['/home/nea/ensemble-src-wt-revive-gate-base', ...]`, `daemon.__file__` = base,
`child_reports.__file__` = base. The probe plugin lives at `/tmp/probe_resolve.py` (outside both trees).

Base tree facts: `tests/unit/services/test_child_reports.py` EXISTS (109,666 B) and collects **50** tests
with NO overlay and NO collection error. `scripts/run_tests_scrubbed.sh` EXISTS in the base tree but is
**not re-pointable**: it does `cd "$(dirname "$0")/.."; exec .venv/bin/pytest "$@"` — a relative,
hard-coded interpreter that does not exist in the base tree (no `.venv` there). The scrubber also refuses
(exit 78) when ambient `ENSEMBLE_SELF_ENV=live` — the run leg must pass `ENSEMBLE_SELF_ENV=dev`.

Base PG leg assessed NOT cheap: `tests/postgres/test_child_reports_narrow_guard_pg.py` **does not exist at
base** (it is a tip artifact), the base tree has no `.venv`, and `pg_engine` drops+recreates the whole
schema per session (a concurrent base PG session would destroy the tip legs' schema). The base conftest is
byte-identical to the tip's (`sha256 54220d4687824574f81d40afe981dc2460c7f5adc4d56cbed8c2e122def73e7a`),
same six `PG_TEST_*` vars, same `ALLOWED_SCRATCH_DBS` (including `ensemble_test`). ⇒ the base leg is the
**unit** leg; the PG legs stay on the tip side.
