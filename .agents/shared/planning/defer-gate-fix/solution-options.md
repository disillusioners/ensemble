# Defer-Gate Post-Settle Window — Solution Options (Ranked)

**Date:** 2026-09-03 · **Mode:** competitive fan-out, 3 workers, same skill (`data-flow-design`), different approaches · **Evidence:** worker reports pinned at `latest`, branch tests `fix/defer-gate-post-settle-window` @ `853abb1b` · **Companion:** `recommendation.md` (the pick + implementation outline)

---

## 1. The decisive adjudication: WHERE does the folding hole get fixed?

The three reports disagree on the claim-folding site (`task/repository.py:1357-1371`), and this disagreement determines the ranking:

- **Workers A + B** treat folding as a claim-time hole: add an instance-liveness `EXISTS` INSIDE `claim_pending_task`'s atomic SQL. B (job-granularity) proves it **cannot** reach that SQL at all and "converges on A at the claim site by necessity."
- **Worker C** diagnoses the folding behavior as **correct**: the message Task IS completed — there is no task race — so the task-granular race-guard properly clears. The defect is **admission-time**: the GATE said "idle" for a busy project. Per the 2026-07-23 plan §4, the claim-guard exists "strictly as a belt-and-suspenders race guard, **not as the definition of idle**" — widening it with mission-liveness re-defines idle at the claim layer, cutting against the two-leg design.

**Adjudication: C's diagnosis wins as the primary fix.** Supporting evidence: (i) all 5 committed RED tests are gate/probe tests — none exercises the claim path as the defect; (ii) the 2026-07-23 incident entered via gate admission ("be336411 admitted at 10:36:22"), not a bare claim; (iii) plan §4's leg separation. **Residual risk acknowledged:** a narrow TOCTOU remains (gate-check at T1 straddling a complete short message turn whose mirror then settles at T0 with the instance waiting on children → claim at T2 finds no active task). This is exactly the class A's in-SQL clause would close — retained as a documented optional hardening ticket (see `recommendation.md` §5), not the primary fix, because it modifies the most concurrency-sensitive SQL in the daemon (A's own risk note: "one wrong index = cascade") on a surgical branch.

## 2. Ranked options

### 🥇 #1 — **Gate-widening via shared SQL constants ("C-PR1 carrying B-shape semantics")** ← RECOMMENDED
Widen the job-side idle predicate at BOTH repository methods (defer `:700-768`, background `:770-893`): busy = (`admission_state='active'` non-excluded-queue job with live/absent instance) **OR** (settled mirror: `job_type='message' AND admission_state='done'` with non-terminal instance). Implement the two predicate bodies as **shared SQL-text constants** in one new module (`_idle_predicate_sql.py`) consumed by Gate A defer+background, Gate B defer+background, and maintenance `_is_idle` — agreement by construction. Claim-folding SQL **unchanged** (stays task-granular race-guard per plan §4); the folding RED scenario is a **gate test** proving the gate catches what the claim cannot. PAUSED holds, WAITING_CHILDREN blocks, terminal set {completed,error,terminated,failed}, lineage-scoped by construction, defer-queue exclusion handles self-deadlock.
- **One line:** the architecture-correct fix (idle at the gate, race-guard untouched) with the narrowest tested busy-set, delivered through one shared constant so the five consumer sites cannot drift.

