# The nest-ops shakedown — FOLLOW-UP LEDGER (deferred, NOT actioned)

Lane: `fix/nest-ops-shakedown-defects`. Opened by the tidier fold
(2026-10-25). Every row here is a DEFERRED item: recorded, NOT
actioned in this lane. Nothing in this file is a task for the next
commission unless it is re-opened there.

The tidier's rule for the fold: *a deferred extraction is a fine
outcome*. Deferral is a legitimate result, not a failure — and a
deferral recorded here is cheaper than a destabilising refactor two
days before the tester gate.

---

## 1. Carried into this fold (the tidier's own defer list)

| ID | Deferred item | Why it is still deferred | Where it would land |
|---|---|---|---|
| H1 | `_build_health_report` SRP split (one 400-line function: the windowed walk + seven detectors + the batched evidence legs) | The function's boundaries are load-bearing for the F4b window contract, the seen-set guard and the evidence-leg degradation. Splitting it now would touch the exact surface the tester is about to gate, for a readability gain that a later commission can take with the whole contract in view. | read-side decomposition commission |
| H3 | `daemon/repositories/instance/repository.py` is >3000 lines | A decomposition of that size is its own commission (module boundaries, the SQLModel session discipline, the `_enrich_instances` seam). Doing it inside a defect-patch lane would put a 3000-line file's worth of churn in the same diff as a behaviour patch — unreviewable in the time available. | repository decomposition commission |
| — | CHANGELOG placeholder ordering | The `<STAGED-VERSION>` placeholder is substituted by the giter lane at stage time (T5.5 shipped the literal placeholder on purpose). Re-ordering it in this lane would fight the stage-time substitution. | giter lane, at stage |
| — | Remaining lows: `Any`-typing, `_protection_reasons`' unused `manager` arg, the while-else idiom comment, `__all__`, the `_STATUS_CANONICAL_MAP` public alias, pre-existing unused imports | Cosmetic or API-surface changes with a real (if small) blast radius and no defect behind them. They belong to a cleanup lane, not to a defect patch. The one unused import this fold DID remove (`DISPATCH_CHAIN_KEY` in `daemon/tools/job_queue.py`) was removed only because the fold itself had just made it dead. | cleanup lane |

## 2. Added by the reviewer verdict (2026-10-24, the GO-for-the-tester-gate pass)

| ID | Deferred item | Why it is still deferred | Where it would land |
|---|---|---|---|
| ML-1 | The defensive message-lane metadata pop at `daemon/services/instance_messaging.py:2206` | UNREACHABLE today: no message-metadata ingress exists for the reserved key, so the pop never fires. The consumption-time lane check already backstops the same property. Removing it would delete a backstop for a path that does not exist yet; changing it would touch the FROZEN message-lane seam (the adjudication in flight at the reviewer explicitly froze `daemon/services/instance_messaging.py`'s `__ens_dispatch_chain__` metadata surface). | a future F11/DF-4 follow-up, once the ingress exists or the seam is unfrozen |

## 3. Recorded by this fold (the tidier pass, 2026-10-25)

| ID | Deferred item | Why it is still deferred | Where it would land |
|---|---|---|---|
| F-1 | The `not_` import in `daemon/repositories/instance/repository.py` (line 14) is unused — the code uses the `.not_in()` METHOD, not the `not_` function | Pre-existing (not introduced by this lane) and outside the fold's named scope; the commission's defer list already carries "pre-existing unused imports". | cleanup lane |
| F-2 | The census's CONSUMER-side pin (`test_a_null_census_group_key_is_bucketed_as_unknown`) is not mutation-sensitive ON ITS OWN | The consumer pin feeds a `FleetCounts` directly (the instances table's `agent_id`/`status` columns are NOT NULL, so a NULL group key cannot be produced through the real schema). The REPOSITORY-side defect is covered by the source-shape pin (`test_the_census_does_not_coerce_a_null_group_key_to_a_string`), which IS mutation-sensitive — verified RED. Together they cover the path; the consumer pin alone does not. | no action needed; noted so the next reader does not assume both pins are mutation-grade |
| F-3 | The `fake != REAL_HOME` arm of the S6 HOME fence's escape guard has no mutation leg | Defence in depth: `install_fake_home` CONSTRUCTS the fake from a `tmp_path`, so the fake can never BE the real home — the arm is unreachable by construction. The two reachable arms (the live-shaped guard and the "the redirect took effect" proof) ARE mutation-verified RED. | no action needed |

## 4. The two seams this fold left alone (and proved untouched)

| Seam | Status |
|---|---|
| The message-lane `__ens_dispatch_chain__` metadata surface (`daemon/services/instance_messaging.py`) | UNTOUCHED. `git diff` scoped to that file is EMPTY for this fold. The reviewer froze it; only the `_resolve_target_readonly` to_thread seam was released, and that seam is in `daemon/tools/instance_read.py`, not here. |
| The `asyncio.to_thread` shape around `_resolve_target_readonly` | RELEASED and folded (RIDE-ALONG 1): the `repo.get` and `manager.find_near_instance` calls now run off the event loop. The function's own shape — its signature, its four refusal texts, its WARNING — is unchanged, and the F8 spy pins stay green. |

## 5. What this fold DID land (for the next reader's orientation)

The fold's items 1–17 and the three ride-alongs all landed; see the
lane's git log from `f32e50527` (gate-token structural) through the
final commit. The structural pins added or tightened:

* the protected gate returns its refusal token STRUCTURALLY, and the
  preview derives `would_refuse_with` from it (the old hardcoded
  fallback could mislabel a confirmation refusal);
* a failed parent-status probe is never memoized (it poisoned every
  later child of that parent with a false orphan flag);
* a degraded husk evidence scan is visible in the payload
  (`evidence_legs: "degraded"`);
* a row served in two windows is counted ONCE (the seen-set guard);
* the F9 reclaim copy is pinned by EXACT-EQUALITY against ops-side
  §6.2 (a reworded clause no longer passes);
* the HOME fence is a MECHANISM, not a declaration.
