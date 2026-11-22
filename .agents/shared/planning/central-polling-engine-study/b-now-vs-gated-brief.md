# Decision Brief — Question #1: Build the Subset Engine (B) NOW, or GATE-HELD?

**For:** Emperor architecture discussion (deciding now) · **Date:** 2026-11-21
**Source:** frozen study `decision-report.md` (§B.2, §C, §D, §E) + `census-periodic.md`, `census-events.md`, `liveness-evidence.md`. No new investigation; one read-only sizing sanity-check disclosed in §1. Citation tiers per study header: **[C]** census @ `bf4bfde6e`, **[L]** reviewer-verified @ v0.31.22 tag.

---

## 1. WHAT B ACTUALLY IS (frozen §B.2, component terms)

One lifespan asyncio task owning a registry of jobs; per job an explicit state machine, a strategy-selected execution class, and a durable row. In-repo precedent: this is the **maintenance-tier pattern (maintenance.py, 2,772 lines [C]) generalized + hardened + applied estate-wide** — not a new pattern, a new guarantee set on a known one.

| # | Component | What it is (mechanism) | Effort (h) | Conf. |
|---|---|---|---|---|
| 1 | **JobSpec + registration API** | `register({id, trigger: Interval(seconds, jitter, first_tick_immediate\|offset) \| Event(kind,filter), max_runtime_s, timeout_policy cancel\|extend, retry{max,backoff_base,cap}, priority 0-9, concurrency_class, kill_switch_key?, dependencies:[getters]})`; validation (exactly one trigger, max_runtime>0); `engine_jobs` table + migration file (house rule: 1 SQL file/table) + RAM cache; dynamic re-reg = upsert by id, version bump, cancel in-flight, re-arm — no restart | 20 (14–28) | M |
| 2 | **Execution model** | Single engine tick loop; dispatch by concurrency class: MAIN_LOOP (in-memory only) / DB_OFFLOAD (`asyncio.to_thread`, house off-loop discipline v0.27.14) / DEDICATED_EXECUTOR (small pool); cancellation + graceful stop | 28 (20–36) | M |
| 3 | **Single-flight** | Per-job `asyncio.Lock` keyed by id, acquired before dispatch, held across executor threads, released in `finally`; concurrent due → `SKIPPED_OVERLAP` (counted skip, never queued). Precedent: `MaintenanceRunLock` (126 lines [C]) | 8 (6–10) | H |
| 4 | **Honest state machine + persistence** | `IDLE→DUE→RUNNING→{SUCCEEDED, SKIPPED_<reason>, FAILED_BACKOFF, TIMED_OUT}`; **every terminal transition stamps** `last_run_at/last_outcome/last_skip_reason`; DB source-of-truth (`engine_runs` append-only + `engine_jobs` mutable) projected into the **converged job_metrics family** (extends landed v0.31.11 `last_success_ts`/`consecutive_skips` [L] with `next_due, last_duration, last_outcome, skips_by_reason, dead-flag`) | 20 (14–28) | M |
| 5 | **Trap-impossibility** | (a) *Permanently-due impossible:* stamp-on-skip + due ⇔ `now − last_run_at ≥ interval` regardless of outcome — due-state cannot strand (the maintenance.py:260-269 [C] trap was skip-before-stamp). (b) *Born-dead impossible:* dependencies resolved by **calling the getter at first tick** (never captured in `__init__` — the OrphanWatcher api.py:1920→:2972→:5710 trap [L]) + **boot wiring assertion that fails LOUD** + `dead=true` meta-flag when zero runs in 2× interval | 10 (8–14) | H |
| 6 | **Kill switches + fail-to-legacy** | Per-job `kill_switch_key` (env/config) + engine-wide `ENSEMBLE_ENGINE_ENABLED` + `ENSEMBLE_ENGINE_SHADOW_MODE`; engine failure posture = fail-to-legacy, never fail-stuck | 8 (6–10) | H |
| 7 | **Boot phases + staggering** | Classes `BOOT_SWEEP` (awaited, deadlined) / `BOOT_PASS` / `FIRST_TICK_IMMEDIATE` / `FIRST_TICK_DELAYED(+offset)`; per-job first-tick jitter desynchronizes the 300s club; the ~11 awaited passes keep deadlines | 10 (8–14) | M |
| 8 | **Config continuity** | `SERVICES_*` knobs → `engine.jobs.<id>.interval_seconds`, defaults preserved (12 in-scope sweep knobs of the 34 `interval_seconds` knobs in config.py [C]); dead-knob cleanup list rides along | 10 (8–14) | M |
| 9 | **Dual-path harness + pilot** | `SERVICES_<MODULE>_USE_ENGINE` flag; both paths compiled, flag selects which starts the loop — **rollback is one flag, not a code change**; pilot = fixed OrphanWatcher | 20 (14–28) | M |
| 10 | **Tests** | Unit pack + mutation-verified state-machine regression pins + PACKS.md registration (house standard) | 28 (20–40) | M |
| | **TOTAL (B base)** | | **≈162 h (118–222)** | **L-M** |

