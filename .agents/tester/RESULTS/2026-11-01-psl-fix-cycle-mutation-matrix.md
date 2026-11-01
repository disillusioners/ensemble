# project-skill-loader — Phase-4 code-review FIX CYCLE: mutation matrix artifact

**Date:** 2026-11-01 · **Lane:** `feature/project-skill-loader` · **HEAD:** `53eb6f9e4` (fix-cycle fold)
**Scope:** code-review W1–W7 fold (7 warnings; advisories deferred to the tidier pass)
**Verdict:** 🟢 **12/12 discriminating legs RED→GREEN** + 1 belt leg as expected; harness exit 0.

---

## 1. Why this file exists (the verification gap it closes)

A prior report carried a **"42/42 mutation re-verify"** claim with **no locatable artifact in any
worktree file**. That figure is **RETRACTED as unverifiable**. In its place this file records an
*enumerated* leg set — every leg named in code (`tests/packs/psl_fix_cycle_mutation_matrix.py`),
planted, run, restored (sha-verified) and re-run. The claim is now exactly what the table says:
**12 discriminating legs + 1 belt leg, all accounted for**.

## 2. Reproduce

```bash
cd /home/nea/ensemble-src-wt-project-skill-loader
.venv/bin/python tests/packs/psl_fix_cycle_mutation_matrix.py \
    [--legs W1-corpus,W6] [--out /tmp/psl-fix-cycle-matrix.tsv]
# exit 0 ⇔ every leg RED-with-defect AND GREEN-after-restore
```

The harness scrubs `POSTGRES_*` / `DATABASE_URL` / `ENSEMBLE_SELF_ENV` **per variable** (never
`env -u POSTGRES_*`, which is a silent no-op), runs each leg's single pin with
`--timeout=120 -p no:randomly`, restores with `git checkout -- <file>`, and compares the file
sha256 before/after. A leg that stays GREEN with its defect planted is reported
`NON-DISCRIMINATING` and fails the run.

## 3. Captured output (verbatim, 2026-11-01)

```
leg	file	node	planted_sha	with_defect	restored_sha	restored
W1-corpus	daemon/services/project_skill_source/service.py	test_corpus_empty_when_index_rebound_to_other_project	3b975a52e962	rc=1	71eea2fd59c0	rc=0 :: RED->GREEN
W1-lookup	daemon/services/project_skill_source/service.py	test_lookup_explicit_miss_when_index_rebound	3f2b9e12c10d	rc=1	71eea2fd59c0	rc=0 :: RED->GREEN
W2	daemon/services/skill_search_service.py	test_file_embeddings_bridged_off_the_loop_thread	f5b2e235e0e3	rc=1	c0eb02b1de54	rc=0 :: RED->GREEN
W3	daemon/manager.py	test_gcjob_registered	fdf889781362	rc=1	2595e2dcc660	rc=0 :: RED->GREEN
W4	daemon/tools/skill_tools.py	test_dbrows_tag_only_when_file_source_wired	6f8dfe957ab8	rc=1	81e6d4588363	rc=0 :: RED->GREEN
W5	daemon/tools/skill_tools.py	test_pseudo_id_refused_when_not_indexed	e448481a63d7	rc=1	81e6d4588363	rc=0 :: RED->GREEN
W5-off-guard	daemon/tools/skill_tools.py	test_pseudo_id_refused_under_knob_off	6aa72d53eb60	rc=0	81e6d4588363	rc=0 :: BELT (expected non-discriminating)
W6	daemon/services/project_skill_source/service.py	test_body_file_only_symlink_refused_at_read	456fcb0625af	rc=1	71eea2fd59c0	rc=0 :: RED->GREEN
P4-M1	daemon/manager.py	test_site_1_search_ctor_threads_file_source	99bcaca28e68	rc=1	2595e2dcc660	rc=0 :: RED->GREEN
P4-M3	daemon/services/skill_search_service.py	test_stage3_skipped_no_llm_call	f2d4042432d3	rc=1	c0eb02b1de54	rc=0 :: RED->GREEN
P4-M4	daemon/api.py	test_refresh_call_threads_skill_sources	0dff8d8b4b4e	rc=1	8a00e247ea86	rc=0 :: RED->GREEN
P2-M4	daemon/services/skill_search_service.py	test_db_entries_tagged_when_fusion_active	ec416b128b37	rc=1	c0eb02b1de54	rc=0 :: RED->GREEN
P3-M3	daemon/tools/skill_tools.py	test_search_threads_instance_id	90d67d667dc7	rc=1	81e6d4588363	rc=0 :: RED->GREEN

SUMMARY: 13/13 legs accounted (12 discriminating legs RED->GREEN, 1 belt leg(s) as expected)
HARNESS_EXIT=0
```

