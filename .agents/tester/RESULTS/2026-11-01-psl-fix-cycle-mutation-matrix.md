# project-skill-loader — Phase-4 code-review FIX CYCLE: mutation matrix artifact

**Date:** 2026-11-01 (regenerated at the pre-gate follow-up) · **Lane:** `feature/project-skill-loader`
**Base:** `49dc3027b` (tidier fold landed) + the single additive pre-gate commit
(`pre-gate follow-up: F1 A3a mutation leg + delegation sentinel + 7 lows`; the matrix ran
against exactly that tree — every mutation is restored against the staged content, and the
final-HEAD re-run below is the same tree post-commit)
**Verdict:** 🟢 **13/13 discriminating legs RED→GREEN** + 1 belt leg as expected —
**14 legs total (13 + 1)**; harness exit 0.

---

## 1. Why this file exists (the verification gap it closes)

A prior report carried a **"42/42 mutation re-verify"** claim with **no locatable artifact in any
worktree file**. That figure is **RETRACTED as unverifiable**. In its place this file records an
*enumerated* leg set — every leg named in code (`tests/packs/psl_fix_cycle_mutation_matrix.py`),
planted, run, restored (sha-verified) and re-run.

**Second retraction (pre-gate follow-up, F1).** The tidier fold's report CLAIMED an A3(a)
mutation leg ("mutation-verified RED->GREEN" in the `49dc3027b` commit message) but **no such leg
existed in the harness and no row existed in this artifact** — the claim was unverifiable and is
retracted. Worse, the A3a pin as written only asserted *both-accessors-tolerant*: a re-drift
mutation (the property re-embedding its own `or {}` instead of delegating) kept the pin GREEN.
Both halves are now landed: the pin carries a **delegation sentinel** (monkeypatch
`file_corpus_stats`, assert `last_file_corpus_stats` returns exactly the sentinel) and the harness
carries the **A3a leg**. The claim is now exactly what the table says: **13 discriminating legs +
1 belt leg, all accounted for**.

## 2. Reproduce

```bash
cd /home/nea/ensemble-src-wt-project-skill-loader
.venv/bin/python tests/packs/psl_fix_cycle_mutation_matrix.py \
    [--legs W1-corpus,A3a] [--out /tmp/psl-fix-cycle-matrix.tsv]
# exit 0 ⇔ every leg RED-with-defect AND GREEN-after-restore
```

The harness scrubs `POSTGRES_*` / `DATABASE_URL` / `ENSEMBLE_SELF_ENV` **per variable** (never
`env -u POSTGRES_*`, which is a silent no-op), runs each leg's single pin with
`--timeout=120 -p no:randomly`, restores with `git checkout -- <file>`, and compares the file
sha256 before/after. A leg that stays GREEN with its defect planted is reported
`NON-DISCRIMINATING` and fails the run.

## 3. Captured output (verbatim, 2026-11-01, pre-gate follow-up run)

