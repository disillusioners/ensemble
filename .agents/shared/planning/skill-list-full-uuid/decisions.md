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

Stage-only lane: **NO live promote, NO live restarts, NO `.env` edits, zero live contact.** Surgical diff (~6 lines + one method, 3 files, + the P1-6 docstring corrections). Migration census delta EMPTY expected at stage (standing rule: delta-empty-legitimate). No tool-category registration changes ⇒ no generated-artifact regen. Do not touch `.agents/shared/planning/metrics-unification/**`, `screenshots/**`, or the tester RESULTS doc (read-only evidence). **Next free version slot (W6 fold; council census 2026-11-03, re-verified read-only): `v0.28.10`** — `releases/` = {v0.28.7, v0.28.8, v0.28.9}, highest tag `v0.28.9`, live `v0.28.9`; **re-derive at stage time** (concurrent lanes may consume it).

## Planner-level adjudications

| # | Adjudication | Rationale |
|---|---|---|
| A-1 | **Commit granularity**: three commits — (1) the 4-site render fix, (2) the 2-line stale fold, (3) the pin pack (+ PACKS.md row). | The render fixes are one semantic unit (the id-emission contract); separating fold from pins keeps reviewer delta rounds and mutation re-runs cleanly attributable; the fold must NOT ride in the fix commit (it is test-side remediation of a stale expectation, v0.28.5 R1 precedent doctrine-asserted in its own commit). |
| A-2 | **Fold-list membership**: exactly `test_skill_tools_pf_surface.py:480/:488` (2 lines; OFF-leg :498 reuses `legacy_line`). | Full-corpus sweep (implementation-steps.md §P2b) — every other candidate hit adjudicated unaffected with reason; nothing else pins the 8-hex render or pseudo-id-as-id shape. |
| A-3 | **M2b added** (7th mutation leg): plant `to_dict()` id-revert on the fused seam. | R5's M2 plant text reaches only :145; the fused path is a distinct render seam named IN by R2(d) — without M2b a to_dict-only regression has no digest-proven mutation leg. Extension of ruling coverage, not redesign. |
| A-4 | **Token-only edits at :765/:1119-1123**: keep the `short_id` variable name; keep the `"Use skill_search() to find it."` sentence and the `"unknown"` fallback. | R4 mandates ❌-shape-change: renaming the local or trimming the sentence is diff-widening churn with zero contract value. |
| A-5 | **`to_dict()` implementation shape**: `dataclasses.fields`-based; `pseudo_id` = attribute value inserted BEFORE the `id` override; no `version` key (property, not field); mutable fields (`manifest` dict, `shadows` list) copied defensively (mirrors `to_summary` :181-182 practice). | Field-set equality is the pinned invariant (R6-7b); copies prevent aliasing surprises in JSON serialization. |
| A-6 | **Pin exercises BOTH search render paths** (fused + file-only degraded via :219). | R2(d) names two seams under ONE ruling item; a single-path pin would leave the other unpinned (observation O-2). |
| A-7 | **Injection branch discriminator**: adopt the `_is_project_file_skill` helper semantics WHOLE (A2 fold): `pf:` address-prefix test FIRST, then `source == "project-file"` duck-tag fallback — i.e. skill_search_service.py:**:118-130** (the module-level helper), not a bare `getattr(source)` test. | Safe both directions: DB `Skill` rows carry neither a `pf:` address nor a `source` field, while a file skill constructed without the source tag still resolves via its address; matches the pipeline's duck-typed design (fixtures use light fakes). |
| A-8 *(council A6)* | *(cosmetic, council-noted)* The `short_id` local variable NAME at :765 is retained post-fix (A-4 minimal-diff adjudication stands). | Local-scope name only; renaming widens the diff with zero contract value. |

## Council fold record (2026-11-03 — verdict APPROVE-WITH-ADVISORIES, 0 critical / 7 warnings / 6 advisories)

Per-item discharge (every fold verified against HEAD `c4161335b` before amending; one sub-claim did not reproduce — noted):

