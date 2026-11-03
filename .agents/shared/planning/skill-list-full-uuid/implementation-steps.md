# Implementation Steps — wi-76d89e11 (lane `skill-list-full-uuid`)

Ordered, small, single surgical lane — **no phases beyond P1–P3**. Shipped-code delta target: ~6 changed lines + one short method, 3 files; one 2-line test fold; one new pin pack. All edits in the worktree `/home/nea/ensemble-src-wt-skill-list-full-uuid` on `fix/skill-list-full-uuid`.

Commit granularity (adjudication A-1): three commits —
1. `fix(skill-tools): emit verbatim-consumable ids — full uuid (db) + address (project-file) at the 4 render seams + agent-visible docstrings (wi-76d89e11)`
2. `test(skill-tools): fold stale 8-hex expectation + docstring tense in pf_surface (wi-76d89e11)`
3. `test(skill-tools): pin pack — skill id verbatim contract, M1-M6+M2b mutation legs (wi-76d89e11)`

---

## P1 — the emission-site fixes (4 sites, 5 edits, 3 files + the W1 docstring cluster; details + before→after in `plan-overview.md` §4)

Order is dependency-free; this sequence keeps each file reviewable in one pass:

1. **`daemon/tools/skill_tools.py:765`** — skill_list DB bracket token: `skill_id[:8]` → `skill_id` (one token; render :769-771 + `db_suffix` :762 byte-unchanged). *(P1-1)*
2. **`daemon/tools/skill_tools.py:1119-1123`** — skill_create success line: `created.id[:8]` → `created.id` (inside the existing None-guard ternary; sentence + `"unknown"` fallback kept). *(P1-2)*
3. **`daemon/tools/skill_tools.py:145`** — `_pf_skill_payload`: `"id"` re-keys to `getattr(file_skill, "address", None)`; ADD `"pseudo_id": getattr(file_skill, "id", None)` adjacent. All other keys untouched. Covers the fused-path file entries AND the file-only degraded path (consumer at :219). *(P1-3)*
4. **`daemon/services/project_skill_source/model.py`** — NEW `ProjectFileSkill.to_dict()`: `dataclasses.fields`-based dict, `pseudo_id` additive (attribute value), `id` = address. `_json_default` (skill_tools.py:91-96) picks it up with NO edit there. Field-set pin target: `set(to_dict()) == fields ∪ {"pseudo_id"}`; NO `version` key (property, not field — O-3). *(P1-4)*
5. **`daemon/services/skill_injection_service.py` `_format_injection` (:1262 read / :1271-1275 render)** — one file-only branch adopting the `_is_project_file_skill` helper semantics (skill_search_service.py:118-130: `pf:` address-prefix test FIRST, then `source == "project-file"` duck-tag — safe both directions; A2 fold): file body renders the ADDRESS in the id segment; DB path byte-unchanged; `track_injection` attribution untouched (stays pseudo-id). *(P1-5)*
6. **Agent-visible docstring corrections (W1 fold — doc-only, 3 sites, same commit)**: skill_tools.py:904 (skill_list `_full_doc_` example `- [<short_id>]` → `- [<id>]`), :1160 (`<short_id>` → `<id>`), :1163-1164 ("first 8 chars" sentence → full-uuid wording). The :1126 `Use skill_search() to find it.` pointer is ADJUDICATED RETENTION (A-4 — discovery guidance, not truncation teaching; no edit). Companion test-docstring tense fix rides P2 (P2c). *(P1-6 — details in plan-overview.md §4 P1-6)*

**Forbidden in P1** (ruling R3/R2 — the unsafe rewrite): remapping the `ProjectFileSkill.id` ATTRIBUTE, DB rows, or any internal join key. Pseudo-id remains THE internal key everywhere (embeddings, usage records, attribution, gc joins).

## P2 — stale-expectation fold + planner sweep results

### P2a — The fold (exactly 2 lines, 1 file)

