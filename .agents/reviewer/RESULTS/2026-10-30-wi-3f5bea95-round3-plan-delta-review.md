# Round 3 — Plan-side Delta Gate — wi-3f5bea95

- **Target:** plan set (uncommitted round-3 fold) @ worktree HEAD `c3d87af26`, branch `fix/eventloop-sync-db-hotpaths`
- **Date:** 2026-10-30 · **Worker:** `eb978b33` (`plan-review`, agentic alias) · **Prior:** round-1/round-2 artifacts in this directory
- **VERDICT: APPROVE-WITH-ADVISORIES** — 0 critical, 3 warnings, 4 advisories

## Closure scoreboard (all verified in-tree, closure map NOT trusted)

| Item | Status |
|---|---|
| AB-1/F-3/M-1 seam form (submit → concurrent-future done-callback → wrap_future; run_in_executor banned + grep pin; pin (x) max-concurrent ≤ W) | **CLOSED** |
| AB-2 permit counter + waiter deque (semaphore retired; waiter count first-class; atomic close; uniform post-close error) | **CLOSED** |
| AB-3 G5 re-spec (pinned fixture clock, normalize-then-re-hash, compared_shapes == 8, detail endpoint; jobs_active_badge measured non-regression) | **PARTIAL** — propagation gaps below |
| AB-4 fourth batch task-timing (`get_timing_by_work_ids` verified at `task/repository.py:698`, returns `(started_at, completed_at)`; 3-file boundary holds) | **CLOSED** |
| AB-5 enumerated edit regions (concrete file+window; constants.py additive-only; `list_child_ids_permanent` untouched) | **CLOSED** |
| F-4 pack values (2/1/0/≤5 stay; MissionResolver dropped; pack 4 → `:788`/`:863`; task-bearing sweep added; no operative `==3`/`≤6`) | **CLOSED** |
| F-1/M-2 root_only pre-accumulation + delivered-population identity pin | **CLOSED** |
| F-5 unit class beside every frozen G9 total | **CLOSED** |
| M-3 G1 attribution conjunct (predicate coherent, all three conjuncts) | **CLOSED** |
| M-4 D-13 corrected in place, dated | **CLOSED** |
| M-5 retraction stamps (6× c3d87af26; phase5:11 struck+dated; increment-#2 IN FLIGHT) | **CLOSED** |
| M-6 bridged-read marker predicate + `DB_OFFLOAD_DRAIN_BOUND_S = 10` arithmetic | **CLOSED** |
| Cheap notes (D-78 + 14 landings) | all present |

No new phantom anchors (22 probes: 20 exact / 1 near / 1 inherited-history). G1 predicate coherent.

## Warnings — fold before implementation dispatch (all one-liners)

1. **Stale `compared_shapes == 7`** at `plan-overview.md:558` and `decisions.md:899` (A-2 amendment rows) vs the 8-shape canon everywhere else — a stale fence value inside an amendment table can be read as the fence.
2. **"Four named residuals" not propagated to five** at `plan-overview.md:250` (honesty clause), `phase5-plan.md:25` (5.7(d) CHANGELOG requirement), `plan-overview.md:534` (W-1), `decisions.md:926` (D-50) — the CHANGELOG/closeout will under-report the residual set if folded as-is.
3. **seed.py citation drift**: plan cites `:116`/`:244`; in-tree `FIXTURE_EPOCH` is `:117`, `_fixture_now()` usages `:143`/`:271`.

## Advisories (may ride)

- Scope the `run_in_executor` grep pin to `daemon/services/db_offload.py` (blanket grep false-positives on ~1,066 legitimate sites, D-39).
- State waiter-deque bound (or explicit unbounded disclosure) + pin the D-19 alarm threshold; label FIFO.
- Field-level pins: state `result_summary` is trivially None-on-both-sides and `mission_ref` rides the mission batch (non-trivial pins = the two timing fields).
- Historical `==3`/`≤6` survivors in `decisions.md:670` / `architecture-recommendation.md:327` are acceptable as amended history.

## Decision notes for the leader (not blockers)

1. **Residual taxonomy:** `jobs_active_badge` is enumerated as G1 residual (5) but is a G9 non-regression shape with no on-loop wedge claim — different class from the four on-loop wedge sites. Pick one framing ("five wedge residuals" vs "four wedge residuals + one G9-only shape") and apply uniformly; the second is the more precise reading of RULING 1.
2. **Waiter deque:** intentionally unbounded? Name the cap or disclose.

## Conditions

PLAN = APPROVE-WITH-ADVISORIES, conditional on folding Warnings 1–3 (pure text propagation) before implementation dispatch. Advisories may ride the next doc pass. The harness increment #2 remains OUT OF SCOPE here and still owes its own council pass before any HEAD run.