**Assumptions:** one senior dev lane, focused hours; PG + migration ceremony included; **excludes** event-substrate plumbing (GAP-13 outbox wiring, GAP-14 depth emitters — separate work under every option) and **excludes** per-module migrations beyond the pilot (**marginal ≈4–10 h/module** once B exists, conf L). Elapsed ≈ **5–8 weeks** incl. plan-approve + promote ceremonies. Sizing sanity-check (disclosed): `wc -l` on the precedent services at `bf4bfde6e` — maintenance.py 2,772; sweeps 250–483; run-lock 126 — B is a generalization of a pattern this codebase already contains, which is why the estimate is M-confidence and not L. For contrast, the **C+D layer ships for ≈82 h (58–112)**: helpers (honest-state, supervised, jitter, single-flight, assertion-getter) ~32 h; job_metrics convergence ~12 h; the two live-defect fixes ~12 h; maintenance live-verification ~6 h; tests/packs ~20 h.

## 2. WHAT BUILD-NOW BUYS ON DAY ONE (vs each measured pain)

| Census pain | C+D fixes it WITHOUT B? | B-only? |
|---|---|---|
| 4 unsupervised loops die permanently on one exception (JobLock [L], SourcesGc, TmpImage, upgrade-journal [C]) | **YES** — `supervised()` wrapper + max-runtime converts PEXIT to supervised restart with backoff, per module, at adoption | No — B only adds *uniform enforcement* of the wrapper |
| Born-dead wiring trap (OrphanWatcher [L]) | **YES** — late-bind fix + assertion baked into the helper | No |
| Starvation invisibility / counter-lies (6.54-day class, historical [L]) | **YES** — job_metrics convergence + honest-state helper (v0.31.11 already proved the pattern in prod) | No |
| 300s phase-lock club | **YES at adoption** — jitter helper per adopting module | **Partially** — B guarantees estate-wide staggering incl. boot-pass orchestration |
| Boot thundering-herd (~11 awaited passes) | **Partially** — first-tick jitter only; awaited passes stay as-is | **YES** — boot-phase classes + deadlines (but **zero measured pain**: no SLO exists, no breach on record) |
| Voluntary-adoption discipline (GAP-16 uneven switches, dead knobs, two dormant-trapped mechanisms that *looked* healthy [C][L]) | **NO — this is the one** | **YES** — registration *carries* the contract (honest state, audit, kill switch, max-runtime); no adopter can silently opt out of the guarantees |
| One config surface (SERVICES_* unification) | No — convention only | **YES** — but collecting it means touching every migrated module anyway |
| Event triggers ("trigger on which event") | No | **Also no** — generality needs substrate work (GAP-13/14/15) that is NOT in B's base cost; terminalization consumption is deliverable under both |

**Honest summary: the B-only list is short — enforced uniform contract (the adoption-discipline kill), guaranteed estate-wide staggering incl. boot orchestration, real config unification — and none of those three has measured pain today** (no contention metrics, no boot SLO, no breach on record — council finding, study §D.4).

## 3. WHAT BUILD-NOW COSTS AND RISKS