`tests/tools/test_skill_tools_pf_surface.py`, test `test_dbrows_tag_only_when_file_source_wired` (:468):

- **:480** fixture: `"id": "abcdef0123456789"` → `"id": "abcdef01-2345-6789-abcd-ef0123456789"` (uuid4-shaped fixture)
- **:488** expected: `legacy_line = "- [abcdef01] code-review — workflow — active"` → `legacy_line = "- [abcdef01-2345-6789-abcd-ef0123456789] code-review — workflow — active"`

The knob-OFF leg at **:498** re-uses `legacy_line` (`assert off_out.splitlines()[1] == legacy_line`) — no third line needed. The pin's PURPOSE (the ` — source: db` suffix additivity, ON at :492 / OFF at :497-498) is preserved; only the id token grows. Precedent: v0.28.5 R1 stale-expectation fold ruling.

### P2b — Planner-run sweep (every id-shape hit adjudicated; fold list + verified-unaffected corpus)

Sweep method: grep over `tests/` + `test/packs/` for `[:8]`, 8-hex assertion patterns (`abcdef01`, bracket-line renders), `"id"` expectations on file payloads, `pf-<hex16>` fixtures, `created with id`, `(id: …, match score` segments, and `source: db`/`source: project-file` renders; every id-shape hit adjudicated below (W7 fold: completeness claim scoped to id-shape hits — tool-name-only matches adjudicated by category, above).

**FOLD list (stale expectations on the OLD shapes): exactly the 2 lines above — nothing else.**

**Verified-UNAFFECTED corpus (each with reason — no fold, no action):**

