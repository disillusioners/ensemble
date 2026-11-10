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

---

## 11. F-2 — the per-purpose split of the constellation budget

### The leader's ruling (verbatim)

> F-2 (leader ruling: split per-purpose counters<=1 / tier2<=1 +
> extraction-first ordering; false 'at most one per pass' comments)

### What the code did (the defect)

V2-D 3.9 caps the constellation at **"max 1 extraction, max 1 tier-2 card
summarization per PASS"**. The code implemented ONE shared pool of TWO
calls with **no per-purpose cap**, and the Tier-2 summarization ran
INSIDE the per-chunk extraction. Two consequences, both observed by the
council's own probe:

* a Tier-2-heavy pass could spend BOTH calls on Tier-2 and leave the
  extraction with ZERO — the probe produced **"2 tier-2 calls, 0
  extraction"**, which defeats the T3.1b seed (the extraction is the
  fallback that writes the stars);
* the comments claimed an invariant the code did not have ("Tier-2
  summarization: at most one star per pass" / "return  # at most one per
  pass") — the cap was per INVOCATION, not per pass.

### What shipped

1. **The per-purpose counters** — `ConstellationCallBudget` now carries a
   shared pool (2) AND a per-purpose cap
   (`MEMOIR_CONSTELLATION_MAX_CALLS_PER_PURPOSE_PER_PASS = 1`).
   `take(purpose)` refuses when EITHER the pool OR the purpose's cap is
   spent. The purposes are `"extraction"` and `"tier2"`.
2. **The extraction-first ordering** — the Tier-2 phase is now a
   SEPARATE, PASS-LEVEL phase that runs AFTER every chunk's extraction
   (`MemoirConstellationService.summarize_qualifying`). The per-chunk
   `extract()` no longer summarizes; the standalone path calls the phase
   itself so its behaviour is unchanged.
3. **The comments now state the true invariant** — the cap is "at most
   ONE extraction call and at most ONE Tier-2 summarization per PASS",
   and the ordering guarantee is written down at both call sites.
4. **THE SCAN SHAPE changed with it (declared; CARRIED TO THE
   TIDIER):** the Tier-2 phase collects its candidate stars with an
   AGENT-WIDE query
   (`select(MemoirLine.star_id).where(MemoirLine.agent_id == agent)`)
   and the phase now runs ONCE per PASS. The banked tidier item
   "per-chunk Tier-2 scan O(stars)x3" is therefore **de facto ONE
   agent-wide scan per PASS** — the per-chunk x3 shape is GONE. The
   per-pass O(stars) scan REMAINS (the phase still walks the agent's
   distinct star ids), and it still runs inside the 60 s tick budget.
   This is a DECLARATION, not a fix: no scan shape changed behaviour
   here beyond what F-2 already shipped.

### The receipts (the legs are not vacuous)

| Recipe | Mutation | Red node |
|---|---|---|
| M1c | the per-purpose cap removed | `TestThePerPurposeBudget::test_a_second_extraction_in_the_same_pass_is_refused` (`assert 2 == 1`) + `TestThePerPassCallBudget::test_a_multi_chunk_pass_spends_at_most_two_constellation_llm_calls` (`assert 2 == 1`) |
| M1b | the pre-fix shape (no per-purpose cap + the per-chunk Tier-2) | `TestThePerPurposeBudget::test_the_extraction_is_taken_before_the_tier2` (`assert 0 == 1` — the council's probe reproduced verbatim) + `TestThePerPurposeBudget::test_a_second_extraction_in_the_same_pass_is_refused` (`assert 2 == 1`) + `TestThePerPurposeBudget::test_a_second_tier2_in_the_same_pass_is_refused` (`assert 2 == 1`) |

**THE FINDING (disclosed):** with the per-purpose cap in place, the
per-chunk Tier-2 is **not observable on its own** — the cap alone
already forbids the second Tier-2 call, so the ordering is a SECOND
defence rather than the load-bearing one. The M1b receipt therefore
removes BOTH (the full pre-fix shape) to show the pack catches the
defect class; a receipt for the per-chunk Tier-2 alone is IMPOSSIBLE by
construction and is NOT claimed.

---

## 12. F-3 — the durable pass identity and the atomic pass unit

### The leader's ruling (verbatim)

> F-3 (leader ruling: FIX not waiver — durable pass id minted per pass
> not per chunk, edges+reflected_at in ONE transaction, crash-then-retry
> leg via PRODUCTION site; the guard was inert because the unit leg
> hand-supplied a stable id)

### What the code did (the defect)

* the pass id was minted **per chunk**
  (`memoir_reflection_service.py`, `f"reflect-{uuid.uuid4().hex[:12]}"`),
  so a crash-retry re-processed the same work under a NEW stamp and the
  `last_pass_id` guard (V2-29 / SF2) — which only binds when the stamp
  is STABLE — inflated `mention_count`;
* `record_line` committed per edge and `commit_pass` marked the
  watermark in a **separate transaction**, so a crash between them left
  an edge without its mark: exactly the window V2-29 / A2 exist to
  close;
* the unit leg passed only because it **hand-supplied** a stable pass id
  (the guard-blindness class: the guard needed a value the production
  path could never provide).

### What shipped

1. **The durable identity, minted at the PRODUCTION site** —
   `durable_pass_id(agent_id, cadence, episodes)` derives
   `reflect-<16 hex>` from the agent, the cadence and the pass's
   **ANCHOR episode** (its oldest considered episode). The anchor is
   durable state: a crashed pass never marked it, so the retry mints
   the SAME id and the guard binds. The mint lives in `_run_pass` — the
   production pass loop — and is threaded into EVERY chunk.
   **No new column, no migration:** the identity is DERIVED, not
   persisted.
   *THE TRADE-OFF (disclosed, CORRECTED 2026-11-10):* two passes that
   consider the same anchor episode share the identity, so the second
   one does not inflate the mention counts. In the COMMON case the
   guard UNDER-counts — but it CAN over-count, by exactly ONE, at an
   ANCHOR TRANSITION. **THE OLD SENTENCE ("the guard under-counts, it
   never over-counts") WAS MEASURABLY FALSE and is withdrawn here.**
   *THE OVER-COUNT (measured, reproducible):* when the pass ANCHOR
   MOVES between passes — a chunk left UNMARKED (an R2 synthesis-parse
   failure, a write failure, or a cap-deferral) while an older chunk
   was marked — the next pass mints a DIFFERENT id, the guard sees a
   DISTINCT pass, and the same `(star, episode)` pair is counted TWICE.
   *THE PROBE (the shape, so the claim is reproducible):* P1 anchor =
   E-old → P2 anchor = E-new → the pair is counted TWICE (count = 2)
   → P3 over the new anchor is STABLE (no further inflation). The
   over-count is therefore BOUNDED to +1 per anchor transition.
   *THE BLAST RADIUS:* `mention_count` is READ in exactly ONE place —
   the star-card "lines" payload
   (`daemon/repositories/memoir/constellation_repository.py:464`) — so
   it is DISPLAY ONLY and never a trigger: the tier-2 trigger is
   `COUNT(*)` over the `memoir_line` rows, NEVER `SUM(mention_count)`
   (V2-22).
   *NO REDESIGN HERE:* the identity derivation is NOT changed by this
   note — a redesign of the identity is a NEW leader decision, not a
   documentation fix.
2. **The atomic pass unit** — `MemoirReflectionRepository.unit_of_work()`
   opens ONE transaction; the constellation edges
   (`record_line(..., session=…)`) and the per-episode watermark
   (`mark_reflected(..., session=…)`) ride the SAME session. The
   atomicity scope is the **processed episodes' unit (the chunk)**, not
   the whole pass — the lane's partial-commit posture stands.
   **A FAILED unit leaves the chunk UNMARKED** (the edges rolled back,
   so the next pass has to retry them; marking them would lose the
   seed). This is the one behaviour change the unit forced, and it is
   enforced by `unmarked_episode_ids`.
3. **The star resolution moved OUT of the unit** — the canonical upsert
   and the alias binding are their own transactions (they happen before
   the unit opens), so the unit never nests a commit inside its
   transaction. The price: a crash between the resolution and the unit
   leaves an orphan star (a star with no lines) — harmless, and the
   retry fills it.

### The receipts

| Recipe | Mutation | Red node |
|---|---|---|
| F-3a | the per-chunk random mint restored (`os.urandom(6).hex()` — the same shape as the old per-chunk `uuid4`) | `TestTheDurablePassIdentity::test_the_retry_of_the_same_pass_reuses_the_identity` (`assert 'reflect-8ab4151164e7' == 'reflect-a6482bef57d9'` — the retry minted a NEW stamp) |
| F-3b | the edges and the marks written in SEPARATE transactions | `TestTheDurablePassIdentity::test_the_edges_roll_back_with_the_marks` (the orphan edge survived the failed unit) |

Both legs mint their ids **through the production site** — the test
never hands the pass an id.

---

## 13. F-4 — the mutation recipes live HERE, not in a commit message

The lane standard: a mutation recipe is **node name + observed RED
marker + restore proof**, and it belongs in this file. A commit message
is not a durable home: it is not greppable, it is not in the tree of
the NEXT phase, and it disappears with the reflog.

### The five phase-3 recipes that lived only in a commit message

Migrated from `f15a0bb7b`'s commit body ("AC6 mutation proofs (all
reverted; sha256 before == after)") and from the phase-3 delta's
review receipts:

| # | Recipe | Node | Observed RED marker | Restore |
|---|---|---|---|---|
| 1 | AC6 (a) — fragments measured via `SUM(mention_count)` | `TestTwoTier::test_one_heavily_mentioned_episode_stays_tier_1` | `assert 10 == 1` | sha256-identical |
| 2 | AC6 (b) — `memoir_not_found` for zero-episode stars | `TestTheZeroEpisodeStar::test_a_zero_episode_star_renders_an_empty_dossier_success` | the REFUSED code leaked into the empty-dossier success | sha256-identical |
| 3 | AC6 (c) — heat incremented during background extraction | `TestExtractionAndAlias::test_background_extraction_never_increments_heat` | `assert 1.0 == 0.0` | sha256-identical |
| 4 | M1 — the constellation budget is per CHUNK (not per pass) | `TestThePerPassCallBudget::test_a_multi_chunk_pass_spends_at_most_two_constellation_llm_calls` | `assert 3 <= 2` (the pass spent 3 constellation calls against the cap of 2) | sha256-identical |
| 5 | M2b — a FAILED connected-episode fetch still increments the heat | `TestTheHeatHonesty::test_a_failed_episode_fetch_renders_but_leaves_the_heat_alone` | `assert 1.0 == 0.0` | sha256-identical |

**THE COUNT (a correction):** the phase-3 commit body carried **three**
recipes (the AC6 trio above); the delta's two (M1, M2b) carried the
count to five. All five are here — and the delta's two were NOT in any
commit body (the delta commit `e76fef843` has an EMPTY body), so their
recipes existed only in the reviewer's report.

### The close-out recipes (this commit)

| Recipe | Mutation | Red node |
|---|---|---|
| M1b | the pre-fix shape (no per-purpose cap + the per-chunk Tier-2) | `TestThePerPurposeBudget::test_the_extraction_is_taken_before_the_tier2` (`assert 0 == 1` — the extraction was STARVED: 0 calls) + `TestThePerPurposeBudget::test_a_second_extraction_in_the_same_pass_is_refused` (`assert 2 == 1` — two extractions) + `TestThePerPurposeBudget::test_a_second_tier2_in_the_same_pass_is_refused` (`assert 2 == 1` — two Tier-2 calls) |
| M1c | the per-purpose cap removed | `TestThePerPurposeBudget::test_a_second_extraction_in_the_same_pass_is_refused` (`assert 2 == 1` — two extractions) + `TestThePerPassCallBudget::test_a_multi_chunk_pass_spends_at_most_two_constellation_llm_calls` (`assert 2 == 1` — two extractions) |
| R1 | the LLM paths take an unbudgeted call again | `TestTheBudgetRefusal::test_the_llm_extraction_is_refused_without_a_budget` (`DID NOT RAISE <class 'Exception'>`) + `TestTheBudgetRefusal::test_the_tier2_summarization_is_refused_without_a_budget` (`DID NOT RAISE <class 'Exception'>`) |
| R2 | the synthesis parse failure skips the constellation extraction again | `TestTheSeedSurvivesAParseFailure::test_a_failed_synthesis_batch_still_seeds_the_constellation` (`assert None is not None` — the star was never written) |
| R3 | a DELETED episode still counts as a hit | `TestTheHeatHonesty::test_a_deleted_episode_does_not_accrue_heat` (`assert 1.0 == 0.0`) |
| F-3a | the per-chunk random mint | `test_the_retry_of_the_same_pass_reuses_the_identity` (the retry minted a NEW stamp) |
| F-3b | the edges and the marks in separate transactions | `test_the_edges_roll_back_with_the_marks` (the orphan edge survived) |

Every receipt restores the mutated file **byte-identically** (the
sha256 is captured before the mutation and re-checked after the
restore) and re-runs the pack to GREEN.

**THE RED SETS (re-measured 2026-11-10):** the M1b, M1c and R1 rows
above were re-measured with the mutation applied, the pack's selector
run, and the mutated file restored byte-identically (sha256 before ==
sha256 after). Each row now carries **EVERY** red node of its
mutation — M1b reddens THREE, M1c TWO, R1 TWO — not a subset.

---

## 14. The LANE RULE this close-out adopted (and it starts NOW)

**A delta's own legs land selector-registered and re-pinned IN THE SAME
COMMIT.**

The phase-3 delta added two legs — `TestThePerPassCallBudget` and
`TestTheHeatHonesty` — and registered NEITHER in a pack selector. A
tester running only the four packs would have **PASSED with both
regressions restored**: the budget regression and the heat regression
were invisible to the gate. The packs now name both classes EXPLICITLY
and their node-count pins move in the same commit as the legs.

The node-count pins that moved with this close-out (measured with
`--collect-only`, not estimated):

| Pack | Before | After |
|---|---|---|
| `memoir_v2_constellation_service_test` | 6 | 17 |
| `memoir_v2_about_tool_test` | 27 | 29 |

The four close-out classes (`TestThePerPurposeBudget`,
`TestTheDurablePassIdentity`, `TestTheBudgetRefusal`,
`TestTheSeedSurvivesAParseFailure`) are named in the constellation
pack's selector as well — a leg that no pack selects is a leg the gate
cannot see.

### THE PACK STATUS — a FIFTH memoir pack is RED (pre-existing)

The close-out commit body claims "the four packs PASS (17 / 4 / 29 /
26)". Those four PASS (re-measured 2026-11-10 at this commit):

| Pack | Nodes | Verdict |
|---|---|---|
| `memoir_v2_constellation_service_test` | 17 | PASS |
| `memoir_v2_star_description_test` | 4 | PASS |
| `memoir_v2_about_tool_test` | 29 | PASS |
| `memoir_v2_tool_registration_test` | 26 | PASS |

**A FIFTH memoir pack is RED — and it is PRE-EXISTING, not this
lane's:** `memoir_v2_reflection_service_test` reports
`passed=43 != EXPECTED_PASSED_N=32`. The pack file
(`test/packs/memoir_v2_reflection_service_test.sh`, pin 32) and its
test module (`tests/unit/test_memoir_v2_reflection_service.py`, 43
nodes) are **byte-identical at the base commit `e76fef843` and at this
commit** (same blob `ef5ab884…`), so the drift cannot have been caused
by this lane's close-out. It is already tracked in the shared KV
(`memoir-v2.phase4-residuals`, item 2 — pre-existing pack-pin drift).

**WHY IT IS NAMED HERE ANYWAY:** this lane DID modify
`daemon/services/memoir_reflection_service.py` (F-3's durable pass
identity and the atomic pass unit), so the RED reflection pack sits
next to code this lane touched — a reader of the commit body would
otherwise have no way to tell the two apart. The RED is a stale
node-count PIN, not a failing assertion: the pack's 43 nodes all
PASS. (`memoir_inner_soul_redirect_test` is likewise RED in this
WORKTREE — but its RED is that pack's own TREE-FENCE, which flags the
worktree's UNCOMMITTED files (the foreign `.agents/approver/` WIP, and
transiently this commit's own notes edit) because its allow-list is a
PREFIX list that does not admit `docs/memoir-v2/`. The pack file is
byte-identical at the base commit and at this commit, so the drift is
pre-existing and outside this lane.)

---

## 15. Phase 5 — Persona Gate (JEV) Implementation & Mutation Receipts

Phase 5 delivers the JEV-gated Persona Evolution subsystem, activating
the dormant v1 persona authoring seam per plan `v2-phase5-plan.md`
(flags V2-9, V2-13, V2-20, V2-37, V2-38, V2-39, DD-9).

**THE FIX ROUND (2026-11-10) — what this commit changes.** The
independent mutation-grade review found 2 CRITICALs, 3 unpinned
properties and 3 advisories against the phase-5 implementation; this
commit is the fix round for all of them except the carried F4 (§15.5).

**THE DELTA (2026-11-10, this commit) — what THIS commit changes.** The
delta re-review of `2d0898c11` confirmed C1's invariant closed and C2
fully closed, and found ONE CRITICAL raise-path residual plus five
advisories. **The delta's findings are labelled ΔF1..ΔF6 here** — they
are NOT the earlier fix round's F1/F2/F3 (the doubly-broken fallback
lane and the router's production branch).

* **ΔF1 (CRITICAL) — the raise path.** `process_candidate` can raise
  AFTER the classification was paid for and BEFORE any ledger row
  exists (the composition `try` covers only `compose_persona_document`;
  `create_draft` / `submit` sit OUTSIDE it). The pre-fix shape let
  that escape the candidate loop, and the `finally`-park then wrote the
  candidate as `classification=NULL, attempts=0,
  last_error='deferred:composition_budget'`. Four measured harms: the
  attempt counter froze at 0 (the terminal-at-5 ladder was UNREACHABLE
  on this path); the PAID classification was discarded (a re-classify
  AND a re-compose on EVERY pass — 8 classify calls over 8 passes for
  ONE candidate); the lane hit a PERMANENT HEAD-OF-LINE STALL
  (`get_pending_candidates` orders `created_at ASC`, so the poison row
  was re-selected first every pass and broke the loop before anyone
  else — two later candidates stayed `pending` through 8 passes); and a
  store failure was MASKED as a budget deferral. **THE MECHANISM:** the
  per-candidate `process_candidate(...)` call is now wrapped in a
  `try/except` INSIDE the loop. On an exception the candidate is
  JOURNALED (`_journal_gate_error`): the classification already paid
  for is PRESERVED, the attempt counter advances by one, the
  diagnostic is `gate_error: <Type>: <message>` (a NON-deferral
  marker), the status is `terminal` at `MAX_ATTEMPTS` and `pending`
  below it, and the id is marked processed so the `finally`-park does
  not clobber the row. **The pass CONTINUES** — the failure is
  contained, so the lane does not stall. §15.2 row 11 is the receipt.
  **RE-VERIFIED IN THIS WORKTREE (2026-11-10):** the four harms were
  re-measured under the exact-shape revert (the bare finally-park) with
  8 passes over 3 candidates whose proposal store always fails: **8
  classify calls over 8 passes** for ONE candidate (the paid
  classification re-paid every pass), `attempts=0` frozen,
  `classification=None`, `last_error='deferred:composition_budget'`
  (the store failure MASKED as a deferral), and the two later
  candidates STILL `pending` after 8 passes (the head-of-line stall).
  The SAME probe on the FIXED shape measures 3 classify calls (one per
  candidate — the stored classification is reused on every retry), the
  attempt ladder 3/5 → 4/5 → 5/5 → `terminal`, the classification
  PRESERVED (`'new_rule'`), and the diagnostic
  `gate_error: RuntimeError: the proposal store exploded`.
* **ΔF2 — the false claim.** The C1 bullet below claimed "a candidate
  whose classification is ALREADY KNOWN keeps it". That was FALSE on
  the current-pass raise path (the classification was paid for and then
  DISCARDED). The bullet — and the `_park_unprocessed_candidates`
  docstring — now state the post-fix truth.
* **ΔF3 — the timeout pin.** `TestProductionWiring`'s timeout
  assertion compared against `float(service.settings.call_timeout_s)`,
  which DEFAULTS to 45.0 — a hard-coded 45.0 in the gate's construction
  was invisible (measured: 0 red nodes). The leg now drives
  NON-default settings (`call_timeout_s=7.25`), so that mutation
  REDDENS (§15.2 row 12).
* **ΔF4 — the two missing PLAN-DEFECT entries.** §15.6 now records all
  THREE plan omissions (the comparator, the budget-deferral
  disposition, and the mutation-application site).
* **ΔF5 — row 4's membership.** §15.2 row 4 claimed 1 red node; the
  exact-shape revert reddens 2 (the row-4 and row-4b recipes' red sets
  are a UNION). The true membership is now stated.
* **ΔF6 — the boot-frozen gate timeout (DISCLOSED CARRY).** After
  construction, `apply_settings(7.0)` leaves the gate at 45.0
  (service 7.0 / gate 45.0) — the gate's per-call timeout is frozen at
  construction time. Impact ≈ 0 today (the lane rides the reflection
  caller's caller), but "one truth per knob" is only half true. §15.7
  records it. NOT changed here: live-knob propagation is a bigger
  change than this commit's scope.

* **C1 — the silent production loss.** The per-pass composition budget
  ended the candidate loop with a bare `break`, so a candidate the
  budget never reached was **dropped with NO ledger row**. The ONLY
  re-selection source is `get_pending_candidates()` (the ledger) and the
  sole production caller passes ONLY this pass's accepted syntheses —
  so such a candidate was **PERMANENTLY LOST** from the JEV lane, the
  exact data loss V2-38 exists to forbid. It fires on the DEFAULT
  configuration whenever a pass yields ≥ 2 gated candidates (the
  synthesis cap is 4). **THE MECHANISM:** `process_pass` now records
  the ids it processed and, after the loop, parks EVERY candidate it did
  NOT process (`_park_unprocessed_candidates`) as a `pending` ledger row
  with `last_error='deferred:composition_budget'`. A candidate whose
  classification is ALREADY KNOWN **from an earlier pass** keeps it (the
  retry must not re-pay the classifier); a candidate that was never
  classified is stored with `classification=NULL` and is classified on
  the next pass (it was never classified — there is nothing to store).
  **A candidate whose classification was determined THIS pass and then
  RAISED is NOT parked at all** — the raise path journals it with that
  classification PRESERVED (ΔF1; `gate_error: …`, never the deferral
  marker). A candidate that ALREADY has a row is left alone, so a
  diagnostic `last_error` is never clobbered.
* **C2 — the gate integrity.** The two AC6 legs were SELF-MUTATING
  (they asserted a property of a subclass the test itself defined) and
  were therefore UNFALSIFIABLE. They are now production-revert recipes
  (§15.2 rows 1-2), and the receipts that claimed otherwise are
  CORRECTED.
* **C3 — the two unpinned properties.** The suppression rule
  (V2-D 5.2 / AC1) and the heuristic ORDERING (V2-D 5.1) now have
  legs that a production revert reddens (§15.2 rows 5-6).
* **F1 / F2 — the doubly-broken local fallback lane** is FIXED and
  exercised (§15.2 rows 7-9); the production-wiring verdict is in §15.5.
* **F3 — the router's production branch** (the engine fallback) is now
  the PRIMARY case in the router tests (§15.2 row 3 + the new
  `TestProductionRepositoryResolution` class).
* **The precedence** — the plan's comparator — is implemented and the
  ambiguity is recorded for the caller's ruling (§15.4).

### 15.1 PREMISE-DRIFT Reconciliation

The dispatch brief noted: *"mark_superseded exercised from the SERVICE
(the v1 zero-caller path activates HERE)"*. Investigation of
`daemon/repositories/memoir/repository.py:322` and line 1173 confirmed
that `mark_superseded` is the episode replacement pointer
(`UPDATE memoir_episodes SET superseded_by = ...`), an episodic store
method uncalled in v1 and not relevant to persona versioning.

The actual v1 zero-caller path activated by Phase 5 is
`MemoirPersonaRepository.submit()`, as pinned by
`tests/unit/test_memoir_persona_lane.py:176`
(`test_submit_is_a_seam_without_a_v1_caller`). In v1, persona was
read-only; Phase 5 activates the persona evolution seam from the service
layer. The allowed caller set was updated in the invariant test:
- `daemon/services/memoir_persona_gate.py` (calls `create_draft` and `submit`)
- `daemon/routers/memoir.py` (calls `list_pending`, `approve`, `reject`)
- `daemon/api.py` (wiring and mount)

### 15.2 The Mutation Receipts (AC6 / §13 Format) — CORRECTED

**THE CORRECTION OF ROWS 1-2 (the independent mutation-grade review's
finding).** The rows this section carried before were **receipts of
NOTHING**: each leg defined a `MutatedService` / `MutatedRepo` subclass
INSIDE the test and asserted that the mutant it had just built
misbehaved. **No production revert could redden them** — they asserted
a property of the test's own subclass, not of the production code. The
"Byte-identical" column attested to a restore of a mutation that never
touched production. Both legs are now **PRODUCTION REVERT recipes**: the
leg asserts PRODUCTION behavior, and the mutation is applied to the
production file, run, observed RED, and restored byte-identically.

Every recipe below was run in this worktree on 2026-11-10 against the
production files, with the ambient `POSTGRES_*` variables scrubbed
per-variable (never the `env -u POSTGRES_*` no-op glob). Every mutated
file was restored **byte-identically** (sha256 before == sha256 after),
and the packs were re-run GREEN after the restore.

| # | Recipe (the PRODUCTION revert) | Node(s) | Observed RED marker | What it ACTUALLY proves | Restore |
|---|---|---|---|---|---|
| 1 | **AC6 (a)** — the classifier exception DEFAULTS to `none` (the production revert of the park) | `tests/unit/test_memoir_v2_jev_classifier.py::TestCandidateParkingInJevLedger::test_classifier_failure_parks_candidate_with_classification_null` | `AssertionError: assert 'suppressed' == 'pending'` (:241) | the production classifier-failure path PARKS (never defaults to `none`) | sha256-identical (`fbf22ad83d53`) |
| | | `…::TestCandidateParkingInJevLedger::test_classifier_failure_retries_and_terminalizes_at_max_attempts` | `AssertionError: assert 'suppressed' == 'pending'` (:264) | the retry ladder counts attempts and reaches `terminal:gate_unavailable` at 5 | sha256-identical |
| | | `…::TestMutationLegA::test_a_failing_classifier_parks_the_candidate_and_writes_no_version` | `AssertionError: assert 'suppressed' == 'pending'` (:339) | the park is durable AND no persona version is drafted for a failed classification | sha256-identical |
| 2 | **AC6 (b)** — `_require_human_lane` removed from the production `approve()` | `tests/unit/test_memoir_v2_persona_proposal.py::TestMutationLegB::test_the_production_approve_refuses_a_non_session_actor` | `Failed: DID NOT RAISE <class 'daemon.services.prompt_version_service.HumanLaneRequiredError'>` (:177) | the PRODUCTION `approve()` refuses a non-session actor (no subclass, no mutant) | sha256-identical (`b3b7abe8ff85`) |
| | | `…::TestHumanLaneEnforcement::test_approve_with_bearer_lane_raises_human_lane_required_error` | `Failed: DID NOT RAISE <class '…HumanLaneRequiredError'>` (:142) | the refusal is fail-closed — the version stays `pending_approval` | sha256-identical |
| 3 | **AC6 (c)** — the route stops mapping `HumanLaneRequiredError` to 403 | `tests/unit/test_memoir_v2_persona_router.py::TestMutationLegC::test_bearer_approval_is_refused` | `assert 500 == 403` (:391) | the HTTP surface maps the refusal to 403 `HUMAN_LANE_REQUIRED` | sha256-identical (`b4943fe42e67`) |
| 4 | **C1** — the bare composition-budget break (no ledger row — the pre-fix shape) | `tests/unit/test_memoir_v2_persona_composition.py::TestCompositionBudgetPerPass::test_a_budget_deferred_candidate_is_parked_and_picked_up_next_pass` | `assert None is not None` (:276) | a candidate the budget never reached is PARKED — without the row it is LOST from the JEV lane forever | sha256-identical (`fbf22ad83d53`) |
| | | `tests/unit/test_memoir_v2_persona_composition.py::TestRaisePathParkSemantics::test_the_gate_error_marker_and_the_budget_deferral_marker_stay_distinguishable` | `assert None is not None` (:571) | **THE ROW-4 / ROW-4b UNION (ΔF5 — the true membership is 2 NODES, not 1):** the exact-shape revert (the park removed) reddens this node AND the row-4 node above. The other four raise-path legs stay GREEN under this recipe — the raise path is journaled by the handler, NOT by the park — so the two recipes are INDEPENDENT. | sha256-identical (`641484ebe9c0`) |
| 4b | **C1(b)** — the park is skipped when NO candidate was processed (the failure path) | `tests/unit/test_memoir_v2_persona_composition.py::TestRaisePathParkSemantics::test_the_gate_error_marker_and_the_budget_deferral_marker_stay_distinguishable` | `assert None is not None` (:571) | **THE NODE CHANGED (ΔF1):** the leg this recipe named (`test_a_candidate_that_raises_is_parked_not_lost`) **RATIFIED the stall** — it asserted only `status=='pending'` for a raising candidate, so the DISCARDED classification, the FROZEN attempt counter and the HEAD-OF-LINE STALL all passed. It is REPLACED by `TestRaisePathParkSemantics` (5 legs). The property this recipe proved (the deferral marker still lands on a candidate the budget never reached) is now proved by the marker-distinguishability leg; the RAISE path has its own recipe (row 11). | sha256-identical (`641484ebe9c0`) |
| 5 | **C3(a)** — the SUPPRESSED classes draft + submit (the suppression rule removed) | `tests/unit/test_memoir_v2_jev_classifier.py::TestSuppressionRule::test_a_reinforcement_candidate_never_creates_or_submits_a_version` | `AssertionError: assert 'drafted' == 'suppressed'` (:382) | `reinforcement` NEVER creates or submits a version (V2-D 5.2) | sha256-identical |
| | | `…::TestSuppressionRule::test_a_trivial_candidate_never_creates_or_submits_a_version` | `AssertionError: assert 'pending' == 'suppressed'` (:407) | the same for the heuristic `none` path | sha256-identical |
| 6 | **C3(b)** — the heuristic result is IGNORED (the LLM is called for every candidate) | `tests/unit/test_memoir_v2_jev_classifier.py::TestHeuristicOrdering::test_a_trivial_candidate_makes_zero_llm_calls` | `assert ['You are the…r backticks.'] == []` (:440) | the rule filter runs FIRST — a trivial candidate makes ZERO LLM calls (V2-D 5.1) | sha256-identical |
| | | `…::TestHeuristicOrdering::test_a_verbatim_candidate_makes_zero_llm_calls` | `assert ['You are the…r backticks.'] == []` (:460) | the same for the verbatim-overlap path | sha256-identical |
| 7 | **F1** — the failover arguments REVERSED (the pre-fix shape) | `tests/unit/test_memoir_v2_jev_classifier.py::TestLocalFallbackLane::test_the_local_fallback_lane_calls_the_provider` | `AttributeError: 'function' object has no attribute 'get'` | the gate's OWN failover lane is wired with the right argument order | sha256-identical |
| 8 | **F2(a)** — `httpx_timeout` called with the keyword it does not accept | (the same node) | `TypeError: httpx_timeout() got an unexpected keyword argument 'connect'` | the local lane calls the house timeout helper correctly | sha256-identical |
| 9 | **F2(b)** — the per-call timeout is HARD-CODED (it stops riding the knob) | (the same node) | `assert 45.0 == 12.5` (:619) | the local lane's timeout RIDES `call_timeout_s` (no second copy of the truth) | sha256-identical |
| 10 | **PREC** — the precedence resolution removed (a multi-label response is refused) | `tests/unit/test_memoir_v2_jev_classifier.py::TestPrecedenceResolution::test_a_multi_label_response_resolves_by_precedence` | `JevClassificationError: Classification "['new_rule', 'conflict']" not in ('conflict', 'boundary_change', 'new_rule', 'reinforcement', 'none')` | a multi-label response resolves by the documented precedence | sha256-identical |
| | | `…::TestPrecedenceResolution::test_a_compound_string_response_resolves_by_precedence` | `JevClassificationError: Classification 'boundary_change, new_rule' not in (…)` | the same for a compound string | sha256-identical |
| 11 | **ΔF1** — the bare finally-park on the RAISE path (the pre-fix control flow: the raise ESCAPES the candidate loop and the `finally`-park writes `classification=NULL, attempts=0, last_error='deferred:composition_budget'`) | `tests/unit/test_memoir_v2_persona_composition.py::TestRaisePathParkSemantics::test_a_raising_candidate_is_journaled_as_a_gate_error_not_a_deferral` | `RuntimeError: the proposal store exploded` (:389) | the raise is CONTAINED and JOURNALED as `gate_error: …` (never the deferral marker), with the attempt consumed | sha256-identical (`641484ebe9c0`) |
| | | `…::TestRaisePathParkSemantics::test_a_raising_candidate_terminalizes_by_pass_five` | `RuntimeError: the proposal store exploded` (:428) | the retry ladder TERMINALIZES (the attempt counter advances to `MAX_ATTEMPTS`) | sha256-identical |
| | | `…::TestRaisePathParkSemantics::test_a_later_clean_candidate_still_drafts_in_the_same_pass` | `RuntimeError: the proposal store exploded` (:465) | NO HEAD-OF-LINE STALL — the pass continues and a later CLEAN candidate drafts in the SAME pass | sha256-identical |
| | | `…::TestRaisePathParkSemantics::test_a_raise_after_classification_preserves_the_classification` | `RuntimeError: the proposal store exploded` (:511) | the PAID classification is PRESERVED — the retry does NOT re-pay the classifier (1 classify call across 2 passes) | sha256-identical |
| | | `…::TestRaisePathParkSemantics::test_the_gate_error_marker_and_the_budget_deferral_marker_stay_distinguishable` | `RuntimeError: the proposal store exploded` (:563) | a gate error and a budget deferral are DISTINGUISHABLE ledger states | sha256-identical |
| 12 | **ΔF3** — the gate's per-call timeout HARD-CODED (it stops riding the knob) | `tests/unit/test_memoir_v2_jev_classifier.py::TestProductionWiring::test_the_gate_classifies_through_the_reflection_services_caller` | `AssertionError: assert 45.0 == 7.25` | the gate's timeout RIDES the reflection service's `call_timeout_s` — driven with NON-DEFAULT settings (`7.25`), so the hard-coded 45.0 is VISIBLE (pre-fix the same mutation was INVISIBLE: 0 red nodes) | sha256-identical (`79168a361a20`) |

**NO RECEIPT CLAIMS A PROPERTY IT DOES NOT EXERCISE.** Rows 1-3 are
now production reverts (they were receipts of nothing before). The
"what it actually proves" column is the receipt's own claim, and every
row's RED marker was observed in THIS worktree on 2026-11-10.

### 15.3 Test Pack Pins (Measured with `--collect-only`, Same-Commit Registered)

The four phase-5 packs, re-measured in this fix round (2026-11-10).
Every class in every module is now named EXPLICITLY in the pack's `-k`
selector, and the node-count pins move in the SAME commit as the legs
(the LANE RULE of §14):

| Pack | Before (phase 5) | After (the fix round) | After (THIS delta) | Verdict |
|---|---|---|---|---|
| `memoir_v2_jev_classifier_test` | 14 | **26** | **26** (ΔF3 strengthens a pin, the node count does not move) | PASS |
| `memoir_v2_persona_composition_test` | 5 | **7** | **11** (the ratifying raise-path leg is REPLACED by 5 legs) | PASS |
| `memoir_v2_persona_proposal_test` | 5 | **5** | **5** | PASS |
| `memoir_v2_persona_router_test` | 12 | **15** | **15** | PASS |

The class census (measured with `--collect-only -q`):

| Module | Classes (node counts) |
|---|---|
| `test_memoir_v2_jev_classifier.py` | `TestJevHeuristicFilter` (6) · `TestJevLlmClassifier` (4) · `TestCandidateParkingInJevLedger` (3) · `TestHeuristicOrdering` (2) · `TestLocalFallbackLane` (2) · `TestPrecedenceResolution` (5) · `TestSuppressionRule` (2) · `TestMutationLegA` (1) · `TestProductionWiring` (1) |
| `test_memoir_v2_persona_composition.py` | `TestPersonaComposition` (1) · `TestUnbuildableDiffParking` (3) · `TestCompositionBudgetPerPass` (3) · `TestRaisePathParkSemantics` (5) |
| `test_memoir_v2_persona_proposal.py` | `TestProposalCreationAndIsolation` (1) · `TestHumanLaneEnforcement` (3) · `TestMutationLegB` (1) |
| `test_memoir_v2_persona_router.py` | `TestMemoirPersonaRouter` (11) · `TestProductionRepositoryResolution` (3) · `TestMutationLegC` (1) |

### 15.4 The PRECEDENCE AMBIGUITY — the plan's comparator, and the caller's ruling

**THE AMBIGUITY (recorded for the ruling).** The plan writes the gated
classes as `conflict > boundary_change > new_rule`
(`v2-phase5-plan.md` §1.2, §3.1). That reads as a taxonomy PRIORITY,
but the plan never defines a COMPARATOR: nothing said what an
ambiguous or multi-label classifier response RESOLVES to. Before this
fix round the production parser accepted ONE label and raised
`JevClassificationError` for anything else — so a model answering
`["conflict", "new_rule"]` was PARKED (a candidate the gate could not
adjudicate), and the plan's `>` was never exercised by ANY code path.

**THE INTERPRETATION IMPLEMENTED.** A documented precedence
RESOLUTION now applies to the classifier output:

* the comparator is the taxonomy order —
  `conflict > boundary_change > new_rule > reinforcement > none`
  (`JEV_CLASSIFICATION_PRECEDENCE`);
* a response carrying MORE THAN ONE known label resolves to the FIRST
  match in that order;
* a response carrying exactly ONE known label is unchanged;
* a response carrying NO known label is still a FAILURE
  (`JevClassificationError`) — the candidate is PARKED, never defaulted.

**WHY THE GATED CLASSES OUTRANK THE SUPPRESSED ONES.** The plan's
`>` only orders the three gated classes; it says nothing about a
gated class versus a suppressed one. This implementation resolves a
gated class ABOVE a suppressed one, and the reason is V2-38: the
lane's constitution forbids a SILENT loss of a candidate. A model that
says "this contradicts an existing rule" and also mentions "none" has
reported a conflict; downgrading that to a suppressed class would drop
a real identity change without a trace — exactly the failure mode the
ledger was built to prevent. The direction chosen therefore favours
VISIBILITY over persona-churn restraint. The alternative reading (a
suppressed class masks a gated one) is defensible on churn grounds and
is the caller's to overrule.

**THE CALLER'S RULING IS REQUESTED ON:** (a) the gated-over-suppressed
direction, and (b) whether the resolution should stay INSIDE the
classifier parser (as implemented) or move to the taxonomy itself.

### 15.5 The CARRY — F4 (house-inherited, fail-closed, NOT changed here)

**F4: `auth_method='internal'` approve returns 500.** The actor's
anti-forgery `ValueError` escapes outside the router's `try` block
(`daemon/routers/memoir.py:158-165`). This shape is **HOUSE-INHERITED**
— `daemon/routers/prompts.py:635` has the identical shape — and it is
**fail-closed**: the version stays `pending_approval` and the persona
pointer does not move. Changing shared router behavior is out of this
lane's scope, so the defect is CARRIED and DISCLOSED here, not fixed.

**THE PRODUCTION WIRING VERDICT (F1 / F2 / F3).** The gate DOES
classify in production today: the reflection service constructs
`MemoirPersonaGateService` and injects **its own** caller
(`daemon/services/memoir_reflection_service.py:384-393`), so the gate
never uses its own fallback lane in production — which is why the
doubly-broken local lane was dormant. The local lane is now FIXED and
exercised by `TestLocalFallbackLane`, and the wiring itself is pinned
by `TestProductionWiring` (the gate's `_llm_caller` IS the reflection
service's caller object, and the gate rides the reflection service's
`call_timeout_s` knob — with NON-DEFAULT settings, so the pin
discriminates: ΔF3 / §15.2 row 12).

### 15.6 THE PLAN DEFECTS — three omissions the plan still carries

These are the plan-side gaps this lane found. They are recorded here
because the fix belongs to the PLAN, not to the code: a plan that
leaves a disposition undefined will keep permitting a silent loss.

1. **THE COMPARATOR (this one already existed — §15.4).** The plan
   writes the gated classes as `conflict > boundary_change > new_rule`
   — a TAXONOMY PRIORITY — and never defines a COMPARATOR: nothing said
   what an ambiguous or multi-label classifier response RESOLVES to.
   The implementation supplied one (`JEV_CLASSIFICATION_PRECEDENCE`,
   §15.4) and the caller's ruling is requested there.
2. **THE BUDGET-DEFERRAL DISPOSITION (NEW — ΔF4).** The plan's §3.3
   (Persona Document Composition Specification) and its task T5.3 fix
   the per-pass composition ceiling
   (`MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS = 1`) and the
   unbuildable-diff parking, but they **never state the DISPOSITION of
   a candidate the composition budget did NOT reach**. §3.3's
   "Once-Marker Discipline (ND-14)" says what happens to a candidate
   that WAS evaluated (`status='drafted'` or `'suppressed'`), and its
   "Unbuildable Diff Handling" says what happens to a candidate whose
   diff failed — but a candidate the budget never reached falls through
   BOTH. **That omission is what permitted the silent loss (C1):** with
   no disposition required, the implementation ended the loop with a
   bare `break` and the candidate simply VANISHED from the JEV lane (no
   ledger row, no re-selection — the ledger is the only re-selection
   source). **THE PLAN SHOULD REQUIRE:** every candidate a pass does
   not evaluate MUST land a ledger row stating its disposition (the
   budget-deferral marker), so the ledger stays the single re-selection
   source. The same requirement now covers the RAISE path (ΔF1): a
   candidate whose evaluation FAILED must land a row carrying a
   FAILURE marker (`gate_error: …`), never the deferral marker.
3. **THE MUTATION-APPLICATION SITE (NEW — ΔF4).** AC6 (Mutation) reads:
   *"Defaulting to `none` on classifier exception flips AC2 RED;
   removing `_require_human_lane` from `approve()` flips AC4 RED;
   approving via bearer token flips AC5 RED."* It names the MUTATION
   but never **WHERE the mutation is applied** — and the two
   implementations that satisfy those words are not equivalent. A leg
   that asserts a property of a subclass the TEST ITSELF defines (a
   self-mutating mutant) proves NOTHING about production: it passes
   whatever the production code does, so it cannot fail. **THE PLAN
   SHOULD REQUIRE PRODUCTION-REVERT recipes:** the leg asserts
   PRODUCTION behavior, and the mutation is applied to the PRODUCTION
   file, run, observed RED, and restored byte-identically (§15.2 rows
   1-3 are the corrected form of that requirement; rows 11-12 are this
   delta's).

### 15.7 THE DISCLOSED CARRIES (this delta)

**ΔF6 — THE GATE'S PER-CALL TIMEOUT IS BOOT-FROZEN.** The gate is
constructed with `call_timeout_s=float(self._settings.call_timeout_s)`
(`daemon/services/memoir_reflection_service.py:394`), so the value is
copied at CONSTRUCTION time. The worker re-resolves the settings every
tick and calls `apply_settings(...)` — which the SERVICE adopts but the
gate does not. MEASURED (2026-11-10, this worktree):

```
at construction:      service=45.0  gate=45.0
after apply_settings: service=7.0   gate=45.0
gate rides the knob?  False
```

**THE IMPACT IS ≈ 0 TODAY** — in production the lane rides the
reflection service's injected caller (the gate's own fallback lane,
which is the only consumer of `_call_timeout_s`, is dormant). But
"one truth per knob" is only HALF true: the gate holds a snapshot of
the knob, not the knob. **NOT CHANGED HERE** — live-knob propagation
(the worker re-resolving the gate's timeout, or the gate reading the
service's settings per call) is a bigger change than this commit's
scope, and it is DISCLOSED rather than silently inherited.

**THE RAISE PATH'S COMPOSITION CALL IS NOT COUNTED AGAINST THE
PER-PASS CEILING.** A raise that happens AFTER the composition
(`create_draft` / `submit` failing) has already spent one composition
LLM call, but the raise path never returns an outcome, so the counter
`composition_calls` is not incremented — and because the raise path
CONTINUES the loop (instead of taking the post-increment `break`), the
per-pass ceiling can be overshot by the number of FAILING candidates in
that pass. MEASURED on the fixed shape (8 passes over 3 candidates whose
proposal store always fails): 15 composition calls, i.e. 3 per pass
while the ceiling is 1. On the HAPPY path the ceiling holds (the
post-increment `break` stops the loop); the overshoot is specific to a
FAILING store — and a failing store is exactly when the pass must keep
going rather than stall. The overshoot is bounded by the candidate
count (≤ 5 re-selected + ≤ 4 new). **NOT CHANGED HERE** (the fix is a
budget-accounting redesign — the ceiling's PRE-check is scoped to the
retry path, so a FRESH candidate is only stopped by the post-increment
`break` — which is beyond this commit's scope) — DISCLOSED here so the
next reader does not read the ceiling as a hard bound.



---

## 16. Phase 6 — Keepsake Proposals & Wake-Refresh (implementation notes + AC6 receipts)

Phase 6 delivers the LAST build phase: `MemoirKeepsakeProposalService`
(qualification, once-ever staging, the measured `[PROPOSALS]` briefing
zone, the 1/day enqueue bridge) and the wake-refresh context seam
(`memoir_refresh`), per plan `v2-phase6-plan.md` (§3.1, §3.2, T6.1–T6.6).

### 16.1 What shipped (file census)

| File | Change |
|---|---|
| `daemon/services/memoir_keepsake_proposal_service.py` | NEW — the service (qualification, once-ever staging, zone selection, the bridge, `process_pass`) |
| `daemon/repositories/memoir/models.py` | `PROPOSAL_LINE_TEMPLATE`, `PROPOSALS_ZONE_HEADER`, `MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET` (1000), `MEMOIR_PROPOSALS_ZONE_MAX` (3), `MEMOIR_PROPOSAL_REASON_CHAR_CAP`, `render_proposal_line` |
| `daemon/services/memoir_briefing.py` | `admit_proposal_lines` (the zone's own measured admission), the ladder in `render_briefing_body`, `_render_briefing_body_for` (the ONE shared render path used by BOTH the briefing and the refresh), `_read_proposal_lines`, `escape_for_context_block` (exported spelling) |
| `daemon/services/context_messages.py` | `CONTEXT_KIND_MEMOIR_REFRESH`, `MEMOIR_REFRESH_BLOCK_TITLE`, `MEMOIR_REFRESH_SOURCE`, `_assemble_memoir_refresh`, `_maybe_memoir_refresh_message`, the `wake_source` parameter, the `memoir_refresh` branch in `_stable_id_for` + the UPDATED closed-list ValueError message, the refresh evaluation at BOTH assembly sites, `_make_context_message(source=…)` |
| `daemon/services/instance_messaging.py` | the `wake_source=message_source` thread at the dispatch site |
| `daemon/services/memoir_reflection_service.py` | the `keepsake_proposal_service` seam + the proposal phase in `_run_pass` (isolated, never fails the pass) |
| `daemon/tools/memoir_knob.py` | `MemoirWakeRefreshKnobState`, `resolve_memoir_wake_refresh_knob`, `memoir_wake_refresh_enabled`, `_emit_wake_refresh_once` |
| `daemon/config.py` | `MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR`, `MemoirWakeRefreshConfig`, the `memoir_wake_refresh` section on `Config` |
| `daemon/api.py` | the proposal service constructed against the shared engine and passed to the reflection service (advisory-failure wrapper) |
| `tests/unit/test_memoir_keepsake_proposals.py` | NEW (30 nodes) |
| `tests/unit/test_memoir_wake_refresh.py` | NEW (40 nodes) |
| `test/packs/memoir_v2_keepsake_proposals_test.sh` | NEW pack (pin 30) |
| `test/packs/memoir_v2_proposal_notification_test.sh` | NEW pack (pin 9) |
| `test/packs/memoir_v2_wake_refresh_test.sh` | NEW pack (pin 40) |
| `tests/integration/test_lcancheck_family_separation.py` | the `_STABLE_ID_SUPPORTED_KINDS` set grew by `memoir_refresh` (the closed list grew by exactly one) |

### 16.2 THE PLAN-DEFINED GAPS (recorded, not improvised around)

The plan names an "enduring significance" HEURISTIC but never defines
it, and it names no substance floor, no per-pass staging cap and no
proposal-line reason bound. These are the definitions this build
adopted, each recorded so a reviewer can overrule them explicitly:

| Gap | The rule adopted | Where |
|---|---|---|
| the "enduring significance" heuristic | NOT a keepsake, NOT superseded, and a collapsed body of ≥ 80 chars (a terse line scored 9 is an intensity signal, not an enduring one) | `MemoirKeepsakeProposalService.is_enduring` / `MEMOIR_PROPOSAL_MIN_BODY_CHARS` |
| the per-pass staging cap | 10 candidates per pass (a large backfill must not flood the ledger/zone in one tick) | `MEMOIR_PROPOSAL_STAGE_CAP_PER_PASS` |
| the proposal reason | COMPOSED from row facts (`enduring significance: effective poignancy N/10[, K star(s)]`), capped at 160 chars — never model text (there is NO LLM call in this phase) | `compose_reason` / `MEMOIR_PROPOSAL_REASON_CHAR_CAP` |
| the bridge message | the plan's §3.1 sentence verbatim, carrying the title | `_bridge_message` |
| no active ari instance | the row stays `pending` (slot preserved); NO `failed` transition is written — a missing instance is a RETRYABLE condition, not a proposal failure | `notify_once` |

The plan's `notification_status` vocabulary (4 tokens) is untouched: this
phase writes `pending` and `enqueued` only; `sent` stays reserved and
`failed` stays available but is NOT used for transient bridge failures
(see the row above) — the same choice V2-23 → V2-40's invariant implies.

### 16.3 THE §A6 ADMISSION LADDER — the code cross-references the quote

`render_briefing_body` implements the ladder and its docstring CARRIES
the verbatim V2-D 6.5 quote (cross-referenced, never paraphrased):

> with explicit rung order **fit dynamic → trim dynamic → trim proposals (drop weakest first) → rung-2 withhold**

The measured basis is `stable_chars` (the escaped STABLE zone alone) for
the rung-2 withhold; the total-based trims measure the COMPOSED body.
Both readings are the plan's own (§V2-D 6.5's measurement-basis bullet),
and the reachability claim is pinned by
`TestAdmissionLadder::test_the_total_based_trim_is_reachable_within_1000_chars_of_the_ceiling`:
because the zone is admitted at ≤ 1,000 chars, the total-based proposals
trim can fire only when `stable + dynamic` lands within the zone's own
bound of 24,000.

The zone's own admission (`admit_proposal_lines`) measures the SHIPPED
bytes — each line escaped first, the header wrapper INCLUDED — and stops
at the first line that does not fit (the lines arrive strongest-first, so
admitting a later, weaker line while dropping a stronger one would invert
the drop-weakest-first order the trim rung owns).

### 16.4 THE AC6 MUTATION RECEIPTS (production reverts, byte-identical restores)

Every recipe below was applied to the PRODUCTION file in this worktree on
2026-11-10, run, observed RED, and restored **byte-identically**
(`sha256sum -c` before/after == OK for all three touched files). The
ambient `ENSEMBLE_*` knob family was scrubbed per-variable; no
`POSTGRES_*` variable was read (throwaway SQLite only).

| # | Recipe (the PRODUCTION revert) | Node(s) that went RED | Observed RED marker | Restore |
|---|---|---|---|---|
| **(a) AC6(a)** | `mark_enqueued(...)` moved ABOVE `self._enqueue(...)` in `notify_once` — the slot consumed BEFORE the enqueue is accepted | `TestEnqueueBridge::test_a_failed_enqueue_leaves_the_row_pending` | `AssertionError: assert 'enqueued' == 'pending'` (the row was consumed despite the failed bridge) | `sha256sum -c` OK |
| **(b) AC6(b)** | the trim rungs SWAPPED in `render_briefing_body` — proposals trimmed before dynamic | `TestAdmissionLadder::test_rung_2_trim_dynamic_before_any_proposal_is_dropped` | `AssertionError: assert 5 == (5 - 1)` — zero dynamic lines trimmed because the proposals were eaten first | `sha256sum -c` OK |
| **(c) AC6(c)** | the alarm body line REPLACED with `[SYSTEM CONTEXT: Memoir Refresh] MUTANT(c) alarm body` in `self_alarm_service.compose_alarm_message` | `TestStampPurity::test_the_alarm_template_is_untouched_by_the_refresh` | `AssertionError: assert '[SYSTEM CONTEXT: Memoir' not in message` | `sha256sum -c` OK |
| **(d) AC6(d)** | the once-ever PRE-CHECK deleted from `process_pass` (`if False:`) | `TestOnceEver::test_a_second_pass_never_re_proposes_the_same_episode` | `AssertionError: the ONCE-EVER skip must happen BEFORE any staging attempt — stage_candidate was called for ['8a50…']` | `sha256sum -c` OK |

**WHY (d) IS NOT VACUOUS (the phase-5 lesson, applied).** The unique
constraint would ALSO swallow the duplicate write as the same skip — a
naive `staged == 0` assertion would stay GREEN with the pre-check
deleted, because the DB does the same job. The leg therefore SPIES on
`stage_candidate` and asserts the second pass makes ZERO staging
attempts: the once-ever rule is a READ-side decision (V2-36), and the
constraint is only the racing fallback.

Files touched by the recipes (sha256 before == after):
`memoir_keepsake_proposal_service.py`, `memoir_briefing.py`,
`self_alarm_service.py`.

### 16.5 Pack pins (measured with `--collect-only`, same-commit)

| Pack | Nodes | Selector |
|---|---|---|
| `memoir_v2_keepsake_proposals_test` | **30** | `TestQualification`, `TestOnceEver`, `TestProposalsZoneSelection`, `TestProposalsZoneRender`, `TestAdmissionLadder`, `TestReflectionPassIntegration` |
| `memoir_v2_proposal_notification_test` | **9** | `TestEnqueueBridge`, `TestNoPinAuthority` |
| `memoir_v2_wake_refresh_test` | **40** | `TestStableIdRegistration`, `TestRefreshSeam`, `TestWakeRefreshKnob`, `TestStampPurity`, `TestWakeSourceThreading`, `TestStructuralSupersede` |

Every class in both new modules is selected by exactly one pack (the lane
rule: a leg no pack selects is a leg the gate cannot see).

### 16.6 Disclosures

1. **THE GATE ORDER IS A CONJUNCTION — cost-ordered, not prose-ordered.**
   `_assemble_memoir_refresh` evaluates `wake_source == "scheduler"`
   FIRST, then the knob, then the instance-row read. The plan's §3.2 prose
   lists the agent gate before the `wake_source` gate, but the three
   conditions are ANDed, so no ordering changes the outcome — and the
   chosen order keeps every non-scheduler assembly (i.e. nearly all of
   the daemon's traffic) entirely DB-free. Pinned by
   `TestRefreshSeam::test_a_non_scheduler_assembly_costs_no_db_read`.
2. **NO `failed` TRANSITION ON BRIDGE FAILURE.** See §16.2 — a transient
   bridge failure is retryable, so the row stays `pending`. Writing
   `failed` would convert a momentary loop outage into a permanently
   dead proposal row (the vocabulary allows it; the plan's §3.1 wording
   says "status remains 'pending'").
3. **THE REFRESH IS NOT GATED ON THE v1 BRIEFING KNOB.** The lane's own
   `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` decides. That is the plan's
   gate chain verbatim; the two lanes are independent by construction
   (pinned by `test_a_briefing_knob_off_does_not_disable_the_refresh`).
4. **NO LLM CALLS in phase 6** — heuristic + render only. Pinned at the
   AST level (`TestNoPinAuthority::test_the_service_makes_no_llm_calls`).
5. **NO PIN AUTHORITY** — the service holds no reference to
   `set_pin`/`clear_pin`/`memoir_pin` (AST census, same class). Pinning
   remains the v1 `memoir_pin(id, reason, actor)` confirmation path.
6. **The proposal zone is read through the CALLER'S engine** — the
   briefing builds it via `_guarded_read`, so a ledger failure degrades
   ONLY the zone (the persona / keepsakes / dynamic zones still ship).