| Item | Discharge → doc § |
|---|---|
| W1 docstrings teach truncated shape | plan-overview §4 **P1-6** (3 daemon docstring sites as explicit P1 edit rows; :1126 pointer adjudicated retention) + implementation-steps P1 step 6; test-side companion → P2c |
| W2 M5 embedding half vacuous (harness `embedding_service=None` ⇒ `[]` at BASE) | pin-spec §M **M5** — seed via `replace_for_skill` (repo receipt project_skill_file/repository.py:245); asserts (a) shape, (b) non-empty at sha16, (c) empty at address |
| W3 M6 needs the REAL indexed pseudo-id | pin-spec §M **M6** + §D1-4 feedback leg — `list_by_project(PROJECT_ID)[0].id` idiom (pf_surface :732-734) |
| W4 nested id path + scoped field-set | pin-spec §D1-4 — nested `["injected"][i]["skill"]["id"]` (precedent fusion_guard :150); field-set equality fused-path-only; file-only path asserts consumability + pseudo_id + address |
| W5 fused leg stage-3 unspecified | pin-spec §H — `_skill_search_service` wiring; `config=None` ⇒ deterministic `_degraded_select` (gate verified :692-697, `self._config` stored plainly :527); BOTH degraded seams named; implementer-TODO flag on the constructor-vs-manager.py confirmation |
| W6 version slot unnamed | decisions §Ground rules (A-0) + plan-overview §7 row 8 — **v0.28.10**, census re-verified read-only, re-derive-at-stage caveat |
| W7 P2b corpus row false + citation + omissions + "COMPLETE" overclaim | implementation-steps §P2b — test_skill_tools.py row corrected (:999/:1033 stub/mock id assertions, unaffected, reworded); e2e row re-verified (**33 `[:8]` hits in test_u7s15 — the council's "has no `[:8]`" did NOT reproduce; citation retained with grep-count evidence**); tool-name-only 5+ files row added; header softened to "every id-shape hit adjudicated" |
| A1 stale anchors (`_render_pf_view` :399-400; `_format_injection` :1173-1332 context) | plan-overview §3 drift table (two new rows) + §5 row fixed + §2/P1-5 wording |
| A2 discriminator cite | decisions **A-7** amended (adopt `_is_project_file_skill` :118-130 whole) + plan-overview P1-5 |
| A3 uuid mint is model default_factory | plan-overview §2 control-leg receipt corrected (repro phrasing noted) |
| A4 M5 anchor :1115 | pin-spec §M M5 (`id=meta.pseudo_id` at :1115, `_to_file_skill` :1111-1122) |
| A5 P1-4 sketch omits A-5 copies | plan-overview P1-4 — explicit "implementation follows A-5, not the sketch" note |
| A6 short_id name note | plan-overview §4 header note + decisions A-8 (council A6) row |

## Routed follow-ups (report to dispatcher; NOT this lane)

1. **skill_fix `pf-` refusal gap** (skill_tools.py:1211) — pseudo-id falls through to `dispatch_fix` as a bogus DB id; one-token fix (`or skill_id.startswith(PSEUDO_ID_PREFIX)` → `_pf_refusal`) but belongs to the P7-refusal family that must move together (skill_analyze / skill_evolve / skill_get_metrics + REST POST /{id}/fix, :1201-1206). Harmless today (evolution lane disabled) and LESS reachable after this lane. → skill-evolution/fix-dispatch lane.
2. **REST non-sharing finding** — REST shares NOTHING with the tool projection (full uuid via `to_dict()` at routers/skills.py:282-308; REST search DB-only v1, skill_search_service.py:592-600). The "FE/REST list if it shares the projection" premise resolves to: it does not. → parked v2 fusion (no action).
3. **skill_feedback asymmetry is deliberate** (dual-form routing, :1449-1466) — do not "fix"; compatibility path for pre-fix transcripts (M6 armor).
4. **Echo truncations** (:1258 skill_fix, :1489 skill_feedback display) — display hygiene lane (caller-held echoes; both pass full ids downstream where functional).
5. **`_render_pf_view` `- id: <pseudo-id>` line + `body_path` root-join display oddity** (repro surprise #5) — untouched, low-value shape churn.

## Gate order (binding)

**reviewer council → fresh approver → implementation (P1–P3) → code council → tidier → tester functional → stage (NO promote).**

Tester functional gate expectations seeded by this plan: original symptom proven BOTH directions (repro anchors BASE); pack matrix green; net-new deterministic reds = 0 (the A-2 fold list is the complete prediction); migration census delta EMPTY; staged verification per house standard; promote remains the user's ceremony.
