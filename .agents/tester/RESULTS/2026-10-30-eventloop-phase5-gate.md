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

---

# Amendment 1 — the four "not run" legs, executed (2026-10-30, post-gate)

Same pins, in-process, no children. **The verdict does not change: GATE FAIL** (FAIL-1 G5
bytes, FAIL-2 G4 badge max). These legs were run because they are independent of the jobs
bytes and are required for a clean pass later.

## G11 — live-version truth (read from the install dir; no daemon contact)

| item | value |
|---|---|
| `releases/current` → | `/home/nea/agents-ensemble/releases/current` (symlink) |
| `state.json` | `current=v0.27.12`, `previous=v0.27.11`, `in_flight=None`, `pending_op=None`, `quarantined=[]` |
| release dirs on disk | `v0.27.4, v0.27.7, v0.27.8, v0.27.11, v0.27.12, v0.27.13` |
| `v0.27.13` | **exists, manifest version `v0.27.13`, NOT promoted** (current is still 0.27.12) |

⇒ **live = v0.27.12; v0.27.13 staged-unpromoted; this lane (superseding v0.27.13 at
promote) is therefore unreleased.** G11 PASS.

## G12 — attribution (path class → owning commits, BASE..payload = 36 commits)

| path class | owning commits |
|---|---|
| production `daemon/**` | P1 `377f0b627` · P2 `fa42ba328` · P3 `a40321001` · P4 `85452628c` · council/seam fixes `4c4020a20`,`63bc43b41`,`250082d7b`,`4be3cade2`,`ef1404c54`,`09ce9d369`,`a677bdcaf` · tidier `054ba46b6` · final `f636756aa` |
| tests (developer) | `acd035318` (P1 pins), `8e6df58e4`→`836334e87`, `4c4020a20`, `63bc43b41`, `250082d7b`, `ef1404c54`, `2fd676df6`, `5f83d5ddc`, `f4fa4cc8b`, `09ce9d369`, `054ba46b6`, `f636756aa` |
| tests (tester) | `5f22bfb08` (fence add), `10b3c982d` (fence restore), `6efebfb6` (three packs) |
| `.agents/tester/**` | harness `d42ba2976`,`42764588c`,`c3d87af26`,`3e976696b`,`dd6d1ed12`,`469ca7d68` · docs `7ac87d8a5`,`0d8693820`,`71eef27e1`,`46a385f8e`,`acfa5fa1b`,`ba8e276bf`,`40727f70c` |
| `.agents/shared/**` | `69c69b3a9` (W1 planning set), `d42ba2976`, `42764588c` |
| `frontend/**` · `plugins/**` · root files | **ZERO commits** (no frontend/plugin/root change in this lane) |

## D-70 — additive-only, per-hunk region map (9 files / 176 deleted lines)

| file | deleted | hunks (old-range) | region verdict |
|---|---|---|---|
| `daemon/services/work_resolver.py` | **143** | `1233,1277,1303,1356,1367,1376,1397,1414,1424,1436,1450,1461,1468,1472,1479,1482` (P3 restructure) · `1778`,`1846`,`1869` (**D-B three sites**) · `2371,2404,2409,2415,2431` (`_batch_instances` degraded) | **INSIDE** P3 tasks 3.2–3.7 · the deletions are in-place replacements at the cited sites |
| `daemon/routers/work.py` | 8 | `181` | INSIDE 3.6/3.7 |
| `daemon/routers/instances.py` | 6 | `14` (import) `345,388,476,550` (offload/batch call sites) | INSIDE 2.1–2.6 |
| `daemon/services/instance_lifecycle.py` | 6 | `5111,5115,5140` (children batch + detail-share) | INSIDE 2.2/2.4 |
| `daemon/services/job_queue_service.py` | 4 | `1800` (pagination docstring) | INSIDE (named-pair/limit doc; **low risk**) |
| `daemon/tools/instance.py` | 4 | `37` (constants import) `6168` (docstring caveat) | INSIDE (W-3 named pair) |
| `daemon/tools/instance_read.py` | 2 | `1674` (`FLEET_SCAN_LIMIT` wiring) `87` (`CATEGORY_DOC`) | INSIDE (W-3) — `:87` is a **documentation string**, flagged low risk |
| `daemon/routers/queues.py` | 2 | `651,663` (defer-blocked offload) | INSIDE 4.1 |
| `daemon/routers/messages.py` | 1 | `204` (send path offload) | INSIDE P4 |

