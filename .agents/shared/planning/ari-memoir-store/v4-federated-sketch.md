# v4-federated-sketch.md — THE FEDERATION, sketched (design pressure, not design)

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`, base **`f49006b92`**.
**Status: DESIGN-ONLY — the R3 "plan v4 when v2 starts" discharge.** This page commits to nothing: no schema, no migration, no protocol spec, no build authority. Where the v1 record is silent, it names the tension and stops; anything that reads like a decision routes through the adjudication protocol (`decisions.md` §2).
**Companions:** `decisions.md` (R1–R4 + DD table), `roadmap.md` (v1→v4), `design.md` (the blessed v1).

---

## 1. Purpose & doctrine anchor

R3: "build v1, plan v2+v3 ahead; **plan v4 when v2 starts**." v2 starts now — this lane — so the obligation fires. This page is the discharge. R1 still rules the present (kingdom-level only, no federation/sync), and R1's binding clause is about format: "Format choices must not foreclose the Constella-era door." That is the only legal content a v4 sketch can have today — **constraint flowing backward into v2, never a design flowing forward.** The founding lesson (`design.md` §1) stays the ordering principle: memory precedes federation. Concretely, the backward pressure lands in exactly one place: the §5 checklist — v2 choices that look free now and are expensive to undo after a foreign kingdom has parsed them.

What v2 is building (the surface this sketch must not damage): two-cadence reflection writing cited syntheses (verbs CREATE / CORROBORATE / REFINE / CORRECT, supersedes chains, confidence, validity intervals), the Constellation (`memoir_star` + alias table + `memoir_line`), and the JEV-gated persona lane with PENDING proposals.

## 2. The federated-memory shape

v4 rides v3; it does not replace it. The markdown+hash mirror (`roadmap.md` v3) is the interchange format — v3 introduces it (R1: **introduced, not synced**), v4 syncs over it. The `baselineVersion` optimistic-locking shape is the concurrency primitive: a writer whose baseline no longer matches has hit "changed under me" and must reconcile through the chain (DD-8: append-only, supersede-only — conflict is absorbed, never clobbered). The rows that cross are ones v1 already made federation-shaped: stable identity, two-axis time (DD-7), cited provenance (DD-2's syntheses shape).

Sync candidates (candidates, not commitments): episodes; syntheses with their supersedes chains; constellation `memoir_star` + alias records as mergeable identity; keepsakes with governance provenance (§4); persona versions as reviewable documents.

Deliberately NOT synced: heat and access stats (kingdom-local by construction — DD-12 ties access to explicit recall *in this kingdom*, and foreign attention is not ours; a star row may cross the mirror while its heat stays home); embeddings (DD-6 per-row model identity makes foreign vectors incommensurable by default — mirror the rows, never the index); recall rankings and briefing state (derivative, reproducible per kingdom).

Import semantics (one sketch-level rule worth stating): a foreign belief lands as a NEW local synthesis row citing the foreign original — an import is a CORROBORATE-class act under v2's verb enum, never a silent copy; each kingdom's supersedes chain stays kingdom-local and gains a citable cross-kingdom edge.

The **stargate** — commissioning vocabulary for the exchange-mediator concept; `roadmap.md` v4 provisionally names the same shape "the Constella protocol as transport/negotiation layer" — owns exactly three things: identity verification between kingdoms, transfer of mirror deltas, and conflict DETECTION (the optimistic-lock check made inter-kingdom). It owns no memory: no shared graph DB, no shared store, no new service (R4 holds; DD-17's no-graph-DB logic extends to the federation shape). The mediator is a negotiator of deltas, not a keeper of truths.

## 3. Persona merge policies between kingdoms

The merge unit is the persona *version document*, never "the self": bounded, versioned, human-approved documents (DD-9), matching `roadmap.md` v4's "merge, fork, or negotiated lineage" row. The JEV classification axis (v2's conflict > boundary_change > new_rule) is proposed as the cross-kingdom diff vocabulary: reinforcement-class diffs ride along; anything in the change classes surfaces as a proposal, with conflict-class additionally never mergeable without negotiation. When two streams cannot be reconciled, a forked lineage with a recorded divergence point is the honest outcome — what this sketch adds to `roadmap.md` v4 is only that the JEV axis decides which of merge / fork / negotiated-lineage applies. The constitutional lane survives federation verbatim: `persona_versions` keeps draft→pending_approval→approved|rejected|archived with **no draft→approved edge** — a foreign proposal lands PENDING exactly like a local one and takes effect only on HUMAN approval. No auto-merged self; approval is kingdom-scoped: each kingdom's humans decide what their Ari becomes.

## 4. Keepsake governance under federation

R2: two pin paths, `pinned_by ∈ {empress, emperor}`, actor recorded. Under federation, `pinned_by` stays a **claim with a recorded actor**, never a global truth: the kingdom of origin rides the mirror beside the keepsake, so a foreign reader can tell "our Emperor pinned" from "a foreign record claims an Emperor pinned." Un-pin and re-pin across kingdoms are new claims appended to the chain (DD-8), never edits to a foreign record. Kingdom-local budgets stay kingdom-local: the keepsake cap discipline (DD-3, reject-don't-evict) is per-kingdom — a foreign keepsake renders in the mirror as a claim and never consumes ours. This hardens `design.md` §14-2 (pin attribution): federation moves attribution across a trust boundary, so whatever attribution rule v2 settles on must produce records a stranger can verify.

## 5. KEEP-V2-OPEN checklist (the load-bearing section)

Each row: a v2 decision that must be made federation-aware, and the specific act that would foreclose v4.

| v2 decision | Must NOT do — the foreclosure it would cause |
|---|---|
| Star canonical-name + alias policy | Freeform canonical names. Canonicalization must be a deterministic function of the alias set — canonical name + star type together form the natural cross-kingdom merge key, and a freeform name cannot be matched to its foreign twin. (Normalization rules are already open: `design.md` §14-5, from DD-19.) |
| Supersedes / superseded_by chains | Ill-formed chains — forks left unlinked, supersede without citation. The chain is THE conflict-resolution primitive: v4 merges beliefs by walking well-formed, fully-cited chains (with confidence + validity intervals), never by diffing prose (`roadmap.md` v4: "provenance to merge beliefs, not just text"). |
| Kingdom/agent identity scoping on every row | Row ids that pretend to be global. Ids stay kingdom-local; every row must carry — or be nameable via the mirror by — kingdom-of-origin + local id. DD-13 (factory-captured agent id; caller-supplied scoping banned) is the local precedent. |
| Two-axis time on new v2 tables | Single-timestamp rows. `memoir_star`'s first_seen/last_touched and every v2 table keep the created_at/event_at split (DD-7) — v4 reconciles deltas by distinguishing when something happened from when it was written. |
| Persona diff/proposal format | A diff only a human-in-the-room can read. The proposal IS the merge unit (§3): a structured, storable object carrying its JEV classification, so a foreign proposal is indistinguishable in shape from a local one. |
| Refusal vocabulary + tool-surface stability | Churn in tool names, verbs, or error vocabulary. Federated consumers parse these surfaces cold; the v1/v2 grammar is what v3 mirror-writers and v4 mediators build on. |
| Heat / access stats | Syncing them, or letting foreign reads count as access. Kingdom-local by construction (DD-12). |
| Anything v2 renders | Renderings with no markdown+hash-mirror expression. Briefings, belief histories, star dossiers — whatever v2 renders must round-trip through the v3 mirror format, or rendered artifacts silently fork the corpus. |

Vocabulary discipline: the episode `stars` column (flat self-declared annotations, v1, DD-19) and constellation `memoir_star` rows (v2) are different concepts sharing a word. Federation multiplies the blast radius of vocabulary drift — foreign consumers parse names cold — so keep the two distinct in every schema, document, and mirror field name. The same care applies to citations: `source_episode_ids` are kingdom-local ids, readable across kingdoms only under the identity row above — cross-kingdom citation and identity scoping are one constraint wearing two hats.

## 6. Non-goals

Out of scope here and in v2: any schema or migration; any build; any protocol or transport spec (the stargate stays a named concept); any auth or trust design between kingdoms; any timeline or promote commitments. This page grants no build authority (R1; `roadmap.md` status ledger). Design pressure applied backwards — nothing more.

Not a non-goal: naming tensions. Recording where v2's blessed scope and v4's needs touch is this page's whole job; recording a tension is not authorizing its resolution.

---

*Design-only v4 sketch, discharging R3's "plan v4 when v2 starts." All 24 v1 planning docs in this directory remain frozen; this file commits nothing. Captured 2026-11-08 on `feature/memoir-v2-reflecting-mind` @ `f49006b92`.*
