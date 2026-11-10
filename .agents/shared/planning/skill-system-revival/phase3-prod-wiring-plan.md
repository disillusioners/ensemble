# Phase 3 — production wiring plan (skill-system-revival)

**Lane:** skill-system-revival Phase 3 · SCOPE A+B+C (implementation only — staging is a LATER dispatch)
**Board:** `wi-4d1fa093` · **PM:** ENSEMBLE-27
**Worktree:** `/home/nea/ensemble-src-wt-skill-capture-wiring` · **Branch:** `feature/skill-capture-prod-wiring` @ `0efdf106f` (= `origin/latest`, v0.29.15, upstream UNSET)
**Author:** coder lane · **Date:** 2026-11-10 (UTC)

---

## §0 — HEADLINE: the commissioned root cause is FALSIFIED; the real hole is a COVERAGE hole

The commission (and Phase-2's anomaly `3248db8f`) states:

> *"the production capture enqueue path is structurally disconnected … `set_job_dispatcher` (`skill_metrics_service.py:327`,
> a method on `SkillMetricsService`) has **NO production caller** — while `daemon/api.py:3887` calls the DIFFERENT
> module-level `set_skill_job_dispatcher` (`daemon/routers/skills.py:183`) … The name collision is what every prior gate
> missed."*

**That statement is FALSE at HEAD `0efdf106f`.** `SkillMetricsService.set_job_dispatcher` **does** have a production caller —
`daemon/manager.py:8197`, inside `InstanceManager.set_job_queue_service`, which the production boot reaches
unconditionally at `daemon/api.py:1502`. The "no production caller" claim is a **grep-scoped miss**: the caller lives in
`daemon/manager.py`, which the Phase-2 search evidently did not cover (the search covered `daemon/api.py` +
`daemon/routers/` + `daemon/services/`).

**Proof by execution** (the REAL production method body, called on a lightweight fake `self`; no DB, no network):

```
$ cd /home/nea/ensemble-src-wt-skill-capture-wiring
$ env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
      .venv/bin/python /tmp/p3proof/proof_manager_wiring.py
BEFORE  metrics._skill_job_dispatcher = <absent>
AFTER   manager._skill_job_dispatcher  = SkillJobDispatcher
AFTER   metrics._skill_job_dispatcher  = SkillJobDispatcher
SAME OBJECT?                          = True
```

**Proof from the LIVE host** (system journal — `ensemble-main.service`, v0.29.15; both boots):

```
18:12:39 - daemon.manager - INFO - SkillJobDispatcher wired to manager (evolution_lane_enabled=False, ...)
18:12:44 - daemon.api   - INFO - Skills router wired: store=True search=True metrics=True evolution=True triggers=True dispatcher=True usage_repo=True
19:37:38 - daemon.manager - INFO - SkillJobDispatcher wired to manager (evolution_lane_enabled=False, ...)
19:37:45 - daemon.api   - INFO - Skills router wired: store=True search=True metrics=True evolution=True triggers=True dispatcher=True usage_repo=True
```

`daemon/manager.py:8176` (`"SkillJobDispatcher wired to manager"`) is logged **before** `daemon/manager.py:8197`
(`metrics.set_job_dispatcher(...)`) in the SAME method body — so the service binding ran in both live boots.

**⇒ What was actually broken in the Phase-2 window** (this is the finding that matters operationally):

| Time (UTC) | Event | Evidence |
|---|---|---|
| 18:11:02 → 18:12:14 | live daemon restarted (the v0.29.15 promote) | `journalctl -u ensemble-main` |
| 18:52 → **19:36:06** | capture-lane refusals recorded — `refusal_reason: "capture_lane_off"`, `prompt_source: "skill_metrics_service"` | `/home/nea/agents-ensemble/data/skill-capture-observability/skill-capture-20261110.jsonl` (7 rows) |
| 19:36:01 → 19:37:12 | **THE** restart — the only one whose process env carried the flags | `journalctl -u ensemble-main`; `/proc/2390247/environ` → `ENSEMBLE_SKILL_CAPTURE_ENABLED=1`, `ENSEMBLE_SKILL_EVOLUTION_ENABLED=1` |

The Phase-2 observation window (≤ 19:36) was measured against the **pre-flip process**, whose environment still had the
capture lane OFF — the lane's own sink records say so (`capture_lane_off`). The `.env` edit alone cannot reach a running
process; the flag-carrying restart landed at **19:37:11**, *after* the observation window closed.