| Test site | Shape | Why unaffected |
|---|---|---|
| `tests/tools/test_skill_tools.py` (whole file) | soft-fail + service-call coverage | **W7 fold (row corrected)**: the file DOES carry id-render assertions — **:999** (`parsed["injected"][0]["skill"]["id"] == "sk-x"`) and **:1033** (`== "sk-bare"`, the `_json_default` `__dict__`-fallback leg, test :1006-1033). Both run against STUB/MOCK surfaces: fake search services returning hand-built dict entries (:999) and a bare to_dict-less object (:1033) — neither is a `ProjectFileSkill`, and our change adds `to_dict` only to that class; stub entries pass through `json.dumps` unchanged. UNAFFECTED both sides — and :1006-1033 doubles as armor that the `__dict__` fallback still works for to_dict-less objects |
| tool-name-only matches (5+ files: `test_skill_feedback_tool.py`, `test_skill_service_init.py`, `test_skill_repository.py`, `test_registry_skill_injection.py`, `test_skill_capture_killswitch.py`, plus wanderer/llm-failover/integration-flow files) | matched skill TOOL NAMES in imports/mocks only | **W7 fold**: no id-shape assertions anywhere in them; benign category adjudication |
| `tests/tools/test_skill_tools_pf_surface.py:744-770` | pseudo-id feedback routing/refusals (`pf-deadbeefdeadbeef`, `pf:` address routing) | routing is R2g NO-CHANGE; these keep running green and form adjacent armor for M6 |
| `tests/tools/test_skill_search_fusion_guard.py:150/:200/:231` | payload `skill.id == "db-skill-1"` | DB-shaped fixtures; DB render path unchanged |
| `tests/services/test_skill_search_fusion.py:875-911` | `_tag_result_sources` dedupe; `ProjectFileSkill(id="pf-deadbeefdeadbeef")` fixture | asserts object identity (`is file_skill`) + source tagging, NOT the payload id value; adding `to_dict()` changes no attribute |
| `tests/services/test_skill_injection_service.py:561/:594/:596` | `(id: skill-1|s1|s2, match score: …)` | DB-shaped SimpleNamespace fixtures with NO `source` attr (helper :55-80) → the new duck-type branch never fires → segment byte-identical |
| `tests/unit/test_context_messages.py:714` | `(id: abc, match score: 0.85)` | same — DB-shaped fixture, no `source` attr |
| `tests/integration/test_context_injection_integration.py:195` | `(id: sk-1, match score: 0.90)` | same |
| `tests/unit/routers/test_skills.py:370/:406/:752-782` | REST `body["id"]` assertions (mock `to_dict` → full strings) | REST untouched; already emits full ids |
| `tests/unit/routers/test_skills_search_invisibility.py:231` | `not projected["id"].startswith("pf-")` | REST search DB-only v1; our change never reaches REST |
| `tests/unit/test_project_skill_source_index.py:860` | internal `_inflight_refreshes` keyed by pseudo-id | internal key — stays pseudo-id (R3) |
| `tests/unit/test_project_skill_source_phase4_wiring.py:796` | `ProjectFileSkill(id="pf-abcdef0123456789")` fixture | off-loop scan test; asserts thread/kwargs, not payload id; attribute stays pseudo-id |
| `tests/postgres/test_project_skill_source_phase4_pg.py:144-154` | table-column existence assertions | schema untouched |
| `tests/postgres/test_project_skill_fusion_pg.py` | no id-render assertions | — |
| `tests/packs/psl_fix_cycle_mutation_matrix.py:130-136` | mutation pack; W4 leg exact-string-mutates the `db_suffix = " — source: db" if …` line (skill_tools.py:762) | our P1-1 edits :765 only — the pack's `old=` string survives verbatim; no pack leg touches `short_id`/`[:8]`/`_pf_skill_payload` |
| shell packs in `test/packs/` | — | NO existing pack pins skill_list/skill_view/skill_create renders (grep: zero hits) |
| e2e `[:8]` hits (`tests/e2e/test_u7s15_realseat_acceptance.py` — **33 hits, re-verified at HEAD by grep count**; the council's "has no `[:8]`" sub-claim did NOT reproduce — citation retained with evidence; + 4 other e2e files incl. `mock_source_server.py`) | instance/job/mission-id display prefixes (`job_id[:8]`, `parent_id[:8]`, …) | unrelated domain — not skill ids |

### P2c — Test-docstring tense correction (W1 fold, test-side, rides the fold commit)

`tests/tools/test_skill_tools_pf_surface.py:726-729` (`test_pseudo_id_form_accepted`): the docstring "The ``pf-<hex16>`` pseudo-id the injection path renders is accepted directly" goes stale post-fix (the injection path will render the ADDRESS). Reword to the compatibility framing: the `pf-` pseudo-id form REMAINS accepted — legacy-transcript compatibility per R2g/M6. Docstring only; the test body (verified :730-735: real-index `list_by_project(PROJECT_ID)[0].id` idiom) is untouched and stays green.

## P3 — the pin pack (concretized in `pin-spec.md`)

- New pytest file `tests/tools/test_skill_id_verbatim_contract.py` (sibling of `test_skill_tools_pf_surface.py`; same harness idioms: `_make_manager`-style manager shim, AsyncMock store, `tmp_path`+engine canon fixtures, real tool factory `create_skill_tools`).
- New shell pack `test/packs/skill_id_verbatim_contract_unit_test.sh`, registered in `.agents/tester/PACKS.md` with a DERIVED blast-radius note.
- Legs (i)–(iv) + mutation legs M1-M6 (ruling R5) + planner-added M2b; RED-at-BASE/GREEN-at-HEAD at BASE `c4161335b` anchored to the runtime repro RESULTS doc.

## Explicitly NOT in this lane

- No REST/FE work (fusion parked v2; REST finding is report-only).
- No `skill_fix` `pf-` refusal closure (routed, §5 of plan-overview).
- No echo-hygiene changes (:1258/:1489 — routed display-hygiene lane).
- No schema/migration authoring; census delta EMPTY expected at stage.
- No `.agents/shared/planning/metrics-unification/**`, no `screenshots/**`, no edits to the tester RESULTS doc (read-only evidence).
