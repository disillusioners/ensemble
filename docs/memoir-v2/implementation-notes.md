# Memoir Store v2 — implementation notes

Lane-scoped notes for the Memoir Store v2 ("The Reflecting Mind") lane:
the things the plan did not say, and the disclosures the staged
release notes must carry.

This file is the lane's own record. It lives under the repo's `docs/`
tree (the lane's plan set lives under `.agents/shared/planning/`, which
is READ-ONLY for the implementation lanes).

---

## 1. The gap acknowledgment — a plan's enumerated pin list is NOT exhaustive

### What happened

Phase 1 of this lane shipped two new schema artifacts (the v2 tables
and the v2 migration file). Both of them moved a **v1 exact-census
pin** whose value the phase-1 plan's pin-enumeration did not name. The
pins were not "wrong" — they were exact, and they became false because
the tree legitimately grew. But the plan's enumerated list of pins to
update did not include them, so the phase-1 implementer discovered them
only when the suite went RED.

The two pins that moved:

| Pin | Site | v1 value | v2 value | What grew |
|---|---|---|---|---|
| the memoir **table-set census** | `tests/unit/test_memoir_schema_contract.py` → `test_the_memoir_tables_are_registered` | 4 tables | 10 tables | the v2 substrate registers SIX new tables (`memoir_star`, `memoir_star_alias`, `memoir_line`, `memoir_reflection_state`, `memoir_proposal_ledger`, `memoir_jev_ledger`) into the SAME SQLModel metadata |
| the **`*memoir*.sql` migration glob** | `tests/unit/test_memoir_schema_contract.py` → `test_the_lane_adds_exactly_one_migration_file` | 1 file | 2 files | `daemon/migrations/versions/20261109_021154_create_memoir_v2_tables.sql` joins `20261105_102821_create_memoir_tables.sql` |

Both pins are now **exact** again — the census names the ten tables
that exist, and the glob names the two files that exist. Neither was
loosened into a subset; that is the point of an exact census.

### The lesson (carry it into the next lane's plan)

**A plan's enumerated pin list is a floor, not a ceiling.** The
enumeration names the pins the plan author *knew about*; a lane that
adds a schema artifact will move pins the author never looked at. A
pin list that is treated as exhaustive produces a surprise RED at the
end of a phase — when the cheapest moment to reason about it has
passed.

Two mitigations, both cheap:

1. **Census the tree BEFORE writing the plan's pin list.** A pin is
   only "exact" relative to a census; the census is what the plan
   should enumerate, and the pins should be derived from it.
2. **When a pin moves, treat it as a FINDING, not as noise.** A moved
   exact-census pin is evidence that the plan's mental model of the
   tree was incomplete — the same incompleteness can hide a pin that
   moved for the WRONG reason. Record the movement (as this note
   does) instead of silently re-baselining it.

---

## 2. Disclosure A1 — the kill-switch is OFF-direction effective; worker EXISTENCE stays boot-gated

This is a **documentation-only** disclosure. No code change accompanies
it, and none is planned in this lane.

### What the kill-switch does

The reflection lane's kill-switch is `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`.
It is resolved per call by
`daemon.tools.memoir_knob.resolve_memoir_reflection_knob` (the ENV tier
is read LIVE; the config section decides only when the ENV tier is
ABSENT).

**OFF is effective at RUNTIME.** A running worker re-reads the knob on
every tick and parks when it is OFF, and resumes when it returns
(`daemon/services/memoir_reflection_worker.py` — the park/resume state
machine). Flipping the knob to `false` on a running daemon takes effect
without a restart.

**ON is NOT symmetric — worker EXISTENCE is boot-gated.** The worker
task is created inside the lifespan, under a BOOT-time resolution of
the same knob:

```python
_reflection_boot_knob = resolve_memoir_reflection_knob(config)
if _reflection_boot_knob.enabled:
    ...   # the worker task is created here
else:
    app.state.memoir_reflection_worker_task = None
    app.state.memoir_reflection_worker = None
```

(`daemon/api.py` — the Memoir Store v2 reflection worker block.)

So: **a mid-run flip of the kill-switch from OFF to ON cannot
resurrect a worker that was never created.** The knob's OFF direction
works at runtime; its ON direction needs the worker to exist, and the
worker's existence is decided once, at boot.

### What this means for an operator

- Turning the lane OFF: immediate, no restart. ✓
- Turning the lane ON when it was OFF at boot: **the worker does not
  come up until the daemon restarts.** A mid-run flip to `true` is
  honoured by every *call-time* reader of the knob (the recall lane's
  footer, the tool layer, the health surface) — but no reflection pass
  runs until the worker exists.
