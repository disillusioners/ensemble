# decisions.md — Memoir Store v1 — rulings, embedded decisions, adjudication protocol

**Lane:** `feature/ari-memoir-store` @ worktree `/home/nea/ensemble-src-wt-memoir-store`, base **`1f6898ae2`** == origin/latest.
**Status:** **PHASE A — FAITHFUL CAPTURE.** Design blessed in a joint Emperor + Ari design session, **2026-11-04**. Nothing in this file is a new decision by the documenter. Section 1 is **verbatim** (rulings). Section 3 records decisions that are **embedded in the blessed design** — restated, indexed, and attributed, not chosen here.
**Companions:** `design.md` (the blessed design), `roadmap.md` (v1→v4 under the rolling doctrine).

---

## 1. The Emperor's rulings — R1–R4 (VERBATIM, binding)

> **R1.** KINGDOM-LEVEL ONLY in v1 — single daemon, local Postgres, NO federation/sync. Format choices must not foreclose the Constella-era door (markdown+hash mirror is v3, introduced-not-synced).
>
> **R2.** KEEPSAKE GOVERNANCE = BOTH: user (via Ari in chat) and ari (via tool) may pin/unpin. pinned_by ∈ {empress, emperor}.
>
> **R3.** ROLLING ROADMAP DOCTRINE: build v1, plan v2+v3 ahead; plan v4 when v2 starts. Doctrine: "crafting into future correct and improve the present plan."
>
> **R4.** LEARN-NOT-ADOPT: no new external memory dependencies. Postgres only; no graph DB; no new services.

**Status:** these four are the **top of the authority stack**. They are not re-litigable by planning or implementation. A plan or implementation that violates R1/R2/R4 is wrong by definition — there is no "but it was more efficient" defence. R3 governs *how far ahead* planning must look, and is quoted again in `roadmap.md`.

---

## 2. Decision protocol (binding for Phase B and beyond)

**This design is BINDING INPUT.** It is not a proposal, not a sketch, and not a starting point for re-derivation. The blessed substance in `design.md` — columns, caps, verbs, gates, env names, table names, tool names, seams — is the specification.

**Deviations discovered during planning or implementation do not get decided locally.** They go through **ARCHITECT ADJUDICATION** and are recorded in §4.

```text
Discover a deviation / impossibility / tension while planning or implementing
  → STOP treating it as a local choice
  → write it down: what the design says, what reality forces, what the options are
  → submit to the ARCHITECT for adjudication
  → the adjudication is RECORDED in §4 (date + deviation + adjudication + outcome)
  → only then implement
```

| Rule | Statement |
|---|---|
| **P-1** | The blessed design is binding input. Silence is not permission to invent — it is a trigger for §14-style flagging (`design.md`) and adjudication. |
| **P-2** | **No silent deviation.** A deviation that is not recorded in §4 is a defect, even if the code works. |
| **P-3** | Deviations are adjudicated by the **architect**, not by the implementing worker, not by the planner, and not by the documenter. |
| **P-4** | An adjudication **supersedes** the affected design text only to the extent it states. Everything else in `design.md` keeps standing — adjudication is surgical, never a re-blessing of the whole. |
| **P-5** | Rulings R1–R4 are **not adjudicable downward**. No architect may authorise a federation, a graph DB, a new external memory dependency, or a unilateral keepsake-governance narrowing. Only the Emperor can change an Emperor ruling. |
| **P-6** | Open items carried into Phase B live in `design.md` §14 (intake) and are resolved **through this protocol**, then recorded in §4. An item resolved by fiat without a §4 row did not happen. |
| **P-7** | Where the design and a *shipped code anchor* disagree about line numbers, the code wins as a **lead**; the house rule applies — recorded line numbers are leads, **grep at use time**. Anchor drift is not a design deviation and does not need adjudication; it needs a re-grep and a note. |

---

## 3. Decisions embedded in the blessed design (recorded, indexed, attributed)

These are **not new decisions by the documenter**. They are the load-bearing choices already present in the blessed design, indexed here so Phase B can cite them by number and so no future reader has to re-derive *why* an obvious-looking alternative was not taken. Each row states the decision, the **rejected alternative**, and the **reason as the design gives it**.