`planted_sha` ≠ `restored_sha` on every leg (proof the mutation actually landed) and
`restored_sha` == the committed blob on every leg (proof the restore is byte-exact).

## 4. Leg map (what each leg plants, and where it is claimed)

| Leg | Planted defect | Discriminating pin |
|---|---|---|
| W1-corpus | `corpus_for` project re-check dropped (`if index is None:`) | `TestCorpusProjectBinding::test_corpus_empty_when_index_rebound_to_other_project` |
| W1-lookup | `lookup_explicit` post-gate re-check dropped (`if False:`) | `…::test_lookup_explicit_miss_when_index_rebound` |
| W2 | `await asyncio.to_thread(...)` reverted to a direct sync call | `TestStage2DualSourceAdapter::test_file_embeddings_bridged_off_the_loop_thread` |
| W3 | `project_skill_gc` maintenance registration removed | `TestGcMaintenanceWiring::test_gcjob_registered` |
| W4 | `source: db` suffix made unconditional | `TestSkillListFiles::test_dbrows_tag_only_when_file_source_wired` |
| W5 | `pf-` id/project validation removed (`if False:`) | `TestSkillFeedbackPf::test_pseudo_id_refused_when_not_indexed` |
| W5-off-guard | knob-OFF guard removed | `…::test_pseudo_id_refused_under_knob_off` — **BELT**: with no file source `_resolve_pf_scope` already returns `(None, [])`, so the tool refuses one layer earlier. Kept visible rather than silently dropped. |
| W6 | body-file re-check **and** `O_NOFOLLOW` reverted to a plain `open()` | `TestReadTimeContainmentSwap::test_body_file_only_symlink_refused_at_read` |
| P4-M1 / P4-M3 / P4-M4 / P2-M4 / P3-M3 | re-proven from the pre-existing PACKS table | see PACKS.md |

**W6 note.** The fix is two layers (realpath validation + `O_NOFOLLOW`); either alone refuses the
attack, so the leg reverts **both** — the pin proves the CLASS is closed, not each layer
individually. That is stated rather than pretending each layer is independently discriminated.

## 5. Pack runs at this HEAD (fenced, per-variable scrub, worktree `.venv`, py 3.14.7)

| Command | Result |
|---|---|
| `pytest tests/unit/test_project_skill_source_index.py tests/unit/test_project_skill_source_phase4_wiring.py tests/unit/test_project_skill_source_phase4_readiness.py tests/tools/test_skill_tools_pf_surface.py tests/tools/test_skill_search_fusion_guard.py` | **114 passed** (127 s) |
| `pytest tests/services/test_skill_search_fusion.py tests/tools/test_skill_tools.py tests/unit/routers/test_skills_search_invisibility.py tests/unit/routers/test_skills_pf_refusals.py` | **76 passed** (56 s) |
| `pytest tests/unit/test_project_skill_source_{model,manifest,resolver,embedding,migrations}.py tests/services/test_skill_injection_step0.py` | **114 passed** (26 s) |

Collect-only at this HEAD: index **32**, wiring **33**, pf_surface **32** (was 29 / 29 / 29 pre-fold;
the +N are the pins in §4). Pre-fold same three packs: **87 passed** — so the fix cycle adds nodes
and introduces no failures on either side (no net-new-red claim is made; both sides are 0-failure
for the packs named).

**Not run here:** the real-PG packs (`tests/postgres/test_project_skill_source_pg.py`,
`…_fusion_pg.py`, `…_phase4_pg.py`). The folded code is DB-engine-agnostic (an in-process dict
re-check, a `to_thread` bridge, an `O_NOFOLLOW` open, a tool-layer guard, a maintenance
registration); no SQL, no schema, no migration changed. Disclosed rather than claimed.

**No migrations** were added or edited by this cycle (`git diff --stat 434db8c6a..HEAD --
daemon/migrations/versions/` is empty).
