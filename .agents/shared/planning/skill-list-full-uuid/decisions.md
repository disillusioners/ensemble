# Decisions — wi-76d89e11 (lane `skill-list-full-uuid`)

## Binding inputs (not re-litigated — planner-light mode)

**Architect rulings R1–R7** (`.agents/shared/planning/skill-list-full-uuid/architecture-ruling.md`) — recorded BINDING:
- **R1** id-emission contract per source: db → full uuid4; project-file → address `pf:<ns>:<name>`; surfacing the address as the file-skill agent-facing `id` is NOT an id-format/minting change; pseudo-id stays the INTERNAL key.
- **R2** scope: IN = (a) skill_list :765, (b) skill_create :1119-1125, (d) search payload :145 + fused `to_dict()` seam, (e) injection file body; OUT = (c) skill_fix/skill_feedback echo truncations (display hygiene), (f) skill_fix `pf-` refusal gap (skill-evolution/fix-dispatch lane), (h) REST/FE (parked v2); NO-CHANGE = (g) skill_feedback dual-form routing (load-bearing), skill_view's own `- id:` render line, low-match no-id rows.
- **R3** coupling map: the ONE unsafe rewrite = remapping the `ProjectFileSkill.id` ATTRIBUTE (embeddings + attribution silently degrade) — prohibited, pinned by M5.
- **R4** payload preservation: value-only changes at :765/:1119/:1262-segment; additive `pseudo_id` keys only; `to_dict()` field-set == dataclass fields; everything else byte-identical.
- **R5** pin spec (concretized in `pin-spec.md`): real-tool harness, verbatim list→view legs, identity assertions, str-equal value assertions, M1–M6, RED-at-BASE at c4161335b.
- **R6** safety properties → preserved (mapped in `risk-register.md`).
- **R7** premise corrections: DB ids were never 8-hex (display truncation); file skills have no uuid (address is the only verbatim-consumable form); minimal fix shape = 4 sites in 3 files.

**Dispatcher binding shape** — mapped clause-by-clause in `plan-overview.md` §7.

## Ground rules (A-0, binding)

Stage-only lane: **NO live promote, NO live restarts, NO `.env` edits, zero live contact.** Surgical diff (~6 lines + one method, 3 files). Migration census delta EMPTY expected at stage (standing rule: delta-empty-legitimate). No tool-category registration changes ⇒ no generated-artifact regen. Do not touch `.agents/shared/planning/metrics-unification/**`, `screenshots/**`, or the tester RESULTS doc (read-only evidence).

## Planner-level adjudications

| # | Adjudication | Rationale |
|---|---|---|
| A-1 | **Commit granularity**: three commits — (1) the 4-site render fix, (2) the 2-line stale fold, (3) the pin pack (+ PACKS.md row). | The render fixes are one semantic unit (the id-emission contract); separating fold from pins keeps reviewer delta rounds and mutation re-runs cleanly attributable; the fold must NOT ride in the fix commit (it is test-side remediation of a stale expectation, v0.28.5 R1 precedent doctrine-asserted in its own commit). |
| A-2 | **Fold-list membership**: exactly `test_skill_tools_pf_surface.py:480/:488` (2 lines; OFF-leg :498 reuses `legacy_line`). | Full-corpus sweep (implementation-steps.md §P2b) — every other candidate hit adjudicated unaffected with reason; nothing else pins the 8-hex render or pseudo-id-as-id shape. |
| A-3 | **M2b added** (7th mutation leg): plant `to_dict()` id-revert on the fused seam. | R5's M2 plant text reaches only :145; the fused path is a distinct render seam named IN by R2(d) — without M2b a to_dict-only regression has no digest-proven mutation leg. Extension of ruling coverage, not redesign. |
| A-4 | **Token-only edits at :765/:1119-1123**: keep the `short_id` variable name; keep the `"Use skill_search() to find it."` sentence and the `"unknown"` fallback. | R4 mandates ❌-shape-change: renaming the local or trimming the sentence is diff-widening churn with zero contract value. |
| A-5 | **`to_dict()` implementation shape**: `dataclasses.fields`-based; `pseudo_id` = attribute value inserted BEFORE the `id` override; no `version` key (property, not field); mutable fields (`manifest` dict, `shadows` list) copied defensively (mirrors `to_summary` :181-182 practice). | Field-set equality is the pinned invariant (R6-7b); copies prevent aliasing surprises in JSON serialization. |
| A-6 | **Pin exercises BOTH search render paths** (fused + file-only degraded via :219). | R2(d) names two seams under ONE ruling item; a single-path pin would leave the other unpinned (observation O-2). |
| A-7 | **Injection branch discriminator**: `getattr(skill, "source", None) == "project-file"` (import/compare against the model's `source` field default), matching skill_search_service.py:127-130. | Ruling R2(e) prescribes exactly this duck-type; do NOT invent an isinstance-based test (fixtures use SimpleNamespace duck-types by design). |

## Routed follow-ups (report to dispatcher; NOT this lane)

1. **skill_fix `pf-` refusal gap** (skill_tools.py:1211) — pseudo-id falls through to `dispatch_fix` as a bogus DB id; one-token fix (`or skill_id.startswith(PSEUDO_ID_PREFIX)` → `_pf_refusal`) but belongs to the P7-refusal family that must move together (skill_analyze / skill_evolve / skill_get_metrics + REST POST /{id}/fix, :1201-1206). Harmless today (evolution lane disabled) and LESS reachable after this lane. → skill-evolution/fix-dispatch lane.
2. **REST non-sharing finding** — REST shares NOTHING with the tool projection (full uuid via `to_dict()` at routers/skills.py:282-308; REST search DB-only v1, skill_search_service.py:592-600). The "FE/REST list if it shares the projection" premise resolves to: it does not. → parked v2 fusion (no action).
3. **skill_feedback asymmetry is deliberate** (dual-form routing, :1449-1466) — do not "fix"; compatibility path for pre-fix transcripts (M6 armor).
4. **Echo truncations** (:1258 skill_fix, :1489 skill_feedback display) — display hygiene lane (caller-held echoes; both pass full ids downstream where functional).
5. **`_render_pf_view` `- id: <pseudo-id>` line + `body_path` root-join display oddity** (repro surprise #5) — untouched, low-value shape churn.

## Gate order (binding)

**reviewer council → fresh approver → implementation (P1–P3) → code council → tidier → tester functional → stage (NO promote).**

Tester functional gate expectations seeded by this plan: original symptom proven BOTH directions (repro anchors BASE); pack matrix green; net-new deterministic reds = 0 (the A-2 fold list is the complete prediction); migration census delta EMPTY; staged verification per house standard; promote remains the user's ceremony.