```
leg	file	node	planted_sha	with_defect	restored_sha	restored
W1-corpus	daemon/services/project_skill_source/service.py	test_corpus_empty_when_index_rebound_to_other_project	b2955786ef00	rc=1	c0e2c46daee7	rc=0 :: RED->GREEN
W1-lookup	daemon/services/project_skill_source/service.py	test_lookup_explicit_miss_when_index_rebound	c7f1a3900cd3	rc=1	c0e2c46daee7	rc=0 :: RED->GREEN
W2	daemon/services/skill_search_service.py	test_file_embeddings_bridged_off_the_loop_thread	7809b6a23441	rc=1	fe25d763b373	rc=0 :: RED->GREEN
W3	daemon/manager.py	test_gcjob_registered	734e5340fd26	rc=1	d49fc7fed3f8	rc=0 :: RED->GREEN
W4	daemon/tools/skill_tools.py	test_dbrows_tag_only_when_file_source_wired	d26fe389fdc7	rc=1	6219846ab5d3	rc=0 :: RED->GREEN
W5	daemon/tools/skill_tools.py	test_pseudo_id_refused_when_not_indexed	fd7eea926b39	rc=1	6219846ab5d3	rc=0 :: RED->GREEN
W5-off-guard	daemon/tools/skill_tools.py	test_pseudo_id_refused_under_knob_off	fe6b8b143295	rc=0	6219846ab5d3	rc=0 :: BELT (expected non-discriminating)
W6	daemon/services/project_skill_source/service.py	test_body_file_only_symlink_refused_at_read	6d9665d3dc09	rc=1	c0e2c46daee7	rc=0 :: RED->GREEN
A3a	daemon/services/skill_search_service.py	test_fusion_stats_accessors_unified_and_none_tolerant	406f8485dab0	rc=1	fe25d763b373	rc=0 :: RED->GREEN
P4-M1	daemon/manager.py	test_site_1_search_ctor_threads_file_source	d07a7f4fac8b	rc=1	d49fc7fed3f8	rc=0 :: RED->GREEN
P4-M3	daemon/services/skill_search_service.py	test_stage3_skipped_no_llm_call	389074e7c276	rc=1	fe25d763b373	rc=0 :: RED->GREEN
P4-M4	daemon/api.py	test_refresh_call_threads_skill_sources	abb0e8caa090	rc=1	2cea40868410	rc=0 :: RED->GREEN
P2-M4	daemon/services/skill_search_service.py	test_db_entries_tagged_when_fusion_active	daffb7fe314c	rc=1	fe25d763b373	rc=0 :: RED->GREEN
P3-M3	daemon/tools/skill_tools.py	test_search_threads_instance_id	3a05ac993a4c	rc=1	6219846ab5d3	rc=0 :: RED->GREEN

SUMMARY: 14/14 legs accounted (13 discriminating legs RED->GREEN, 1 belt leg(s) as expected)
HARNESS_EXIT=0
```

`planted_sha` ≠ `restored_sha` on every leg (proof the mutation actually landed) and
`restored_sha` == the staged blob on every leg (proof the restore is byte-exact).

**Anchor note (W1-corpus under the Low-2 extraction).** The duplicated
`ensure_index` + W1 re-assert blocks in `corpus_for` / `metadata_for` were extracted into
`_ensure_scoped_index()` (log output byte-identical) in the same pre-gate commit. The
W1-corpus anchor `if index is None or index.project_id != project_id:` now has exactly ONE
occurrence (the helper, called by both seams) — the leg plants it there and the
`corpus_for`-scoped pin still discriminates through the call chain.

## 4. Leg map (what each leg plants, and where it is claimed)

| Leg | Planted defect | Discriminating pin |
|---|---|---|
| W1-corpus | scoped-index project re-check dropped (`if index is None:`) | `TestCorpusProjectBinding::test_corpus_empty_when_index_rebound_to_other_project` |
| W1-lookup | `lookup_explicit` post-gate re-check dropped (`if False:`) | `…::test_lookup_explicit_miss_when_index_rebound` |
| W2 | `await asyncio.to_thread(...)` reverted to a direct sync call | `TestStage2DualSourceAdapter::test_file_embeddings_bridged_off_the_loop_thread` |
| W3 | `project_skill_gc` maintenance registration removed | `TestGcMaintenanceWiring::test_gcjob_registered` |
| W4 | `source: db` suffix made unconditional | `TestSkillListFiles::test_dbrows_tag_only_when_file_source_wired` |
| W5 | `pf-` id/project validation removed (`if False:`) | `TestSkillFeedbackPf::test_pseudo_id_refused_when_not_indexed` |
| W5-off-guard | knob-OFF guard removed | `…::test_pseudo_id_refused_under_knob_off` — **BELT**: with no file source `_resolve_pf_scope` already returns `(None, [])`, so the tool refuses one layer earlier. Kept visible rather than silently dropped. |
| W6 | body-file re-check **and** `O_NOFOLLOW` reverted to a plain `open()` | `TestReadTimeContainmentSwap::test_body_file_only_symlink_refused_at_read` |
| **A3a** | property re-embeds its own tolerant `or {}` form — the delegation `return self.file_corpus_stats()` is dropped. Every both-accessors-tolerant assertion still passes; **only the delegation sentinel discriminates** | `test_fusion_stats_accessors_unified_and_none_tolerant` (strengthened in the same commit) |
| P4-M1 / P4-M3 / P4-M4 / P2-M4 / P3-M3 | re-proven from the pre-existing PACKS table | see PACKS.md |