**No file-wide deletion, no removed function body, no test file gutted.** Every deletion is
an in-place replacement at a plan-cited edit region ⇒ **D-70 COMPLIANT** (two documentation-
string deletions flagged low-risk, not blocking).

## P4 4.7 — readiness behavioural leg (leak_signal / pool_state, BASE vs HEAD)

Real daemon booted per tree (`python -m daemon`, scratch PG, `PORT` = free port asserted
outside {9797,8088,8079,7979}, `HOST=127.0.0.1`, FAKE home + temp data dir, auth inert,
MCP builtins off), `/readyz` sampled before and after a 20-request BFS/flat load burst.

| tree | leak_signal (before → after) | pool_state (before → after) |
|---|---|---|
| **BASE** `b1183a637` | `{detected:false, checkedout_rising:false, streak:0}` → **identical** | sqlalchemy `{checkedout:1,size:15,overflow:-5}` · psycopg `{pool_size:2,pool_max:10,available:2,waiting:0}` → identical |
| **HEAD** `f636756aa` | `{detected:false, checkedout_rising:false, streak:0}` → **identical** | sqlalchemy `{checkedout:1,size:15,overflow:-4}` · psycopg `{pool_size:2,pool_max:10,available:2,waiting:0}` → identical |

**Read: the leak oracle's behaviour is unchanged — no leak signal is attributable to this
lane** (no new `detected`, no rising checkout streak, no waiting requests, same pool shape
at both trees; the `overflow -4/-5` difference is the pool's own accounting and appears in
both). **Caveat stated plainly:** the load was light (empty fixture, p50 ≈ 0.09 s), so this
proves *unchanged under light load*, not *no-leak under saturation*. A seeded-fixture
saturation run remains owed if the leader wants the stronger claim.

## 5.11 — boot smoke (bridged read in flight at SIGTERM)

| assertion | measured |
|---|---|
| banner | `Starting Ensemble v0.27.13` (both trees) |
| `/livez` | **200** after ~17 s boot |
| `/readyz` | **200**, `components_False=[]`, `detail.leak_signal.detected=false` |
| SIGTERM | exit **-15 in 0.27 s** (bounded, ≪ TimeoutStopSec 90) |
| `"Engine is disposed"` | **0 occurrences** |
| tracebacks in shutdown | **0** |
| scratch PG / data dir | harness-provisioned scratch DB **dropped** (`dropped=true`); FAKE home + temp data dir; no `.env` edit; no :9797 contact |
| **bridged read in flight** | **NOT ACHIEVED** — the fixture is empty, so the BFS read returned `200` in **0.01 s** and was already complete when the signal landed; `db_offload` drain logged nothing |

**Honest verdict on 5.11: PARTIAL.** The boot/liveness/termination envelope is proven
(banner, 200/200, no disposed-engine error, bounded exit, clean teardown), but the
*in-flight* precondition — the whole point of the leg, since the lane's only lifespan edit is
`await db_offload.shutdown()` before `manager.shutdown()` — **was not exercised**. A seeded
run (a few hundred instances so the BFS read is genuinely in the bridge at signal time) is
required to close it. Recording PARTIAL rather than PASS is the difference between evidence
and a plausible story.

## Incidents during Amendment 1 (disclosed)

