# Pin Spec — wi-76d89e11: skill id verbatim contract (ruling R5, concretized)

**Artifacts**

- pytest: `tests/tools/test_skill_id_verbatim_contract.py` (sibling of `tests/tools/test_skill_tools_pf_surface.py`; same idioms: `_make_manager`-style manager shim, `AsyncMock` store, `tmp_path` + engine + canon fixtures)
- pack: `test/packs/skill_id_verbatim_contract_unit_test.sh`, registered in `.agents/tester/PACKS.md` with a **DERIVED blast-radius note** (house convention)

**House conventions applied** (per ruling R5): RED-at-BASE proof; planted-mutation legs RED→GREEN with digests; vacuous-pin guard (assert the RESOLVED skill is the intended one — never merely "no ERROR"); no generated-artifact hand-edits (N/A — no category registration changes, so no `tool-categories.json` regen; v0.28.9 lesson).

---

## H — Harness shape (leg (i): real-tool construction)

Mirror the repro's REAL production construction (reference: `.agents/tester/RESULTS/2026-11-03-wi76d89e11-phase0-runtime-repro.md` §(a) — its harness is the canonical shape):

- **Scratch SQLite** `sqlite:///<tmp>/pin.db` via `SQLModel.metadata.create_all` (house norm — NEVER the migration runner; fresh-SQLite runner boot is broken by `20260714_000001`).
- **Real repos** via the same factories `manager.py` uses: `create_skill_repository` / `create_skill_lineage_repository` / `create_project_repository` / `create_instance_repository` + `ProjectSkillFileRepository` / `ProjectSkillEmbeddingRepository` / `ProjectSkillUsageRecordRepository`.
- **Real services**: `SkillStoreService(skill_repo, lineage_repo, embedding_service=None)` (manager.py:2419 shape) and `ProjectSkillSourceService(instance_repository, project_repository, file_repository, embedding_repository, embedding_service=None, llm_config={})` (manager.py:2321 shape).
- **Real tool factory**: `create_skill_tools(manager, current_instance_id)` → invoke via `tool.ainvoke({...})` (the true agent path). Manager shim carries ONLY the real service/repo objects on the exact attributes the tool layer reads: `_skill_store_service`, `_project_skill_source`, `_project_skill_file_repository`, `_project_skill_usage_repository`, `_instance_repository`.
- **Seed BOTH sources**: ≥1 DB skill via the real creation path `SkillStoreService.create_skill(...)` (known uuid4 fixture); ≥1 indexed file skill via the real loader `ProjectSkillSourceService.ensure_index(worktree_canon, project_id)` (the 5 canon `.agents/tester/skills/*/` dirs at this base; or a minimal tmp canon).
- **Fences (mandatory)**: one-shot scratch DB per run (`rm` first — UNIQUE collision on `instances.instance_id`, repro surprise #6); per-variable env scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT -u ENSEMBLE_SELF_ENV -u PGPASSWORD -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE` — NEVER `env -u POSTGRES_*`); `HOME` → scratch (FAKE_HOME-style); import guard asserting `daemon/__init__.py` resolves from THIS worktree; no daemon boot; zero live contact.

## D1 — The verbatim-contract legs ((ii) + (iii) + (iv))

1. **(ii) list → verbatim → view**: invoke `skill_list` with BOTH sources wired; parse EVERY DB row's bracket token (`- [<token>] …`) and EVERY file row's address token (`- [<ns>] pf:<ns>:<name> …`); feed EACH token VERBATIM into `skill_view`.
2. **(iii) success AND identity (vacuous-pin guard)**: each `skill_view` call must NOT return the refusal envelope (`ERROR: skill_view: no skill with id`) AND the returned document's `# <name>` header must EQUAL the intended fixture skill's name (DB fixture name; file skill dir name) — proving the resolved skill IS the intended one, for BOTH sources.
3. **(iv) value equality (per source — the dispatcher's "id == stored full uuid" clause, both sources):**
   - DB: bracket token **str-equal** to the stored full uuid4 (the `Skill.id` fixture value from `SkillStoreService.create_skill` — NOT a regex/shape match).
   - File: address token **str-equal** to `mint_address(ns, name)` (import from `daemon.services.project_skill_source.model`).
4. **Search-payload legs (dispatcher pin-spec item (iv))**, over BOTH render paths — fused (store + file source wired → `skill_search` returns merged JSON) and file-only degraded (store absent → `_file_only_bm25_search` path):
   - `id` **consumable**: take the file entry's `"id"` from the parsed JSON, feed verbatim to `skill_view` → resolves with identity assertion (same as D1-iii).
   - **additive `pseudo_id` present**: file entry carries `"pseudo_id"` matching the sha16 form `pf-` + 16 hex AND equal to the indexed row's `project_skill_files.id` (the internal-key continuity — R3).
   - **field-set equality** (fused path, pins the `to_dict()` seam against vanishing keys): `set(entry.keys()) == {f.name for f in dataclasses.fields(ProjectFileSkill)} | {"pseudo_id"}` — every key the `__dict__` path used to emit still present; `id == entry["address"]`; `version` ABSENT (property, not field — O-3).
   - **skill_feedback dual-form routing intact** (R2g): with `pf:` address AND with `pf-<hex16>` inputs, `_feedback_pf_skill` still records a `project_skill_usage_records` row keyed by the RESOLVED pseudo-id; W5 refusals (knob-off :744-760 shape / not-indexed :762-770 shape) still refuse with zero writes.
   - **skill_create leg**: after `skill_create`, the success line's id token (parse `created with id <token>.`) resolves verbatim in `skill_view` with identity assertion, and `token ==` the row's stored full uuid.
   - **injection body leg**: `_render_file_skill`/`_format_injection` output for a file skill contains `(id: pf:<ns>:<name>, match score:` — and does NOT contain `(id: pf-` for file entries; DB-path injection output still renders `(id: <full uuid>`.

## M — Mutation legs (planted, RED→GREEN, digest-proven)

| Leg | Plant (exact) | Goes RED |
|---|---|---|
| **M1** | skill_tools.py:765 — re-introduce `[:8]` (`short_id = skill_id[:8] if isinstance(skill_id, str) else "????????"`) | D1 (ii)/(iii)/(iv) DB legs |
| **M2** | skill_tools.py:145 — revert `"id"` to `getattr(file_skill, "id", None)` | search-payload leg (file-only path) |
| **M2b** (planner-added, adjudication A-3) | model.py `to_dict()` — revert `d["id"] = self.address` to keep `d["id"] = d["pseudo_id"]`-shape (id = attribute) | search-payload FUSED-path leg (covers the second render seam of R2(d); M2's plant text only reaches :145) |
| **M3** | skill_injection_service.py — revert the formatter branch (file body renders `getattr(skill, "id")` again) | injection body leg |
| **M4** | skill_tools.py:1119-1123 — revert to `created.id[:8]` | skill_create leg |
| **M5** (safety invariant — R3 hard constraint) | at the CONSTRUCTION site `daemon/services/project_skill_source/service.py:1114`, plant the UNSAFE rewrite: construct `ProjectFileSkill(id=mint_address(...))` instead of the pseudo-id | embeddings invariant leg: assert `ProjectFileSkill.id` keeps `mint_pseudo_id` shape (`pf-`+16hex) AND `file_embeddings_for(<sha16>)` still returns the indexed rows (a rerank over an indexed fixture still consumes embedding rows) — the degradation-not-error failure mode |
| **M6** (safety invariant — R2g) | skill_tools.py:1465 — remove `or skill_id.startswith(PSEUDO_ID_PREFIX)` | legacy-transcript feedback leg: a `pf-<hex16>` input (pre-fix transcript shape) must still record metrics |

Mutation legs run as pack-level plants (source edit → run pack → RED → restore → GREEN), digests archived per house convention (the `psl_fix_cycle_mutation_matrix.py` pattern is the in-repo precedent for pack-managed plants).

## PROC — RED-at-BASE / GREEN-at-HEAD procedure

- **BASE commit: `c4161335b406b884d1de6c8e7bf4b3d1f7fe2abe`** (branch base == origin/latest at lane open). The repro anchor is `.agents/tester/RESULTS/2026-11-03-wi76d89e11-phase0-runtime-repro.md` (verbatim refusal envelope + control leg) — the pin's expected BASE behavior must MATCH it.
- **Expected RED at BASE**: D1 DB legs (ii)/(iii)/(iv); skill_create leg; search-payload `id`-consumable + `pseudo_id` + field-set legs (both paths); injection body leg.
- **Expected GREEN at BASE (pinned anyway as regression armor)**: D1 file-row legs (address already emitted — repro §(b)/(c): all 5 RESOLVE); feedback dual-form routing legs (existing armor at pf_surface :744-770); REST-untouched assertions N/A (tool-layer pack).
- **GREEN-at-HEAD**: all legs green after the P1 diff; the P2a-folded pf_surface W4 pin green in the same run; full skill-test corpus green (the P2b sweep predicts zero other reddens).
- **Non-vacuity**: every RED-at-BASE leg has an explicit identity/value assertion (D1-iii/(iv)), so a pin that "passes because it asserts nothing" is structurally impossible; M-legs provide the second direction (planted regression ⇒ RED).