- **Effort:** ≈162 h (118–222) base + 4–10 h/module marginal × ~20 engine-eligible modules ≈ **80–200 h more** in migration tail; total program ≈ **250–420 h** spread over phases.
- **New SPOF / coupling:** every migrated module depends on one runtime. **Who watches the watchman:** the census found the daemon has **no supervisor tradition** — 4 loops die today with nothing re-spawning them ([C][L]); "the engine will be supervised" is an unbuilt, unproven claim that must be built as part of B (engine-independent watchdog + fail-to-legacy), not assumed.
- **Rework risk (design meets reality):** the council's own B→C de-escalation criteria name the failure shapes — tick drift >1 interval/24h; dispatch p95 >50 ms; audit rows >0.5% of DB writes or skip-rows nobody reads; serialized queuing >+10% of cadence; any member whose contract B can't express (StalePause/RDRS thread topology is the named risk case).
- **Migration overhead:** each module = dual-path wiring + flag + pack updates + parity observation window before flag default flips (house gates: PACKS registration, mutation pins, promote ceremony).
- **The historical caution, answered head-on:** the 6.54-day starvation **was a central scheduler** (maintenance tier, v0.31.10 [L]). What makes B not-a-repeat, by design property: (1) stamp-on-skip + durable runs — the original starvation was *invisible* (skips left no telemetry; today's landed job_metrics closes exactly that [L]); (2) `dead=true` meta-liveness (zero runs in 2× interval) — the signal that flags the failure class on day 1, engine-wide; (3) due-state cannot strand (no success-gated `last_run`); (4) per-job max-runtime — a wedged job cannot block the tier (the maintenance.py:267 [C] no-timeout gap); (5) boot wiring assertion — no born-dead. **Residual B cannot eliminate:** if B's *own* tick loop wedges or dies, everything it hosts stops — the starvation shape recurs **at the engine level**, with a bigger blast radius than any single module, unless the engine-watchdog + fail-to-legacy posture is real and drilled. B concentrates risk even as it polices it.

## 4. WHAT GATE-HELD MEANS OPERATIONALLY

**Ships now (C+D, ≈82 h):** late-bind OrphanWatcher fix; bash-reaper retention lane; `supervised()`/max-runtime (kills the PEXIT class at adoption); honest-state helper; jitter (breaks the 300s club as modules adopt); **job_metrics convergence** (starvation-class visibility estate-wide); dependency assertions (born-dead class visible at boot). Pains fixed now: born-dead, PEXIT, starvation-visibility, counter-lies, unbounded registry, diagnostic cost. **B's design stays frozen and implementation-ready** (§B.2 = the build spec; nothing in C is thrown away — B uses C's primitives as internals).

**The four measured escalation triggers — detection is the C-shadow's job:**

