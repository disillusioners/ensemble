# Tidier Review — db-pool idle-in-transaction lane (quality/maintainability pass)

**Verdict: NEEDS WORK** (non-blocking for the design; 6 High are small, surgical fixes)
**37 findings: 6 High / 17 Medium / 14 Low** + 10 deferred to Reviewer
**Iteration: 001** (of the shared 3-iteration cap)

- Worktree `/home/nea/ensemble-src-wt-dbpool-idletxn`, branch `fix/db-pool-idle-txn`, range `19fc11c8b..1641f7189` (42 files, +8019/−238).
- Dispatch: 3 parallel skill-per-worker reviewers — `tidier-readable-code` (11 daemon files), `tidier-static-hygiene` (test surface), `tidier-robustness` (scripts + error-handling hunks). All reported fully; no gaps, no re-dispatches.
- Craftsmanship lens only. Correctness is adjudicated upstream (code-review council 0 critical); architecture/correctness/security observations are in **Deferred to Reviewer**, not counted as findings.

## Scope

- **Daemon (11 files)**: manager.py, tools/instance.py, tools/ens_db_tools.py, services/readiness.py, repositories/factory.py, constants.py, persistence.py, repositories/mission_record/repository.py, services/mission_record_service.py, services/wedge_capture/db_capture.py, routers/queues.py.
- **Tests (30 files)**: 17 new unit packs (`tests/unit/tools/test_dbpool_*.py`), 3 new PG packs, 4 new integration packs (incl. `tests/integration/test_db_pool_idle_in_transaction_guard.py`), 2 new shell packs (`tests/packs/dbpool_{gate_ladder,collected_fence}.sh`), 4 modified existing files (incl. the `constants.py` seal re-pin).
- **Scripts**: `scripts/audit/borrow_census.py` (new).
- Adjudicated constraints honored by all workers: D-47/D-59/Δ3 module-constant convention, F8 P0/P1/P2 shape + strict-False carrier consumer, disabled backstop + boot log line, F9.1, frozen 46-pin/26-pack inventory, DO-NOT-TOUCH surfaces (not flagged).

## Coverage

| Worker | Surface | Result |
|---|---|---|
| readable-code | 11 daemon files, full diff + worktree verification | 2H/5M/4L |
| static-hygiene | 30 test files (full read + AST census; 1 permitted pack run: 7 passed, 1 xfailed) | 2H/4M/7L |
| robustness | 3 scripts (2 `bash -n` clean; census executed read-only ×2) + 10 daemon error-path hunks | 2H/9M/3L |

Aggregation merges (stated): hygiene-M3 folded into **T-H3** (same file, same root cause); robustness's out-of-scope indent route folded into **T-M11**; two substantive halves promoted from workers' deferred lists into findings with the correctness half left deferred (vacuous assert → T-H3; `""`-on-miss → T-M15). No severity re-ranks beyond merges. Dispatch-inventory correction (hygiene): the guard pack lives at `tests/integration/`, not `tests/unit/`; unit packs number 17, not 19 — all reviewed regardless.

## Caller's seven checks — headline answers

1. **Dead code / unused / debug / commented-out**: daemon changed hunks are CLEAN (grep-verified: no print/TODO/FIXME/pdb residue, no commented-out code). Exceptions: one provably-unreachable branch (T-M10) and 8 unused imports in test packs (T-M17).
2. **Naming**: consistently good across all new identifiers (`child_row_missing`, `fetch_agent_id`, `carrier_check_needed`, `MANAGER_POOL_CONFIGURED_CAPACITY`, Δ3 constants, `adapter_pool_dsn`) — no findings.
3. **Duplication**: YES, real — P0/P1/P2 twins already have comment drift (T-H6); two-probe copy-paste with a wrong citation ×2 (T-M13); `_function_source` ×3 with divergent contracts (T-M15); ~200–250 removable harness lines (T-M16).
4. **Comment/docstring accuracy**: 2 High (T-H5 false "re-tried next cycle", T-H3 docstring-vs-body) + 4 Medium (T-M4, T-M9, T-M12, T-M13). The F9.1 mistake class recurs.
5. **Test-pack hygiene**: declared-count discipline near-perfect (23/24 exact; the 24th is T-H3); marker/header/sentinel conventions uniform; 2 defect-class pack bugs (T-H3, T-H4).
6. **Script hygiene**: the two instruments have false-green holes (T-H1, T-M1, T-M2) and the census reports families it cannot measure (T-H2).
7. **Structure/placement**: no placement violations found in daemon hunks; one dedent leftover (T-M11).

