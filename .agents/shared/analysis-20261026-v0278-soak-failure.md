# Analysis 2026-10-26: v0.27.8 promote rolled back on a soak failure that was NOT a release defect

- **Date:** 2026-10-26 20:41–20:50 UTC (pipeline), 20:59–21:03 UTC (manual upgrade by operator DevOps lane)
- **Subject:** promote to v0.27.8 → gates green → 120s adaptive soak saw zero green probes → ADR-005 auto-rollback → v0.27.8 quarantined
- **Outcome:** v0.27.8 subsequently installed MANUALLY (launcher swap + symlink flip, no gate) and served green within ~90s → **the release was never defective**
- **Audience:** ensemble dev team — the fix list at the bottom is yours

## TL;DR / verdict

**Trigger:** the checkpoint `AsyncConnectionPool` (and the DB probes sharing it) **saturated under the post-boot workload burst**. Auto-continue's resume of 4 instances is the **largest single contributor, but not the only trigger** — degradation was already underway ~21 s before the resumes, from the boot-recovery burst (MCP schema lazy-load ×4, `plane` MCP retry storm, job-recovery reconcile).

**Amplifier:** the readiness detector's budgets are steady-state budgets with **no boot-window grace** — sentinel `aget_tuple` at **1.0 s**, `AsyncConnectionPool.check()` must acquire a connection (none idle during the burst), SELECT 1 / queue probes likewise. "Busy but healthy" is reported identically to "broken."

**Consequence:** the 120 s adaptive soak (early-exit on 3 consecutive green probes) sampled **entirely inside the burst** → `consecutive_green=0` → C-1 never-green protection → auto-rollback + quarantine of a healthy release.

## Evidence (daemon journal, 2026-10-26)

```
20:42:31  gate: readyz ALL GREEN (database/queue_freshness/services/checkpoint_saver) — daemon ~75s old
20:42:39  GET /api/prompts/pending-count → 503        ← already degraded, 21s BEFORE any resume
20:42:43  eager_warm_schemas: primed 4/5 MCP servers (plane: 0 tools — 'Connection closed' retries)
20:42:43-20:42:57  Lazy-loaded 7 MCP tool schemas ×4 instances (4c47b25b, 20db5984, 81c602de, 00e50a5b)
20:43:00  [RESUME] ×4 instances  (auto-continue resume burst)
20:43:00  [Compaction] skipping proactive on non-quiescent checkpoint ×4
20:43:17  [CheckpointPerf] op=aget latency_ms=7596     ← checkpoint read takes 7.6s
20:43:17  [Readiness] degraded: database: SELECT 1 probe failed or timed out
20:43:33  [Readiness] degraded: checkpoint_saver: sentinel read (aget_tuple) timed out after 1.0s;
                              AsyncConnectionPool.check() failed or timed out
20:43:41  [CheckpointPerf] op=aget latency_ms=94      ← recovers when the burst eases
20:43:33 → 20:45:21  degraded every ~15-30s (sentinel / check() / SELECT 1 / queue probe)
promote: soak: /livez transient-exhausted at 15s; /readyz transient-exhausted at 45/60/90/105s
promote: WARN: soak FAILURE: cap-reach at 105s with consecutive_green=0 < 3 — auto-rollback (ADR-005 / C-1)
20:49:59  rollback event: auto-rollback v0.27.8 → v0.27.4 (gate fail: soak failure; re-gate green)
```

