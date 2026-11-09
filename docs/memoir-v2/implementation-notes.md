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