- Turning the lane ON when it was already ON at boot: no-op.

### Why this is disclosed rather than fixed

Boot-gating the worker's EXISTENCE is deliberate: creating the worker
unconditionally would put a background LLM-calling task into every
daemon, including the ones whose operator explicitly disabled the
lane. The alternative (a supervisor that spawns the worker on a
mid-run ON flip) is a real design change with its own failure modes
(a worker that appears and disappears across flips, a boot-time knob
resolution that disagrees with the call-time one). It is out of this
lane's scope; this note records the asymmetry so the staged release
notes carry it.

---

## 3. Carry-in hygiene — the pin-strength lessons

Recorded here because they are the kind of lesson that is expensive to
re-learn:

- **A key-spelling pin cannot see a dead value.** The reflection lane's
  `llm_config` used to be pinned by an AST leg that read the dict's KEY
  NAMES. A typo'd ATTRIBUTE (`base_url_backu` instead of
  `base_url_backup`) left the VALUE permanently `None` — and every
  leg stayed GREEN, because the key was still spelled right. The fix is
  the IMPORTABLE seam (`daemon.api._reflection_llm_config`): the pins
  now drive the REAL dict, and the facade's own `is_configured` gate is
  asserted against it.
- **A pin that reads a value must be pinned at the VALUE's consumer,
  not at its producer.** The seam between the lifespan helper and the
  failover facade is now pinned end-to-end (the service hands the
  facade a dict that still carries the backup URL), so a strip at
  either end turns the suite RED.
- **An ambient environment variable is an input.** A test that builds a
  `Config()` reads the ENV tier at construction; under an ambient
  `ENSEMBLE_MEMOIR_REFLECTION_ENABLED=false` a "boot tier says ON"
  premise never held. The dual-recall suite now scrubs every reflection
  knob through an autouse fixture.

---

## 4. The failover request ceiling (disclosure for the staged notes)

The reflection lane's LLM calls go through the house HA facade
(`daemon.services.llm_failover.invoke_raw_with_failover`).

- Facade attempts per LLM call: **6** with a configured backup, **3**
  without one — `derive_ha_attempt_ceiling`
  (`daemon/llm_error_classifier.py`), consumed at
  `daemon/services/llm_failover.py` (the raw-SDK path).
- The openai SDK's own retry is disabled (`max_retries=0`, the house
  lane's shape — see `daemon/plugin_subsystem/opendesign/generate.py`).
  With the SDK's default of 2, each facade attempt would issue up to
  three HTTP requests and one LLM call would reach up to 18 requests.
- **HTTP-request ceiling per LLM call: 6** (backup configured) / **3**
  (no backup).
- **Per pass: 24 HTTP requests** — the reflection lane's 4-call budget
  (`MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS = 4`) × 6. The
  constellation lane has its OWN 2-call budget
  (`MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS = 2`) on the same
  caller, so a pass that spends both budgets reaches **36** requests.

The wall-clock bounds are separate and stay as designed: a 45 s
per-request timeout (`MEMOIR_REFLECTION_CALL_TIMEOUT_S`) and the
facade's 45 s wall-clock cap (which fires BETWEEN attempts), inside a
60 s per-pass tick budget (`MEMOIR_REFLECTION_TICK_BUDGET_S`).

---

## 5. V2-44 — the tick budget reconciliation (the phase-4 fix round)

