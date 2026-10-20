# ari-tools Phase 2 (self_alarm) — independent-verification gotchas

Date: 2026-10-20
Lane: independent verification of `99a28e4f3..bb2d109a7` (+ tip `bfff96933`) on `feature/ari-tools`
Worktree: `/home/nea/ensemble-src-wt-ari-tools`
Related: `RESULTS/2026-10-20-ari-tools-phase2-self-alarm-verification.md`

Gotchas banked while verifying the `self_alarm` wrapper. Each one cost a lane time or could have
produced a WRONG VERDICT if missed.

---

## 1. The repo's `addopts` silently deselects a whole spec-required PIN FILE

`pyproject.toml:94` → `addopts = "-m 'not integration and not postgres and not slow'"`.

`tests/integration/test_service_tool_flag_off_byte_identical.py` carries `@pytest.mark.integration` on
**all 9** of its test functions and **no** module-level `pytestmark`. Consequence:

| invocation | result |
|---|---|
| default addopts, no `-m` | `no tests collected (9 deselected)`, **exit 5** |
| `--override-ini="addopts="` | `9 passed` |
| `--override-ini="addopts=" -m integration` | `9 passed` |

`tests/integration/test_maintenancer_spawn_resolves_tools.py` is the mirror image: **no** selection
marker (its only `@pytest.mark.*` is `@pytest.mark.parametrize`) ⇒ 13 collected and run by default,
and `13 deselected` / exit 5 if you force `-m integration`.

⇒ **A default `pytest` run proves only FOUR of the five spec-required exact-set pins.** Any claim of
"all five pins green" must name an explicit `-m integration` or addopts-override invocation.
The marker regime of each named file must be established **before** running it (grep the marker +
`--collect-only` under each `-m` expression), never inferred from the file name or from the repo default.

**Trap:** pytest's summary line **omits the `deselected` token entirely when the count is zero**. An
absent token is NOT "nothing was deselected". Compare two runs only on the same evidence shape.

---

## 2. A fake registry that does not reproduce the REAL status value

`tests/unit/tools/test_self_alarm_tools.py`'s `FakeRegistry.start_adapter` writes `status='running'`.
The **real** `daemon/sources/registry.py:611` `SourceRegistry.start_adapter` writes `status='starting'`.

The wrapper tolerates both (it asserts `status != 'stopped'` and the plan counts `'starting'` as
armed), so there is **no behavioural divergence** — but a fake must reproduce the REAL value, and
where the production code tolerates both, that tolerance must be reported rather than assumed.

---

## 3. The fresh-object contract — copy this pattern, don't invent one

The engine's `update_schedule` **mutates before it answers** and `update_source_config` returns a
**FRESHLY LOADED object** (its own session) — so a later `update_source_status` write, which runs in
ANOTHER session, does not alias the returned object.

The phase-2 test double reproduces this correctly AND documents why:

```python
# The REAL repository returns a FRESHLY LOADED object (its own
# session), so the later ``update_source_status`` write — which
# runs in ANOTHER session — does NOT alias the returned object.
snapshot = FakeRow(source_id=row.source_id, name=row.name, ...)
return snapshot
```

⇒ An **in-place-mutating** double (mutating the stored row and returning it) breaks engine semantics
and will make a correct implementation look broken. When reviewing any new double against the
scheduling service, check this first.

---

## 4. The real scheduler adapter path

`daemon/sources/adapters/scheduler.py` (exported via `daemon/sources/adapters/__init__.py`) —
**not** `daemon/sources/scheduler.py`. The fired-transition blocks are
`adapters/scheduler.py:599-609` (one-time execution ⇒ `_on_complete_callback(source_id, completed=True)`)
and `daemon/sources/registry.py:514-526` (the registry installs the closure ⇒
`update_source_status(source_id, "stopped")` + `update_source_config(source_id, enabled=False)`).

---

## 5. A literal tool NAME in `tools.allow` really does bypass the privileged CATEGORY gate

`daemon/tools/instance.py:478` — `allowed_tools.add(item)` — adds a literal tool name to the allow
set. The privileged-category default-deny strip works on the CATEGORY, so a literal `self_alarm`
entry in a non-ari agent's `tools.allow` resolves the tool. Only the **runtime `agent_id == "ari"`
gate inside the tool body** stops it. This is exactly why D10 mandates all three layers, and why
"the privileged category alone" is not a sufficient boundary.

---

## 6. Commission briefs carry unreliable commit counts

The brief for this verification said `99a28e4f3..bb2d109a7` was "8 commits". `git rev-list --count`
says **6**. The shared KV's "7 commits total (base-inclusive)" counts the base. ⇒ Always re-derive
the commit count from git and report it; a wrong count propagates into release manifests.

---

## 7. Still standing from Phase 1: the `dev.sh` EXIT-trap live-collateral hazard