**⇒ The hole that is REAL, and that Phase 3 must close: there is NO regression test over the production binding.**
Every existing pin injects the dispatcher **test-side** (`tests/services/test_skill_capture_observability.py:690`,
`tests/unit/test_skill_capture_killswitch_verification.py:348`, `tests/integration/test_skill_capture.py:176`, …).
**Scope of the claim, stated precisely (round-2 S2):** at HEAD `0efdf106f` the api seam did not exist yet, so the
green-suite proof below held for the production binding **as a whole**. Post-fix the two bindings are NOT symmetric:
the **api-seam binding is under test** (Phase 3's regression suite drives `daemon.api._wire_skills_router_services`
and pins the `lifespan` call), while the **manager-side binding** (`daemon/manager.py:8196-8197`) **remains
untested** — deleting it alone still leaves the suite green; it is the idempotent FALLBACK for boot paths that
never run the api `lifespan`. Deleting the api seam NOW goes RED.
That is the shape of hole the commission describes; the commission's *explanation* of it is wrong.

---

## §1 — Confirmed seam evidence (both setters, their targets, the production dispatcher instance)

### 1.1 The ROUTER setter — `daemon/routers/skills.py:183`

```python
163: # (module globals)
164: _skill_job_dispatcher: SkillJobDispatcher | None = None
...
183: def set_skill_job_dispatcher(dispatcher: SkillJobDispatcher) -> None:
...
193:     global _skill_job_dispatcher
194:     _skill_job_dispatcher = dispatcher
```

* **Target object:** the module-level global `_skill_job_dispatcher` of `daemon.routers.skills`.
* **Reader:** `_require_skill_dispatcher()` (`daemon/routers/skills.py:238-251`, 503 when unset) → the REST
  `POST /api/skills/{id}/fix` endpoint (`daemon/routers/skills.py:1364`).
* **Production caller:** `daemon/api.py:3886-3887` (see §1.3).

### 1.2 The SERVICE setter — `daemon/services/skill_metrics_service.py:327` — **the REAL setter for the capture lane**

```python
327:     def set_job_dispatcher(self, dispatcher: Any) -> None:
...
352:         self._skill_job_dispatcher = dispatcher
```

* **Target object:** the `SkillMetricsService` instance's own `_skill_job_dispatcher` attribute.
* **Reader:** `daemon/services/skill_metrics_service.py:812-816` — the ONLY production call site of `enqueue_capture`:

```python
812:         if task_details is not None and self._skill_job_dispatcher is not None:
813:             try:
814:                 await self._skill_job_dispatcher.enqueue_capture(
815:                     task_details.get("project_id"),
816:                     task_details,
```

* **Production caller:** `daemon/manager.py:8195-8197` (see §1.4) — **the caller the Phase-2 search missed**.

### 1.3 The api.py:3887 seam — what production constructs and passes there

```python
# daemon/api.py (inside `lifespan`, 4-space indent — unconditional)
3883:     # Job dispatcher — module-level global + setter. This is the
3884:     # chokepoint the ``POST /api/skills/{id}/fix`` endpoint calls into
3885:     # to enqueue a ``skill_evolution`` JobItem on ``system_parallel_queue``.
3886:     if getattr(manager, "_skill_job_dispatcher", None) is not None:
3887:         set_skill_job_dispatcher(manager._skill_job_dispatcher)
```

The instance passed is **`manager._skill_job_dispatcher`** — a `SkillJobDispatcher` constructed at
`daemon/manager.py:8148` inside `InstanceManager.set_job_queue_service` (`def` at `daemon/manager.py:8092`):

```python
8148:             self._skill_job_dispatcher = SkillJobDispatcher(
8149:                 job_service=service,
8150:                 queue_repo=queue_repo,
8151:                 evolution_lane_enabled=bool(getattr(_skill_evo_cfg, "evolution_lane_enabled", False)),
...
8176:             logger.info("SkillJobDispatcher wired to manager (...)", ...)
```

### 1.4 The production call site of the SERVICE setter — `daemon/manager.py:8195-8197`

```python
# daemon/manager.py — inside `InstanceManager.set_job_queue_service` (def at :8092)
8192:         # Wire the metrics service's job dispatcher handle so the
8193:         # CAPTURED flow (Phase 5) can actually enqueue capture jobs
8194:         # instead of just computing eligibility. The metrics service
8195:         metrics = getattr(self, "_skill_metrics_service", None)
8196:         if metrics is not None and self._skill_job_dispatcher is not None:
8197:             metrics.set_job_dispatcher(self._skill_job_dispatcher)
```

Reached in production by `daemon/api.py:1502` (unconditional, inside `lifespan`):

```python
1501:     # Wire services into InstanceManager
1502:     manager.set_job_queue_service(job_queue_service)
```