**Status:** NEW decision. Recorded here (the lane's own notes file). The
planning ledger (`.agents/shared/planning/ari-memoir-store/v2-decisions.md`)
is READ-ONLY for the implementation lanes — the caller folds this text in.

### The contradiction

Three documents disagreed about the tick's time budget:

| Source | Number | What it actually bounds |
|---|---|---|
| `v2-phase4-plan.md` objective 1 | **20 s** | "tick budget 20s" |
| V2-D 4.2 | 30 s | the **PER-CALL timeout** (`MEMOIR_POIGNANCY_CALL_TIMEOUT_S`) — NOT a tick budget |
| the shipped code (`TICK_SCORE_BUDGET_S`) | 60 s | the tick's TOTAL scoring budget |

### The decision — V2-44 (verbatim)

> **The tick's TOTAL scoring budget is 20 seconds — the PLAN's number.**
> The code's 60 s is retired. V2-D 4.2's "30 s" is not a tick budget:
> it is the PER-CALL timeout (`MEMOIR_POIGNANCY_CALL_TIMEOUT_S`), a
> different knob, and it stays 30 s.
>
> **The budget is now a REAL wall-clock bound.** The old shape checked
> the budget only BETWEEN episodes: one episode could run to the
> per-call timeout (30 s) and the tick could reach ~1.5x its budget.
> Each episode's wait is now sized to the tick's REMAINING budget
> (`asyncio.wait_for` — the embedding lane's AM-28 shape), so the tick's
> scoring work can never exceed the budget.

### The consequences that MUST be disclosed

1. **The per-call timeout (30 s) is no longer reachable inside a tick.**
   The tick budget (20 s) binds first: a scoring call that needs more
   than the tick's remaining budget is abandoned at the budget, the
   episode counts a `poignancy_call_timeout` attempt, and the abandoned
   call keeps running in the executor until its own timeout fires. A
   model that consistently needs more than 20 s per call therefore
   loses its rows to the attempt cap instead of being scored — the
   operator's lever is `ENSEMBLE_MEMOIR_POIGNANCY_TICK_S` (the tick
   interval) and the backfill/knob family, not a per-call knob.
2. **The lane's executor has TWO threads** (the embedding lane's AM-29
   shape). V2-D 4.2's parenthetical said `max_workers=1`; that is the
   ONE deviation this round makes to the decision text, and it is
   forced: the scoring calls stay SEQUENTIAL, and the second thread
   exists so the tick's own accounting write (the failed attempt of an
   abandoned call) does not queue behind the abandoned call. With one
   thread the tick's wall-clock bound would be broken by the length of
   the hung call — the bound V2-44 exists to create.
3. **An abandoned call and the NEXT tick's call can overlap.** In the
   pathological hang case the lane may have two scoring calls in flight
   (one abandoned, one from the next tick). The normal path is
   strictly sequential. Two concrete effects of that overlap: an
   abandoned call that later SUCCEEDS still calls
   `record_poignancy_score` (the row's status moves to `scored` and the
   score is written) after the tick already recorded the
   `poignancy_call_timeout` attempt; an abandoned call that later FAILS
   takes the failure-record path AGAIN — a SECOND attempt increment on
   top of the timeout's — accelerating the row's terminalization. Both
   effects are reachable in the pathological-hang case only.

---

## 6. The worst-case ceiling of a poignancy tick (staged release notes)