1. **A stray test daemon bound port 8088.** The first boot attempt ran `python -m daemon`
   with `DAEMON_PORT` set but `PORT` unset; `config.yaml` defaults `daemon.port` to
   `${PORT:-8088}`, so the child bound **:8088** (the ensemble self-system port) with a
   scratch DB. Detected via `ss -ltnp`, identity confirmed from `/proc/<pid>/environ`
   (`POSTGRES_DB=evloop_gate_*`, data dir under `/tmp/gate-p47-*`), **SIGTERM'd only that
   pid**, :8088 freed, live `:9797` verified `200` before and after. Root cause is mine
   (wrong env var name); the harness itself passes the port to uvicorn explicitly.
   **Mitigation now in the script: `PORT` set explicitly and a hard assert that the chosen
   port ∉ {9797, 8088, 8079, 7979}.**
2. `psql -c` multi-statement wrapping made `DROP DATABASE` fail inside a transaction — fixed
   by single-statement calls; scratch PG provisioning now uses the harness's own
   `common.create_scratch_pg` (proven env contract) instead of a hand-rolled one.

---

# Amendment 2 — SEEDED 5.11 boot smoke + SEEDED P4 4.7 saturation (2026-10-30)

Runner: `/tmp/gate_seeded.py` — boots the real daemon (`python -m daemon`) on a scratch PG,
seeds **330 `instances` rows** (30 roots × 10 children, direct insert, FIXTURE_EPOCH
timestamps) so BFS reads carry real dwell, then drives a **16-thread saturation burst**.
Port asserted ∉ {9797, 8088, 8079, 7979}; `HOST=127.0.0.1`; FAKE home + temp data dir;
no `.env` edit; no live contact; scratch DB dropped.

Measured at the **payload `f636756aa`** (HEAD worktree) and **BASE `b1183a637`**:

| quantity | BASE | HEAD (payload) |
|---|---|---|
| boot → `/livez` | 17.1 s | 17.1 s |
| single BFS(100) read | **1.119 s** | **0.106 s** (10.5× faster) |
| concurrent reads started-not-finished at probe | 16 | 16 |
| `/readyz` **idle** — leak_signal | `{detected:false, checkedout_rising:false, streak:0}` | identical |
| `/readyz` **saturated** | **no response within the 10 s probe budget** (`status=None`, 16 reads in flight) | **200 in 0.245 s**, `leak_signal {detected:false, checkedout_rising:false, streak:0}` |
| pool_state saturated | n/a (probe unanswered) | sqlalchemy `{checkedout:1,size:15,overflow:-6}` · psycopg `{available:2,waiting:0}` |
| SIGTERM **with 16 bridged reads in flight** | n/a (no bridge at BASE) | **exit −15 in 1.16 s**, `bounded_90s=true` |
| `Engine is disposed` | 0 | **0** |
| tracebacks on shutdown | 0 | **0** |
| seam load lines | — | `db_offload: backpressure alarm — waiters=1 inflight=6 sum=7 threshold=8` ×3 |

## Verdicts

- **5.11 boot smoke — PASS (seeded).** The precondition is now real: **16 bridged reads were
  started-not-finished at the moment of SIGTERM** (`marker_asserted=true`), the seam was
  under load (backpressure alarms), exit was **1.16 s** — bounded far inside
  `TimeoutStopSec=90` — with **zero** `Engine is disposed` and **zero** tracebacks. This is
  the evidence the empty-fixture run could not give; `db_offload.shutdown()` was exercised
  with live in-flight work.
- **P4 4.7 saturation — PASS at HEAD, with a notable BASE-side observation.** Under identical
  saturation the leak oracle stays silent at HEAD (`detected=false`, no rising checkout, no
  waiting requests) ⇒ **no leak signal is attributable to this lane**. The BASE comparison
  could not be completed *at the same load* because **BASE's `/readyz` did not answer at all
  within 10 s while 16 BFS reads were in flight** — the loop is starved by the very defect
  this lane removes. Reported as measured; the exact probe exception text was not captured
  (the runner recorded `status=None`), which I am disclosing rather than filling in.