`_skill_metrics_service` is constructed in `InstanceManager.__init__` (`daemon/manager.py:2673`, guarded by
`if self.config.skill_evolution is not None:` — and `skill_evolution` is `Field(default_factory=SkillEvolutionConfig)`
at `daemon/config.py:6890`, so it is **never** `None` in production).

### 1.5 The capture path, end to end (for the record)

`daemon/services/job_queue_service.py:3617` / `:3665` → `manager._skill_metrics_service` →
`SkillMetricsService.record_task_completion` → `_check_capture_eligibility` (gate 0 = `ENSEMBLE_SKILL_CAPTURE_ENABLED`,
`daemon/services/skill_metrics_service.py:663`) → `evolution_service.check_and_capture` → **`:812-816` →
`SkillJobDispatcher.enqueue_capture`** (`daemon/services/skill_job_dispatcher.py:1084`, its own kill-switch gate at
`:1126`).

---

## §2 — Chosen injection point + the exact lines to add

### 2.1 The change

**Extract** the skills-router wiring block (`daemon/api.py:3847-3906`) into a module-level function and add ONE
production binding line to it:

```python
def _wire_skills_router_services(manager: Any) -> None:
    """… (the moved block, unchanged) …

    # Job dispatcher — ALSO into the SkillMetricsService (the CAPTURED
    # lane).  The ROUTER setter above binds the REST-facing dispatcher;
    # the CAPTURE lane reads a DIFFERENT handle — the metrics service's
    # OWN `_skill_job_dispatcher` (daemon/services/skill_metrics_service.py:812),
    # set through SkillMetricsService.set_job_dispatcher (:327).  Binding it
    # HERE makes the production boot — not a test-side injection — the owner
    # of the capture lane's dispatcher.
    metrics_service = getattr(manager, "_skill_metrics_service", None)
    if metrics_service is not None and getattr(manager, "_skill_job_dispatcher", None) is not None:
        metrics_service.set_job_dispatcher(manager._skill_job_dispatcher)
```

and in `lifespan`, the block becomes one call:

```python
    _wire_skills_router_services(manager)
```

### 2.2 Why this shape

| Requirement | How it is met |
|---|---|
| Evidence-based | §1 — the REAL setter is `SkillMetricsService.set_job_dispatcher`; the production dispatcher is `manager._skill_job_dispatcher` (built at `manager.py:8148`). No guessing. |
| Boot-safe | The injection is guarded (`metrics_service is not None and dispatcher is not None`); with the skill flags OFF the services still exist and the binding is harmless — the ON/OFF decision stays with the lane gates (`ENSEMBLE_SKILL_CAPTURE_ENABLED`, `ENSEMBLE_SKILL_EVOLUTION_ENABLED`). |
| No test-only symbols in production code | The new code imports nothing from `tests/`; the moved block's imports are unchanged (`daemon.routers.skills`). |
| No import cycles | The function body only touches `manager` attributes and the already-imported router module. |
| Minimal diff | The block is a **pure move** (no semantic change) + **one** binding statement + one call site. `daemon/manager.py:8196-8197` stays (same instance, idempotent — it is the manager-owned fallback for any boot path that does not run the api lifespan). |

### 2.3 Why the block is extracted rather than tested through a copy

A test that re-implements the wiring is vacuous (it goes green when production drops the line). The extracted function is
**the production code**, called by the production boot — so the regression test drives the real seam. The extraction is a
pure move: same statements, same order, same log line.

---

## §3 — Regression-test design (SCOPE B)

**File:** `tests/unit/test_skill_capture_production_wiring.py`
**Class:** `TestSkillCaptureProductionWiring` (named after the behaviour, not the mechanism)

### 3.1 The test

The test builds a manager double that carries the REAL collaborators the production seam touches:

* `_skill_metrics_service` → a **real** `SkillMetricsService` (its `_skill_job_dispatcher` starts **absent**);
* `_skill_job_dispatcher` → a **sentinel** dispatcher object (the "production instance");
* the other `_skill_*` attributes → simple stubs.

It then drives the **production seam** (`daemon.api._wire_skills_router_services(manager)`) and asserts the **production
boot result**:

1. `manager._skill_metrics_service._skill_job_dispatcher is manager._skill_job_dispatcher` — the capture lane holds the
   production instance (identity, not equality-with-a-test-double);
2. **cross-seam identity** — `daemon.routers.skills._skill_job_dispatcher is manager._skill_job_dispatcher`: the REST seam
   and the capture seam hold the SAME production instance. A test-side injection into the metrics service alone cannot
   produce this;
