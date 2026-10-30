# Phase-5 FUNCTIONAL GATE — eventloop-sync-DB hot paths (wi-3f5bea95)

**Verdict: ❌ GATE FAIL — 2 red fences, both on HEAD, both attributable.**

Executed by the tester **in-process** (no child instances: the `coding`-alias child lane died
on upstream rate limits 6×, including a single-lane probe; the leader authorised direct
execution). Serial stages, checkpointed at `/tmp/gate-ckpt/progress.txt`.

## Pins (state these in any citation)

| item | value |
|---|---|
| BASE tree | `b1183a637649f94c23b94dfc745df44381dc54ab` |
| HEAD (code payload) | `f636756aa724f4280d7c9aa5aa99268975ace0b4` |
| Branch tip | `fix/eventloop-sync-db-hotpaths` @ `f636756aa` (36 commits over BASE) |
| Harness | `.agents/tester/harness/eventloop-phase0`, `main.py` sha256 `11057a76ad1b0af5dfb689bc249e23cd06ec0cc0d4a35dd3b10282d6a02d6a8f` — **identical in both trees**, repaired `gate_g5` marker present, `selftest 62/62` |
| BASE worktree | `/home/nea/ensemble-src-wt-gate-base` (detached @ BASE) |
| HEAD worktree | `/home/nea/ensemble-src-wt-gate-head` (detached @ payload) |
| BASE reference artifact | `raw/BASELINE.full.base.json` sha256 `2440959ba240249369ccd233c0d053a5ad812db26bf45cf2b17933c0d4c80d31` |

## Gate table

| G | Result | Evidence |
|---|---|---|
| **G1 (three-part)** | **PASS** | family pin on the **loop thread**: BASE **1/8** → HEAD **0/8**; narrow BASE **1/8** → HEAD **0/8**; non-vacuity: `captures 8/8`, `capture_loop_section_collected 8/8`, `predicate_discriminator_selftest` PASS; `capture_load_leg_active` 135 (BASE) / 1500 (HEAD) requests. **Attribution table below: 0 unattributable captures** |
| **G2** | **PASS** | HEAD loaded flat p95 **0.1565 s** ≤ 0.230 (one-sided). BASE **1.0922 s** `FAIL_BASE_EQUIVALENT`. Provenance annotation: commission-body anchors `0.02–0.10 s` healthy / `0.067–0.23 s` floor band are **unverified plan figures**, recorded beside the measured value; measured HEAD is **above** the 0.10 healthy anchor but inside the gate's one-sided band ⇒ PASS annotated, better-than-BASE by 7× |
| **G4(e)** | **PASS** | `g4_reference_provenance` PASS (`mode=base simulated=False tree=b1183a637649`); paired: home OK, jobs_page OK, **badge FAIL** — see FAIL-2 |
| **G5 (9-entry)** | **❌ FAIL** | `COMPARED 9/9 — DIFFER: ['shape:jobs_page', 'shape:jobs_recent_badge']`. children-order fences PASS (`47/47`, `1/1`); `g5_shapes_collected 9/9 all-200=True`; `jobs_active` non-regression 7 ≤ 7 |
| **G6** | **PASS** | collected BASE 52 → HEAD 63 fences (no skips by node id); FAILED count BASE 0 / HEAD 2, both enumerated |
| **G7** | **PASS** | mode contract respected: BASE run PASS (defect measurable), HEAD run FAIL only on the 2 items above; no mode leakage (mode-suffixed artifacts) |
| **G9 (per-shape, unit classes)** | **PASS** | HEAD: flat `{'SELECT instance_ui_prefs':1}` total 1≤2; BFS same; work/defer/jobs_active/jobs_recent/jobs_page `{}`; missions `{instances:2, job_queue_items:1}` = exact routed-out census #4 (**exempt** from on-loop-0). Round-trips per request: 4 / 8 / 6 / 89(unchanged) / 7 / 7 / 3(unchanged) |
| **G10 (re-anchored)** | **PASS (with note)** | observable "0 that were 1 at BASE": HEAD resolves MORE, not fewer — no page-job becomes `work_record is None`; measured direction is `None → populated` (see FAIL-1). Exact `:951` / `:152-153` / `:383-405` line-pins **not re-verified by this lane** (static probe lane died with the rate limit) — open item |
| **G11 (live truth)** | **NOT RUN** | the static lane that owned it died; and reading it needs only release files, deferred to the re-run |
| **G12 (attribution)** | **PARTIAL** | commit list captured (36 commits BASE..payload); per-path ownership table **not completed** (static lane died). Known: P1 `377f0b627`, P2 `fa42ba328`, P3 `a40321001`, P4 `85452628c`, harness `c3d87af26`→`dd6d1ed12`, tester `469ca7d68`/`6efebfb6`/`10b3c982`/`40727f70` |
| **N2 flip (core claim)** | **PASS** | BASE **1/8** captures carry the instances-path DB frames **on the loop thread** → HEAD **0/8**. Diagnostic any-thread stays **8/8** at both trees (bridge-thread frames, correctly ignored) |
| **Packs (137 nodes)** | **PASS** | children-order **9**, p3.9 **14**, p4.5 **10**, p2.8 **6**, db_offload_seam **21**, work_resolver **77** — all green at the payload in a clean worktree; fences satisfied (0 failed, 0 skipped) |
| **D-70 additive-only** | **NEEDS-ADJUDICATION** | 9 files carry deletions (176 lines total): `work_resolver.py` 143, `routers/work.py` 8, `instances.py` 6, `instance_lifecycle.py` 6, `job_queue_service.py` 4, `tools/instance.py` 4, `tools/instance_read.py` 2, `routers/queues.py` 2, `routers/messages.py` 1. Per-hunk region mapping not completed (static lane died) |
| **P4 4.7 readiness** | **NOT RUN** | deferred — see below |
| **5.11 boot smoke** | **NOT RUN** | deferred — see below |