- **Drain logging gap (instrument-side, not a defect):** a *clean* drain logs nothing —
  `db_offload.shutdown()` only warns on bound-exceeded/fallback. So "the drain ran" is
  inferable from the timing (1.16 s ≈ in-flight completion) but not directly printed.
  **Recommendation for the lane:** one INFO line `db_offload: drained inflight=N elapsed=…s`
  would make the drain observable instead of inferred.

---

# Amendment 3 — RE-RUN AT PAYLOAD `c9eac9747`: G5 RESOLVED (GATE PASS-WITH-DISCLOSURES; one G4 flake open)

**New pins.** Code payload `c9eac9747388852a47d3a3ac11487bb3d8c44d3f`
("fix: restore BASE jobs payload bytes — root_only parity in the ensure_job_ids patch-up",
2 files +152/−2; `work_resolver.py` 22 insertions/2 deletions). Branch tip at measurement:
`9e26242aa` (docs-only, on top of the payload). BASE unchanged: `b1183a637649f94c23b94dfc745df44381dc54ab`,
reference `BASELINE.full.base.json` sha256 `2440959b…`. Harness sha unchanged
`11057a76…` (identical in both trees, repaired `gate_g5` marker present).
New worktree: `/home/nea/ensemble-src-wt-gate-head2` (detached at the payload).

## 1. G5 — RESOLVED. Nine-entry byte identity, both runs

Verbatim fence (both runs):

```
g5_normalized_bytes_equal_vs_reference
  OK :: COMPARED 9/9 shape(s) — all byte-identical after the children-order normalization
```

The corrected exclusion rendering is in play (`COMPARED n/9`, no excluded shape named
because none is excluded — `g5_shapes_collected 9/9 all-200=True`). Body-level confirmation:

| shape | BASE len | payload len | verdict |
|---|---|---|---|
| `jobs_page` | 82469 | **82469** | identical (`norm 5531aaa4…` both) |
| `jobs_recent_badge` | 8218 | **8218** | identical (`norm 3013b577…` both) |
| `jobs_active_badge` | 41320 | 41320 | identical (was already clean — the no-limit shape, matching the patch-up mechanism) |
| `instances_flat_badge` / `instances_bfs_home` / `work` / `defer_blocked` / `missions_badge` / `detail:{id}` | — | — | identical |

`g5_children_order` 47/47 + 1/1 ordered; `g5_jobs_active_non_regression` 7 ≤ 7.
**FAIL-1 is closed: the `mission_id`/`mission_epoch`/`mission_liveness`/`mission_terminal_reason`
population and the `mission_ref.liveness` flip are gone; BASE bytes are restored.**

## 2. G4 badge leg — TWO runs at the new payload: 1 PASS / 1 FAIL

| run | verdict | `g4_paired[profile_badge]` verbatim | loaded flat p95 |
|---|---|---|---|
| RUN1 | **FAIL** (62/63) | `stalls 2<=1, max 0.8332<=1.0583, p95 0.5381<=0.5236+0.5, samples 186>=0.5*170` | 0.1318 s |
| RUN2 | **PASS** (63/63) | `stalls 1<=1, max 0.8657<=1.0583, p95 0.5753<=0.5236+0.5, samples 188>=0.5*170` | 0.1606 s |

**Read, not laundered:** the *original* failure mode (max 1.0594 > 1.0583, +1.1 ms) is gone —
max is now comfortably inside BASE in both runs (0.83 / 0.87 vs 1.0583). What remains is a
**knife-edge STALL-COUNT flake**: BASE has 1 stall; RUN1 counted **2**, RUN2 counted **1**.
Same profile, same tree, same reference. So the badge leg is **marginally unstable at the
stall boundary**, not a reproducible regression. **Adjudication needed from the leader:** a
third run (evidence either way) or a documented waiver of the stall count for this leg — I
will not convert one failing run into a pass by choosing the nicer number.