---

## Findings

### 🔴 High

**T-H1 — [High] Error Handling (exit-code fidelity): gate ladder prints `LADDER: OK`, exits 0, having executed ZERO gates** — `tests/packs/dbpool_gate_ladder.sh:102-104` (filter early-return), `:170`, `:243-246`, `:248-252`
- Problem: with an unmatched `ROW_FILTER` (typo'd/renamed/not-yet-added gate id) every `run_row` returns at `:103`; the two non-`run_row` blocks are filtered too; `FAILED` is never set → `LADDER: OK (mode=…)` + exit 0. In `--run` mode that string is the evidence artifact for "gate X passed".
- Impact: false-green on the gate runner itself — the instrument every downstream gate trusts.
- Fix: count matched rows (`MATCHED=$((MATCHED+1))` after the filter test) and fail closed when 0: `LADDER: FATAL — no gate row matched '${ROW_FILTER}'` → exit 1. Optionally validate `--run <id>` against the canonical id set up front (exit 2 on unknown).
- Evidence: static trace `:52-59` → `:102-104` → `:170` → `:243` → `:248-252`; no zero-match counter anywhere in the script.

**T-H2 — [High] Error Handling (false-clean instrument): borrow census reports two families it structurally CANNOT measure, and labels its TOTAL as covering them** — `scripts/audit/borrow_census.py:16-23` (family table), `:26-35`+`:75-93` (detectors), `:106-108` (TOTAL)
- Problem: `FAMILIES` advertises six; `scan()` has NO branch incrementing `B` (every `SQLModelSession` site silently re-labelled A) and NO F detector at all (no `psycopg.connect`/`raw_connection`/`getconn`/`putconn` matcher). Executed: fresh run reports `B: 0, F: 0` — while the tree contains 180 `SQLModelSession(` sites and one live raw-psycopg site (`daemon/services/wedge_capture/db_capture.py:195`).
- Impact: false-clean on the class most likely to leak. **Cross-worker corroboration**: the lane's own audit artifact records `B=175` — the script cannot reproduce its own recorded output.
- Fix: implement both detectors (split the matcher so `SQLModelSession` counts B; add an F matcher) and relabel the TOTAL to the families actually summed — or remove B/F from `FAMILIES` and the TOTAL label if intentionally out of scope.

**T-H3 — [High] File Hygiene (declared-count contract): `EXPECTED_COLLECTED = 2` but the pack collects 1; the docstring advertises the missing second leg** — `tests/integration/test_dbpool_probe_bound_pg.py:60` (declaration) vs `:124` (only test), `:14-21` (docstring), `:190-197` (body)
- Problem: one test function, no parametrize, no generator applies (`tests/integration/conftest.py` has none) — collects 1, declares 2. Every other new pack is exact (census: expected=129 vs actual=128; this pack is the whole delta). The docstring bullet "the backend row is EXPECTED TO PERSIST in `pg_stat_activity` with `state='idle'`" is never asserted — the only statement is `assert rows is not None`, which is vacuous (`.all()` never returns `None`).
- Impact: the pack-local constant IS the C3 false-green defense; an unsatisfiable declaration will FATAL the fence row and contradict the tester's R-1 baseline (`expected-collected.md` row 23 still TBD).
- Fix: reconcile explicitly in the pack docstring — (a) `EXPECTED_COLLECTED = 1` and reword the bullet to "the census runs and returns a result set — persistence is a disclosure, not an assertion"; or (b) implement the persistence leg (fetch `state`, assert present + `idle`), which also makes `=2` true. Re-state at R-1.

**T-H4 — [High] Code Smells (copy-paste regex typo): `[^n]*` instead of `[^\n]*` in the sentinel END-marker reader** — `tests/unit/tools/test_dbpool_consistency_pin.py:148` (vs `:146` BEGIN)
- Problem: `r"<!--\s*EXEMPT:END[^n]*-->"` excludes the letter `n`, not newline. Asymmetric with its sibling; green today only because the current marker is `<!-- EXEMPT:END -->` (a space is not `n`).
- Impact: any marker text containing `n` breaks with a misleading error; worse, `[^n]*` matches newlines → a wrong-span match is fed into `_remove_exempt_region` (`:172-175`) → silent wrong-region strip in the artifact every W-11 cross-artifact check then scans.
- Fix: `r"<!--\s*EXEMPT:END[^\n]*-->"` + a symmetry comment; optional hardening: assert begin/end each occur exactly once (contract §6.3 already mandates it; the pin never checks).

**T-H5 — [High] Readability (comment misstates behavior — F9.1 class): timeout comment claims "the row is re-tried next cycle"; nothing re-tries it** — `daemon/manager.py:11768` (new async-twin comment) and `:11318-11323` (sync helper docstring, base sentence this lane copied and extended without correcting)
- Problem: on `TimeoutError`, `content = "[No response content]"` (`:11779-11783`), then P2 unconditionally delivers (`:11823-11831`) — terminal INJECTED with degraded content. The deliver-anyway design is explicit at `daemon/services/child_reports.py:2803`. No re-fetch path exists.
- Impact: an operator reasoning about the timeout's blast radius concludes "worst case = delay"; actual worst case = permanently empty report. Wrong comments outlive correct code.
- Fix (two-line edit, both sites): "a budget-exceeding fetch degrades to the fallback content and the row is delivered with it (base-parity degraded delivery — nothing re-fetches the content later)."

**T-H6 — [High] Code Smells (twin duplication already drifting): P0/P1/P2 blocks duplicated ~65 lines verbatim between sync/async reconcile, and the load-bearing rationale now lives in only ONE twin** — `daemon/manager.py` sync `:10898-10953`, `:11188-11219` vs async `:11697-11758`, `:11787-11830`
- Problem: the CODE is currently identical (verified by block diff); the COMMENTS are not, and the drift materialized inside this single lane: the async carrier-check header alone carries "keyed on the P0-observed `report_message_id` (write-once per row lifecycle)" and "bounded by `pool_timeout=30s`" (both claims verified TRUE — sole assignment `:11490`) — a future editor of the sync twin sees neither. Sub-shape decision, F8.7 re-read, and F8.6 re-assert comments likewise diverge in wording.
- Impact: the next fix lands in one twin; the other silently diverges — the exact copy-paste-drift bite the caller asked about, demonstrated by this lane's own commit sequence.
- Fix: **(b) minimal now** — make the three comment blocks byte-identical with one canonical wording (recommended pre-tester: no structural change); **(a) optional follow-up** — extract `_reconcile_p0_snapshot()` + shared seam helper returning a small dataclass (`needs_content_fetch` / `report_message_id_pre` / `carrier_check_needed` / `fetch_agent_id`). The approved P0/P1/P2 SHAPE is untouched by either; the frozen pins assert behavior, not this internal structure.

### 🟡 Medium

**T-M1 — fence discards pytest's exit status AND stderr, so "collected N" cannot be distinguished from "collected N with errors"** — `tests/packs/dbpool_collected_fence.sh:26-30`
- `2>/dev/null` + `|| true` + count-only grep throw away all three signals; pytest's summary appends `"%d error"` segments the fence never sees. Necessary-but-not-sufficient count → the anti-false-green instrument has its own false-green window (count-preserving collection errors). Fix: `set -o pipefail`, capture `rc`, FATAL on `rc != 0` with retained output, and grep-fail on `N error` in the summary.

**T-M2 — `certify()` PRINTS the resolved `daemon.__file__` but never asserts it** — `tests/packs/dbpool_gate_ladder.sh:83-87` (`:166-167` call site)
- The `cd` is load-bearing (header `:11-13`: any other cwd resolves `daemon` to the SHARED checkout); the printed path is never checked, so certification exits 0 on exactly the condition it exists to catch (worktree shadowed/missing → import succeeds from the shared checkout). Fix: capture the path and `case "$resolved" in "$WORKTREE"/*)` else FATAL.

**T-M3 — temp `HOME` dirs never removed (no trap on either script); fence lacks an argument-count guard** — `tests/packs/dbpool_collected_fence.sh:20`,`:27`; `tests/packs/dbpool_gate_ladder.sh:79`,`:85`,`:142`
- Every run leaks `/tmp/tmp.*` (a 17-row `--run` leaks 18+); the fence under 2 args aborts on bash's raw `unbound variable` instead of a usage line. Fix: `trap 'rm -rf "$HOME"' EXIT` + `if [ "$#" -lt 2 ]; then usage; exit 1; fi`.

**T-M4 — census docstring misstates the error stream, and the "Exit code 0 always" contract is false** — `scripts/audit/borrow_census.py:8-9` vs `:110-111`, `:70-74`
- PARSE-ERROR lines go to stdout, docstring says stderr; only `SyntaxError` is caught, so one non-UTF-8 file (`UnicodeDecodeError` from `open().read()`) or unreadable file aborts the whole census with a traceback and non-zero exit. Fix: `file=sys.stderr` + catch `(SyntaxError, UnicodeDecodeError, OSError)` per file, record as PARSE-ERROR, and state the exit contract precisely.

**T-M5 — census runs cleanly over a NON-EXISTENT root and reports a complete zero census (false-clean via typo)** — `scripts/audit/borrow_census.py:103-105`
- Executed: bogus path → all counters 0, exit 0. No argparse (no `--help`, extra args ignored). Fix: argparse with required `root` + fail closed if `daemon/` absent (`exit 2`).

**T-M6 — malformed exception: `IntegrityError(message, None, None)` puts the message in the SQL slot and leaves `orig=None`** — `daemon/repositories/mission_record/repository.py:597-602`, `:611-617`
- SQLAlchemy renders `(builtins.NoneType) None` as the primary line (verified by execution); `exc.orig is None` defeats standard `isinstance(exc.orig, psycopg…)` classification; `exc.statement` holds prose. Fix: supply a real `orig` (`RuntimeError(<the message>)`) so the text lands in the primary slot — or raise a dedicated conflict type (pairs with T-M7).

**T-M7 — broadened catch converts a NON-conflict IntegrityError into "return the existing open epoch" (new fail-open surface)** — `daemon/services/mission_record_service.py:552-585` (catch `:552`, re-resolve `:579-584`)
- Base's re-resolve was keyed `(anchor, founding_message_id)`; now anchor-alone. The repository's own-session path absorbs the genuine conflict (`ON CONFLICT DO NOTHING` + `RETURNING` loser signal, `repository.py:567`), so every IntegrityError now reaching this handler is a DIFFERENT shape (NOT NULL/FK/check violation, or the repo's own fail-closed guards `:597`/`:611`) — silently converted into "mint succeeded". Fix: narrow to a dedicated conflict type raised at the guard sites; re-raise all other IntegrityError unchanged. (Behavioral reachability question → Deferred #2.)

**T-M8 — probe statement-bound classification asymmetry + swallowed server-side reason** — `daemon/services/readiness.py:1307-1319` (classification), `:1519-1530` (docstring), `:1543-1564`, `:1838-1881` (probes)
- `_guarded` maps client timeout → grace-eligible `probe_timeout` but ANY raised exception → `hard_component_red`; the new `SET LOCAL statement_timeout` makes a server-side 57014 possible, and the client budget also covers pool checkout (so the server bound can fire first); when the client fires first, the abandoned probe thread's later exception is silently discarded. Fix: (1) catch the driver cancel in the probe body and re-raise as `TimeoutError`; (2) attach a done-callback logging the abandoned thread's exception; (3) restate the margin honestly (`client_budget + checkout allowance`) or widen it. (Whether 57014 should be grace-eligible → Deferred #1.)

**T-M9 — fetch-helper "Returns:" contract contradicts the lane's own new W-1 note, four lines apart** — `daemon/manager.py:11305-11307` vs `:11327` (sync); `:11380-11382` vs `:11391` (async)
- The new W-1 note correctly says a missing child Instance row SKIPS the fetch (content stays `""`); the pre-existing contract line still promises `"[No response content]"` **on missing**. Actual return for missing child: `""`. Fix the contract line: "or `""` when the child Instance row is missing (fetch skipped); `"[No response content]"` on empty/failure" — and decide deliberately which the artifact should carry. (Fix together with T-H5 — same docstrings.)

**T-M10 — provably-unreachable defensive branch** — `daemon/tools/instance.py:1346-1347`
- `if _bus is None: return None` duplicates the check at `:1229-1234`; `_bus` bound once at `:1228`, never reassigned — the branch can never fire. Fix: delete, or keep with a comment referencing the first check as reorder-insurance.

**T-M11 — misaligned continuation left by the F8.5 mechanical dedent** — `daemon/tools/instance.py:1350`
- `target_instance_id=parent_instance_id,` at 16 spaces vs sibling kwargs at 12 (`:1351`,`:1355`,`:1356`). Valid Python; reads like a botched dedent. Fix: align to 12. (Independently surfaced by two workers.)

**T-M12 — the two canonical homes of backstop enablement give CONFLICTING one-edit instructions** — `daemon/constants.py:283-289` vs `daemon/repositories/factory.py:268-270`
- constants.py: "Enablement = append the clause to the template in factory.py"; factory.py: "flip the flag below" (`MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED`, `:249`). Following constants.py literally leaves the flag False while the bound is applied → boot log says DISABLED (`factory.py:369-373`) while the engine carries the bound — the honesty apparatus lies. Fix: reword constants.py to point at the flag flip.

**T-M13 — the two readiness probes copy-paste the F4-A/F4-B/D-07 blocks, and the duplicated cross-reference is wrong in BOTH copies** — `daemon/services/readiness.py:1546-1561` vs `:1838-1853`
- Only the budget constant differs between `make_db_probe`/`make_queue_probe`; the in-block comment cites "the `ens_db_tools.py:787-797` house idiom" in both — `:787-797` is the docstring region; the actual `SET LOCAL statement_timeout` idiom is at `:814-819`/`:903-907`. Wrong at birth, wrong twice. Fix: extract `_apply_probe_statement_bound(conn, budget_s)` used by both; replace the line-range citation with a symbol citation. (The derivation itself IS single-sourced at `_probe_statement_timeout_ms` `:1591-1603` — credit.)

**T-M14 — "DSN built SEPARATELY" narrative is false; `adapter_pool_dsn` is an alias, not a separation** — `daemon/persistence.py:378-392`
- `adapter_pool_dsn = _build_pg_connection_string(config)` (`:387`) is byte-identical to `conn_string` (`:234`, same builder/config/env); 100% of the attribution comes from `server_settings` (`:390-392`). The variable supports a narrative, not a separation — a reader may later assume conninfo isolation that doesn't exist. Fix: pass `conn_string` directly, delete the alias, reword: "same conninfo as the P2 oracle pool (T2.7/AC18); attribution rides asyncpg's own `server_settings`."

**T-M15 — `_function_source` exists ×3 under one name with THREE different contracts; the silent `""` variant disarms absence-assertions** — `tests/unit/tools/test_dbpool_mission_mint.py:87` · `test_dbpool_probe_bound.py:79` · `test_dbpool_probe_retention_static.py:55`
- `ast.walk` (all nested) vs `tree.body` (top-level) vs `tree.body` returning `""` on miss. The `""` variant: presence-asserts still RED on rename ✓, but absence-asserts (`"SET LOCAL" not in source`) go GREEN VACUOUSLY when the target is renamed/moved. Fix: one shared helper (see T-M16) with a loud raise-on-miss contract; `""`-on-miss only as an explicitly-named optional variant whose callers assert non-empty.

**T-M16 — harness duplication across the new packs: ≈200–250 removable lines (quantified)** — house convention: `tests/helpers/<feature>_fixtures.py`
- `_is_manager_session_scope` + `_SCOPE_MARKERS` ×2 byte-identical (`fix_site_static.py:69,79` / `occupancy_static.py:59,62`); `_function_source` ×3 (T-M15); `_ScratchDb` + scratch-db fixture + PG-skip boilerplate ×2 ~75 lines each (`churn_census.py:153,202` / `test_db_pool_idle_in_transaction_guard.py:240,289`); the 5-line `_PG_*` env block ×4 **byte-identical** (`churn_census.py:92-96`, `deferred_report_reconcile_pg.py:90-94`, `probe_bound_pg.py:62-66`, `guard.py:109-113`) — re-implementing the DSN contract `tests/postgres/conftest.py:69-73` already owns (drift ⇒ integration packs talk to a different cluster than `postgres/` packs); saturated-pool fake pair ×2 renamed (`_FakePool/_FakeEngine` vs `_SaturatedPool/_SaturatedEngine`); probe-call wrapper ×2 renamed (`_call` vs `_call_probe`). Fix: `tests/helpers/dbpool_fixtures.py` + move the `_PG_*` block to `tests/integration/conftest.py` as a session fixture.

**T-M17 — 8 unused imports across 7 new packs; one carries signal** — `test_dbpool_census_families.py:45` (`sys`), `test_dbpool_probe_failure_class.py:34` (`pytest`), `test_dbpool_repair_engine_isolation.py:48` (`factory_mod` — confirm not a future monkeypatch target; sibling `test_pg_engine_utc_session.py:39` uses the same alias actively), `test_dbpool_mission_mint_pg.py:46` (`Any`), `test_dbpool_probe_starvation_pg.py:44` (`pytest`), `test_dbpool_churn_census.py:83` (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` — dead residue of an assertion the churn pack no longer makes, while the guard pack's declaration table (`:23`) still claims the churn cycle is budget-judged), `test_dbpool_probe_bound_pg.py:48` (`contextmanager`), `test_db_pool_idle_in_transaction_guard.py:91` (`Any`).
- Fix: delete all eight; for `churn_census.py:83` either use the constant or amend the guard pack's table row to state what the churn pack actually does (see Deferred #5).

### 🟢 Low

**T-L1** — missing `expected-collected.md` is misreported as a missing/TBD ROW (`gate_ladder.sh:65-75`, `collected_fence.sh:21-22`) → stat the artifact first, emit a distinct FATAL.
**T-L2** — row id interpolated unescaped into an ERE (`gate_ladder.sh:68-69`, `G1.1`'s `.` matches any char) → `grep -F` or `${row//./\\.}`.
**T-L3** — admission pre-check defends missing pool but not a pool without `checkedout()` (`readiness.py:1488-1520`; only `QueuePool` has it) → `getattr(pool, "checkedout", None)`; missing ⇒ capacity unknown, don't refuse.
**T-L4** — annotation drift within the same pack family (36 new helpers; e.g. `probe_bound.py:79` fully typed vs untyped fixtures in siblings) → one lane convention: annotate return types + `pytest.MonkeyPatch` params.
**T-L5** — 3 `Any` sites + 3 unexplained `# type: ignore[arg-type]` (`probe_bound.py:172,184,194`, `probe_failure_class.py:100`, `txn_hygiene.py:89`, `deferred_report_reconcile_pg.py:253` dynamic stub) → typed holder class + one-line justification comments.
**T-L6** — `_SaturatedPool` is the only bare fake class (`probe_failure_class.py:49`) → one-line docstring.
**T-L7** — hard-coded `15` where the sibling binds the constant (`churn_census.py:303,311` vs `MANAGER_POOL_CONFIGURED_CAPACITY`, `readiness.py:152`) → import and use it.
**T-L8** — function-local imports added next to the module-level import block (`test_connect_timeout.py:70-71,240-241` vs `:40`) → hoist.
**T-L9** — the fence lacks the worktree-path rationale note the ladder's header carries (`collected_fence.sh:23,28` vs `gate_ladder.sh:46-47`, D-29) → add the one-line rationale; no path change.
**T-L10** — pre-existing pack amended to declare `EXPECTED_COLLECTED = 10` (`test_pg_engine_utc_session.py:47-48`; count verified correct) while `expected-collected.md` says the constant applies to NEW packs only → flag row 1 to the R-1 measurer so base/head sums don't drift by 10.
**T-L11** — non-contiguous sqlalchemy imports (`repository.py:25-27`) → `from sqlalchemy import text, update`.
**T-L12** — the psycopg3 `rowcount = -1` rationale duplicated near-verbatim in two layers (`repository.py` insert_mission docstring vs `mission_record_service.py:514-523`) → keep the derivation in the repo, point to it from the service.
**T-L13** — one new line over the file's wrap width (`ens_db_tools.py:1534`, 93 chars; sibling wrapped at `:1513`) → break after `TRANSACTION —`.
**T-L14** — single-use intermediate `child_agent_id` duplicated across both twins (`manager.py:10902-10904` sync / `:11701-11703` async) → inline or drop.

## Positive observations (verified, not assumed)

- **Daemon changed hunks are clean of dead code, debug artifacts, and commented-out code**; naming is consistently good; no placement violations.
- **F3 is a strict improvement**: explicit `BEGIN`s outside try gone; unconditional `finally`-rollback on every path, DDL and DML consistently; the preview and audit row survive the outer wrapper.
- **D-07 rollback placement + docstring are correct** (SQLAlchemy no-op semantics; load-bearing on PG).
- **The P0/P1/P2 split has no partial-write path**: both new early-returns precede the write scope; strict-False consumer is fail-safe; W-5's exit-status capture is provably correct (`pipefail` inheritance + `&&`/`|` precedence).
- **Timeout bindings bind the right exceptions** on this runtime (Python 3.13 aliasing verified).
- **Declared-count discipline near-perfect** (23/24 exact); marker/header conventions uniform; sentinels imported, not re-spelled; the strict xfail on the W-11 drift is exemplary.
- **Seal re-pin verified sound**: exactly one row changed; pinned sha equals actual `daemon/constants.py` bytes (recomputed); comment format matches the V-1 precedent; the prior-value comment is accurate through the intermediate commit.
- **Env-scrub convention correct everywhere**: per-variable `env -u` (the house critical note honored).

## Recommendations (prioritized)

1. **The evidence instruments first** (they gate everything downstream): T-H1 + T-M1 + T-M2 + T-M3 — small diffs, all in two shell scripts; a false-green gate runner/fence/census undermines every gate the tester is about to run.
2. **T-H2 + T-M4 + T-M5**: fix the census instrument (implement B/F detectors or de-scope them honestly, fix the stream/exit contract, fail on bogus roots).
3. **T-H3**: reconcile the declared count (decide count-vs-second-leg explicitly, docstring states the choice).
4. **T-H4**: one-character fix + symmetry comment.
5. **One manager.py comment-reconciliation pass**: T-H5 + T-M9 + T-H6(option b) — same file, same docstrings, ~15 lines total; canonicalize the twin comments byte-identical. Defer the helper extraction (T-H6 option a) as a post-tester follow-up commission — lower risk before the gate.
6. **mission_record error-shape pair**: T-M6 + T-M7 together (dedicated conflict type fixes both).
7. **Mechanical batch**: T-M13 (probe-block extraction), T-M14, T-M16 (fixtures file), T-M17 (imports), T-M10/T-M11/T-M12.
8. **Lows opportunistically** — T-L7 and T-L10 are the two with teeth (spurious-RED risk; R-1 reconciliation drift).

## Deferred to Reviewer (correctness/architecture — not adjudicated here)

1. **57014 → `hard_component_red` classification semantics** — whether a server-cancelled probe should be grace-eligible is a readiness-semantics decision (readiness.py:1307-1319).
2. **Reachability of T-M7's blast radius** — can a non-conflict IntegrityError actually reach the anchor-keyed re-resolve post-F6? If yes, silent loss of a requested mint.
3. **Carrier-check id consistency** — the check keys on P0-observed `report_message_id` while the (c) branch uses the P2 re-read value; strict-False makes it fail-safe (next-sweep deferral) but the consistency question is correctness.
4. **Upsert value NULL-filtering semantics** vs base ORM insert (`repository.py` — server-defaults-apply change).
5. **Churn-pack residence-budget assertion**: missing from the churn pack (dead import T-M17) vs guard-pack declaration table over-claiming (`guard.py:22-24` vs `churn_census.py:298` UNMEASURABLE).
6. **Timing-dependent 0.75s lock-holder sleep** (`probe_bound_pg.py:~140`) — flakiness surface.
7. **Dynamic `type("F8Holder", (), {})()` stub** semantics for the F8-c/F8-d seam (`deferred_report_reconcile_pg.py:253`).
8. **Async twin +2 existence-SELECTs on the event loop** per (c)-shape reconcile (pre-existing shape, now heavier).
9. **`seam_hook` executes synchronously on the loop** in the async twin (test-only today; a blocking hook would stall mid-reconcile if it grows).
10. **`report_message_id_pre: str | None` passed to `_has_live_process_report_carrier(message_id: str)`** — safe via `carrier_check_needed` today, invisible to a future type-checker gate.

(Route-A strict-False residual is already adjudicated in `review.dbpool.a1` — noted, not re-opened.)

---

*Dispatch record: 3 parallel skill-per-worker reviewers (readable-code / static-hygiene / robustness), all reported fully. Aggregation merges and promotions stated in Coverage. Process note: none of the three relays visibly evidenced the skill_feedback-first tool call (recurring house pattern, ~11th occurrence); accepted on concrete-artifact strength (executed evidence, AST censuses, library-source verification, coverage tables).*