Promote-probe semantics (lib.sh:3066): `0 = green, 1 = red, 2 = transient-exhausted`; "transient-exhausted" = the probe **timed out with no response** (readyz's internal probe chain takes seconds once components fail) — it is counted as "no green".

## Why "the release was fine" is provable

- v0.27.8 passed **every gate**: integrity (CURRENT drift-check + TARGET manifest), livez, readyz (all four components incl. `checkpoint_saver`), version verify
- The **same binary**, installed manually at 20:59 (launcher swap + symlink flip, no gate), served **readyz green at 21:03:15** — ~90 s after boot, i.e. once the resume burst eased
- Post-rollback the v0.27.4 daemon exhibited the identical degradation signature (99–108% CPU + same readiness reasons) → the condition tracks **machine load**, not release identity

## Fix list (team duty; ordered by impact)

1. **Do not auto-continue during a promote's soak window.** The resume burst is the largest slice of
   the saturation; sequence resumes AFTER the ceremony (or hold them while a promote txn is open).
   `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` only gates the *boot* pass — it cannot suppress a
   resume that happens mid-soak.
2. **Give the readiness detector a boot/burst grace window.** The 1.0 s sentinel budget is a
   steady-state budget. Under a burst the honest signal is "degraded, recovering" — not a hard
   fail. A bounded grace (e.g. first N s after boot, or after a resume burst) would keep the
   detector honest without crying wolf.
3. **Size the checkpoint pool for the burst.** `AsyncConnectionPool.check()` failing means the probe
   could not obtain an idle connection — with N workers × resumed turns all checkpointing
   concurrently, the pool needs headroom for the probe (and for `aget`).
4. **Soak semantics:** consider requiring 3 consecutive greens *with* a burst-aware window (or
   accepting "green after the burst settles") so a healthy release isn't rolled back for being
   observed during a load spike. The C-1 never-green protection itself is correct — it should not
   fire on a release that never had a fair observation window.

## Related docs (same directory)

- `incident-20261010-checkpoint-connection-pool.md` — the original pool defect + Oct-11 volume-full
  outage + `checkpoint_blobs` retention item
- `incident-20261022-eventloop-cpu-peg.md` — the **loop-starvation** family (different mechanism:
  one CPU-bound task pegging the single asyncio thread). **Correction appended there:** the
  auto-continue kill-switch must be set in `/home/nea/agents-ensemble/.env` (it is pinned to `1`
  there); `systemctl set-environment` does not reach this daemon.
- `protection-plan-install-dir-20261012.md` — install-dir hardening (Layers 1/3/4)

## Operational notes

- v0.27.8 installed manually 20:59–21:03 UTC; journal reconciled
  (`current=v0.27.8`, `previous=v0.27.4`, quarantine cleared, backup `state.json.pre-manual-v0278.*.bak`).
- `ENSEMBLE_MISSION_RECORDS=enforce` confirmed active on this boot; enforce mode surfaced a new
  `UniqueViolation` on `ux_mission_records_anchor_open` during `mint_boot_epoch_if_none` — likely a
  second mint path racing; worth its own ticket.

---

## Addendum 2026-10-26 21:45 UTC — actual co-cause of the "down/flaky" period: Context7 MCP init storm (dead API host)

The v0.27.8 promote's soak failure (see the analysis above) had **a second, dominant co-cause** that also
explained the "system is down / www.ensem.dev flaky" reports between 20:40 and 21:41 — including on
**v0.27.4 after the rollback, with ZERO agent work running**.

### Root cause

Context7's API host moved; the package's default base is now **globally unresolvable**:

```
@upstash/context7-mcp v4.4.4:
  export const CONTEXT7_API_BASE_URL = process.env.CONTEXT7_API_URL || `${CONTEXT7_BASE_URL}/api`
  → fetches  ${CONTEXT7_API_BASE_URL}/v2/entra/config/<audience>   AT INIT

api.context7.com  →  NXDOMAIN on 1.1.1.1, 8.8.8.8, and every resolver we tried (the host moved)
https://context7.com/api/v1/search?query=test  →  200 in 0.89s   (LIVE API)
https://mcp.context7.com/mcp                 →  405 in 0.82s   (MCP endpoint alive)
```

### Failure chain (observed)

1. Every context7 MCP init hangs on the dead DNS name → 30 s timeout → 3 attempts → health check → repeat
   every ~2–4 min (forever; nothing circuit-breaks it).
2. Each attempt spawns `npm exec @upstash/context7-mcp` + the node server at **88–156 % CPU** and leaks an
   unclosed aiohttp session; failed attempts leave children behind.
3. The daemon's main process pegs at **105–114 % CPU with zero agent work**; the checkpoint
   `AsyncConnectionPool` probes then blow their 1.0 s budget (sentinel `aget_tuple`, `check()`,
   SELECT 1) → readiness degraded, `/livez`+`/readyz` unresponsive → FE shows "down" while ordinary
   API calls still return 200.
4. The promote's 120 s adaptive soak sampled entirely inside the storm → zero green probes → ADR-005
   auto-rollback of a healthy release.

### Fix applied (operator, 2026-10-26 21:40)

The context7 row is a **protected built-in** (config immutable via the API; template schema is empty),
but MCP children inherit the daemon's environment → set in `/home/nea/agents-ensemble/.env`:

```
CONTEXT7_API_URL=https://context7.com/api
CONTEXT7_BASE_URL=https://context7.com
```

then restart the daemon. Result: context7 tools load normally (`mcp_context7_resolve-library-id`,
`mcp_context7_query-docs` served), `/readyz` all green, **0 degraded flaps**, CPU 48–62 % and falling.

### Team follow-ups

1. **Pin the context7 API base in the built-in template** (`config_schema` is empty today → the URL is
   unsettable), so a future host move doesn't silently wedge every boot.
2. **Circuit-break the MCP warmup**: a server failing init N times should be disabled/quarantined for
   the boot instead of retried forever; failed attempts must kill their child processes.
3. **Fail fast on dead API hosts** (pre-resolve the base at init and refuse cleanly instead of hanging
   until the 30 s timeout).
4. **Budget the MCP warmup outside the loop / at boot with grace** — its churn currently starves the
   checkpoint pool's probes, which is what actually makes the daemon look "down".
5. Consider `CONTEXT7_API_KEY` for the API (anonymous search worked, but rate limits apply).

## FIXES LANDED (2026-10-28 — feature/soak-gate-mcp-warmup, staged as v0.27.11)

Every failure mode in the analysis above now has a fix on the lane's branch.

**What the gate now does instead of what it did.** The readiness detector no
longer lumps "busy but recovering" together with "broken". Each failure carries
a failure CLASS, and only the recovering classes earn a grace window
(`GRACE_HORIZON_S=120s`, measured from each observation, so it slides instead of
accumulating). While the window is open the gate reports
"degraded, grace-held, <reason>" — the reason is always visible and the gate
never renders healthy while a component is down. A pool-leak signature is
grace-INELIGIBLE: leaks are never forgiven by the window. The soak counter
behind the gate now has GREEN / HELD / RESET semantics, so three consecutive
greens are reachable again after a burst of holds (a hold no longer silently
resets the streak), with a budget of at most 2 holds per green
(`MAX_HOLDS_PER_GREEN=2`) and an absolute bound of soak-entry + 300 s.
**The never-green trigger itself is UNCHANGED and UNCONDITIONAL — a genuinely
broken release still auto-rolls back.**

**Auto-continue hold.** While a promote transaction is live, automatic resumes
defer instead of racing it: four seams route through the hold, liveness is a
two-leg predicate (heartbeat plus a 120 s boot window), release needs two
consecutive false readings, and deferred resumes are re-queued — never dropped.

**Checkpoint pool.** Pool `max_size` is raised 5 → 10 (sized for the ceiling of
7 concurrent checkpoint writers plus the readiness probe, with headroom), and
the size/timeout are now configurable via the new
`LIMITS_CHECKPOINT_POOL_MAX_SIZE` / `LIMITS_CHECKPOINT_POOL_TIMEOUT_S` knobs.
The ghost-waiter path is bounded at 5.0 s. Stated plainly: this sizing does NOT
cure the SQLAlchemy-side connection leak — that remains the follow-up
commission's job (job 634c38f2).

**MCP warmup off the event loop.** Warmup work moved to dedicated executors
(process census + secret resolution), the SDK handshake is bounded at 30 s, and
warmup runs under 60 s (per-server create) / 90 s (total) budgets. Cancellation
now runs the transport's `__aexit__` with a `/proc` census on both legs, so no
orphaned MCP children survive a cancelled warmup.

**MCP degradation is survivable.** A new `init_guard.py` circuit-breaker
(threshold 3 failures, 300 s recovery probe, dead-DNS hosts quarantined on the
first failure) sits at five seams. A failing server is quarantined for the
boot, journaled, and surfaced in readiness — and it can recover WITHOUT a
restart (the recovery wiring was itself a defect caught by review before
staging and fixed). A DNS pre-check fails fast on dead hosts instead of hanging
to the 30 s timeout. The context7 base URL is now settable through the
protected template (config schema v3, with an env-PRESERVING heal so existing
operator values survive the schema bump). Degraded servers are advisory-only in
the readyz detail payload and carry `health_state` on `/api/mcp-servers`.

**Honest limits.** (i) The queue_freshness probe's own transaction leak is still
live (out of scope here), so a HEALTHY release exhibiting that signature can
still be rolled back until the follow-up commission lands — interim protection
is carried by the hold, the pool resize, and the grace/leak gates.
(ii) The DNS pre-check catches unresolvable hosts, not resolvable-but-dead
ones. (iii) Cold-path connection failures with the warmup pool disabled do not
feed the breaker.

**Verification.** Functional gate: PARTIAL / GO-WITH-DISCLOSURES — the incident
shape was reproduced at base and closed at HEAD (15/15), never-green was proven
end-to-end through a real `promote.sh` auto-rollback at BOTH revisions, 38
mutation legs were planted with all 21 documented legs RED→GREEN, the prod
context7 row was verified clean (SELECT-only), and an isolated boot smoke ran
green with the live fence held. Full evidence:
`.agents/tester/RESULTS/2026-10-28-soak-gate-mcp-warmup-functional-gate.md`.
