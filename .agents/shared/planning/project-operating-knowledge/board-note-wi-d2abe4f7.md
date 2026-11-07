# Board note — wi-d2abe4f7 (the parked "Guidelines system" work item)

**Purpose of this file:** the POK Phase-2 lane's handoff CONTENT for
ari's board update on `wi-d2abe4f7`. The lane NEVER writes the board
(ari/leader dual-gate owns the board write) — this note is what ari
applies. **Intent: annotate-and-close** — the board item becomes a
POINTER to the POK mechanism; the board is a pointer, never a
duplicate of the design or the code.

---

## Disposition

`D3-folded-into-POK` — the parked guidelines work item is folded into
the Project Operating Knowledge (POK) trust tier. Decision status:
**DECIDED-mechanism-attached** (ratified by the Emperor 2026-11-07;
the mechanism is this lane's implementation — D3 is UN-PARKED).

## Mechanism (one line)

**One system, two trust tiers: agent-captured vs dev-curated via
`source_tier`; curated = the parked guidelines (the dev-curated
immutable reference band), delivered in a dedicated injection band
(cap 2 per view); write/supersede/flip curator-only.** Concretely:
POK entries carry `source_tier ∈ agent|curated` (TEXT NOT NULL
DEFAULT 'agent', CHECK-closed, additive column on
`project_operating_knowledge`); curated entries render in the
curated band of the per-view admission ladder (between pins and
ranked actives, inside the SHARED 8-entry / 2,900-char budget — no
separate slice, no ranking boost); superseding or reclassifying a
curated entry requires a curator (leader / project-manager per A-9);
tier flips are a `pok_curate` parameter, both directions; an omitted
tier on content supersede INHERITS the target's tier (refresh never
reclassifies authority).

**Finalization sha:** the final commit of the tier implementation is
`4594241b716d8f20db21f114b87ca2ebc6f64673` (branch
`feature/project-operating-knowledge`; the tier spans
`194d2d9f5` schema → `a6f3566ef` semantics → `1db11e430` curated
band → `4594241b7` the AC-T1..T7 test pack).

## Seeding status

**mechanism live; content authoring future** — draft preserved
verbatim in history event `8ffd3082-a1bb-4273-a5ea-e9ac87f5ceca`;
tool-lane seeding pending retrieval; **NO SQL backfill** (seeding is
the tool lane: a curator seeds per slug via
`pok_upsert(source_tier='curated')`, one call per slug, once the
draft text is retrievable — `project_history_search` after the dev
daemon returns, or operator-hands-verbatim).

## Pointers

- **Design:** `.agents/shared/planning/project-operating-knowledge/trust-tier-addendum.md`
  (T-1..T-8 + AC-T1..T7 — the mechanism, the injection semantics,
  the mutation lattice, the seeding contract, and the acceptance
  gate). Companion to `architecture-recommendation.md` v3.4 +
  `decisions.md` (A-2…A-19, ER-1) in the same directory; closes
  planner gap G-1 (`implementation-readiness.md:58`).
- **Preserved draft:** project-history event
  `8ffd3082-a1bb-4273-a5ea-e9ac87f5ceca` (the "Guidelines system"
  text, preserved verbatim; not in-repo, not in RAG — documented by
  `plane-pm-issue-tools/phase5-plan.md:37-41`). The event's open
  questions are ALL answered by the fold: the mechanism is the POK
  trust tier (this note + the addendum); no separate guidelines
  subsystem will be built.

## Close shape (for ari)

Annotate `wi-d2abe4f7` with the disposition, the mechanism line, the
finalization sha, and the seeding status above; keep the pointers;
close the item — **annotate-and-close; the board is a pointer, never
a duplicate.**
