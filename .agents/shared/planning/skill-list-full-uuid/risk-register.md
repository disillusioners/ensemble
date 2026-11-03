# Risk Register — wi-76d89e11 (ruling R6 properties → dependency → guarding pin)

Short form: what each safety property depends on, and which pin guards it.

| # | Safety property (R6) | Depends on (in the diff) | Guarding pin |
|---|---|---|---|
| 1 | skill_view exact-PK resolution unchanged (DB `session.get`; file `lookup_explicit`) | NOTHING — view branch + repo untouched; only EMITTERS change | D1 legs (ii)/(iii) exercise BOTH view branches through the new emissions (`pin-spec.md` §D1) |
| 2 | No change to id minting/scheme (uuid4 PK default; `mint_pseudo_id`; `mint_address`) | NOTHING — model mint functions + `Skill` PK untouched | M5 (attribute keeps `pf-`+16hex mint shape; embeddings rows still consumed) + D1 (iv) str-equal-to-stored-value assertions |
| 3 | No schema change (zero `.sql`; project_skill tables untouched) | Render-layer-only diff | Stage-time migration census delta EMPTY (standing rule: delta-empty-legitimate); no migration authored anywhere in P1–P3 |
| 4 | skill_feedback routing semantics unchanged (BOTH `pf:` and `pf-` accepted; W5 fail-closed; metrics-only keyed by RESOLVED pseudo-id) | Implementer restraint — an over-eager "cleanup" of the now-rarer `pf-` branch at :1463-1466 | M6 (branch removal ⇒ RED: legacy-transcript `pf-` input must still record metrics) + existing armor at `test_skill_tools_pf_surface.py:744-770` + the pin's dual-form routing leg (§D1-4) |
| 5 | Fail-closed behaviors intact (project-scope refusals, body-unreadable, P7 refusals) | NOTHING — untouched paths | Existing pins (`test_skill_tools_pf_surface.py:348/:370/:561` per ruling R6-5) keep running in the corpus (P2b: unaffected) |
| 6 | No REST change | Tool-layer-only diff | Diff inventory asserts REST files untouched (plan-overview §5); REST test corpus green (P2b rows: `test_skills.py`, `test_skills_search_invisibility.py`) |
| 7 | No live behavior change beyond the id string rendered to agents | (a) the `.id` ATTRIBUTE never remapped; (b) `to_dict()` never drops a field | (a) M5; (b) field-set-equality pin `set(to_dict()) == fields ∪ {"pseudo_id"}` + every value source-identical except `id` (`pin-spec.md` §D1-4); M2b digest-proves the fused seam |
| 8 | Byte-identity of all unaffected payload parts (separators, `db_suffix`, file-row lines, headers/counts, injection segment layout, create-line sentence) | Token-only edits (A-4) + additive-only keys | Folded W4 pin (`:488` post-fold) still asserts the suffix-additivity + OFF-leg byte-shape; file-row lines asserted verbatim in D1 (ii) parsing; R4 table is the review checklist |

**Lane-level risks**

| Risk | Mitigation |
|---|---|
| An implementer "improves" beyond the 4 sites (echo sites, view render line, `to_summary`) | The OUT/NO-CHANGE table (plan-overview §5) is the reviewer checklist; diff-shape expectation stated numerically (~6 lines + 1 method, 3 files) |
| Unexpected test reddens at gate time (stale expectations the sweep missed) | A-2 sweep is COMPLETE (every grep hit adjudicated in implementation-steps §P2b); prediction: net-new deterministic reds = 0 outside the 2-line fold |
| Mutation plants touch lines the diff moved (pack exact-string mismatch) | All plant strings quoted against HEAD line numbers (§M); plants are pack-managed with restore legs (house pattern) |
| Harness accidentally resolves the SHARED checkout or live install | Import guard + `HOME`→scratch + per-variable env scrub are mandatory pack assertions (§H fences); the repro's one-shot DB rule (surprise #6) baked in |