## FAIL-1 (blocking) — G5: the jobs payload changes, and the change is *semantic*

Two of the nine canonical entries differ, and the diff is not ordering or noise — it is
**newly populated data**. Attributable by field (smoke-profile `--capture-bodies`, full
reproduction of the same class; `instances_*` and `work` bodies are 0-diff-path identical):

```
shape:jobs_page         BASE len 82469 -> HEAD len 83441 (+972 B)
shape:jobs_recent_badge BASE len  8218 -> HEAD len  8362 (+144 B)

/jobs[i]/mission_id               None -> 'fx-…'        (populated)
/jobs[i]/mission_epoch            None -> 1             (populated)
/jobs[i]/mission_liveness         None -> 'completed'   (populated)
/jobs[i]/mission_terminal_reason  None -> 'completed'   (populated)
/jobs[i]/mission_ref/liveness     'failed'|'cancelled' -> 'completed'   (CHANGED VALUE)
```

This is the P3/D-B orphan-miss closure doing exactly what it was built to do — resolving
work records that BASE failed to resolve — but the plan's byte-identity premise is
**narrower than the behaviour**: `architecture-recommendation.md:157` argues byte-identity
only for **batch-proven-absent** ids ("`mission_liveness` stays `None` both ways"). The
measured change is on rows whose instances **exist** and whose mission fields were
previously suppressed by the guards. So either (a) the plan's G5 exclusion list needs the
`mission_*` family added and the change disclosed as intended, or (b) the resolver
semantics moved when they should not have. **That is an adjudication, not a tester call.**
Note the direction: `mission_ref.liveness 'failed' → 'completed'` means BASE was serving a
stale liveness for the same job — the change looks like a *fix*, which is precisely why it
cannot be silently absorbed: it is a client-visible payload contract change.

## FAIL-2 (advisory-strength, but the rule is no-tolerance) — G4 badge

`g4_paired[profile_badge]`: `stalls 1<=1` ✔, `p95 0.5235<=1.0236` ✔,
**`max 1.0594 > 1.0583`** — over by **1.1 ms** on a single-sample max. The G4 rule carries
no tolerance. Likely measurement noise (p95 and stalls are clean, and the same shape's
p95 improved), but I will not launder a failing fence into a pass: it is a FAIL and it
needs either a re-run to see if it reproduces or an explicit leader waiver.

## G1 attribution table (required deliverable)