### 🥈 #2 — **A: instance-liveness keying, inline at all three sites**
Same truthmaker (mission liveness = canonical instance status — the Mission projection's exact semantics), propagated inline: broadened admission-state set (`IN ('queued','active','done')`) at probe + Gate B, plus an in-SQL instance-liveness EXISTS inside the atomic claim (with self-deadlock bind `j_a.instance_id != task.instance_id`).
- **One line:** right semantics, but it modifies the hottest concurrency path (its own self-score: Complexity High, Risk High) and re-defines idle at the claim layer against the 2026-07-23 two-leg intent — the strong fallback if the user wants claim-time belt-and-suspenders NOW.

### #3 — **B: mirror-aware filter, standalone**
Special-case Leg-1b at the two repository predicates only, inline, no sharing mechanism.
- **One line:** closes Gates A/B/maintenance in ~30 LOC but leaves the claim seam open in principle (its own verdict: "coherent narrow patch, not a standalone fix") — its semantics are adopted INSIDE #1; standalone it's partial.

### #4 — **C-full (PR1 + PR2): shared constants also interpolated into the atomic claim**
#1 plus refactoring `claim_pending_task`'s EXISTS bodies to interpolate the same constants.
- **One line:** zero behavioral gain over #1 (the claim-guard doesn't need widening under the gate-time diagnosis), pure refactor with f-string/parameter-alignment risk on the hottest path — demoted to optional follow-up; C itself concedes "if PR2 is dropped… fine."

## 3. Comparison matrix

| Criterion | #1 C-PR1+B-shape | #2 A inline | #3 B standalone | #4 C-full |
|---|---|---|---|---|
| **Correctness coverage** (incl. folding hole) | Gate A+B+bg+maintenance closed; folding closed at gate-time + RED proof-test; residual TOCTOU documented w/ ticket | All sites closed incl. claim-time; closes the residual TOCTOU too | Gates only; claim seam open in principle (B's own 🔴) | Same as #1 (PR2 adds no behavior) |
| **Over-blocking risk** | Low — narrow tested busy-set; lineage-scoped; baseline GREEN #2 preserved | Low-Med — broader set incl. all `done` jobs w/ live instances (task rows too, not just mirrors) needs the always-on empirical check A names | Low | Low |
| **Three-site agreement** | **By construction** (one SQL body per predicate; 5 consumers import it; body-assertion test) | By discipline (3 hand-propagated sites, A flags lockstep risk) | Two SQL sites, defer/bg parity by discipline | By construction incl. claim (but wrapper still local — C's honest ceiling) |
| **Hot-path safety** | Claim SQL untouched | **Modifies the atomic claim** (A: HIGH risk) | Claim SQL untouched | Modifies the atomic claim (refactor) |
| **Performance** | ≈ current (one OR branch; existing `admission_state` index; EXISTS short-circuit) | +1 EXISTS in claim hot path (EXPLAIN required) | ≈ current | Same as #1 + refactor risk |
| **Diff size** | ~200 LOC (80 constants + widening + tests) | ~120-180 LOC (3 sites + binds + tests) | ~30 LOC | #1 + ~30 LOC |
| **2026-07-23 intent fidelity** | ✅ Idle at gate; claim-guard stays race-guard | ⚠️ Claim-guard partially re-defines idle | ✅ but incomplete | ✅/⚠️ |
| **Census 23** | unchanged (read-only predicate) | unchanged | unchanged | unchanged |

## 4. Key facts all three reports agree on (adopted without further debate)

- The busy-instance terminal set is `{completed, error, terminated, failed}` (existing W2 invariant, matches Mission projection F7 "all four revivable"); PAUSED holds by-design (`7ecf09e2`); WAITING_CHILDREN blocks (the entire point).
- Background gate **shares the fix** (same predicate, scope-parameterized: defer=project, background=system — §4.1 asymmetry preserved); no separate semantics.
- Self-deadlock is structurally handled by defer-queue exclusion (the candidate's own row is on the defer queue); A's extra candidate-instance bind is optional hardening — pin it with A's proposed self-deadlock test either way.
- Legacy `active` clause retained (pre-Fix-B rows, queue-stage rows with NULL instance pass through to Leg 2).
- Constitution: census stays 23 (no admission_state writers); one I3 clarifying line to document — **"a settled mirror of a non-terminal instance counts as live for the defer/background gate, terminal for everything else"** (dual-semantics line; docs/job-queue.md + a one-paragraph ADR-style note in decisions.md).

## 5. Open scenarios (named, not blocking)

- **Dead-lettered mirrors with live instances** (`admission_state='dead'`): neither the tested busy-set nor A's `IN ('queued','active','done')` covers it; B flagged it 🟡 (narrow frequency). Recommend a follow-up scenario test; if it turns out reachable in prod, the busy-set widens by one term in the shared constant (the #1 mechanism makes that a 1-line change).
- **Always-on population** (~145 non-terminal instances: experiencer/kb-importer/explorer): lineage scoping should confine them, but A's named empirical validation (do their `job_queue_items.instance_id` rows JOIN cross-project?) should run pre-merge — cheap SQL check.
- **Zombie reaper `_has_live_work`** (job_queue_service.py:1457): candidate 6th consumer of the shared predicate; left out of the fix branch deliberately (small-scope), noted for the follow-up.