| Component | Bound | Source |
|---|---|---|
| the tick's TOTAL scoring work | **20 s** | `TICK_SCORE_BUDGET_S` (V2-44) |
| one scoring call | min(30 s, the tick's remaining budget) — the budget binds first | `MEMOIR_POIGNANCY_CALL_TIMEOUT_S` |
| one episode's wait | the tick's remaining budget (≥ 0.05 s) | `_process_episode` |
| the scoring calls per tick | ≤ 10 (the batch cap) | `MEMOIR_POIGNANCY_BATCH_CAP` |
| the HTTP requests per scoring call | 6 with a configured backup / 3 without one | the HA facade (`llm_failover`) |
| the HTTP requests per tick | ≤ 60 (10 calls × 6) | derived |
| the dead-credential path | **1 request per tick** — the tick stops at the first rejection | the latch (below) |
| the tick's wall clock | 20 s of scoring + the census + the failure records (short DB round trips, NOT counted in the 20 s) | derived |
| the latched tick interval | 300 s (until the first success) | `LATCH_TICK_INTERVAL_S` |
| the row's terminal path | 5 attempts (one per tick) → `terminal:<reason>` | `MEMOIR_POIGNANCY_ATTEMPT_CAP` |

---

## 7. The lane-down latch (V2-D 4.6 / D3.9-D3.10) — what was missing and what shipped

### What was missing

The service's own comment claimed the lane-down latch existed
("a 401/403 is an IMMEDIATE terminal … the tick counts as an all-fail
tick (the lane-down latch)") — only the ROW half was implemented. A
dead credential burned one request per pending row per tick and
terminalized the whole unscored backlog, because the lane CREATES
terminal states: every row the dead credential touched was excluded
permanently.

### What shipped

* a **credential-rejected streak** (`snapshot()["credential_rejected_streak"]`),
* an **all-fail streak** (`snapshot()["consecutive_all_fail_ticks"]`),
* the **latch** (`snapshot()["latched"]`) and the **slowed tick**
  (300 s, `snapshot()["latch_tick_interval_s"]`), released by the first
  success,
* the tick **stops at the first credential rejection** — the remaining
  rows are DEFERRED, so a dead credential costs ONE request per tick.

**Scope of these protections in the MIXED-tick case.** "Stops at the
first credential rejection" is exact only for an ALL-FAIL tick. A MIXED
tick — one that scores at least one row and THEN hits a 401/403 — also
breaks out of the scoring loop, so the DEFERRAL protection holds in
both cases: one request per tick, and the remaining rows are deferred.
What does NOT hold in the mixed-tick case is the latch: the tick lands
in its scored-at-least-one branch, which RELEASES the latch and resets
the credential-rejected streak, so after a mixed tick the lane retries
at the NORMAL tick interval, not the latched 300 s.

### The one deliberate deviation from the embedding lane

The embedding lane CONTINUES with the remaining candidates after a
credential rejection (its rows are embeddings: idempotent, and the
failure is retried). This lane CREATES terminal states, so continuing
would permanently exclude the rest of the backlog against a wall. The
deviation is recorded here (V2-D 4.6's intent — "a dead credential
must not burn a row's attempt budget against a wall" — is served by
stopping, not by continuing) and in the code at the deviation site.

---

## 8. The operator recovery path (W6) — the requeue

The lane creates terminal states, so a transient outage used to exclude
rows PERMANENTLY and the only recovery was manual SQL.

**THE RECOVERY PATH (both surfaces):**

* repository: `MemoirRepository.requeue_poignancy_episode(episode_id, *, restore_legacy=False)`
* service: `MemoirPoignancyService.requeue_episode(episode_id, *, restore_legacy=False)`

The write is ONE statement: the terminal marker is cleared (or restored
to `'legacy'`), the attempt counter is reset to 0, and the stale score
is dropped, so the row re-enters the scoring queue on the next tick.
The authored `importance` is NEVER touched (V2-10 / §3-A2).

**`restore_legacy=True` MATTERS.** A legacy row MUST keep the `'legacy'`
marker: restoring `NULL` would move it into the ALWAYS-scored set and
BYPASS the backfill fence (V2-14 / §3-A3).

**WHY THERE IS NO OPERATOR TOOL.** The plan's phase-4 tool surface is
the knob family and the status inspection; a requeue tool would add a
new tool to the closed tool surface (and move the tool-vocabulary
census). The method + this note ARE the recovery path; a tool is a
follow-up, not a silent gap.

---

## 9. The transitive provider edge (W7) — a phase-1/2 BACKLOG NOTE

**NO production change.** The write path's provider-freedom pin is now
a TRANSITIVE AST walk scoped to the `insert_episode` closure (the
callable reachability set) instead of a direct-import name check on the
one module that owns the INSERT.

**THE KNOWN EDGE (pre-existing, phase 1/2, READ-PATH):**
`daemon/repositories/memoir/repository.py` imports
`daemon.services.skill_search_service` (which imports `openai` at the
module level) inside `_lexical_rank_sqlite` — the LEXICAL RANKER, a
read path. It is UNREACHABLE from the append closure (the walk proves
it: the module never enters the reachable set), and phase 4 added ZERO
import edges.

**THE BACKLOG ITEM (phase 1/2, NOT this lane):** the lexical ranker
reaches a module that imports an LLM provider. It is a READ path and
the write path is provably unaffected, so it is recorded here rather
than fixed. If a future lane moves the ranker or the repository's
import graph, the transitive pin will catch a change to that edge.

**THE PIN'S SCOPE (disclosed):** the walk follows CALLS through the
project's own modules (`daemon/…`); a third-party module is out of
scope (a dependency's own imports are not the write path's
responsibility), and a RELATIVE import (`from .x import y`) inside a
reachable module is not resolved to its file (the module-level import
name is still checked).

---

## 10. The LLM-config seam (W3) — the THIRD occurrence of the class

The poignancy lane shipped WITHOUT the importable-seam pin that the
reflection lane earned the hard way: every poignancy test built its OWN
`llm_config` dict, so a typo'd attribute in the REAL builder
(`daemon/api.py::_poignancy_llm_config`) was structurally invisible.

The seam is now pinned end-to-end: the helper is module-level and
importable, the lifespan's call site IS that helper (an AST assertion),
the REAL dict carries the backup VALUE, and the facade's own
`is_configured` gate is asserted OPEN against the real dict (a dead
value turns the pin RED — proven by the mutation: a typo'd
`base_url_backup` attribute leaves the value `None` while every
key-spelling leg stays GREEN).

The same class appeared three times in this lane: the reflection
lane's `llm_config`, the FAILOVER CLIENT's `max_retries=0` (a
carry-in), and now the poignancy builder. The house rule that follows:
**a pin that reads a value must be pinned at the value's CONSUMER, and
the consumer's own gate (`is_configured`) is the assertion.**