| capture | reason | loop-thread verdict | attributed site | owning routed item |
|---|---|---|---|---|
| 4 × `sustained_cpu` (HEAD) | CPU-sustained trigger | **no instances-path frames on the loop** (0/8) | bridge-thread DB work (`db-offload_*`) | — (not a violation) |
| `boot-records/` | capture metadata dir | n/a | n/a | n/a |
| **`sustained_loop_lag` captures** | — | **ZERO exist at HEAD** | — | — |
| on-loop residuals (G9, per shape) | `SELECT instance_ui_prefs` ×1 (flat, BFS) | attributable | `daemon/routers/instances.py:496` prefs merge | D-9 residual (enumerated) |
| on-loop residuals | `SELECT instances`×2 + `job_queue_items`×1 (missions) | attributable | missions census | routed-out **census #4** (G9-exempt) |
| executor-class | jobs_active_badge 7 rts | NON-REGRESSION only (7 ≤ 7) | jobs enrichment population | **C-4 unbounded** (enumerated residual) |

**Zero unattributable captures ⇒ G1 satisfies "scoped closure + enumerated residuals".**

## Success criteria vs measured

| criterion | target | measured HEAD | verdict |
|---|---|---|---|
| /api/instances floor | 0.067–0.23 s band (commission-body, unverified) | **0.1565 s** loaded flat p95 (BASE 1.0922 s) | inside gate band; above the 0.10 "healthy" anchor ⇒ annotated |
| round-trips per request | O(1)/bounded | flat **4** (≤6), BFS **8** (≤12), work **6** (≤12), jobs recent **7** (≤25), jobs page **7** (≤30); defer **89**, missions **3** unchanged | PASS |
| loop-lag under synthetic load | vs 1.408 s p95 baseline | badge p95 **0.5235 s**, home **0.5056 s**, jobs_page **0.5122 s** | PASS (baseline beaten ≈2.7×) |
| N2 pin | BASE positive → HEAD negative | **1/8 → 0/8** | PASS |

## Disclosures (carried, not remediated)

1. **ITEM-2** — degraded-batch Task `root_only` is **fail-open under instance-lookup degradation in BOTH BASE and HEAD** (pre-existing, base-faithful); the batched path widens the exposure per-row → per-page; the Job branch fails loud instead. Not fixed by design.
2. `ensure_job_ids` pre-`root_only` draw order (unchanged).
3. `/api/work` population unbounded (Q2) — measured 1873 rts at BASE, 6 at HEAD after batching; no limit lever exists.
4. **T-3 deferred** — `daemon/routers/jobs_crud.py:943` still on-loop (known remaining site; re-entry needs A+C + DEAD fixtures).
5. **D-B observation-window narrowing** — the race disclosure is a comment, not an engineering fix (probability ≈ 0).
6. **async-native deferral unfunded** — the lane is bridge-based (C), not async-native.
7. Two instrument doc-warnings folded earlier (`469ca7d68`); six tidier advisories: `054ba46b6` landed a tidier pass, **not re-verified here**.
8. Pre-existing, unrelated: wedge-capture census `c.n_live_tup` (R-10) and `resume-history read failed` warnings appear in both trees; `asyncio.iscoroutinefunction` deprecation noise; `PermissionError` on root-owned `/tmp/pytest-of-nea/garbage-*` teardown.

## Not run — stated, not hidden

- **P4 4.7 readiness behavioural leg** (leak_signal/pool_state BASE-vs-HEAD under load): not run. It cannot change a verdict already red on G5; it is required for a clean pass.
- **5.11 boot smoke** with a bridged read in flight at SIGTERM: not run (same reason). The lane's lifespan edit (`db_offload.shutdown()` before `manager.shutdown()`) is therefore **unproven at this gate**.
- **G11 live-version truth**, **G12 per-path table**, **D-70 per-hunk region mapping**, **G10 exact line-pins**: owners of those were the static lane, which died with the rate limit. Their absence does not soften the FAIL; they must be completed on the re-run.

## What would flip this to PASS

1. **G5**: either a plan amendment that adds the `mission_*` family (+ `mission_ref.liveness` transitions) to the permitted difference classes **with the developer's disclosure that the jobs payload changes**, or a resolver change that restores BASE bytes on rows whose instances exist. I recommend the former if D-B was always intended to resolve those rows — but it must be *written down*, because right now the plan says the opposite.
2. **G4 badge max**: one re-run to see whether the +1.1 ms reproduces; if it does, it is a real (if tiny) regression in the max-lag tail.
3. Then the four not-run legs (P4 4.7, 5.11 boot smoke, G11, G12/D-70 static) to make the pass complete.

**Nothing in this report was executed against the live daemon, the live `.env`, or port 9797.**
Scratch PG databases were created and dropped by the harness (`evloop_scratch_test_*`,
`dropped=True`, 0 rows) and by the packs (P2.8 self-provisioning). No stage, no promote.
