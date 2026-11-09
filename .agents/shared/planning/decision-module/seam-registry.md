# Seam Registry — Typed Overlay Seams Over `POST /v1/decisions`

> **Status:** **RATIFIED P1 SHADOW-ONLY — Emperor ratification 2026-11-09: "yeah, look good, go ahead, shadow only for now".** Design of record. P1 implementation authorised (client + registry + shadow instrumentation); seams 2/3/4 unregistered; S4 shadow-only. (The leader's giter commits at finalize.)
> **Scope items:** 2 (registry) + 3 (catalog, incl. the re-validation table). **Normative source:** `scope-and-doctrine.md` — cited by §id, never re-quoted.
> **Base:** `docs/decision-module` @ `5da1021f2`

---

## §1 Purpose and shape

The registry is the **single place** where the ensemble decides *whether a typed overlay may be consulted at a given decision point*, and — if so — *how its answer may be combined with the heuristic answer*. It exists because the census found **no unified decision module** (`scope-and-doctrine.md` §S.1): 16 decision points, each with 1–3 callers inside one subsystem, each rolling its own logic. The registry is **not** that unification — it does not replace any lane's decision logic (that is a rejected alternative, `architecture-recommendation.md` §4 (RA-5)). It is the **overlay admission point**: one place where D1–D5 are enforced uniformly, so that no lane has to re-implement fail-open, shadow-logging, or the exclusion list.

**Shape:**

```
seam registration (a record)  ──►  registry owns the call  ──►  seam owns the merge
        │                                │                              │
  what may be asked           env + transport + timeout        how the overlay answer
  how to combine it           fail-open + shadow log           may move the outcome
  its monotonicity contract   metrics + kill switches          (never past the floor)
```

**The registry owns the call so that no seam can own a retry.** A seam that could call the proxy itself could add a retry, a failover, a cache, or a second call — each of which would breach D2 (fail-open, single attempt, no blocking). Centralising the call is what makes D2 enforceable rather than aspirational.

---

## §2 The typed seam interface contract

A seam registers **a record**, not a call site. The record has five parts.

| Field | Purpose |
|---|---|
| `seam_id` | Stable id, e.g. `snapshot.l3.selection`. Used by metrics labels, the shadow log, and the kill-switch matrix. |
| `question_kind` | Which proxy typed kind this seam asks for: `predicate` \| `choice` \| `score` (PX-1). |
| `build_input` | Builds the question payload from the seam's own context. **The seam cannot see the transport.** |
| `apply` | Merges the overlay answer into the heuristic answer. **The seam cannot make a network call.** |
| `monotonicity` | The declared direction the overlay may move the outcome. Checked at registration time. |

### §2.1 The `apply` contract — the load-bearing part

`apply` receives **two** arguments: the heuristic outcome and the overlay outcome. It returns the outcome the caller actually uses.

```
apply(heuristic_outcome, overlay_outcome) -> effective_outcome
```

Three rules, all enforceable in the type signature:

1. **The heuristic outcome is always an argument.** There is no `apply(overlay)` overload, so an overlay answer can never *replace* the heuristic answer — it can only be combined with it. This is D1 made structural.
2. **The overlay outcome is optional (`None` ⇒ use the heuristic unchanged).** Every failure path in `thin-client.md` §6 produces `None`, and `None` must yield the heuristic result. This is D2 made structural.
3. **`apply` may not widen the outcome space.** It may not introduce a value that neither input contained. For S1 that means: **it may never introduce an id outside the candidate set, and it may never empty the result** (see §5.1). **Per-direction, not global.** The superset-only constraint (result ⊇ heuristic outcome) applies **only to `TOWARD_COMPLETION` seams** (S4) — see §2.2 and `thin-client.md` §8.4. A `WITHIN_SET` seam (S1) may **replace or reorder** the heuristic's selection within the candidate set; it is bound by the universal no-widening rule (no value outside the inputs) and by "never empty the result", never by superset-of-picks. A **global** superset-only rule would break S1, the only P1 seam, which legitimately replaces the heuristic's selection.

### §2.2 The monotonicity contract — D4 translated

D4's rule is stated for the attestation lane; the registry generalises it so that **every** seam declares its direction and the registry can refuse an incoherent registration.

| Direction | Meaning | Seams |
|---|---|---|
| `TOWARD_COMPLETION` | The overlay may **add** completions above a floor; it may never remove one and may never deny. | S4 |
| `TOWARD_TIGHTENING` | The overlay may only **add** a positive signal; it may never remove one and may never loosen the gate. | S3 |
| `WITHIN_SET` | The overlay may select/reorder **within** an existing candidate set; it may not extend or empty it. | S1 |
| `ADVISORY_ONLY` | The overlay may only annotate; the heuristic decision stands regardless. | S2 |
| `NONE` | The seam is excluded from overlay consultation (EX-1…EX-5). | excluded seams |

**Per-direction monotonicity — enforced at registration AND at apply time (FLAG5).** The universal rule above (no seam may loosen a gate) is the *floor*; the monotonicity contract is *per-direction*:

- **`TOWARD_COMPLETION` (S4): MUST return a superset.** A **subset is refused at registration time** (the `monotonicity` field is validated) **and at apply time** (the registry checks the returned set is a superset of the candidate completions it was given). The contract carries no denial value, so the overlay cannot propose one.
- **`WITHIN_SET` (S1): must stay within the candidate set and never empty it.** It may **replace or reorder** the heuristic's selection — a `WITHIN_SET` seam is **not** bound by superset-of-picks. Only the universal no-widening rule (no id outside the candidate set) and "never empty the result" bind it. A global superset-only constraint would break S1, the only P1 seam.
- **`TOWARD_TIGHTENING` (S3): may only add a positive signal;** never remove one, never loosen the gate.
- **`ADVISORY_ONLY` (S2): annotates only;** the heuristic decision stands regardless.

The registry stores the declared direction and refuses a registration whose `apply` cannot be shown to honour it.

**Generalisation.** Every direction above is a *monotone* restriction on the overlay's power, and **no seam may declare a direction that would let the overlay loosen a gate** — that is the general form of the source doctrine's "monotonically tighter-or-equal, never looser" (`scope-and-doctrine.md` §D4). A registration that omits a direction is refused.

**The `TOWARD_COMPLETION` contract (S4) — ADD-ONLY / SUPERSET-ONLY.** The `apply` signature for a `TOWARD_COMPLETION` seam accepts the heuristic's *candidate completions* and may **add** completions the judge would also have produced. It **may never remove one**: a **subset is forbidden**, because a subset would remove a completion the judge produced — so the returned set must be a **superset** of the completions it was given. The contract carries **no denial value at all**, so the overlay cannot propose one. The `DENY` rung is minted by the heuristic tree (`daemon/services/attestation_gate.py:1024` main path / `:950` error variant) and the `DENIED → ALLOW` flip remains the judge's own rescue (`daemon/graph.py:6398-6420`, deny band `:6391`, triple-gated).

### §2.3 The fail-open fence — the whole consult (F1)

The fail-open covers the **entire consult**, not just the network call:

```
build_input  →  the call  →  apply
      └──────────┬────────────┘
      any exception in ANY stage ⇒ the heuristic result
```

An exception raised while **building** the question, **during** the call, or **while applying** the answer all yield the heuristic outcome. The **registry** owns this fence, not the seam — a seam cannot narrow it, and it cannot decide to raise. The concrete failure class is `seam_fault` (`thin-client.md` §6 row 30).

The same fence carries the **breaker**: a repeatedly-failing seam is short-circuited and answered from the heuristic **without a call** (`controls-and-phasing.md` §3.5).

---

## §3 Registration-time exclusion enforcement

The standing exclusion list (EX-1 … EX-5, defined verbatim in `scope-and-doctrine.md` §D5.1) is **enforced at registration time**: a registration naming an excluded role is **refused before any call path exists**.

- The list is **referenced by §id** here. It is **not re-quoted** — the canonical text lives in exactly one place in this document set, and that place is `scope-and-doctrine.md` §D5.1.
- The registry carries a `denied_seam_roles` set populated from the same canonical source, so a registry and the doctrine cannot drift apart.
- Enforcement is **not** a runtime check at the call site — a refused registration has no call path to guard.

**Extension is not a registry concern.** Adding an entry to the standing list requires a documented **cause** and the **Emperor's approval** (`scope-and-doctrine.md` §D5.2). The registry has no mechanism to self-extend, because a self-extending deny-list would be a deny-list that erodes.

### §3.4 Default states, boot seeding, and admission

**Every live store key has a stated default.** The source doctrine says it plainly: *"All behind master env gate + per-seam gates, default off"* (`docs/jev-system-one-integration-discussion.md:95`).

| Key | Default | Boot seed | Meaning |
|---|---|---|---|
| `decision_global_enabled` | **OFF** | `DECISION_ENABLED` (unset ⇒ OFF) | the global master: OFF ⇒ no seam is consulted at all |
| `decision_seam_<id>_enabled` | **OFF** | `DECISION_SEAM_<ID>` (unset ⇒ OFF) | **per-seam default is OFF** — a seam is never consulted unless its **own** key is explicitly ON |
| `decision_seam_<id>_stage` | `shadow` | `DECISION_SEAM_<ID>_STAGE` (unset ⇒ `shadow`) | the stage: `shadow` logs but never acts — the **safe** default, and the only one compatible with D3 |

**The admission rule, stated once — TWO GATES (FLAG12).**
- **CONSULT gate** — a seam is CONSULTED only if **the global master is ON** AND **its own per-seam key is ON** AND **the seam's existing gate switch is ON** AND **the overlay is configured** (`DECISION_BASE_URL` + `DECISION_API_KEY` present). **Stage does NOT gate consultation** — a shadow-stage seam IS consulted (that is how the A/B record is produced). Any CONSULT-gate condition OFF or UNREADABLE ⇒ the overlay is **NOT consulted** (no call), plus a `switch_unreadable` suppressed row.
- **APPLY gate** — a consulted answer is APPLIED only when the seam's stage is `deciding`; at stage `shadow` the answer is **logged** (the A/B record) and the **heuristic outcome is returned UNCHANGED**, byte-identical to today. The D3 transition refusal (shadow→deciding, absent review+ratification) is the registry's, not an operator's.

Composition of the seam's existing gate switch follows `controls-and-phasing.md` §3.2.2. With a global provisioned but a seam unregistered, the seam's state is **OFF**: **registration is opt-out** — a seam's own key must be set ON explicitly. The S3 gate the overlay rides (`ENSEMBLE_SYMPTOM_REPAIR_LADDER`) is **DEFAULT ON** in source (`daemon/config.py:6356-6357`) — which is exactly why the **per-seam default must be OFF**: the existing gate's default cannot be relied on to keep the overlay off.  **Boot seeding.** At boot the daemon reads the env seeds once and writes them into the store **only where the store has no existing value** for that key (the DB tier wins the ladder), then re-verifies every existing kill (`decision_overlay_switch_reverify_total`). A kill is therefore **never** undone by a restart.

---

## §4 Registry-owned vs seam-owned responsibilities

| Concern | Owner | Why |
|---|---|---|
| Env/config resolution (`DECISION_*`) | **registry** | one resolution path; the seam never reads env |
| Transport, timeout, response cap | **registry** | single attempt, enforced centrally (D2, IP-5) |
| Fail-open on every failure class | **registry** | one implementation of the failure table |
| Shadow logging (A/B rows) | **registry** | the A/B pair must be written together or not at all (D3) |
| Metric families + labels | **registry** | label vocabulary is centralised (IP-4) |
| Kill switches (global + per-seam) | **registry** | switch-off must be provably "never calls out" |
| Question construction | **seam** | the seam knows its own context |
| Merging overlay into heuristic | **seam** | the seam knows its own outcome space |
| Deciding *whether* to consult | **registry** (via registration + kill switch) | D5 exclusion + D3 staging |

---

## §5 Catalog

Each entry: **role**, **anchors** (re-validated — see §6), **typed mapping**, **monotonicity**, **status**, and — for deferred seams — the **deferral rationale** with its prerequisites.

### §5.1 S1 — SNAPSHOT L3 SEARCH SELECTION — **PILOT**

**Role:** stage-3 selection of which snapshot ids to return, over the top-20 candidates produced by the fused stage-2 composite.

**Anchors.** `daemon/services/snapshot_search_service.py:578-685` (`_llm_select`); the `_chat_call` invocation `:663-665`; the OpenAI SDK call `:750-757` (the **only** outbound stage-3 call on the self-constructed-SDK path — an **injected-client** outbound path also exists at `:762-770`); candidate window `_LLM_SELECT_TOP_N = 20` (`:118`, taken at `:625`); the fail-soft branch in `search()` `:410-415`; the malformed-JSON early return `:667-669`; `_parse_selection` (def `:785`) `:785-812`; the discarded fused score `:629`; prompt block `:640-642`. Three blocking consumers: `daemon/tools/snapshot_tools.py:671-677`, `:875-882`, `:549-558` (the call spans `:549-558`; `limit=5` is at `:557`). ⚠️ `daemon/tools/instance.py:3327-3330` (per-target-agent `meta.json` flag) gates **warm-start consumption only** — consumer 2; consumers 1 and 3 are gated by the **tool allow-list** instead. It is therefore **not** the overlay's whole-path switch — that is the registry's `DECISION_SEAM_*` switch (`controls-and-phasing.md` §3). *Candidate-set precondition:* a `choice` question needs **≥ 2 options** or the proxy answers **400**; `reranked[:20]` can be short, so the client pre-flights `n ≥ 2` (`thin-client.md` §6 row 5).

**The seam's contract (as it exists today).** The prompt asks for `{"selected":[ids]}` — *"a JSON array of snapshot ids, **ordered by relevance** to the query. Pick **AT MOST** {limit} ids"* (`:646-655`). So today's contract is **multi-select + ordering**, capped at `limit` — and the JSON is a **prompt instruction**, not a schema guarantee (the request carries only `model`/`messages`/`temperature`).

**Typed mapping — the decision.** The proxy's typed kinds are `predicate` (one boolean + probability), `choice` (pick **one** of 2–255 options, with confidence), `score` (a scalar + level distribution over 2–10 levels). **There is no multi-select primitive.** Three mappings were evaluated:

| Mapping | Calls | Expresses the seam's contract? | Verdict |
|---|---|---|---|
| **A — `choice` over the candidate ids** | 1 | **Yes.** A choice answer carries the **full per-option distribution** (`probabilities[]` of `{value, probability}`), so a single call yields a ranked top-`limit` over all ≤20 candidates — the seam's multi-select **and** its ordering, with no stacking. | **ADOPTED** |
| **A1 — A plus the discarded fused score in each option's `description`** | 1 | Yes, and strictly **more informed** than today's prompt (which discards the score at `:629`) | **named variant — its own shadow cycle** |
| **B — `score` over a ≤10-candidate window** | 1 | Yes, but caps the window at 10 of 20 and encodes candidate ids as the *levels* of an ordered scale — a semantic stretch of a typed contract, and a mapping that cannot generalise to other seams. | rejected |
| **C — additive hint: thread the discarded fused score into the existing prompt** | 0 (no proxy call) | Yes — but no overlay is exercised at all. | **rejected as the pilot; kept as a defect fix** |

**Why A wins the pilot.** (i) One call replaces one call **in C2** — it adds **no** latency to the blocking path there, because it *replaces* the chat call rather than stacking on it (the C0/C1/C2 topology is §5.1.1); (ii) the fail-soft property is *identical* — any failure falls to the existing `:410-415` branch, i.e. the fused stage-2 order; (iii) the overlay's authority is **bounded by the candidate set** — it selects and orders within that set and can never extend it, which is the conservative direction and preserves the seam's output cardinality; (iv) it is the mapping that generalises — the registry's `choice` mapper is reusable, while B's level-encoded-candidates mapping would be bespoke to this seam.  **Two structural guarantees the mapping buys.** (a) The overlay can never introduce an id that was not a candidate, and a top-K of a probability distribution always has K members — so it can never empty the result. (b) A `choice` question **always** selects an option, so the overlay **cannot express "reject all"**. Today's `_llm_select` *can* return `[]` to reject every candidate, but that capability is not one the overlay should have: "none of these are relevant" is expressed as **falling back to the heuristic order**, never as an empty result.  **A0 vs A1 — the pilot runs A0.** `choices[]` entries are `{value, description?}`, so the fused composite score that today's prompt **discards** (`:629`) can ride along in each option's `description` at **zero extra call cost**. **A0** (the pilot) carries *exactly today's evidence* — id, title, tags, summary — so the shadow A/B compares like-for-like. **A1** carries the fused score as an additive prior: strictly more informed than today's prompt, and therefore a **different comparison basis** — A1 must run its own shadow cycle and its own Emperor gate, not ride A0's.  **Why C is rejected *for the pilot* — but not discarded.** Option C is a **pure local change**: it threads the fused composite score (currently dropped at `:629`) into the prompt block (`:640-642`). It has zero proxy cost, zero added latency, zero new failure modes, and it preserves the full multi-select contract. It is also **not an overlay at all** — it never calls the proxy, so it cannot exercise the client, the registry, the fail-open path, the shadow A/B, or the kill switch. It therefore cannot serve as the pilot of the decision module. It is, however, the **right** fix for the adjacent defect and is folded into the separate tiny fix commission (§S.2 of `scope-and-doctrine.md`, "Adjacent defects"), which this design recommends but does not scope.

**Monotonicity: `WITHIN_SET`.** The overlay may select or reorder **within** the candidate set. It may not introduce an id that was not a candidate, and — critically — **it may never empty the result.**

> **The hazard that fixes the mapping's failure edge.** Today, a malformed stage-3 response returns `[]` (`:667-669`): the path is **fail-closed-to-empty**, and it contradicts **two** docstrings (`:27-28`, `:790-792`). If the typed overlay's "no decision" outcome were surfaced as an empty selection, the overlay would **replicate the defect** — and worse, it would do so on the *success* path. The client's "no answer" outcome must therefore be surfaced to the seam as a **failure**, never as an empty selection, so that the existing `:410-415` fail-open branch is reached and the fused stage-2 order is used. This is a binding requirement of the typed mapping, not an optional hardening. The same rule governs the mapping's *semantic* edge: because a `choice` question always selects an option, "reject all candidates" must be expressed as **fail-open** (fall back to the stage-2 order) — never as an empty selection.

**Pilot rationale.** S1 is the smallest blast radius that still exercises the whole mechanism: one module, **zero** consumer changes (all three consumers consume the `search()` envelope), no schema or migration change, no new runtime dependency, an already-bounded fail-open branch, and a per-agent gate (`instance.py:3327-3330`) that can turn the whole path off without touching code.

### §5.1.1 The three configurations — C0 / C1 / C2 (B1)

Every latency, cost, and identity claim in this set attaches to **one of these three named configurations**. There are exactly three, and they are exhaustive.

- **C0 — BASELINE (today).** The chat call only, blocking, ships. **Unchanged.** No overlay is consulted at all. This is the reference behaviour every other configuration is measured against.
- **C1 — SHADOW (P1/P2).** The chat call still ships **unchanged** (blocking, deciding) **plus** the overlay is called **detached** — non-blocking, never mutating the shipped result, never blocking the caller. A row is logged when it lands. Critical-path added wall-clock **≈ 0**; added cost = **one billed call per search**, bounded by the breaker's rate/concurrency budgets (§3.5 of `controls-and-phasing.md`). The A/B basis = the overlay's chosen set vs the chat call's chosen set **over the same candidate set**.
- **C2 — DECIDING (P3+).** The overlay call **only**, blocking, **on the deciding path**. The overlay result ships subject to the **confidence floor**, the **candidate-set intersection** (N9 — a client-side requirement), and **fail-open to the deterministic floor** (cosine/BM25) on any failure. **In C2 the overlay replaces the chat call ON THE DECIDING PATH; the baseline chat selection is RETAINED (not deleted) as the KILL TARGET.**

#### Call topology

| | **C0 BASELINE** | **C1 SHADOW** | **C2 DECIDING** |
|---|---|---|---|
| **Outbound calls per search** | 1 (chat) | **2** (chat + overlay) | 1 (overlay) |
| **Chat call** | blocking, decides | blocking, **decides** | **absent** |
| **Overlay call** | **absent** | **detached** — never blocks the caller | blocking, **decides** |
| **Who decides** | the chat LLM | the chat LLM (the overlay observes only) | the overlay, subject to floor + intersection + fail-open |
| **Who pays** | the chat provider | chat provider **+ 1 overlay call per search** | the overlay provider |
| **What ships** | today's result | **today's result — byte-identical decisions** | **O1** overlay result (floor + intersection applied) · **O2** deterministic fused stage-2 floor on failure · **O3** baseline chat selection on kill — enumerated in §5.1.1.1 |
| **Added wall-clock on the critical path** | — | **≈ 0** (the overlay is detached) | the overlay's latency **replaces** the chat latency — it is **not** additive |
| **Added cost per search** | — | **+1 billed call** | ≈ parity with C0 (one call either way) |

#### §5.1.1.1 The C2 outcome set — three outcomes, and which is which

| # | Outcome | When | What the caller gets |
|---|---|---|---|
| **O1** | **Overlay result** | the overlay call succeeds, passes the **confidence floor**, and its distribution **intersects the candidate set** | the overlay's ranked top-`limit` selection |
| **O2** | **Deterministic fused stage-2 floor** (**FAIL-OPEN**) | the overlay call fails, times out, or its answer fails validation — *any* failure class (`thin-client.md` §6) | the fused stage-2 composite order (BM25 + cosine + tag-overlap) |
| **O3** | **Baseline chat selection** (**KILL TARGET**) | the seam's overlay is **killed** | the pre-overlay chat selection — the path C2 displaced, **retained** for exactly this purpose |

> **The kill outcome and the failure outcome DIFFER in C2.** A **kill** ⇒ **O3**, the baseline chat selection, restoring pre-overlay **production** behaviour. A **failure** ⇒ **O2**, the deterministic fused stage-2 floor, which is a **degradation** — materially different output from the chat LLM's selection (see the decision-quality consequence below). The kill is the operator's lever; the failure path is the system's own safety net. Neither is a substitute for the other.

#### Build requirement — the kill target must be retained

Because **O3** is the kill target, the implementation **MUST retain the baseline chat selection as a restorable code path**. In normal C2 operation that path is **not executed** — it costs **no extra call** — but it must remain **present and reachable** so that a kill restores it in one switch flip. Deleting it (the "replace the chat call" reading taken literally) would make the C2 kill promise **unimplementable**: a C2 implementation without it can only offer **O1** and **O2**, and its kill can only *degrade* to O2.

#### C1 shadow-phase latency / cost envelope

- **Max added wall-clock per `snapshot_search`: ≈ 0 on the critical path.** The detached call is not awaited by the caller; it runs on its own 5 s budget (`thin-client.md` §4.2) and is **discarded** if it lands after the search has already shipped.
- **Calls per search: exactly 1** overlay call. Never more — there is no retry and no failover (PX-6, IP-5).
- **Cost: +1 billed call per search**, subject to the breaker's rate/concurrency budgets (§3.5). A suppressed consult (breaker open, rate-limited, concurrency-limited, switch off) makes **no call and incurs no cost**.
- **Shadow-row-on-landing rule.** The detached call writes **exactly one row when it lands** — a success row, or a failure row including a **timeout** row (`timeout` label). A detached call that never lands (process shutdown, daemon restart) writes **no row**, but it is still counted in `decision_overlay_attempts_total` and in the cost counter, because the upstream attempt **bills** even when the client gives up (PX-6 — client disconnect does not cancel upstream).

#### C2 baseline degradation — stated, not hidden

C2 **replaces** the chat call with the overlay call. The overlay's latency therefore **replaces** the chat call's latency on the critical path — it is not additive and it is not free. What changes is the *nature* of that latency: today's stage-3 chat call sits inside `invoke_raw_with_failover`'s budget (transient/timeout retries plus a base-URL failover), while the overlay is **single-attempt by leader ruling** and falls open to the deterministic floor on failure. So C2 **trades the chat call's retry/failover cushion for a typed, calibrated answer**, and the S1 path's worst-case critical-path latency becomes the overlay's 5 s budget rather than the chat call's.

**⚠️ C2 decision-quality consequence — disclosed, because the latency trade hides it.** The C2 fail-open floor (**O2**) is the **fused stage-2 order** — **not** the chat LLM's selection. The two are **materially different outputs**: O2 is a deterministic BM25 + cosine + tag-overlap composite, whereas the chat LLM's selection is a *semantic* re-ranking — the pilot's C0/C1 baseline. So in C2 an overlay failure does not degrade to "roughly what the chat call would have said"; it degrades to a **lexical** ranking that can differ substantially from it. **An implementer and the P3 Emperor gate must both see this** — P3's exit criteria must therefore compare **O2** against the C0 baseline, not only **O1** against it (`controls-and-phasing.md` §4, P3).

#### The kill contract — stated once, referenced everywhere

> **Kill ⇒ the overlay is NEVER CALLED ⇒ the pre-overlay production path computes the result.**
> For S1 that means the **baseline chat selection** (**O3**) — the path C2 displaced, **retained** for exactly this purpose — with its **unchanged deterministic fail-soft floor**; the pure **cosine/BM25 floor** (**O2**) remains the last-resort fail-soft target.

**Scoped per configuration.** In **C0** and **C1** a kill ⇒ the decisions are **byte-identical** to the pre-overlay system. In **C2** a kill ⇒ the **baseline chat selection** is restored — byte-identical to pre-overlay **production** behaviour — **conditional on the retention build requirement** above. The conditionality is explicit, never implied: without the retained path, C2 offers only O1 and O2, and its kill can only *degrade* to O2.

**Byte-identical DECISIONS; observability differs.** After any kill a suppressed row and `decision_overlay_suppressed_total` deltas are **expected** — they are the operator's **confirmation signal**, not a regression.

This contract is **referenced, not restated**, by `thin-client.md` §6, `controls-and-phasing.md` §3, and `architecture-recommendation.md` §3.

### §5.2 S2 — BUSY-SLOW ARBITRATION — **DEFERRED**

**Role:** decide whether a long-running child is "busy-slow" (needs escalation to a high-tier model) or merely slow (the current outcome: an advisory nudge to the parent).

**Anchors.** `daemon/services/long_tool_nudge.py:1560` (`LongToolNudgeScanner.run_once`); the fire boundary `:1614-1616` (strict `>`); the 5-rung threshold chain `:80-98` with the resolver `:1300-1325`, the 900 s default `:1208` and the constants `:319`/`:327`/`:333`; the advisory notice text `:1156-1160`; delivery `:1471-1477`. `model_tier='high'` resolver `daemon/services/instance_lifecycle.py:1326-1415`; arm path `daemon/tools/instance.py:3245-3296`; tunables `daemon/tools/tunables.py:100-207`.

**Deferral rationale — the honest input gap.** A typed score at this seam would want four facts. **Only one is a structured value at the seam today:**

| Input | State at the seam | What a typed score would have to build first |
|---|---|---|
| elapsed vs threshold | **AVAILABLE** — already a structured comparison (`:1614-1615`), carried in the episode context | nothing — zero plumbing |
| model tier | **NOT AT THE SEAM.** The tier literal exists only as a spawn-time input; a *model name* persists per instance (`instance_metadata["model_override"]`), and only for the override / `llm_models`-pool sources | inference against the boot-snapshot high-tier model + a repo read on the crossing path + plumbing into the episode context — and it stays unreliable for children whose model came from `meta.json`/`llm_models`/the default |
| per-episode LLM error count | **DOES NOT EXIST AT ALL.** `llm_error_classifier.py` classifies error *text* into typed errors without counting; the failover budget is per-call, not per-episode | a new counter, plumbing, and a persistence decision |
| loop-breaker misses | **DOES NOT EXIST AS MISSES.** The only structured artefact is a RAM-only loop-**repair** counter (`daemon/manager.py:6879-6912`) — a different signal, lost on restart | a definition of "miss", new instrumentation, plumbing, a persistence decision |

**Verdict:** a typed decision here would have to **build instrumentation first** and score three facts that are missing, non-persisted, or unmeasurable at the seam. That is not a thin overlay — it is a feature. The lane stays prose (`:1135`, `:1157`, `:1177`) and the seam is **deferred**.

**Prerequisites for a future commission (all four, none assumed):** (1) a persisted per-child model-tier fact covering *all* spawn sources; (2) a per-episode LLM-failure counter with a defined persistence scope; (3) a defined "loop-breaker miss" semantic distinct from loop repairs, with instrumentation; (4) the four inputs threaded into the episode context. Only then does the typed mapping become discussable.

**Monotonicity: `ADVISORY_ONLY`.** Even if it were unblocked, the overlay may only annotate the nudge — it may not itself re-spawn or escalate. The escalation decision remains the parent's.

### §5.3 S3 — GHOST-PROMISE DETECTION — **STAGED**

**Role:** detect a *ghost promise* — the model says it will do something ("I'll write the report") and then does not.

**Three distinct detectors, only one of which is an overlay candidate.**

| Detector | What it is | Overlay candidate? | Anchors |
|---|---|---|---|
| **(A)** colon-suffix re-invoke predicate | `content.rstrip().endswith(":")` on an AI message with tool calls; capped at 3 | **YES** | `daemon/graph.py:4482` (predicate `:4502`), cap `GHOST_PROMISE_REINVOKE_CAP = 3` at `:4479`, router `should_continue` `:4261`, block `:4380-4402`, master gate `:4384` |
| **(B)** junk-report structural rule | last assistant message has no `tool_calls` **and** short assistant history → `[REPORT SANITY]` marker | **NO** — exact, structural, **descriptive-only** | `daemon/services/child_reports.py:1794`; marker `daemon/constants.py:1606`; producer `child_reports.py:1739`; rollback seam `SANITY_FLAG_VERSION` `daemon/constants.py:1614` |
| **(C)** phrase catalogs | MID_WORK (16) + CHILD_TERMINAL (17) markers feeding the fused judge | **NO** — shared feed, see the cross-cutting note | `daemon/services/attestation_marker_scanner.py:118`, `:258` |

**Typed mapping:** one `predicate` over the turn's trailing text → probability. **One call.**

**S3's overlay consult is placed in the router's ladder-routing branch (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`), inside the branch that `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates — so that switch gates the overlay as well as the routing.** The `attestation_gate_node` (`:5932`) is a **different node** and belongs to the **S4** gate surface — it is **not** S3's hook, and the two anchors must never be conflated.

**Placement — the consult sits INSIDE the ladder-routing branch it augments.** `ENSEMBLE_SYMPTOM_REPAIR_LADDER` is a **boot-snapshot Pattern-C routing gate**, not an overlay gate (`daemon/config.py:6356-6410`): it decides whether the ghost-promise *repair route* fires, while the overlay's own admission is the registry's live per-seam switch. The overlay consult is placed **inside the routing branch the ladder switch gates**, so the ladder switch gates the overlay as well as the routing. Composition follows `controls-and-phasing.md` §3.2.2.

**Monotonicity: `TOWARD_TIGHTENING`.** The overlay may only **add** a ghost-promise signal; it may never remove one and may never loosen the gate — the mirror of D4's `TOWARD_COMPLETION`.

**Cross-cutting rule (S3 ↔ S4).** Detectors (B)/(C) are shared machinery: the phrase catalogs live in **one module** and the two seams import it (`daemon/services/attestation_gate.py:107-111`; `daemon/services/attestation_resolver_activation.py:89-93`). The registry registers the **scanner once**; S3 and S4 subscribe. It is never double-registered, and neither seam re-implements the catalogs.

**Why staged, not deferred.** The seam has a working heuristic (detector A), a bounded router switch, and an existing counter family (`report_integrity_junk_report_total`) — but it runs **per turn-end**, so it needs shadow-mode gating before it can act, and its passive detectors are always-on with only `SANITY_FLAG_VERSION` as their rollback seam.

### §5.4 S4 — ATTESTATION RESCUE-LANE — **LAST, RESCUE-BAND-ONLY**

**Role:** the completion gate — decide whether a leader's turn may be treated as a completed piece of work.

**The 5-step ladder** (`daemon/services/attestation_gate.py:653`): (1) `user_answer_pending` ⇒ pending-wakeup allow (`:875`); (2) any pending input > 0 ⇒ pending-wakeup allow (`:920`); (3) attested ∧ quiet ⇒ standalone report ⇒ allow (`:973`), otherwise HOLD + reminder (`:990`); (4) ¬attested ∧ quiet ∧ `denied_count + 1 > bound` ⇒ TERMINAL (`:1016` main path / `:944` error variant, bound default 3); (5) otherwise ⇒ **DENY** + nudge (`:1024` main path / `:950` error variant).

**Deny ownership — the boundary this design must not cross.** `DENY` is minted at **two** sites, **both inside `decide()`**: **`daemon/services/attestation_gate.py:1024`** — step 5, the **main path** that runs every non-attested turn (comment "(5) deny — increment + nudge." at `:1022`) — and **`:950`**, the `report_obligation_read_error` **error-path variant**. `TERMINAL_AFTER_BOUND` likewise: **`:1016`** (main path) and **`:944`** (error variant). **Both DENIED sites are in scope**; guarding `:950` alone would leave the primary deny path unguarded. The composition gate can only *tighten* it (`TERMINAL_AFTER_BOUND` → `DENIED`, `daemon/graph.py:6682-6687`). The **only** `DENIED → ALLOW` flip in the codebase is the judge rescue (`daemon/graph.py:6398-6420`), triple-gated by the deny band (`:6391`), the would-be-DENY veto (`:6283-6286`), and `fused_result.is_complete` (`:6398`). Every other judge outcome — not-complete, error, timeout, unparsable, kill-switch-off, wrapper fault — leaves `DENIED` standing (`:6418-6442`).

**Typed mapping:** one `predicate` over the completion bundle → P(complete). **One call, and it never replaces the judge.** The **canonical judge is consulted FIRST** (`daemon/graph.py:6326`); the overlay consult sits **after** the judge call, **inside the same `fused_judge_on` block** (`daemon/graph.py:6289-6296`), and its answer may only **ADD** completions the judge did not already produce, **above the high-confidence floor**. **The judge is never skipped** — in any band, on any outcome. A below-floor or absent answer adds nothing. Every other band falls through to the canonical judge unchanged.

**⚠️ The overlay may only ADD to the JUDGE's outcome — never substitute for it.** In the deciding configuration the **canonical judge is consulted first** (`daemon/graph.py:6326`), and the overlay is allowed **only to ADD completions the judge would also have produced**, above the high-confidence floor. The overlay's answer **never replaces a judge verdict** and **never flips a `DENY`** — the `DENIED → ALLOW` flip stays owned by the judge's own rescue path (`daemon/graph.py:6398-6420`, deny band `:6391`, triple-gated). This is the only doctrine-consistent form of the substitution: D4 forecloses an overlay that could *loosen* the gate, and a shadow A/B (which measures agreement) **cannot detect** a loosening at runtime — so the *design* must forbid it, not the measurement. If an implementation cannot express the additive form, **S4 remains SHADOW-ONLY** and is dropped from P4's deciding sequence (`controls-and-phasing.md` §4, P4).

**Placement — a build requirement: the consult sits INSIDE the `fused_judge_on` boolean.** The overlay consult **must** be placed inside the gate `fused_judge_on = is_llm_judge_enabled() and gate_config.get("llm_judge_enabled", True)` (`daemon/graph.py:6293-6295`), so that `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` **gates the overlay too** — not only the canonical judge. The house call-site behaviour to copy is the **fail-closed resolver fault**: `except Exception:` sets `fused_judge_on = False` (`daemon/graph.py:6296-6297`), so a resolver fault **disables** the path rather than enabling it. **If an implementation genuinely cannot place the consult inside that boolean, this set must then say so plainly**: the canonical judge's switch does **NOT** stop the overlay, and the operator warning becomes — *"to stop the overlay on S4, kill the per-seam registry switch — **not** `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`."* This set does not leave that ambiguous.

**D4 reconciled with D5.** D4 governs the attestation lane's *rescue band*; D5 governs *which* seams may be overlaid at all. S4 is an **overlay-eligible** seam (it is not on the EX-1…EX-5 list, `scope-and-doctrine.md` §D5.1), and D4 then constrains **how** it may act: add-only, never deny, never loosen. The two clauses are complementary, not in tension.

**Monotonicity: `TOWARD_COMPLETION` — ADD-ONLY / SUPERSET-ONLY.** The `apply` contract may **add** completions the judge would also have produced, and **may never remove one**: a **subset is forbidden**, because a subset would remove a completion the judge produced. The contract carries **no denial value at all**, so the overlay cannot propose one. The `DENY` rung is minted by the heuristic tree (`daemon/services/attestation_gate.py:1024` main path / `:950` error variant) and the `DENIED → ALLOW` flip remains the judge's own rescue (`daemon/graph.py:6398-6420`, deny band `:6391`, triple-gated). This is the whole of D4's content: **the overlay may only ever make the gate looser by adding a completion the judge would also have added — never by removing one, and never by denying.**

⚠️ **Fail-open on S4 is NOT benign — it keeps `DENIED`.** On every other seam, fail-open means *less restrictive*. Here it means the heuristic's `DENY` **stands** and the completion is withheld. That is D4's "monotonically tighter-or-equal, never looser" read as *tightening*. D2's fail-open governs the **overlay's** failure and never relaxes the heuristic's own decision.

**K1 — the S4 shadow phase runs with `ATTESTATION MODE = enforce`, with the overlay's own stage at `shadow`.** The canonical judge is consulted and **decides exactly as today**; the overlay is consulted, its answer **LOGGED, NEVER APPLIED**; the gate behaves **identically to today throughout**. Non-intrusiveness is therefore **by construction**, not by a separate mode.

**"dry mode" is DEMOTED to a pattern precedent only.** The code's own comment reads *"not fired / dry mode → allow, 0 LLM (R3: dry computes + logs, node skipped)"* (`daemon/graph.py:6231-6232`). Dry **makes the additive criterion unevaluable**, because there the reference outcome is the resolver's **no-judge `would_be_outcome`** (`daemon/services/attestation_resolver_activation.py:454-458`) — with no judge consulted there is no judge verdict for the overlay's addition to be compared against. Dry is therefore a **precedent for compute-and-log**, **not** the S4 shadow mode. **S4 remains LAST**, behind its own gate.

**Why last.**

**Two budgets, kept distinct.** The withhold-conversion budget is 8 per dispatch (`WITHHOLD_DENY_BUDGET_DEFAULT`, `daemon/services/attestation_gate.py:613`); the withhold arc remains unbounded *in epochs*. The judge's HTTP budget is separate (≤2 attempts per invocation).

---

## §6 Seam re-validation table

Every anchor below was re-verified against the worktree at `5da1021f2` by a read-only verification pass. Corrections against the originally briefed anchors are published in `scope-and-doctrine.md` §S.3 — **not** silently patched.

| Seam | Role | Re-validated anchors @ `5da1021f2` | Kind | Calls | Monotonicity | Status | Phase |
|---|---|---|---|---|---|---|---|
| **S1** | snapshot L3 stage-3 selection | `daemon/services/snapshot_search_service.py:578-685`, `:663-665`, `:750-757`, `:410-415`, `:667-669`, `:629`, `:640-642`, `:118`; `daemon/tools/snapshot_tools.py:671-677`, `:875-882`, **`:549-558` (the call spans `:549-558`; `limit=5` is at `:557`)**; `daemon/tools/instance.py:3327-3330`; `daemon/services/snapshot_metrics_service.py:31-34` | `choice` | **1** | `WITHIN_SET` | **PILOT** | P1 → P3 |
| **S2** | busy-slow arbitration | `daemon/services/long_tool_nudge.py:1560`, `:1614-1616`, `:80-98`, `:1300-1325`, `:1156-1160`, `:1471-1477`; `daemon/services/instance_lifecycle.py:1326-1415`; `daemon/tools/instance.py:3245-3296`; `daemon/tools/tunables.py:100-207`; `daemon/manager.py:6879-6912` | — | — | `ADVISORY_ONLY` | **deferred** | not scheduled |
| **S3** | ghost-promise detection | `daemon/graph.py:4261`, `:4380-4402`, `:4384`, `:4479`, `:4482`, `:4502`, `:4958`, `:5932`, `:6149`, `:6187-6270`, `:7069-7075`; `daemon/services/child_reports.py:1739`, `:1794`; `daemon/constants.py:1606`, `:1614`; `daemon/services/attestation_marker_scanner.py:118`, `:258`; `daemon/services/attestation_gate.py:107-111`, `:1320`; `daemon/services/attestation_resolver_activation.py:89-93`; `daemon/services/report_integrity_metrics.py:38-92` | `predicate` | **1** | `TOWARD_TIGHTENING` | staged | P4 (own shadow cycle) |
| **S4** | attestation rescue lane | `daemon/services/attestation_gate.py:653`, `:875`, `:920`, `:1016`, `:1024`, `:944`, `:950`, `:973`, `:990`, `:613`, `:2095-2138`, `:2160-2180`; `daemon/graph.py:5032`, `:5044`, `:5059`, `:6149`, `:6187-6270`, `:6214`, `:6283-6288`, `:6289-6296`, `:6326`, `:6398-6420`, `:6637-6699`, `:6682-6687`; `daemon/services/attestation_report_judge.py:703`; `daemon/services/attestation_resolver_activation.py:962`, `:1096-1102`; `daemon/services/attestation_resolver.py:123-130` | `predicate` | **1** | `TOWARD_COMPLETION` | **last** | P4 (after S1 and S3) |

**Excluded seams** (never registered): EX-1 retry classification · EX-2 compaction message selection · EX-3 snapshot L2 trigger-query generation · EX-4 snapshot L1 digest generation · EX-5 symptom ladder rungs — see `scope-and-doctrine.md` §D5.1. The registry refuses a registration naming any of these roles at registration time (`seam-registry.md` §3).