All other G4 profiles PASS in both runs (`home` 0≤0, `jobs_page` 1≤1) and
`g4_reference_provenance` PASS (`mode=base simulated=False tree=b1183a637649`).

## 3. Gate table at the new payload (RUN2, the clean run)

`verdict=PASS, 63/63 fences, 0 failing`. Round-trips per request: flat **4** (on-loop 1 =
prefs residual), BFS **8** (on-loop 1), work **6** (0), defer **89** (0, unchanged),
jobs recent **7** (0), jobs page **7** (0), missions **3** (3, routed census #4 — exempt).
`g2_band[head]` **0.1318 s / 0.1606 s** ≤ 0.230 both runs. Expanded unloaded floor retained.

## 4. SEEDED 5.11 at the new payload — PASS

Seeded 330-instance fixture (one provisioning, reused for P4 4.7):

| assertion | measured at `c9eac9747` |
|---|---|
| boot → `/livez` | 18.1 s |
| single BFS(100) read | 0.099 s |
| **started-not-finished at SIGTERM** | **16** concurrent bridged reads in flight (`marker_asserted=true`) |
| seam under load | `db_offload: backpressure alarm — waiters=1 inflight=6 sum=7 threshold=8` |
| SIGTERM | **exit −15 in 1.36 s** (`bounded_90s=true`) |
| `Engine is disposed` | **0** |
| shutdown tracebacks | **0** |
| scratch PG | dropped |

## 5. SEEDED P4 4.7 — PASS at the payload; the BASE comparator is the notable datum

| tree | `/readyz` idle | `/readyz` **saturated** (16 concurrent BFS) |
|---|---|---|
| **BASE** `b1183a637` | leak silent, pool sqlalchemy `{1,15,-6}` | **no response within the 10 s probe budget** (loop starved by the defect) |
| **HEAD** `c9eac9747` | leak silent, pool `{1,15,-6}`, psycopg `{2/10, avail 2, waiting 0}` | **200 in 0.376 s**, leak `{detected:false, checkedout_rising:false, streak:0}`, same pool shape |

⇒ **no leak signal attributable to the lane under saturation**, and the bridge is not
merely "unchanged under light load" (Amendment 1's caveat) but **unchanged and responsive
under a saturated 16-read burst**, where BASE's readiness probe could not answer at all.

## 6. FINAL VERDICT

**GATE: PASS-WITH-DISCLOSURES.**

- The commission's blocking finding (FAIL-1, G5 jobs bytes) is **RESOLVED and proven twice**
  at the new payload.
- Every gate item is green except **G4 badge, which is 1 PASS / 1 FAIL across two runs on a
  stall-count knife-edge (2 vs 1)** — reported as an open flake requiring a third run or an
  explicit leader waiver, **not** converted into a pass.
- 5.11 (seeded, in-flight) and P4 4.7 (seeded, saturated) are both green at the payload.

### Disclosures (additions this amendment)

- **ROUTED ITEM (not a lane defect):** the developer disclosed the underlying **liveness-source
  asymmetry** — the legacy fallback renders `mission_ref.liveness` from `terminal_reason`,
  while the resolver-backed path renders it from `instance.status`, for **child-instance
  Jobs**; the two disagree (`failed` vs `completed`). The byte-restore restores BASE parity
  by keeping child-instance Jobs out of the resolver-backed payload; the asymmetry itself
  remains a **design fact for a future, separate semantics decision**.
- Carried from Amendment 1: two documentation-string deletions (`instance_read.py:87`,
  `job_queue_service.py:1800`), D-70 **low-risk doc-only**, mapped to their regions.
- Carried: the clean-drain logging gap (`db_offload.shutdown()` logs nothing on success);
  drain observability is inferred from the 1.36 s exit, not printed. Recommend one INFO line.