`dev.sh:28-44`'s EXIT trap falls back to `pkill -f "code-server.*--bind-addr"` when its precise
pid-file branch misses — and a dev boot that never spawns a code-server ALWAYS misses, so the
pattern matches the LIVE install's code-server. Mitigation: sentinel `data_dev/vscode-server.pid`
holding an owned pid makes the precise branch win.
See `LESSONS/2026-10-20-devsh-exit-trap-live-collateral.md`; skill `devsh-exit-trap-live-collateral-guard`.

---

## 8. Ambient `POSTGRES_DB=ensemble_prod` is still the default in this environment

Every DB-touching command must override to `ensemble_dev` (`POSTGRES_HOST=127.0.0.1`,
`POSTGRES_PORT=5432`). The `concurrency_atomic_unit_test` pack additionally **hard-aborts** on a
non-allow-listed `POSTGRES_DB` ⇒ run it with `env -u POSTGRES_DB`.
A verification probe should hard-refuse inside the script itself
(`sys.exit(2) unless POSTGRES_DB == "ensemble_dev"`) so a forgotten shell override cannot reach prod.
---

## 9. CORRECTION to a Phase-1 banked note — the concurrency pack's DB fence is DEFENSIVE, not load-bearing

Phase 1 banked: *"concurrency pack needs `env -u POSTGRES_DB` (ambient `ensemble_prod` from the outer
shell)"*, and earlier commissions recorded the pack **hard-aborting** on a non-allow-listed
`POSTGRES_DB`.

The Phase-2 ensure.md lane re-checked this against the pack's 13 files:

- **None** of the 13 files contains the `HARD ABORT` POSTGRES_DB fence.
- **None** opens a PostgreSQL connection — all are pure in-memory SQLite (`StaticPool`).

⇒ For `test/packs/concurrency_atomic_unit_test.sh` the env-unset is a **hardening measure, not a
load-bearing requirement**. The repo-wide HARD-ABORT fence lives in *other* test files
(`tests/test_prompt_cache_invalidation.py:124`, `tests/test_resource_pid_registry.py:63`,
`tests/test_resource_monitor_storage.py:73`, `tests/test_loader_empty_content_m16.py:48`,
`tests/test_custom_agents_*.py`).

Keep unsetting it — it costs nothing and it is still the right posture — but do not record it as the
reason a run aborts. Also: the pack's **74 skips are STRUCTURAL**
(`pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed")` at module level in 8 of
the 13 files), **pre-existing and baseline-exact** — NOT quarantine rows and NOT fence artefacts.
`QUARANTINE.md` has no row touching any file in this pack.

**Coverage gap disclosed by the ensure.md lane (pre-existing, branch-unrelated):** the
*cascade-completion* nodes that would cover the full parent→child→complete race —
`tests/test_cascade_unified.py::test_root_after_all_children_resolved_is_complete`,
`tests/test_cascade_concurrency.py::test_three_concurrent_resolves_same_parent` and
`::test_five_concurrent_resolves_same_parent` — are **module-level skipped** by the same structural
skip. Those files are byte-untouched by the phase-2 range. The change set touches no cascade or
correlation code, so this is a pack coverage gap, not a defect of the change set — but it should be
known before anyone reads "IMPORTANT-2 deadlock scenario PASS" as full race coverage.

---

## 10. `dev.sh` line references drift — cite the FLAG, not the line

Phase 1 recorded `dev.sh:142` for `--timeout-graceful-shutdown 10`. At the Phase-2 HEAD the flag is
at **`dev.sh:155`** (the explanatory comment at `:152`) — the file grew, the flag did not move
semantically. Any RESULTS note that cites a `dev.sh` LINE NUMBER is stale the next time the file
changes; cite the flag string and re-grep it.

---

## 11. A "zero side effects" proof needs a NON-VACUOUS counter

The Phase-2 dual-gate lane caught its own harness flaw: it first wired the counting spy to
`manager.scheduling_service` while the tool body held the **raw** scheduling module. The counter
therefore read **0 engine calls while the engine actually wrote a real row** — a "zero side effects"
verdict built on it would have been **false**, and the lane's most load-bearing claim (the literal
tool-name allow bypass produces no row) would have been an artefact.

Three rules any side-effect counter must satisfy:

1. **Spy-in-path self-check** — assert the spy is actually reachable from the code under test
   (e.g. `tool.coroutine.__closure__` contains the spy exactly once). Do not trust that you patched
   the right attribute.
2. **Positive control** — the same harness with a *permitted* caller must read **non-zero** (and, on a
   real-service lane, must land a real row). If the control reads zero, the counter is vacuous and the
   whole zero-side-effect claim is **void**.
3. **Counts-and-delegates only** — the spy forwards to the real service and returns its return value
   verbatim; it must never re-implement engine behaviour (the engine's `update_source_config` returns a
   **fresh** object — an in-place-mutating double breaks the reuse path).