| # | Production event that fires it | How the C-shadow sees it (metric → threshold) | Time-to-build-B once fired | Cost of being late |
|---|---|---|---|---|
| T1 | Post-jitter DB contention at 300s-coincidence windows | Converged job_metrics tick-duration clustering (six jobs' `last_duration` spiking co-incidentally) + PG lock-wait/IO in window vs off-window baseline → **≥2× baseline, ≥3 consecutive windows** | ≈5–8 wks (the 162 h + ceremony) | Contained DB contention (latency, no corruption) persists until landed |
| T2 | A `supervised()`-wrapped loop dies anyway (wrapper thesis fails) | Family counters `restarts_total{job}` escalation or **`dead=true` on a wrapped job** | ≈5–8 wks | One module runs dead — **identical exposure to today's 4 unsupervised loops, except now visible** (dead-flag) instead of silent |
| T3 | Boot wall-clock breaches an Emperor-set SLO (SLO must be SET to arm this) | `boot_epoch → readyz-green` delta (both already recorded: wedge_captures.boot_epoch, readiness state) → **breach on ≥2 consecutive boots** | ≈5–8 wks | Slow boots / longer promote windows persist |
| T4 | Event-substrate mass: per-consumer polling measurably wasteful | Empty-poll ratio (polls yielding no work ÷ total) across EventBus/state-table consumers → **≥90% at growing event volume**; honest note: precise arming needs GAP-14 depth plumbing (chicken-and-egg) — proxy: consumer count × cadence vs `event`-table volume | ≈5–8 wks **+ substrate work beyond B** | Wasted polls — the 4.6%/3.3% finding shows this is cheap today (~1.5 empty claims/sec) |

**The structural point:** all four triggers describe *contained, measurable* degradations — none is the catastrophic class — and the C-shadow keeps detection live the entire wait. The gate converts an architecture commitment into an insured bet: worst case, you land B ~6 weeks after the evidence says you needed it, with the pain visible and bounded the whole time.

## 5. HONEST BOTH-SIDES TABLE

| | Build B now | Gate-held (C+D now, B on trigger) |
|---|---|---|
| Pains fixed day one | All C+D pains **plus** the three B-only items (enforced contract, guaranteed staggering incl. boot, config unification) — but the B-only three have zero measured pain today | All *measured* pains (born-dead, PEXIT, starvation-visibility, counter-lies, retention, diagnostics); B-only pains wait for evidence |
| Risk carried | Engine-as-SPOF live **before** supervisor tradition proven; ~20-module migration tail in flight; rework risk if reality violates design assumptions | Voluntary-adoption risk (the documented failure mode) — mitigated by baking assertions/dead-flag into helpers; contained trigger-pains persist ~6 wks if a trigger fires |
| Effort now → later | ≈162 h now + 80–200 h migration tail | ≈82 h now; 162 h later **only if** a trigger fires (possibly never) |
| Reversibility | Per-module one-flag revert + engine-wide OFF; but once ~20 modules migrate, the engine is load-bearing and revert is a posture, not a footnote | Maximum: C adopters unwrap per module; B never started if never triggered |

**5-axis matrix** (1–5, higher better; council weights — Complexity .20, Scalability .20, Maintainability .25, Risk .20, Cost .15):

| Axis | B-now | Gate-held |
|---|---|---|
| Complexity | 2 (runtime + migrations now) | 4 (C layer only) |
| Scalability | 4 (estate-wide uniformity ready) | 3 (grows per-module until trigger) |
| Maintainability | 3 (one substrate IF discipline holds; migration churn ongoing) | 4 (incremental, reversible) |
| Risk | 2 (SPOF pre-proof; churn) | 4 (fail-small, insured) |
| Cost | 2 (162 h + tail) | 4 (82 h; B deferred/never) |
| **Weighted** | **2.65** | **3.80** |

*(Consistent with the study's council spread: coding model scored B-hybrid 3.45 vs C 3.25 with the margin explicitly conditional on event-triggers-in-v1; agentic model scored C+D 4.00 vs B 3.20. The weights above are the study's; the fork's true residue is the values call in §6.)*

## 6. RECOMMENDATION

**Gate-held — with three insurance riders that make it safe, not merely cheap:** (1) the *guarantees* are non-optionally baked into the helpers (assertion + dead-flag + supervised + stamp-on-skip), so even without the runtime, no adopter can silently opt out of the trap-proofing — this is what separates C-here from "voluntary discipline" as usual; (2) set the boot SLO **now** (arms T3); (3) schedule the adoption review at 2 cycles — **<30% uptake of priority targets is the pre-agreed disconfirmer: build B then** (study §E.3-iii). **The fact that should scare us about building now:** the daemon's only catastrophic scheduling failure on record was a central scheduler starving silently for 6.54 days, and the daemon *still* has no supervisor tradition — B-now installs a larger instance of that failure class before the watchman exists. **The fact that should scare us about waiting:** voluntary adoption is this codebase's documented failure mode (GAP-16's missing switches, dead knobs, two mechanisms that ran dead while their counters climbed) — if the riders fail and adoption stalls, the estate is exactly as dark at the next incident, six weeks later. Between those two fears, the first is a *concentration* risk we cannot yet supervise and the second is a *discipline* risk we can measure and pre-committed to act on — so hold the gate, arm the triggers, and let the Emperor's own Decision-1 values call (runtime uniformity per se) override only if uniformity itself is the goal rather than the outcome.

---
*Every claim traced to decision-report.md (§B.2 components, §C phasing, §D counter-argument, §E.3-E.5) and the three census artifacts; effort estimates derived from component scope + in-repo precedent sizing (disclosed sanity-check), confidence L-M, assumptions stated in §1.*