3. the binding is reachable: the metrics service's capture-eligibility path now finds a dispatcher
   (`getattr(metrics, "_skill_job_dispatcher", None) is not None`).

The test **never** calls `SkillMetricsService.set_job_dispatcher` itself.

### 3.2 Mutation legs (both run, both documented in the report)

| Leg | Mutation | Expected |
|---|---|---|
| **1** | Remove the production binding line from `_wire_skills_router_services` (the injection alone) | **RED** — the metrics service keeps its absent/None dispatcher; assertion 1 fails. |
| **2** | Leg-1 mutation **plus** a test-side injection (`metrics.set_job_dispatcher(fake)` inside the test — the shape that fooled every prior gate) | **STILL RED** — assertion 1 is an IDENTITY check against `manager._skill_job_dispatcher`; the fake is not the production instance, and assertion 2 (cross-seam identity) is broken by the fake. |

Both legs are executed against the real tree with the exact commands recorded in the report, and the tree is restored
(sha256-verified) afterwards.

---

## §4 — Runbook fix list (SCOPE C)

Target: `.agents/shared/planning/skill-system-revival/phase2-runbook.md`
**Provenance:** the file is **already tracked in the branch** (`git ls-files` → present; landed by `22d36a071`). The
canonical checkout does **not** carry it (only `phase0-*` + `findings/`), so the canonical copy is a **landing**, not a
recovery.

| # | Defect | Fix |
|---|---|---|
| 1 | **§8.1 A3 gate bands INVERTED (round-2 F1 — this row supersedes its own original fix, which mapped the gate onto the WRONG band).** `feedback_applied IS NOT NULL` is the **BLOCKER band**: those rows are the genuinely-evolved EVIDENCE, `has_genuinely_evolved_for_instance` returns True on them, and the Gate-4 code (`daemon/services/skill_metrics_service.py:733-772`) REFUSES capture (`:765-769` `return None`) — 5 996 rows / 5 944 instances live (2026-11-10T21:52Z census). A gate on this band with "STOP if 0" is TRIVIALLY satisfied (it is ~always non-zero) ⇒ the gate passed by accident. | The GATE is §8.1(a) = the **CANDIDATE band** `feedback_applied IS NULL` — the only PREDICTIVE population: these are selected+succeeded rows with NO feedback evidence, so the instance is NOT genuinely evolved and capture MAY still fire (`:765` falls through). **Expected > 0, STOP if 0** — an empty candidate band means nothing is left to capture ⇒ the lane can never admit ⇒ the gate FAILS. Live census 3 544 rows / 3 525 instances (2026-11-10T21:52Z). The blocker band demotes to §8.1(b) **MONITOR ONLY — never a gate, NEVER carries a STOP**; its non-zero value is the shipped predicate WORKING. Band map derived from the Gate-4 code + the predicate (`daemon/repositories/skill/repository.py:1699-1758`), quoted in the round-2 lane report; grep-derived sweep across runbook + plan, every occurrence reconciled. |
| 2 | Per-pack timeout `300` | `900` (two sites: §8.3 pack loop + the live-PG leg). |
| 3 | Live port `8079` (the dev port) | `9797` (the live port) — every live command in the runbook. |
| 4 | **C10 scope noise** — counts `category IN ('tool-failure','queue-scheduling')` over **all** anomaly rows in the window, so unrelated lanes' rows are read as skill-capture deviations. | Tighten to the lane's own rows: `AND instance_id = 'daemon:skill-capture-observability'` (the lane reporter, `daemon/services/skill_capture_observability.py:139`) — the daemon-side reporter identity used by every lane anomaly site. |
| 5 | **The landing** — the 05:00 ICT daily watch reads the runbook from the canonical checkout. | Write the corrected file **UNTRACKED** to `/home/nea/ensemble-src/.agents/shared/planning/skill-system-revival/phase2-runbook.md` (never commit there) **and** commit the corrected copy in this branch. Prove with `ls -la` + `sha256sum` on both copies. |

---

## §5 — Scope boundaries honoured

* Work happens **only** in `/home/nea/ensemble-src-wt-skill-capture-wiring`; the canonical checkout is touched **only** for
  the single permitted untracked runbook write (§4 defect 5). **No commit in the canonical checkout.**
* **No** push, **no** merge, **no** branch operations beyond committing on `feature/skill-capture-prod-wiring`.
* **No** live `.env` touch, **no** upgrade-journal / rollback-counter writes, **no** queue brake, **no** live DB writes,
  **no** Docker.
* Targeted pytest only (the new test + the adjacent skill-capture pins); the full/regression run is the tester's.