| # | Decision (as blessed) | Alternative NOT taken | Reason (as the design states it) |
|---|---|---|---|
| **DD-1** | **pgvector explicitly deferred to v3-if-ever**: JSONB embeddings + **Python-side cosine** in v1 | pgvector extension in v1 | House precedent (`critical_note_embeddings`); adequate at personal scale; keeps "zero new services / zero new infrastructure" literally true. The door is not closed — it is not walked through. |
| **DD-2** | **Syntheses schema shipped EMPTY in v1** | Ship v1 without the table; add it when v2 builds behavior | So **v2 adds behavior, not migration churn**. The fourth layer exists as a shape from day one. |
| **DD-3** | **Keepsake cap is reject-don't-evict** | Evict the oldest/least-important pin to make room | Critical-notes precedent: "Past the cap, the tool REJECTS … no silent eviction". A keepsake that can be silently evicted is not a keepsake. |
| **DD-4** | **NO `memoir_about` in v1** | Ship entity-centric recall now | Entity recall depends on the Constellation layer (star identity + alias merge), which is v2. Shipping `about` before star identity would return near-misses as if they were answers. |
| **DD-5** | **Importance self-declared in v1** (INTEGER 1–10, default 5) | LLM poignancy scoring at write time | Keeps the hot append free of LLM latency and model drift; poignancy scoring is a v2 concern. |
| **DD-6** | **Embeddings 1536-dim via the existing LLM lane**, with **per-row model identity** and **graceful lexical-only degradation** | A dedicated embedding service/model pin | Reuse the existing lane (no new service, R4); "a dimension is not an identity" — comparing vectors from different models silently produces nonsense, so identity is stored per row; the lane being down must degrade retrieval, never the write. |
| **DD-7** | **Two-axis time**: `created_at` (write) + `event_at` (event, defaults to write) | One timestamp | Memory is written about the past, often late. Time-travel over `event_at` is a query, not a reconstruction. |
| **DD-8** | **Append-only, never hard-delete**: `superseded_by` marks replacement | UPDATE-in-place or DELETE | Nothing a person said to us is deleted. The record survives its own correction. |
| **DD-9** | **Persona edits land PENDING; human-lane approval only** | Live self-edit by the running model | The Hy-Memory production scar: a live model that freehand-edits its durable self drifts unrecoverably. Pending-approval makes the durable self reviewable before it is real. |
| **DD-10** | **Briefing is a turn-1 CONTEXT BLOCK, never a loader/system-prompt injection** | Inject the memoir into the system prompt | The system prompt **freezes at spawn** (`loader.py:1313` cached) — a loader-injected memory is a snapshot of the spawn moment, stale forever. (Audit landmine #1.) |
| **DD-11** | **Briefing is once-per-instance** | Per-turn reinjection | The `project_injected` precedent: a briefing is a wake, not a chant. Repeating a persona + 12 keepsakes every turn is pure token tax with no new information. |
| **DD-12** | **Recency prior NEVER applies to keepsakes**; access stats update **only** on explicit recall | Decay everything uniformly; count briefing reads as access | Keepsakes are permanent by definition. Counting briefing reads as "access" would let the briefing's own existence reshape future rankings — a self-reinforcing loop with no user attention behind it. |
| **DD-13** | **Memory identity comes from the FACTORY-CAPTURED agent id only** — never a caller-supplied scoping key | Accept a `context_key`/agent param from the caller | The `list_context`/`read_context` cross-tree hole is the named anti-pattern (landmine L3): a caller-supplied scope lets any session read another tree. |
| **DD-14** | **Tool category `memoir`, ari EXACTLY, two-layer gate** (category + in-body factory-captured agent id) | Broaden the allow-list "for the team"; rely on the category gate alone | ari-exactly is the notify-user precedent; the in-body check is the only layer prompt content cannot bend (metrics-read exemplar). |
| **DD-15** | **Kill-switch `ENSEMBLE_MEMOIR_ENABLED`, fail-closed** | No switch at all | House pattern (notify-user #16). "Fail-closed" fixes only how an **indeterminate** value is treated (it must not silently enable the surface); the env polarity and the briefing's OFF semantics are **not fixed** by the blessed text — open item, `design.md` §14 item 13. |
| **DD-16** | **inner_soul fix is a redirect branch for ARI ONLY** | Fix by adding a new tool that Ari must remember to call; or change the redirect for all agents | The leak is that the existing reflex routes into a void. A new tool does not fix a reflex. Changing all agents widens blast radius past the mandate. |
| **DD-17** | **Constellation is cold-path only; no graph DB** | Incremental graph maintenance on the hot path; a graph database | Consolidation cost must scale with **change**, not corpus. SQL joins suffice at personal scale; a graph DB is a new service (R4). |
| **DD-18** | **One `.sql` migration file ships** (audit convention), additive `CREATE TABLE IF NOT EXISTS` ⇒ rollback-safe | Rely on `create_all` alone ("it works, why a file?") | 119-file convention: the `.sql` is the persistent audit record. `create_all`-only exemptions are **debts, not precedent**. |
| **DD-19** | **Stars stored flat, unused by retrieval, in v1** | Build star normalization/retrieval now | The seed must be captured from day one (cheap then, impossible to backfill later); deriving structure from it is v2's job. *(Normalization rules themselves are an open item — `design.md` §14 item 5.)* |
| **DD-20** | **Hot append must never block the turn**: tsvector instant, embedding async | Compute the embedding synchronously at write for immediate semantic recall | Memory capture must not be a latency tax on conversation. Lexical-immediately / semantic-soon is the designed asymmetry. |

---

## 4. Adjudication log (EMPTY — ready for Phase B)

**How to use:** one row per adjudicated deviation. The row is written **before** the deviation is implemented (P-2). Anchor drift (P-7) does **not** belong here — it is a re-grep, not a deviation.

| # | Date | Deviation (design says / reality forces) | Adjudication (architect) | Outcome (implemented / superseded / withdrawn) | Links |
|---|---|---|---|---|---|
| _—_ | _—_ | _—_ | _—_ | _—_ | _—_ |

**Columns:** `#` (sequential), `Date` (ISO), `Deviation` (what the design specifies **and** what reality forces — both, not just the problem), `Adjudication` (the architect's ruling, quoted where possible), `Outcome` (implemented as adjudicated / superseded by a later row / withdrawn), `Links` (commit, file, or `design.md` § for the affected text).

**Also record here (same table) if chosen in Phase B:** outcomes for the open items in `design.md` §14 — resolving one *is* an adjudication *if* it changes blessed text. Choosing the keepsake cap integer, for example, fills a delegated blank; it is still worth a row so the number has provenance.

---

## 5. Open items carried into Phase B (cross-reference)

The intake list lives in **`design.md` §14** (14 items: keepsake cap integer; pin attribution mechanism; briefing budget unit normalization; inner_soul anchor drift; star normalization; episode mutability partition; embedding worker identity/durability; persona approval lane vs `prompt_versions`; once-per-instance vs long-lived instances; `memoir_timeline(q?)` semantics; recall output budget; tool-count wording; kill-switch OFF semantics; PG materialization path).

**This file does not duplicate them.** When an item is resolved, the resolution appears in §4 above and, if it changes blessed text, the affected passage in `design.md` is superseded **surgically** per P-4.

---

## 6. Authority stack (who can change what)

| Layer | Contains | Changed by |
|---|---|---|
| **1 — Emperor rulings** | R1–R4 (this file §1) | the Emperor only (P-5) |
| **2 — Blessed design** | `design.md` substance (columns, caps, verbs, gates, env/table names, seams) | architect adjudication (§4), recorded |
| **3 — Plans** | Phase plans, task breakdowns | planner/worker, **within** layers 1–2 |
| **4 — Code** | implementation | worker, **within** layers 1–3; deviations escalate per §2 |
| **5 — This record** | the log of how layers 2–4 moved | append-only; never rewritten to look tidier |

---

*Phase A. Rulings R1–R4 verbatim (Emperor, 2026-11-04 design session). Embedded decisions indexed, not re-decided. Adjudication log empty by design — Phase B fills it. Captured 2026-11-05 on `feature/ari-memoir-store` @ `1f6898ae2`.*
