# Plugin Subsystem Slice ④ — Independent Test Pass (first plugin-skill: opendesign.list_systems)

- **Date**: 2026-10-06, completed 20:17 UTC
- **Commission**: Overnight build — slice ④ independent verification (report-only, no modifications, no boots)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-04`, branch `feature/plugin-subsystem-04`, HEAD `fc3ee0da8df36d9b1cb3ee91b1ab08ffb2ac2446`, base `ea1242201` (2 commits: `e1d4ac165` foundation → `fc3ee0da8` skill+W5)
- **Workers**: `slice01-suite-run` (1c19528f, test-pack-execution) · `slice02-registry-cirunner` (c6386792) · `slice01-git-state` (7b886250) · `slice01-negcheck-greps` (afa7f0db) — all reused/revived
- **Overall verdict**: 🟢 **GREEN — 7/7 checks PASS, zero flags** (all observations informational)
- Code changes by tester: NONE

---

## S4-1 — Scoped suite: PASS
**232 passed / 0 failed / 0 errors / 0 skipped** — claim exact match (+45 vs slice-②'s 187). pytest 4.60s / wall 9.66s, 0 retries. Venv pre-existed, Python 3.14.7; daemon import inside worktree; HEAD + clean confirmed. Trend: 131 → 187 → 232 monotonic, each delta = dispatcher's stated +N.

## S4-2 — Skill load e2e (offline, registration path): PASS
- API: new `daemon.plugin_subsystem.plugin_skill` (642 ln; `PluginSkill`, `PluginSkillRefusal`, `VendoredReference`, `read_skill_file`, `validate_skill_doc`, `list_skill_files`); registry: `scan_plugins_root` now returns **3-tuple (declarations, skills, refusals)**; `PluginRegistry.{get_skill, try_get_skill, iter_skills, skill_ids}`
- Real-tree ad-hoc: declarations `{opendesign}` + **skills `['opendesign.list_systems']` + refusals {}**; `get_skill("opendesign.list_systems")` → PluginSkill (schema 1.0.0, plugin opendesign, license Apache-2.0, consumers ['worker/designer','test/opendesign.list_systems'])
- Vendored reference: alias `systems` → `copy_freely/design-systems/` resolves to `<worktree>/plugins/opendesign/copy_freely/design-systems` — INSIDE the plugin tree ✓ (relative_to guard)
- **Pin visible at load**: `skill.upstream_tag = {'copy_freely': 'open-design-v0.23.0'}` — same literal the slice-① offline pin guard validates ✓

## S4-3 — Refusal probes: PASS ×3 (tests cited + ad-hoc re-fired)
- **(a) outside-tree reference** → `vendored_reference_outside_tree`. Tests: `TestSkillTemplateValidation::test_vendored_reference_outside_tree_absolute_refused` (:439), `::..._dotdot_refused` (:462). Ad-hoc: `../../etc/passwd` AND `/etc/passwd` both refused (distinct messages, same code); triple guard (absolute / `..` segment / relative_to containment); skill NOT registered.
- **(b) consumption.by ["*"]** → `consumption_by_anonymous`. Tests: `::test_consumption_by_anonymous_refused_bare_star` (:508), `::..._globbed_path` (:532, covers `worker/*`), `::..._empty_refused` (:556). Ad-hoc: `["*"]` refused, "NAMED consumers… fail closed".
- **(c) cross-plugin skill-id collision** → **SYMMETRIC** `skills_entry_duplicate` on BOTH plugins. Test: `TestSkillRegistration::test_registry_skill_collision_refused_symmetrically` (:663). Ad-hoc: delta+echo both declaring `shared.collide` → both refused, each message names the other partner, colliding skill ABSENT from global skills_by_id (no half-state). No winner/loser asymmetry by construction (second pass pops from both dicts).

## S4-4 — Count-query derivability: PASS — **DERIVED, not hard-coded**
- Mechanism: consumer path runs `Path.iterdir()` on the resolved vendored reference at consume time → **154** (breakdown: 153 dirs + 1 README.md file — matches CURATION §2 F-1 "152 brand dirs + _schema/ + README.md").
- Shell cross-check: `ls -1 .../design-systems | wc -l` = 154 ✓.
- The "154" in skill body_markdown is documentation of algorithm + expected answer; test `TestCountQueryDerivableFromVendoredTree::test_count_query_derivable_returns_154` (:940) asserts the COMPUTED value (drift → fail → maintainer updates CURATION + test in lockstep).

## S4-5 — Surgical manifest: PASS
`git diff ea1242201..fc3ee0da8 -- plugins/opendesign/MANIFEST.yaml` = **2 hunks, +34/−0 (pure additions)**: (1) W5 parity row (16 ln — 11 upstream preview JPGs excluded with full rationale, parity_boundary.intentionally_not_vendored); (2) `skills.entries` section (18 ln, one entry). **Slice-② class sections byte-untouched.** Skills-section comment pre-documents the slice-③ merge as byte-exact union (matches build-lane conflict-watch).

## S4-6 — W5 tripwire: PASS
`TestW5CarryForward::test_w5_count_matches_upstream_listing` (test_plugin_skill.py:1009): pins **11** JPGs at `/home/nea/opt/open-design/assets/prompt-templates/image/` (upstream currently holds exactly 11 — independently listed; game-screenshot/illustration/etc.). Skip-guard = inline `pytest.skip()` on `is_dir()` — simulated absent-path against /tmp → skip fires with exact reason; present-path asserts 11. Sibling `test_w5_row_present_in_manifest` (:999) uses `@skipif` correctly. (Stylistic inline-vs-decorator note only.)

## S4-7 — Sentinels + branch state: PASS
- Vocab: zero violations; totals byte-identical to slice ② except allowed test-zone growth (execution_mode tests 24→41, lifted_symbol 6→15). New `plugin_skill.py` + new skill YAML: **0 vocab hits, 0 import machinery**. Vendored corpus still vocabulary-silent.
- Dynamic loading: importlib = 6 hits all negative-assertion comments (same set as slice ②); pkg_resources/entry_points/__import__/pkgutil/importlib.metadata = 0.
- Tier-1: `grep -rn "plugin_subsystem" daemon/ --include="*.py" | grep -v "daemon/plugin_subsystem"` → **0 hits**.
- Branch: HEAD exact, branch exact, porcelain clean, **2 commits**, 12-file diff (+2284/−67) all in expected prefixes (new: plugin_skill.py +642, test_plugin_skill.py +1082, skill YAML +91, registry +308, manifest +34); boundary files (pyproject/uv.lock/.agents) untouched. CURATION.md + skills YAML noted-and-cleared (sanctioned zone / the deliverable itself).

---

## Informational notes (no action required)
1. **Skill discovery is manifest-DECLARATIVE, not autoscan** — a skill YAML in `skills/` that the manifest doesn't declare is not picked up (and a declared-but-missing file is a refusal, not silence; `TestSkillRegistration::test_skill_manifest_declares_it` pins this). Load-bearing design choice; future slices adding skills must append manifest entries.
2. Cosmetic hardening for a later slice: sentinel suite could enumerate `plugins/opendesign/skills/**` as an explicitly watched vocabulary-forbidden zone.
3. `consumption_by_anonymous` covers globbed segments too (`worker/*`) — single code, fail-closed.
4. Refusal-code surface is a large closed enum (skill_missing/unparseable/size_exceeded, schema_version_unsupported, plugin_ref_* , upstream_tag_*, license_invalid, content_*, vendored_reference_*, consumption_*, skill_id_*, skills_entry_duplicate) — well-structured for CI reporting.

## Boundary compliance
No boots · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · port 8088 untouched · `/home/nea/opt/open-design` read-only honored (listing only) · no key changes · zero modifications/commits · scratch in /tmp/slice04/ + /tmp/slice01-negcheck/ · `-p no:cacheprovider`.
