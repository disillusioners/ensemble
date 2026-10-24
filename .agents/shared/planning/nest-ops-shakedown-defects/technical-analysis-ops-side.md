# Technical Analysis (OPS SIDE): nest-ops shakedown defect patch

Date: 2026-10-22
Author: planner[v2] via technical-analysis worker (ops-side lane)
Worktree: `/home/nea/ensemble-src-wt-nestops-shakedown`
Branch: `fix/nest-ops-shakedown-defects` — base `origin/latest` `b257842c9`
Analysis depth: deep-dive (implementation-grade: a developer implements from this, a tester writes pins against it)
Status: Ready for Review
Scope: the WRITE side only — `daemon/tools/instance_ops.py` and the provenance/dispatch seam it depends on. The READ side (`daemon/tools/instance_read.py`) is covered by `technical-analysis-read-side.md` and is **not** re-designed here. Every file:line below was re-read in this worktree (or, for DF-4, in `/home/nea/ensemble-src-wt-df4` read-only) before being cited.

---

## Question

How must the five **ops-side** defects be fixed on the live `instance_ops` act surface so that:

1. **F11** — a protected-target terminate on a turn that arrived through an **agent→agent (job) delegation** of a genuine user-origin turn is **ADMITTED**, while a fabricated / expired / agent-rooted chain is still **REFUSED**;
2. **F6** — the zero-mutation **dry-run preview is reachable on a protected target without user provenance**, while the destructive act still refuses without it (Ari's owner-lane ruling);
3. **F7** — `already_protected` in the mark preview reflects **all** protection layers, not just the dynamic marker;
4. **F10** — the mark **apply** response carries the committed **audit row id**;
5. **F9** — the reclaim refusal stops recommending `instance_nudge` (actively dangerous for an idle husk) and offers the correct path;

…and so that every fix is pinned in **both directions** with mutation-verified regressions, in a tree that **contains DF-4** (the forged-root closure) before any delegation widening lands.

---

## Context Summary

`daemon/tools/instance_ops.py` (1844 lines) is the privileged `instance_ops` act category, built by one factory `create_instance_ops_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None)` (`:398`) returning six `@tool` closures — `instance_terminate` (`:497`), `instance_reclaim` (`:895`), `instance_nudge` (`:1048`), `instance_mark` (`:1199`), `instance_answer` (`:1447`), `instance_dismiss` (`:1602`) — `:1758-1765`. Authority is a **second** gate on top of the category: `INSTANCE_OPS_AUTHORIZED_AGENTS = frozenset({"maintenancer"})` (`:132`, helper `:161-174`, D-1), because a literal tool name in an allow list bypasses the category gate. Every act is audited through `daemon/repositories/instance_ops/repository.py` in two declared tiers (D-15): `write_audit_and_act` (`:88-152`, returns `row.id`) + `write_audit_ahead` (`:157-203`, returns `row_id`) + `settle_audit` (`:205-239`), with `write_preview` (`:244-282`) and `write_refusal` (`:284-323`) as single-transaction conveniences.

The five defects share one structural trait: **the act surface encodes policy in places that are not policy**. F6 encodes the provenance gate *before* the `dry_run` fork; the preview therefore cannot show what it is forbidden to do. F7 encodes "protected" twice — once as a three-layer OR (`_is_protected_target`, `:287-313`) and once as a marker-only `isinstance(...)` expression inside mark (`:1232-1235`). F10 discards a return value the ledger already produces. F9 lets a refusal string recommend a tool whose real behaviour on the target class in question is a **dispatch**. F11 is the deepest: the daemon already mints a genuine delegation chain on the `send_message` lane (`internal_agent:<caller_iid>` → `_inherit_provenance_window`, `daemon/manager.py:4919-5020`), but the **job** lane derives its dispatch source from the caller's *agent name*, not its *instance id* (`daemon/tools/job_queue.py:1318-1322`), so the inherit lane — which keys strictly on `internal_agent:` — **clears** the child's window and every job-dispatched terminate on a protected target refuses `no-user-provenance` (live audit row `276b7571-307c-4d76-a940-b28831a2e925`).

Two cross-cutting constraints bound the whole design:

- **DF-4 is a hard precondition for F11.** DF-4 (`fix/df4-provenance-gate`, tip per brief `9959f9f4b`; the DF-4 worktree currently sits one commit further at `240af3731`, a docs+pin commit — verify by *markers*, not SHA) closes the **forged root**: the upgrade sweep's bare-`"api"` fallback becomes the reserved literal `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE = "system:sweep_no_user_origin_wake"` (`daemon/tools/upgrade_journal.py:1191`; call sites `daemon/services/upgrade_journal_sweep.py:1217` / `:1748`), so a sweep-woken Ari can no longer mint a **direct** window with no human ancestor. Widening delegation **before** that closure would let the forged root ride the job lane straight into a protected terminate.
- **F6/F10 edit code DF-4 rewrites.** DF-4's instance_ops.py hunks are `@@ -42,20 +42,30 @@` (module gate-order docstring), `@@ -116,9 +126,9 @@` (category doc), `@@ -330,7 +340,13 @@` and `@@ -366,22 +382,6 @@` (`_protected_gate`), `@@ -806,13 +806,11 @@` (terminate `_full_doc_`). F6 changes the **same** blocks; F11 changes the **mint input**, not the gate. Sequence: **merge latest containing DF-4 → re-verify its closure markers → rebase the anchors → land F11, F6, F10, F7, F9**.

---

## 1. Executive summary — one paragraph per defect

### F11 — delegated (job) user-origin turns cannot arm the protected-target gate 🔴 HIGHEST
**Root cause.** The daemon's provenance mint reads the dispatch **source** string at the single funnel (`daemon/manager.py:9620` stamp, `:9628` inherit). `_inherit_provenance_window` (`:4919-5020`) keys **only** on `internal_agent:<caller_iid>` (`:4947`) and **clears** the entry for everything else (`:4949-4951`). The job tools derive their source server-side as `f"agent:{caller_agent_id}"` (`daemon/tools/job_queue.py:1319`; `job_continue` at `:2062`) — deliberately, as the B3.5 anti-forgery measure (`:1303-1317`, pinned by `tests/test_job_queue_tools.py:147-184`) — which names the caller's **agent**, not its **instance**, so the child is stamped windowless and `_protected_gate` refuses at `no-user-provenance` (`daemon/tools/instance_ops.py:357-368`).
**Chosen design.** Do **not** widen the source grammar. Carry a **daemon-minted enqueue chain token** (`caller_instance_id` captured at the tool seam where `current_instance_id` is already in hand) through the job row → message row → claim context → funnel, and let `_inherit_provenance_window` accept that token **as an alternative caller-identity source for the `agent:` lane only**, running the *same* claim arithmetic (caller direct-or-inherited, liveness at claim time, `depth+1`, cap 4, monotonic decay). The gate itself is untouched (post-DF-4 it is already `verdict.ok`-only). No new trust is minted: the chain confers exactly the authorization the caller already holds on the `send_message` lane.

### F6 — zero-mutation preview requires user provenance 🟠
**Root cause.** `_protected_gate` is called at `:557-583`, **before** the `if dry_run:` fork at `:696`; the gate ignores `dry_run` entirely. Both the confirmation conditions (`:338-355`) and the provenance conditions (`:357-384`) therefore block the preview, and the documented preview payload (`:708-737`) is unproducible on a protected target.
**Chosen design (Ari's ruling).** Move **only** the protected gate behind the `dry_run` fork. [CORRECTED per D-8 (iteration-003 — one of the two spots the iteration-002 sweep missed): ONLY the gate's USER-PROVENANCE conditions move past the fork; the confirmation conditions stay on BOTH paths — an unconfirmed preview still refuses `protected-target-confirmation-required`.] All input/target-validity rails (`auth`, kill switch, `reason`, resolve, self-surgery, row-gone, `target-too-young`) and the read-only **budget** + **cascade/orphan** checks stay on both paths (see §3 for the per-guard justification). The preview keeps its `provenance` echo and gains an `act_gate` / `act_gate_would_pass` block so it never implies apply will pass. The `previewed` audit row is written exactly as today (the ruling guarantees **no mutation of the target**, not silence in the ledger).

### F7 — `already_protected` reflects only the dynamic marker 🟠
**Root cause.** `already_protected` at `:1232-1235` is a bare marker-dict test; the canonical three-layer predicate is `_is_protected_target` (`:287-313`: leader `:297-298`, chat-root via `_is_user_facing_root_target` `:223-239` → `daemon/tools/instance.py:2563-2594`, marker `:309-312`).
**Chosen design.** Compute it from the canonical predicate and add a `protection_reasons: ["leader"|"chat-root"|"marker"]` echo, with one single-home helper both the predicate and the echo read. Apply-time semantics decided in §4: a `protect` marker on a **statically** protected target **succeeds with disclosure**; the existing `marker-already-set` refusal stays byte-identical for the **dynamic** case.

### F10 — mark apply omits the committed audit row id 🟢
**Root cause.** `audit.write_audit_and_act(...)` **returns** `row.id` (`repository.py:88-152`, `:147`), but the mark call site discards it (`:1339-1349`), and the park Tier-2 `row_id` never leaves its local (`:1281-1292`). All six previews already surface `audit_preview_row` (`:734`, `:948`, `:1091`, `:1264`, `:1498`, `:1650`).
**Chosen design.** Capture and echo `audit_row_id` on the three mark apply paths (park / protect / suspect). `preview_run_id` threading is **deferred** (capture-and-echo is enough to join preview→apply post-hoc; the column stays unused plumbing — see §5 and Adjacent F-4).

### F9 — reclaim refusal recommends a dangerous tool 🟢
**Root cause.** The refusal at `:918-928` says "use `instance_nudge` to deliver to a live one". `instance_nudge` on a graph-less target calls `manager.enqueue_message` (`daemon/tools/instance_ops.py:1117-1124`) — a **durable new turn** with no context and no evidence, i.e. exactly what a husk must never receive.
**Chosen design.** Rewrite the copy to name the *actual* paths (`instance_terminate` dry-run first; `instance_health` to read state; `instance_mark(marker="park")` only for a deliberate hold on a pausable target) and state **why** a nudge is wrong. Mirror the distinction into the reclaim and nudge `_full_doc_` blocks (`:991-1016`, `:1139-1157`).

---

## 2. F11 — DELEGATION-CHAIN DESIGN

> **⚖️ ARCHITECT ENRICHMENT 2026-10-24 — §7.3/§7.4 AMENDMENTS (binding; fold into T3.1/T3.3 per phase3-plan.md):**
> (1) **THE HOLE — `job_continue`'s chain is erased by the S3 pop as sketched**: `enqueue_message_job` (`instance_messaging.py:2695`) merges
> its `metadata` into `job_queue_service.enqueue` (`:3059-3077`) and S3 pops the reserved key — the chain never reaches the row. Fix: add
> `dispatch_chain: dict | None = None` as a KEYWORD on `manager.enqueue_message_job`, forwarded as a KEYWORD to `job_queue_service.enqueue`
> (never via metadata); ADD `daemon/services/instance_messaging.py` to the touchpoint table. (2) Normalize ONCE at the TOP of `enqueue`
> (before BOTH create sites: idempotency `:1481`, plain `:1607`); pin both. (3) REST injection closed by THREE controls (see the §2.5
> bracketed correction). (4) S6 must preserve EVERY fail-closed arm verbatim — the sketch omitted the `now_dt is None` guard
> (`:4997-4999`) and the exception-handler fail-closed pop (`:5042-5056`); carry both; exactly one POP path. (5) FREEZE LIST GAINS THREE
> surfaces: `stamp_user_origin_window`'s clear branch (`manager.py:4947`), `restart_provenance`'s direct-first read order
> (`provenance.py:198-233`), `_user_origin_last_stamp` observability (`manager.py:4930-4938`). (6) ADD pins: chain.caller_instance_id ==
> acting instance (mis-bound factory); silent-resume edge (checkpoint resume never mints an inherited window — today's semantics, document
> + pin). (7) DISCLOSE the DF-5 sibling residual (`job_queue_service.py:2605-2609` defer-resend mints `source="api"` → child gets a DIRECT
> window with no chain — out of scope, owner recorded). (8) All DF-4 citations here are base-relative; the §10 anchor-shift table
> (post-DF-4 `origin/latest` = `d4ecdbd3d`) supersedes.

### 2.1 The mechanism as it exists today (verified)

```
USER TURN (source="api" | telegram:… | discord:…)
  └─ manager.stamp_user_origin_window(instance_id, source, message_id)      manager.py:4855-4917
       classify_user_origin(source, registry.get)  upgrade_journal.py:3376-3426
         exact "api"  OR  registered source_type ∈ {telegram,slack,discord,whatsapp}
         reserved lanes fail closed (is_reserved_source)  :3396-3397
       → _user_origin_windows[iid] = {source, message_id, stamped_at, expires_at=now+60m}
       else → POP                                          (NONCE_TTL_S=3600, upgrade_journal.py:140)

DISPATCH to the child (the ONE funnel, every non-silent dispatch)
  └─ manager._process_message_with_tracking(...)                            manager.py:9554
       if not silent:
         stamp_user_origin_window(...)                                      :9620
         _inherit_provenance_window(instance_id, message_source)            :9628
           prefix == "internal_agent:" → caller_iid = source[15:]
             claim caller DIRECT window live?  → parent{depth 0}             :4966-4979
             else caller INHERITED live?       → parent{depth parent+1}      :4980-4984
             else POP (fail-closed)                                          :4985-4990
             depth > 4 → POP                                                 :4990-4993
             expires_at = min(parent_exp, now+60m)                           :4994-4998
             _inherited_provenance_windows[iid] = {root_source, root_message_id,
                                                  root_stamped_at, depth, expires_at}
           ANY OTHER SOURCE → POP                                            :4949-4951

READ (the gate's input)
  └─ restart_provenance(manager, caller_iid)  daemon/tools/provenance.py:170-267
       direct live → pclass="direct",   depth=0
       else inherited live → pclass="inherited", depth>=1, root_source, root_message_id
       else ok=False + observed-source diagnostic
```

**The break.** `job_create` derives `source = f"agent:{caller_agent_id}"` (`job_queue.py:1319`) — the **agent name**; `job_continue` derives the same (`:2062`); both are stored on `JobItem.source` (`daemon/repositories/job_queue/models.py:335`), copied to the message at dispatch (`daemon/services/job_processor.py:1327` → `enqueue_message(source=job.source)`), read by the claim path (`daemon/services/task_processor.py:496` → `ProcessingContext.message_source`) and handed to the funnel. `agent:` is not `internal_agent:` ⇒ POP ⇒ `_user_origin_last_stamp` records the observed source ⇒ the gate refuses `no-user-provenance` **even though the caller held a live, genuine chain**. `agent:` is *already* in `RESERVED_SOURCE_PREFIXES` (`daemon/constants.py:884-885`, `:949`) so it can never arm a user-origin window — the only thing missing is the **instance identity**, which the job lane throws away.

### 2.2 The three candidate designs (evaluated, not asserted)

**Option A — admit `agent:<caller_agent_id>` in `_inherit_provenance_window` with chain validation.**
The funnel would have to turn an **agent id** into **the caller instance** whose window to claim. Nothing in the source string carries it. Implementation would have to scan (`manager.instances` for live graphs, `InstanceRepository.list(agent_id=…)` for the rest), and then *choose among several instances of the same agent*. That choice is the design:

- **Ambiguity is not resolvable.** An agent can hold several live instances with several windows. Picking "any live window" converts a **per-instance** authorization into a **per-agent pool** — the exact semantic the mint's docstring refuses ("Instance-scoped by key (children never inherit — their sources are `internal_*`, S-06)", `manager.py:1560-1570`). Picking "newest `expires_at`" is deterministic but arbitrary, and the audit row would then name a `root_message_id` from a **sibling session** that the acting turn never touched.
- **Cost.** One repo read per candidate window key per dispatch; the window store is small, but this adds DB reads to the hottest dispatch path and re-introduces an instance lookup the claim path deliberately does not do.
- **Forgery.** Not a window forgery (the mint still requires a live parent), but a **provenance-fidelity** loss: "which user turn authorised this" becomes approximate. Post-hoc review (`root_message_id` vs `confirmed_target_id`) is the documented defense-in-depth layer (`decisions.md` D-17 layer (c)); Option A degrades exactly that layer.

**Option B — daemon-minted chain token carried in the job/message payload to the funnel.** *(recommended — §2.3)*
The tool seam that derives the source **also** knows `current_instance_id` (the factory binding, `job_queue.py:1209`). Capture it daemon-side into a reserved payload slot at **enqueue**, thread it unchanged to the funnel, and let the mint claim *that* instance's window. Preserves per-instance semantics exactly, adds a pointer (not a trust), and survives queue delay because the mint still runs at **claim** time against the live window store.

**Option C — variants.**
- *C1 (mint at enqueue into a pending store keyed by message id)*: either the registration races the worker claim (a fast parallel-queue claim can reach the funnel before the pending entry exists → intermittent `no-user-provenance`, i.e. the very bug being fixed), or the registration must happen **inside** `enqueue_message` — at which point it converges on Option B with an extra store to clean up. Rejected.
- *C2 (change the derived source to `internal_agent:<caller_instance_id>`)*: the smallest diff (two lines, `:1319` / `:2062`), and it would work with the existing mint untouched. Rejected on two grounds: (i) it **destroys the operator-facing "who submitted this job" signal** — `JobItem.source` currently reads `agent:ari`, and would read `internal_agent:<uuid>` (the agent *name* disappears from every job listing/UI surface); (ii) it rewrites a **security pin's exact assertion** (`tests/test_job_queue_tools.py:147-184` pins `agent:test-agent` as the B3.5 anti-forgery outcome) and collapses the job lane into the `send_message` lane inside the source taxonomy, so an observer can no longer tell a job dispatch from a direct delegation.

### 2.3 Comparison

| Criterion | A — widen `agent:` | **B — chain token** | C1 — pending store | C2 — source rewrite |
|---|---|---|---|---|
| Forgery resistance | Medium (per-agent pool; no new mint of trust, but approximate attribution) | **Strong** (daemon-bound value, stripped at the service seam, parent liveness still required) | Strong | Strong |
| Provenance fidelity | Low (arbitrary sibling) | **Exact** (per-instance, real `root_message_id`) | Exact | Exact |
| Blast radius | `manager.py` + tests | 7 files, **all additive** (§2.4) | 8+ files + a new store | 2 lines + **rewrites a security pin** |
| DF-4 freeze compatibility | OK | **OK** (touches no frozen surface — §2.6) | OK | OK |
| Queue-delay semantics | Claim-time (correct) | **Claim-time (correct)** | Enqueue-time (stale copies) or racy | Claim-time (correct) |
| Restart behaviour | fail-closed | **fail-closed** (token is a pointer; window dies) | fail-closed | fail-closed |
| Mutation-provable pins | Hard (non-deterministic choice) | **Both directions + an injection pin** | Hard (race) | Easy but churns existing pins |
| Operator observability | unchanged | **unchanged** (`agent:ari` stays) | unchanged | **degraded** |

**Pick: Option B.**

**Reasoning.** B is the only option that keeps the three properties the existing design actually cares about — **per-instance** windows, **exact** attribution in the ledger, and an **unchanged** source taxonomy — while adding no new trust: the token is a *pointer to the caller instance*, and the authorization is still read from that instance's live window at claim time, through the same arithmetic (liveness, depth cap, monotonic decay). A and C1 buy a smaller diff by weakening attribution or introducing a race; C2 buys the smallest diff by degrading observability and rewriting a security pin. The cost of B is mechanical threading across seven files, each change ≤ 15 lines and each independently testable.

**Assumptions.** (i) `current_instance_id` in the job tool factory is daemon-bound (it is: the closure parameter at `job_queue.py:1209`, sourced from the tool-construction site — never a caller argument). (ii) `enqueue_message`/`enqueue_message_job` accept a metadata dict that lands on `MessageQueue.message_metadata` (it does: `daemon/services/instance_messaging.py:2206` `message_metadata=metadata or {}`). (iii) DF-4 has landed, so the only sources that can mint a **direct** window are genuine user-origin ones.

**Reversibility.** High. The chain is additive and default-off: with the token absent (legacy job rows, REST lane, every other dispatch lane) the funnel behaves byte-identically to today. Removing the feature = dropping the token write at two tool sites.

### 2.4 Precise specification

**S1 — chain record shape** (the only new data structure; no schema change):

```python
# Reserved job/message metadata key. Double-underscore namespace, daemon-write-only.
DISPATCH_CHAIN_KEY = "__ens_dispatch_chain__"

chain = {
    "caller_instance_id": str,      # the SUBMITTING instance (daemon-bound)
    "caller_agent_id": str | None,  # observability only; NEVER used for trust
}
```

**S2 — mint sites (write).** Both `agent:`-deriving tool bodies pass the chain into the enqueue call:

| Site | Today | Change |
|---|---|---|
| `daemon/tools/job_queue.py:1318-1322` (`job_create`) | `job_service.enqueue(agent_id=…, source=source, metadata=metadata, …)` (`:1408-1421`) | add `dispatch_chain={"caller_instance_id": current_instance_id, "caller_agent_id": caller_agent_id} if current_instance_id else None` |
| `daemon/tools/job_queue.py:2062` (`job_continue`) | `manager.enqueue_message_job(instance_id=…, message=…, source=f"agent:{caller_agent_id}" if caller_agent_id else "internal_agent:unknown")` (`:2060-2067`) | add `metadata={DISPATCH_CHAIN_KEY: chain} if chain else None` |

(`job_inject` is **not** a mint site: it already stamps `internal_agent:<caller_iid>` at `:3275` / `:3329` and inherits today. No change there.)

**S3 — service seam (single choke point; this is where forgery dies).** `daemon/services/job_queue_service.py::enqueue` gains a keyword-only `dispatch_chain: dict | None = None` and **always** does:

```python
job_metadata = dict(metadata or {})                 # caller-supplied map
job_metadata.pop(DISPATCH_CHAIN_KEY, None)          # caller can NEVER set it
if dispatch_chain:
    job_metadata[DISPATCH_CHAIN_KEY] = dispatch_chain  # daemon value wins by construction
```

`retry_job` (`job_queue_service.py:2809-2820`) already re-enqueues with `metadata=job.job_metadata`; it must now also pass `dispatch_chain=(job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)` so the unconditional pop does not silently drop the chain on retry. **Consequence:** the REST ingress (`daemon/routers/jobs_crud.py:592`, `metadata=body.metadata`) cannot inject a chain — a REST caller's `__ens_dispatch_chain__` is popped, and the pin in §7 proves it.

**S4 — dispatch (job → message).** `daemon/services/job_processor.py` at its three dispatch sites (`:1001`, `:1093`, `:1327`) forwards **only** the reserved key:

```python
chain = (job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)
... = await self._instance_manager.enqueue_message(
    instance_id=instance_id, message=job.message, source=job.source,
    metadata={DISPATCH_CHAIN_KEY: chain} if chain else None,   # NEW
    is_deferred=..., is_background=..., work_id=job.job_id,
)
```

**S5 — claim (message → context → funnel).** `daemon/services/task_processor.py` already reads `message_metadata` (`:496-520`, `task_context`, `silent`, `resume_mode`); add:

```python
dispatch_chain = (message_metadata or {}).get(DISPATCH_CHAIN_KEY)   # NEW
context = ProcessingContext(..., dispatch_chain=dispatch_chain)     # NEW field
```

`ProcessingContext` gains `dispatch_chain: dict | None = None` (`daemon/services/message_processing_pipeline.py:120-175`; the `is_fresh_episode_user_message` field at `:166-175` is the precedent — a persisted-row-derived value threaded claim → context → funnel). `_do_process` (`:419-447`) forwards it as a keyword to the manager funnel. `manager._process_message_with_tracking` (`:9554`) gains `dispatch_chain: dict | None = None` and calls:

```python
if not silent:
    self.stamp_user_origin_window(instance_id, message_source, message_id)   # :9620 UNCHANGED
    self._inherit_provenance_window(instance_id, message_source,
                                    dispatch_chain=dispatch_chain)          # :9628 extended
```

**S6 — the mint (the only provenance-logic change).** Refactor the claim body into one local helper so there is exactly **one** mint implementation, and select the caller identity by lane:

```python
def _inherit_provenance_window(self, instance_id, source, dispatch_chain=None):
    caller_iid = None
    if isinstance(source, str) and source.startswith("internal_agent:"):
        caller_iid = source[len("internal_agent:"):].strip() or None      # existing lane, byte-identical
    elif (isinstance(dispatch_chain, dict)
          and isinstance(source, str) and source.startswith("agent:")):   # NEW: job-dispatch lane only
        candidate = dispatch_chain.get("caller_instance_id")
        if isinstance(candidate, str) and candidate.strip():
            caller_iid = candidate.strip()
    if not caller_iid:
        self._inherited_provenance_windows.pop(instance_id, None)         # fail-closed, unchanged
        return
    # ── the EXISTING claim arithmetic, verbatim, now shared by both lanes ──
    # direct(caller) live → depth 1 ; else inherited(caller) live → depth+1 ; else POP
    # depth > _INHERITED_PROVENANCE_MAX_DEPTH → POP
    # expires_at = min(parent_expires_at, now + PROVENANCE_TTL_S)
```

**Window-entry shape is unchanged** (`{root_source, root_message_id, root_stamped_at, depth, expires_at}`) — which is what satisfies the "root_source=user-origin + depth>0" requirement: `depth ≥ 1` by construction, `root_source` is the caller's inherited `root_source` or the caller's **direct** window `source` (post-DF-4, only genuinely user-origin sources can produce that).

**S7 — gate acceptance rule (no change needed).** Post-DF-4 `_protected_gate` is two conditions — confirmation, then `verdict.ok` (DF-4 `daemon/tools/instance_ops.py:353-385`). `restart_provenance` reads the inherited store (`provenance.py:196-223`) and returns `ok=True, pclass="inherited", depth=≥1` for a live entry, so the minted chain is admitted **within the same 60-minute window rules as depth=0**: the entry's `expires_at` is `min(parent_expiry, now + PROVENANCE_TTL_S)`, so it can never outlive the user's original window. Pre-DF-4 the gate additionally required `pclass == "inherited"` — which a minted chain **also** satisfies, so F11 is functionally correct on either side of the merge; the dependency is **semantic** (forged-root closure), not mechanical.

**S8 — audit ledger (unchanged, preserved).** No schema change, no new columns. An F11-admitted terminate writes `provenance_class="inherited"`, `provenance_depth=N`, `root_source=<genuine user source>`, `root_message_id=<user message>` through the existing `prov_fields` plumbing (`instance_ops.py:672-679`, refusal write `:566-581`, preview `:699-707`). `root_source`/`depth` stay **countable** across the ledger (the requirement: keep). `preview_run_id` is not involved.

### 2.5 Forgery analysis (what a hostile middle agent can and cannot fabricate)

| Attack | Outcome | Why |
|---|---|---|
| Agent submits a job while holding **no** window and hopes the child inherits | **REFUSED** | The mint claims the caller's DIRECT **or** INHERITED window; neither exists ⇒ POP (`:4985-4990`). |
| Agent stamps `metadata={"__ens_dispatch_chain__": {"caller_instance_id": "<ari_iid>"}}` on `job_create` body (REST) | **REFUSED** | `job_queue_service.enqueue` pops the key unconditionally (S3); the pin in §7 is this row. [CORRECTED per arch §7.4: THREE controls, not one — (a) the S3 pop; (b) the LANE check: the chain is honoured only when `source.startswith("agent:")`, which covers `manager.enqueue_message(metadata=…)` writing `message_metadata` directly onto the message row (`instance_messaging.py:2206`), bypassing the job seam; (c) the HTTP reserved-source gate — `jobs_crud.py:~530-545` rejects `RESERVED_SOURCE_PREFIXES` (contains `"agent:"`, `constants.py:946`) with 422. Also note the S3 pop must be normalized ONCE at the TOP of `enqueue`, before BOTH create sites (idempotency `:1481`, plain `:1607`) — per-site pops leave one path open; pin both.]
| Agent passes a hand-built `__ens_dispatch_chain__` through `send_message(context=…)` | **REFUSED** | `context=` lands as `message_metadata["task_context"]` only (`daemon/tools/instance.py:4366`); message-metadata key space is daemon-authored, and the chain is only honored when `source.startswith("agent:")` — `send_message` dispatches `internal_agent:`. |
| Sweep-woken Ari (no human ancestor) delegates a job | **REFUSED** | Post-DF-4 the sweep mints `system:sweep_no_user_origin_wake` ⇒ `classify_user_origin` fails closed (`upgrade_journal.py:3396-3397`) ⇒ Ari holds **no** direct window ⇒ nothing to claim. **This is the row that fails if F11 lands before DF-4.** |
| Agent holds a window from an hour ago | **REFUSED** | Liveness is re-checked at **claim** time (`:4966-4979`); `expires_at` is compared to `now`. |
| Agent chains delegations to launder depth | **REFUSED at depth 5** | `_INHERITED_PROVENANCE_MAX_DEPTH = 4` (`manager.py:222`, check `:4990-4993`), shared by both lanes. |
| Agent extends its own authority by chaining | **no gain** | The child's entry is monotonic-decayed and **shorter** than the parent's; the chain never adds time and never adds scope. |
| Hand-populated store (a bug, not an agent) | **ADMITTED** | The gate is not a proof (D-18 / DF-4 doctrine). F11 narrows **what the mint can produce**; it does not make the gate a proof, and the AC-23b "pinned as truth" fixture stays honest. |

**Net:** F11 confers exactly the authorization the same agent already has on the `send_message` lane (`instance.py:966-971`, `:3495`, `:4154`, `:4261`), at a seam where the daemon already knows the true caller instance. That is the requirement — *stop discarding authority*, not *create it*.

### 2.6 DF-4 sync step (mandatory, explicit, ordered)

**Protocol (one commit, before any F11 code):**

1. **Merge latest containing DF-4** (`git fetch origin && git merge --no-ff origin/latest`); if latest has not yet absorbed the DF-4 branch, merge `fix/df4-provenance-gate` explicitly and record which.
2. **Verify the closure is present** (markers, not SHAs):
   - `grep -rn "SWEEP_NO_USER_ORIGIN_WAKE_SOURCE" daemon/` → the literal in `upgrade_journal.py` **and** both sweep call sites;
   - `grep -rn "direct-provenance-insufficient" daemon/ tests/` → **EMPTY**;
   - `_protected_gate` in `daemon/tools/instance_ops.py` → exactly two conditions (`confirmed_target_id is None`, `!=`, then `not verdict.ok`).
3. **Verify the pins carried** — the renamed genuine-path pins (`test_genuine_direct_window_admits_protected_act`, `test_genuine_inherited_window_admits_protected_act`) and DF-4's sweep/ari-fallback pins exist and are green **before** F11 touches the mint.
4. **Coordinate, don't duplicate.** DF-4 owns: the sweep literal, the gate rewrite, the token mapping pin (`tests/unit/test_instance_ops_registration.py:316-328` becomes "exactly one provenance token"), and the doc corrections. F11 **adds** mint-side pins and does not touch `_protected_gate`, the sweep, or `classify_user_origin`.
5. **Re-base the anchors.** Every remaining file:line in this document that points at `instance_ops.py` moves by DF-4's hunks (`_protected_gate` +10/−16 net, docstrings); re-grep before editing.

**What F11 must NOT change (DF-4's freeze list, `df4-provenance-closure/plan-overview.md:119`):** `stamp_user_origin_window`'s bytes (`manager.py:4855-4917`), `classify_user_origin`'s arm set (`upgrade_journal.py:3376-3426`), the upgrade factor-2 read (`upgrade_tools.py:3318-3366`), the restart gate (`upgrade_tools.py:2720-2730`), the `provenance.py` relocation / alias block. F11 touches **none** of them: it adds a parameter to `_inherit_provenance_window` and a funnel kwarg. **F11 also does not close** the REST-ingress class (critical note `257488dc`: `jobs_crud.py:597` still accepts `body.source="api"`, which mints a **direct** window on the spawned child) nor the DF-5 defer-resend mint; it must simply not add a *new* injection point.

---

## 3. F6 — dry-run gating (per Ari's ruling)

> **⚖️ SUPERSEDED BY D-8 (leader adjudication 2026-10-24, minimal reading — binding):** the confirmation conditions STILL gate the `dry_run` preview; ONLY the user-provenance conditions move past the fork; the act keeps ALL gates. The stale whole-gate statements — SEVEN spots (iteration-003 corrected count; the iteration-002 enumeration missed two): §1 exec-summary F6 paragraph, §3.1, §3.2 line 10, §3.3 guards-table row 10, §3.4, §3.6, and §7.2 F6-i — are **bracket-corrected in place below** (history preserved; never silently rewritten — DF-4 bracket precedent). **Iteration-001 addition to the stale-spot list: `phase5-plan.md` T5.2(c) carried the same stale phrasing** (the §8 enumeration covered only this doc; the plan files were swept and corrected in the iteration-001 fix pass — T5.2(c) now carries the T3.10 wording verbatim). The original stale statements follow; bracket precedent). The whole-gate form is preserved as the documented user-veto path. **D-8 and phase3-plan.md T3.6 are the sole authorities**; also fold the arch §8 refinements: compute/write separation, anchored `_token_of`, token-only preview, F6-iv precedence inversion. See decisions.md D-8 + phase3-plan.md T3.6 + architecture-recommendation.md §8.

### 3.1 Ruling and its exact reading

> "Allow `dry_run=TRUE` previews on protected targets — previews write nothing (note: the preview DOES write an audit `previewed` row — account for that: no MUTATION of the target is what the ruling guarantees), so they do not weaken the act gate; the provenance gate stays EXACTLY as-is on the destructive act."

**Reading applied here:** the entire **protected-target gate** (confirmation-required, confirmation-mismatch, `no-user-provenance`, and — pre-DF-4 — `direct-provenance-insufficient`) is **apply-only**. The confirmation parameter is *also* bypassed on preview, because requiring it to preview recreates the reported dead end (the operator named the target verbatim and was still refused); a preview cannot consent to anything. The preview instead **teaches** the requirement via `act_gate_would_pass`. **This is the one interpretive decision the owner lane may veto** (see §9 Q1). [CORRECTED per D-8 (2026-10-24) — SUPERSEDED by the minimal reading: **only the user-provenance conditions are apply-only**; the confirmation conditions (required + verbatim match) run on BOTH paths — an unconfirmed preview on a protected target still refuses `protected-target-confirmation-required`. Confirmation is agent-satisfiable (the shakedown proved it), and minimal widening is the conservative posture on a privileged surface. Q1 is DECIDED (leader adjudication); the whole-gate form survives only as the user-veto path.]

### 3.2 The reordered terminate body

```
 1  auth (_is_instance_ops_authorized)                    BOTH
 2  kill switch (_resolve_instance_ops_enabled)           BOTH
 3  reason-required                                       BOTH
 4  resolve target → instance-not-found  (audited)        BOTH
 5  self-surgery-refused                                  BOTH
 6  instance-not-found (row gone)                         BOTH
 7  target-too-young (<600s)                              BOTH
 8  budget rails (read-only SELECT over the ledger)       BOTH   ← kept, see 3.3
 9  cascade=False + live descendants (param check)        BOTH   ← kept, see 3.3
10  PROTECTED GATE  ───────────────────────────────────── APPLY ONLY  ← F6 CHANGE  [CORRECTED per D-8: the gate SPLITS here — confirmation conditions on BOTH paths; ONLY the user-provenance condition apply-only]
11  if dry_run: preview (writes `previewed` row)  |  else: apply (Tier-1)
```

### 3.3 Per-guard justification

| Guard | Paths | Why |
|---|---|---|
| 1-7 | both | Input/target validity. A preview of a nonexistent / too-young / self target is not a preview, it is a lie. |
| 8 budget | both | **Read-only** (two `SELECT`s over `instance_ops_audit`, `repository.py:328-371`). The preview payload must populate `budget` anyway (`:589-604`). Keeping the refusal on both paths preserves today's behaviour **byte-identically for non-protected targets** — making it apply-only would be a *second* behavioural change outside this defect. An exhausted budget is exactly what a preview should say out loud. |
| 9 cascade/orphan | both | A **parameter** validity check (`cascade=False` + live descendants without `allow_orphan_descendants`), independent of provenance, and the refusal already **names** the descendants (`:648-679`) — refusing is the informative path. |
| 10 protected gate | **apply only** | F6's defect + Ari's ruling. Mutating nothing is the invariant; the gate protects the mutation. [CORRECTED per D-8 (iteration-003 — the second missed spot): the gate SPLITS here — confirmation conditions on BOTH paths; only the user-provenance conditions apply-only.] |

### 3.4 Preview payload changes

Keep every existing key (`:708-737`) and add:

```json
"protected": true,
"protected_confirmation_required": true,
"confirmation": "required",
    [CORRECTED per D-8: on a REACHABLE preview (post-confirmation) these keys reflect the SHIPPED computation at instance_ops.py:717-721 —
    "confirmation" reads "provided", not "required"; "protected_confirmation_required": true appears only on the CONFIRMATION-REFUSAL output — which under D-8 fires on BOTH paths (an unconfirmed preview refuses too); there is no ‘bypassed apply-path’ vocabulary under D-8 (iteration-003 note); the superseded original phrasing was "appears only on the (bypassed, apply-path) refusal".
    And "would_refuse_with" below must NOT list the confirmation tokens on the preview path — they already passed; list only
    the apply-path provenance token(s).]
"act_gate": {
  "confirmation": "confirmed_target_id must equal target verbatim",
  "provenance": "a LIVE user-origin provenance window on this turn; a delegated chain (send_message OR job dispatch) is accepted; apply refuses with no-user-provenance when absent",
  "would_refuse_with": ["protected-target-confirmation-required", "confirmed-target-mismatch", "no-user-provenance"]  [CORRECTED per D-8: preview-path value is ["no-user-provenance"] only — confirmation already satisfied on any reachable preview]
},
"act_gate_would_pass": {"ok": false, "reason": "no-user-provenance"}
```

`act_gate_would_pass` is computed by **calling `_protected_gate` for diagnostics only** and rendering its refusal token as data. Implementation shape:

```python
if dry_run:
    gate_diag = _protected_gate(...) if protected else None      # DATA, not a refusal
    ... payload["act_gate_would_pass"] = {"ok": gate_diag is None,
                                          "reason": _token_of(gate_diag)}
    return json.dumps(payload) + "\nNO mutation happened (dry-run)"
```

(`_token_of` parses the `reason=` token out of the house envelope, or return `None` when the gate passes.) **Do not** write a second gate implementation; one function, two uses. [CORRECTED per arch §8: (1) `_token_of` must be ANCHORED to the envelope prefix — `^Error: .*? REFUSED — reason=([a-z0-9-]+):` — the gate message embeds `confirmed_target_id={…!r}` and a source hint, so an unanchored search is a decoy risk; (2) the preview carries TOKENS ONLY, never the raw refusal string (it embeds the target id and the caller's own confirmation value); (3) compute/write separation: `_protected_gate` is PURE (`str | None`, no ledger write — the write is at the caller, `instance_ops.py:568-581`), so compute `gate_refusal` once, write a refusal only on the real refusal path (confirmation failures on BOTH paths; provenance refusal on apply only), and on a preview render `act_gate_would_pass = {"ok": gate_refusal is None, "reason": _token_of(gate_refusal)}`.]

### 3.5 Audit-row consistency with the ruling

`write_preview` (`repository.py:244-282`) writes `dry_run=True`, `outcome="previewed"`; it carries the provenance fields already (`:270-279`) and the preview call site passes `**prov_fields` (`instance_ops.py:699-707`). **No change.** The ruling's guarantee is about the **target**, not the ledger: a `previewed` row is system observability and **does not consume the budget** (`count_committed_*` filters `outcome == "committed"`, `repository.py:328-345` / `:347-371`). Pin §7/F6-iii proves that.

### 3.6 Doc surfaces that must mirror

- `instance_terminate._full_doc_` (`:798-853`, DF-4 also rewrites the gate list at `:806-818`): item 5 must read "(**APPLY ONLY** — a dry-run preview is always allowed; the preview reports `act_gate_would_pass`)". [CORRECTED per D-8: item 5 SPLITS — the confirmation conditions read "(BOTH paths — a preview still names its target)"; the provenance condition reads "(APPLY ONLY — a dry-run preview reports `act_gate_would_pass` instead of requiring user provenance)". Also add the F6×F11 posture sentence: the preview may report `no-user-provenance`, which the operator cannot self-satisfy without a genuine user turn — say so plainly, or the preview reads as "one more call from termination".]
- Module docstring `THE GATES, IN ORDER (terminate)` (`:41-47`, DF-4-edited): annotate the protected block `[apply only]`. [CORRECTED per D-8 (iteration-003 remedy 3): the annotation is `[apply only — provenance]` on the PROVENANCE line ONLY — the confirmation lines stay unannotated (= both paths); a bare `[apply only]` on the whole block is the superseded whole-gate form.]
- Category doc (`:110-120`): "Every destructive act defaults to `dry_run=True` — the preview … mutates nothing … and reports what apply will require."
- AC-16-style doc pin (`tests/unit/test_instance_ops_behaviour.py:~585-600` asserts the `dry_run: True (default) = preview, no mutation.` sentence): **update**, do not delete — add the apply-only sentence to the assertion.

### 3.7 What F6 must NOT do

Do not relax the gate on the **apply** path, do not pre-compute `confirmed_target_id` for the caller, do not let the preview write `outcome` other than `previewed`, and do not move the budget/cascade checks (justified above).

---

## 4. F7 — `already_protected` must mean *protected*, not *marked*

### 4.1 Single-home predicate

```python
def _protection_reasons(manager: Any, info: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if (info.get("agent_id") or "") == "leader":
        reasons.append("leader")
    metadata = info.get("metadata") or {}
    source_type = metadata.get("source_type") if isinstance(metadata, dict) else None
    if _is_user_facing_root_target(parent_id=info.get("parent_id"), source_type=source_type):
        reasons.append("chat-root")
    if isinstance(metadata, dict):
        marker = metadata.get(PROTECT_MARKER_KEY)
        if isinstance(marker, dict) and marker.get("protected") is True:
            reasons.append("marker")
    return reasons

def _is_protected_target(manager, info) -> bool:
    return bool(_protection_reasons(manager, info))     # ONE home; the OR becomes a consequence
```

`_is_protected_target` keeps its name and its call site in terminate (`:558`) — byte-identical behaviour, one implementation. The reason vocabulary is **three closed values** — D-19 discipline (no new open vocabulary; the values are the three existing layers).

### 4.2 Preview payload

```json
"marker": "protect",
"already_protected": true,
"protection_reasons": ["leader"]
```

`already_protected` becomes `bool(_protection_reasons(...))`. `protection_reasons` is preview-only (the apply response keeps its current shape plus F10's row id — do not grow two surfaces at once).

### 4.3 The idempotence question: marker on a **statically** protected target

**Decision: SUCCEED WITH DISCLOSURE (write the marker, disclose the static layers), not refuse.**

Rationale:
1. Protection is **monotone by design** — "A protect marker can only ADD protection; nothing in this category can un-protect a statically protected target" (`:281-286`). Writing a redundant marker cannot weaken anything.
2. The existing `marker-already-set` refusal exists for a different condition — the **same** dynamic marker already present (an idempotent no-op). Overloading it for "statically protected" conflates *"you already did this"* with *"this was already true for another reason"*, and would deny an operator the ability to record **intent** ("protect this leader turn") in metadata + ledger.
3. Refusing would also make the tool's behaviour depend on a derivation the operator cannot see — exactly what F7 exists to fix. With `protection_reasons` in the preview, the operator sees the truth *before* applying, and the apply discloses it in the audit `before_snapshot` (add `"protection_reasons": [...]`).
4. It keeps the dynamic case byte-identical: `marker == "protect" and already_protected` becomes `marker == "protect" and "marker" in reasons` — the same refusal, the same token, for the same reason.

**Consequence to pin:** applying `protect` to a leader target writes the marker and returns `marked: true` with `already_protected: true` + `protection_reasons: ["leader"]` (plus F10's `audit_row_id`).

---

## 5. F10 — mark apply must surface the committed audit row id

### 5.1 Shape

protect / suspect (Tier-1), `:1339-1350`:

```python
row_id = audit.write_audit_and_act(session, action="instance_mark", target=…, …)
session.commit()
...
return json.dumps({
    "marked": True,
    "action": "instance_mark",
    "target": target_id,
    "marker": marker,
    "audit": "committed (Tier-1, same transaction as the act)",
    "audit_row_id": row_id,            # NEW — captured inside the session (id is a client-side default, safe after commit)
}, default=str)
```

park (Tier-2), `:1281-1315`: the local `row_id` already exists; echo it after `settle_audit`:

```python
"audit": "committed (Tier-2 write-ahead)",
"audit_row_id": row_id,                # NEW (None only if the ledger was unavailable)
```

**Why capturing inside the session is safe:** `InstanceOpsAudit.id` is a client-side `default_factory=_new_uuid` (`models.py:104-108`), not a DB sequence — the value exists before `flush()`, so no `DetachedInstanceError` risk (contrast the deliberate `write_audit_ahead` capture-inside-session comment, `repository.py:196-199`, which exists for the same reason).

### 5.2 `preview_run_id` threading — DEFERRED (capture-and-echo only)

Reasons to defer:
1. **No caller-side correlation primitive exists.** The preview returns `audit_preview_row` (`:1264`); apply takes no `preview_row_id` argument. Threading would require a **new input parameter** on `instance_mark` (surface growth on a security-adjacent tool) or a server-side "last previewed row for (action,target)" lookup — which is ambiguous under concurrent previews and would silently attach the wrong run.
2. **The join is already possible post-hoc**: `audit_preview_row` (preview) and `audit_row_id` (apply) are both surfaced; a reviewer joins them by target+time+actor. That satisfies the `maintenance_runs.dry_run_run_id` pattern's *purpose* (traceability) without a write-path change.
3. The column is unused plumbing today (zero write sites); leaving it unused is an honest adjacent finding (Adjacent F-4), not a defect this patch must close.

If a later commission wants true linkage, the clean shape is an **optional** `preview_row_id` input validated against the ledger (action+target match, `outcome="previewed"`), written through `write_audit_and_act(preview_run_id=…)` — which already accepts the parameter (`repository.py:88-152`). Record that as the deferred design, do not build it here.

---

## 6. F9 — the reclaim refusal must not recommend a dispatch

### 6.1 Why the current copy is dangerous

`instance_nudge` on a non-live target falls through to `manager.enqueue_message` (`:1117-1124`) — a **durable new turn**: bare `HumanMessage`, no `context=`, no evidence, and a dispatch source `instance_ops:<caller_iid>` that is **not** `internal_agent:`-prefixed, so it also **pops the target's provenance windows**. On an idle, evidence-free husk this manufactures exactly the turn the husk never had.

### 6.2 The corrected copy (CANONICAL — iteration-003 remedy 2: the SINGLE source; D-11 and T3.9 cite this block and F9-iii pins it VERBATIM)

```
f"{target_id!r} is {status!r}, not terminal. instance_reclaim is TERMINAL-ONLY "
"(D-20). For a non-terminal target: run instance_terminate with dry_run=True "
"first if it is a genuine wedge (then apply with confirmed_target_id when the "
"target is protected — WITHOUT a live user-origin chain, direct or delegated, "
"apply refuses no-user-provenance); use instance_health / instance_get to read "
"its state; use instance_mark(marker='park') only for a deliberate hold on a "
"pausable target. Do NOT nudge an idle, evidence-free instance — a nudge "
"DISPATCHES a context-free turn."
```

Properties: names `instance_terminate` (the actual remediation), keeps `reason=target-not-terminal` and the D-20 provenance (**reclaim is terminal-only by design**, `decisions.md` D-20), contains **no** `instance_nudge` recommendation (the pin asserts the token's absence), and states the hazard in one clause so an agent reading only the refusal cannot choose the dangerous path.

### 6.3 Doc cross-references that must stay consistent

| Surface | Current text | Required change |
|---|---|---|
| `instance_reclaim._full_doc_` (`:991-1016`) | "NON-terminal targets refuse with `target-not-terminal` (that is what distinguishes reclaim from `instance_nudge`, which delivers to any target)." (`:1000-1002`) | Keep the descriptive distinction; add "a nudge to an idle, evidence-free target dispatches a context-free turn — it is not a diagnostic". |
| `instance_nudge._full_doc_` (`:1139-1157`) | "Inject a message into ANY instance … A send to a TERMINAL instance REVIVES it" | Add one paragraph: for an idle, never-dispatched instance a nudge creates a **new turn** (no context, no evidence); prefer `instance_health` to diagnose and `instance_terminate` (dry-run first) to remediate. |
| `CATEGORY_DOC` (`:108-120`) | lists the six acts | No change (F9 is copy-level). |

---

## 7. Test / e2e strategy input

### 7.1 Environment rules (binding — same as the read-side lane, §9.1)

- **Real PostgreSQL** for every act/ledger pin. A fresh-SQLite boot is blocked by the pre-existing migration prefix defect `20260714_000001`; the runner is SQLite-only and no-ops on PG.
- **Disposable database**, never `ensemble_prod`, never the live daemon on `:9797`. Copy the existing patterns: `tests/helpers/checkpoint_prune_pg.py:203-236` (`create_disposable_db` / `drop_database` with `WITH (FORCE)` and a `pg_terminate_backend` fallback) or the `tests/integration/test_secrets_v1_basic.py:148-204` fixture (CREATE DB → `SQLModel.metadata.create_all` → yield → terminate backends → DROP).
- **Scrub `POSTGRES_*` per-variable.** `env -u POSTGRES_*` is a **silent no-op** (critical note) — use `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>`.
- Assert `import daemon` resolves inside the worktree before trusting any output.
- **Husk fixture ids** `5ba21b2d-43b7-4378-be53-cca781f9c124`, `7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f` are **fixtures only** — usable as terminate **targets**, **never** dispatched to.

### 7.2 Per-defect pins and the mutation that must turn each RED

> **⚖️ SUPERSEDED IN PART by D-8 (leader adjudication 2026-10-24):** the F6-i row below is split by the bracketed correction into F6-i(a) (unconfirmed preview ⇒ `protected-target-confirmation-required` refusal) and F6-i(b) (confirmed preview, no windows ⇒ preview JSON + `act_gate_would_pass.reason == "no-user-provenance"`); F6-ii/iii unchanged; **add F6-iv** (precedence inversion: guards 8–9 now run before guard 10, so an exhausted budget/cascade on a protected target yields THAT token instead of `protected-target-confirmation-required` — both refusals, no authority granted, but a behaviour change outside the provenance-only claim → document + pin). phase4-plan.md T4.4 carries the corresponding two-legged F6 mutation.

| Defect | Pin | Mutation → RED |
|---|---|---|
| **F11-a (admitted)** | e2e through the **real order**: `stamp_user_origin_window(ari_iid, "api", "root-msg-1")` → `job_create` (tool, `current_instance_id=ari_iid`) → job row carries `__ens_dispatch_chain__` → `job_processor` dispatch → `task_processor` → funnel → maintenancer holds `pclass="inherited", depth=1, root_source="api"` → `instance_terminate(protected_target, confirmed_target_id=…, dry_run=False)` **ADMITTED**; audit row `inherited`/`depth=1`/`root_source="api"`/`root_message_id="root-msg-1"` | Drop the chain at the `job_create` mint site (S2) → child windowless → `no-user-provenance` → RED |
| **F11-b1 (agent-rooted)** | caller dispatched with `system:sweep_no_user_origin_wake` (no window) → job → child → terminate **REFUSED** `no-user-provenance` | Make the mint skip the parent-liveness check → ADMITTED → RED |
| **F11-b2 (expired)** | caller window stamped with `expires_at` in the past → REFUSED | Remove the `exp >= now_dt` compare in the shared claim body → RED |
| **F11-b3 (depth cap)** | caller holds an inherited entry at depth 4 → mint would be 5 → cleared → REFUSED | Raise/remove `_INHERITED_PROVENANCE_MAX_DEPTH` check → RED |
| **F11-b4 (injection)** | REST job create with `body.metadata = {"__ens_dispatch_chain__": {"caller_instance_id": "<ari_iid>"}}` while Ari holds a live window → child **REFUSED** | Remove the unconditional pop in `job_queue_service.enqueue` (S3) → ADMITTED → RED |
| **F11-b5 (legacy)** | job row without the key (pre-change shape) → REFUSED, byte-identical to today | (regression pin; anchor-neutral, declared as such) |
| **F6-i (preview reachable)** | protected target + no windows, `dry_run` default → preview JSON with `protected: true`, `act_gate_would_pass.ok == false`, `reason == "no-user-provenance"`; an `outcome="previewed"` row exists; target untouched [CORRECTED per D-8 — this is now **F6-i(b)**: the fixture ALSO passes `confirmed_target_id` verbatim; **F6-i(a)** = same call WITHOUT confirmation → `protected-target-confirmation-required` refusal] | Move the `_protected_gate` call back above the `dry_run` fork → refusal string → RED [CORRECTED: two legs — (a) move the CONFIRMATION conditions below the fork → F6-i(a) RED; (b) move the PROVENANCE condition back above the fork → F6-i(b) RED] |
| **F6-ii (apply still refuses)** | same fixture, `dry_run=False` → `reason=no-user-provenance`; `outcome="refused"` row; nothing terminated | Delete the gate call on the apply path → RED |
| **F6-iii (budget not armed)** | after 1 committed protected terminate, a preview leaves `count_committed_protected_terminates() == 1` | Make `write_preview` write `outcome="committed"` → RED |
| **F7-i (leader)** | leader target, **no** marker → mark preview shows `already_protected: true`, `protection_reasons == ["leader"]` | Revert to the marker-only expression → RED |
| **F7-ii (chat root)** | chat-root target (root + `source_type="telegram"`) → `true`, `["chat-root"]` | Drop the `_is_user_facing_root_target` leg from `_protection_reasons` → RED |
| **F7-iii (marker)** | marker target → `true`, `["marker"]`; second **apply** still refuses `marker-already-set` | Drop the refusal (scope it to reasons without `"marker"`) → RED |
| **F7-iv (static + marker apply)** | leader target, no marker, apply `protect` → `marked: true`, `already_protected: true`, `["leader"]`, row committed, marker present | Refuse instead of succeed (the rejected alternative) → RED |
| **F10-i (Tier-1)** | apply `protect` → response carries `audit_row_id`; `session.get(InstanceOpsAudit, id)` → `committed`, action/target match | Drop the echo (or leave `write_audit_and_act` unassigned) → RED |
| **F10-ii (park)** | apply `park` → `audit_row_id` present and settled `committed` | Echo `None` → RED |
| **F10-iii (suspect)** | as F10-i | as F10-i |
| **F9-i (copy)** | reclaim on a non-terminal target → `reason=target-not-terminal` in the string, `"instance_nudge"` **not** in the string, `"instance_terminate"` present | Restore the old copy → RED |
| **F9-ii (docs)** | text assertion: reclaim `_full_doc_` keeps "TERMINAL-ONLY"/D-20 and the nudge doc carries the context-free-turn warning | Revert either doc → RED |

### 7.3 Pin-file duties (update, never delete)

- **`tests/unit/test_instance_ops_behaviour.py`** — module docstring `:10-15` pins the AC-23b delegated-forge residual **as truth** ("DO NOT 'fix' this test to assert a refusal"). F11 does **not** invalidate that: a hand-stamped inherited entry is still admitted. Add the F11 pins in a **new** file (`tests/unit/test_instance_ops_delegation_chain.py`) and leave AC-23b where DF-4 puts it (`test_genuine_inherited_window_admits_protected_act`). If DF-4 has not landed, F11 waits.
  *Correction to the dispatch brief:* the file lives at `tests/unit/test_instance_ops_behaviour.py` (not `tests/unit/tools/…`); the AC-23b docstring is at `:10-15` and the test at `:301-360`.
- **`tests/unit/test_instance_ops_registration.py:316-328`** — the token-mapping pin asserts today's two provenance tokens; DF-4 reduces it to one. F11 must not re-add a token, so this pin is DF-4's, untouched.
- **`tests/test_job_queue_tools.py:147-184`** — pins `agent:test-agent` as the B3.5 anti-forgery outcome. F11 **keeps** that value (Option B), so this pin is untouched — which is one of the reasons Option C2 was rejected.
- **F6's doc pin** (`tests/unit/test_instance_ops_behaviour.py:~585-600`) must be **updated** (add the apply-only sentence), not deleted.

### 7.4 Mutation discipline & regression guard

One behaviour neutered per run, restore green between runs, report the **measured** red set (never statically derived); declare anchor-neutral pins explicitly (§7.2 F11-b5). Whole-tree suite: **net-new reds = 0** against `b257842c9`, with pre-existing reds enumerated and attributed. Watch the packs this patch touches: `tests/unit/test_instance_ops_behaviour.py`, `tests/unit/test_instance_ops_registration.py`, `tests/unit/test_instance_ops_prompt_layer.py`, `tests/test_job_queue_tools.py`, `tests/unit/test_pause_never_dispatched_ghosts.py` (the dispatch/turn-shape pins), and the provenance trio (`tests/postgres/test_df4_provenance_closure.py`, `tests/unit/tools/test_df4_sweep_reserved_fallback.py`, `tests/postgres/test_df4_ari_fallback_closure.py`).

---

## 8. Adjacent findings (OUT OF SCOPE — recorded, not fixed)

| # | Finding | Evidence | Why it matters / routing |
|---|---|---|---|
| **F-1** | **The protected gate exists only on `terminate`.** `reclaim`/`nudge`/`mark`/`answer`/`dismiss` have **no** protected-target gate: `reclaim` can revive a protected terminal instance with zero confirmation (`:895-989` has no `_is_protected_target` call); `nudge` can inject into a protected chat root (`:1048-1137`). | `_protected_gate` called once, at `:562` | The protection model is one-tool-deep. Own commission (the gate would have to be re-shaped for non-destructive acts — "confirmation" means something different for a revive). |
| **F-2** | **Nudge liveness test diverges from the canonical helper, and nudge has no self-surgery gate.** Nudge reads the raw dict `manager._graph_tasks` (`:1110-1112`) instead of `manager.has_live_graph_task` (`daemon/manager.py:5500`) — the documented "graphless running target" defect class (`job_queue.py:3300-3312`); and it omits the `target_id == caller_instance_id` refusal every other act carries (`:1226`, `:531`, `:913`). | as cited | Two divergences in one tool. Own commission (behaviour change: a nudge could currently strand a message or nudge yourself). |
| **F-3** | **Nudge's dispatch source pops the target's provenance windows.** `source=f"instance_ops:{caller_iid}"` (`:1119`, `:1122`) is not `internal_agent:`-prefixed, so the target's inherited window is **cleared** by the funnel (`manager.py:4949-4951`). | as cited | A nudge silently costs the target 60 minutes of delegation authority. Fixing it changes the provenance taxonomy for the act lane — own commission. |
| **F-4** | **Uniform no-row-id gap on ALL six apply responses.** Only `mark` is the named defect, but `terminate` (`:786-796`, the sink's return is discarded via `:746-756`), `reclaim` (`:978-989`), `nudge` (`:1098-1137`), `answer` (`:1505-1550`), `dismiss` (`:1657-1729`) all discard their row id. `preview_run_id` has **zero** write sites (`models.py:85-86`, `:175-177`; `repository.py:88-152` accepts it). | as cited | Do not fix under F10 (one named defect). A uniform `audit_row_id` is a small, separate commission — and terminate needs a mutable cell in `_audit_sink` (`:740-744` has the `sink_error` list precedent). |
| **F-5** | **`"note"` vs `"note_line"` payload-key inconsistency.** terminate/reclaim/nudge previews use `"note"` (`:735`, `:949`, `:1092`); mark/answer/dismiss previews use `"note_line"` (`:1265`, `:1499`, `:1651`). | as cited | Cosmetic but a client-side trap. Own commit (pure rename, needs a payload-compat note). |
| **F-6** | **REST-ingress forge residual (open) + DF-5 defer-resend mint.** `jobs_crud.py:597` accepts `body.source` (default `"api"`), which can mint a **direct** window on a spawned child; `job_queue_service.py:2608` defer-resend mints `source="api"`. | critical note `257488dc`; DF-4 disclosure | Explicitly **not** closed by DF-4 or F11. F11 must not widen it (§2.5 row 2 is the guard). |

---

## 9. Open questions (owner-lane ratification; none blocking implementation start)

1. **F6 — does Ari's ruling also bypass the `confirmed_target_id` conditions on preview?** I read it as *yes* (the whole protected gate becomes apply-only; the preview reports `act_gate_would_pass`), because requiring confirmation to preview recreates the reported dead end. If the owner intended confirmation to still gate the preview, only the two provenance conditions move — one-line difference in §3.2, and the F6-i pin's expected body changes from a refusal to a preview-only for the unconfirmed case. [DECIDED 2026-10-24 — leader adjudication: NO, confirmation still gates the preview (minimal reading). D-8 is authoritative; §3 is bracket-corrected.]
2. **F7 — ratify "succeed with disclosure" for `protect` on a statically protected target** (§4.3), versus refusing with a new token. I recommend succeed-with-disclosure; the alternative adds a refusal token and denies intent-recording. The decision is user-visible (one behaviour + one pin).
3. **F11 — is `job_continue` in scope?** I include it (it mints the same `agent:` source at `job_queue.py:2062`, so it has the identical gap). Excluding it leaves a same-class hole; including it adds one mint site and one pin. Ratify.

---

## References

- `daemon/tools/instance_ops.py` — factory `:398`; auth `:132`, `:161-174`; kill switch `:135`, `:177-186`; refusal envelope `:189`; static predicate `:223-239`, `:287-313`; provenance read `:242-259`; hint `:261-268`; audit repo `:316-323`; `_protected_gate` `:326-385`; terminate `:497-796` (gates `:507-555`, protected `:557-583`, budget `:585-642`, cascade `:644-679`, preview `:696-737`, apply/sink `:739-796`, doc `:798-853`); `_descendant_is_live` `:855-861`; reclaim `:895-989` (refusal `:918-928`, doc `:991-1016`); nudge `:1048-1137` (raw `_graph_tasks` `:1110-1112`, dispatch `:1117-1124`, doc `:1139-1157`); mark `:1199-1366` (`already_protected` `:1232-1235`, refusal `:1236-1241`, preview `:1244-1270`, park `:1272-1315`, Tier-1 `:1317-1366`); answer `:1447-1550`; dismiss `:1602-1729`; return `:1758-1765`; module doc gate order `:41-47`.
- `daemon/repositories/instance_ops/repository.py` — `write_audit_and_act` `:88-152` (returns `row.id` `:147`), `write_audit_ahead` `:157-203` (id capture `:196-199`), `settle_audit` `:205-239`, `write_preview` `:244-282`, `write_refusal` `:284-323`, `count_committed_terminates` `:328-345`, `count_committed_protected_terminates` `:347-371`; outcome vocabulary `:60-68`.
- `daemon/repositories/instance_ops/models.py` — table + column docstrings `:44-120`; `preview_run_id` `:85-86`, `:175-177`; id default factory `:104-108`; provenance columns `:78-87` (DF-4 re-words `provenance_class` as observability-only).
- `daemon/manager.py` — provenance knobs `:216`, `:222`; stores `:1560-1600`; `stamp_user_origin_window` `:4855-4917`; `_inherit_provenance_window` `:4919-5020` (prefix `:4947`, clear `:4949-4951`, direct claim `:4966-4979`, inherited claim `:4980-4984`, no-parent `:4985-4990`, cap `:4990-4993`, decay `:4994-4998`, mint `:4999-5005`); funnel `:9554`, stamp `:9620`, inherit `:9628`; `has_live_graph_task` `:5500`; `enqueue_message` `:9443-9505`; `set_injection` `:3752-3845`.
- `daemon/tools/provenance.py` — verdict `:156-167`; `restart_provenance` `:170-267` (direct `:181-213`, inherited `:196-223`, observed-source diagnostic `:250-262`); bounds docstring `:42-52`.
- `daemon/tools/job_queue.py` — `job_create` `:1284`; source derivation `:1303-1322`; enqueue `:1408-1421`; `job_continue` `:1831`, source `:2062`; `job_inject` `:3186`, `internal_agent:` `:3275` / `:3329`.
- `daemon/services/job_queue_service.py` — `enqueue` (metadata path) `:3065` region; `retry_job` `:2809-2820`.
- `daemon/services/job_processor.py` — dispatch sites `:1001`, `:1093`, `:1327`.
- `daemon/services/instance_messaging.py` — `message_metadata=metadata or {}` `:2206`; `enqueue_message_job` metadata merge `:3065-3072`.
- `daemon/services/task_processor.py` — `message_source` `:496`; `ProcessingContext` build `:540-570`.
- `daemon/services/message_processing_pipeline.py` — `ProcessingContext` `:120-175`; `_do_process` funnel call `:419-447`.
- `daemon/repositories/job_queue/models.py` — `JobItem.source` `:335`; `job_metadata` `:355-360`.
- `daemon/constants.py` — `RESERVED_SOURCE_PREFIXES` `:835-975` (`agent:` `:884-885`, `:949`).
- `daemon/tools/instance.py` — `_is_user_facing_root` `:2563-2594`; `internal_agent:` dispatch sites `:966-971`, `:3495`, `:3701`, `:4154`, `:4261`, `:4365`; `task_context` metadata `:4366`.
- `daemon/routers/jobs_crud.py` — REST `source`/`metadata` pass-through `:590-600`.
- `daemon/routers/messages.py` — hardcoded `source="api"` `:690-694`.
- `daemon/tools/upgrade_journal.py` — `NONCE_TTL_S` `:140`; `classify_user_origin` `:3376-3426`; `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE` `:1191` (DF-4).
- `daemon/services/upgrade_journal_sweep.py` — reserved-literal call sites `:1217`, `:1748` (DF-4).
- DF-4 branch (`/home/nea/ensemble-src-wt-df4`, tip `9959f9f4b`, worktree `240af3731`) — `_protected_gate` two-condition rewrite `:353-385`; freeze list `df4-provenance-closure/plan-overview.md:119`; P2/P3 commits `ea719d372` / `e5a273537`.
- `.agents/shared/planning/nest-ops/decisions.md` — D-1 `:13`, D-15 `:536`, D-16 `:593`, D-17 `:619`, D-18 (retraction trace, gate as speed bump) `:646-734`, D-19 `:735-789`, D-20 (reclaim terminal-only) `:790+`.
- `.agents/shared/planning/nest-ops-shakedown-defects/technical-analysis-read-side.md` — §6 read-path purity, §9.1 env rules, §9.4 mutation discipline, §11 class-level findings.
- Tests — `tests/unit/test_instance_ops_behaviour.py` (AC-23b docstring `:10-15`, test `:301-360`, doc-surface pin `~:585-600`), `tests/unit/test_instance_ops_registration.py:316-328`, `tests/test_job_queue_tools.py:147-184`, `tests/helpers/checkpoint_prune_pg.py:203-236`, `tests/integration/test_secrets_v1_basic.py:148-204`.