**W6 note.** The fix is two layers (realpath validation + `O_NOFOLLOW`); either alone refuses the
attack, so the leg reverts **both** — the pin proves the CLASS is closed, not each layer
individually. That is stated rather than pretending each layer is independently discriminated.

**A3a note.** The pre-strengthening pin could NOT have carried this leg: planting the re-drift
shape kept it GREEN (both accessors still tolerated `None`). The delegation sentinel
(monkeypatched `file_corpus_stats` returning a sentinel dict; the property must return it
IDENTICALLY — `is`, not equality) is what makes the leg discriminating. A property that computes
its own copy returns an equal-but-distinct dict and fails.

## 5. Pack runs at this tree (fenced, per-variable scrub, worktree `.venv`, py 3.14.7)

| Command | Result |
|---|---|
| `pytest tests/unit/test_project_skill_source_index.py tests/unit/test_project_skill_source_phase4_wiring.py tests/unit/test_project_skill_source_phase4_readiness.py tests/tools/test_skill_tools_pf_surface.py tests/tools/test_skill_search_fusion_guard.py` | **127 passed** (137 s) |
| `pytest tests/services/test_skill_search_fusion.py tests/tools/test_skill_tools.py tests/unit/routers/test_skills_search_invisibility.py tests/unit/routers/test_skills_pf_refusals.py tests/services/test_skill_injection_step0.py` | **98 passed** (76 s) |

Collect-only at this tree: index **35**, wiring **33**, readiness **17** (was 16 — the behavioral
shutdown sentinel replaced the source-inspection pin and added a belt test), pf_surface **37**,
fusion_guard **5**, pf_refusals **12** (was 10 — the `["pf:foo", "pf:"]` REST parametrize mirror).

**Not run here:** the real-PG packs (`tests/postgres/test_project_skill_source_pg.py`,
`…_fusion_pg.py`, `…_phase4_pg.py`). The folded code is DB-engine-agnostic (an in-process dict
re-check, a `to_thread` bridge, a `O_NOFOLLOW` open, a tool-layer guard, a maintenance
registration); no SQL, no schema, no migration changed. Disclosed rather than claimed.

**No migrations** were added or edited by this cycle or the pre-gate follow-up
(`git diff --stat 49dc3027b..HEAD -- daemon/migrations/versions/` is empty).

## 6. Pre-gate follow-up scope (the single additive commit this artifact rides in)

Zero behavior change; the only sanctioned behavior-adjacent deltas are the drain-failure log
level (DEBUG→WARNING) and the shutdown-drain log gaining `exc_info=True`. Items:

1. **F1** — A3a mutation leg + delegation-sentinel-strengthened pin (this file, §1/§3/§4).
2. Low-1 — 2 surviving `"pf:"` literals → `ADDRESS_PREFIX`
   (`skill_search_service.py` `_is_project_file_skill`, `skill_tools.py` `skill_feedback`
   broad prefix test).
3. Low-2 — `_ensure_scoped_index()` extracted from the duplicated ensure_index + W1 re-assert
   blocks (`corpus_for` / `metadata_for`); log output byte-identical (caller/noun name the seam).
4. Low-3 — `["pf:foo", "pf:"]` parametrize mirrored onto the REST refusal pin.
5. Low-4 — shutdown wiring pin is now a behavioral sentinel (drives the real
   `InstanceManager.shutdown`, asserts `aclose` AWAITED — catches short-circuiting, which the
   source-inspection pin could not) + a belt test for the no-service / idempotent shapes.
6. Low-5 — drain-failure log DEBUG→WARNING; shutdown-drain log lazy `%s` + `exc_info=True`.
7. Low-6 — one-line comment naming the realistic inner-except trigger (`cfg.enabled` property
   raise) at the readiness builder.
8. Low-7 — A1 comment enumeration-exact: 13 pre-existing sites lane-wide — 12 in
   `skill_tools.py` (:530…:1510) + `skill_evolution_tools.py:149`.
