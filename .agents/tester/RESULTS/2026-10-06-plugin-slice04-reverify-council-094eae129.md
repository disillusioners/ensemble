# Plugin Subsystem Slice ④ — Council Re-Verify @ 094eae129 (W1 pin cross-check + C1 epoch family match)

- **Date**: 2026-10-06, completed 20:56 UTC
- **Commission**: slice ④ re-verify after council fixes (report-only, no modifications, no boots)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-04`, branch `feature/plugin-subsystem-04`, HEAD `094eae129fa4a8ec14142979718d32473df0c6b4` (parent `fc3ee0da8` — direct)
- **Workers**: `slice04rv-suite-run` (a15519ac, test-pack-execution, fresh) · `slice02-registry-cirunner` (c6386792, reused w/ slice-④ API context) · `slice04rv-docstring-lineage` (dceca718, fresh)
- **Overall verdict**: 🟢 **GREEN — 6/6 checks PASS** (one task-expectation calibration note, non-blocking)
- Code changes by tester: NONE

---

## 4RV-1 — Scoped suite: PASS
**242 passed / 0 failed / 0 errors / 0 skipped** — exact claim match. pytest 4.46s / 9s wall, 0 retries, Py 3.14.7. Composition directly evidenced: collect-only `-k "ParentPinCrossCheck or SchemaVersionFamilyMatch"` → **10/242 collected, 232 deselected**; both new classes present in test_plugin_skill.py (4 pin-cross-check + 6 family-match tests, all names verified).

## 4RV-2 — C1 epoch agreement: PASS
- MANIFEST.yaml `schema_version: "1.0.1"` (:24); skill YAML `manifest_schema_version: "1.0.1"` (:36). Dual roles documented in-file (skill's OWN `schema_version: 1.0.0` :23 is a separate convention gate).
- E2e re-fire: 1 plugin, 1 skill, **0 refusals**; parent-equality 1.0.1 = 1.0.1 True; registry forwards `parent_schema_version` (plugin_registry.py:362) + `parent_tag_pins` (:357-365, 4-line wiring).

## 4RV-3 — W1 pin cross-check probes: PASS ×4
- (a) skill pin `v9.9.9` under parent `v1.0.0` → **REFUSED `plugin_ref_pin_mismatch`** (test :1103 + ad-hoc; message cites CON §6 read-your-writes invariant + names the closed council gap; location = plugin_ref.upstream_tag.copy_freely)
- (b) same via `scan_plugins_root` → same refusal, skill NOT in skills_by_id (no half-state; test :1134)
- (c) in-family: `1.0.0`/`1.0.1`/`1.0.5` under `1.0.1` **all accepted** — family rule = same major.minor, any patch (`^1\.0\.(\d+)$`; `_family_match` audit trail incl. rejections of 2-component `1.0`, `0.9.0`, non-strings)
- (d) out-of-family: `1.1.0` AND `2.0.0` both REFUSED (tests :1311/:1339 + ad-hoc; location = plugin_ref.manifest_schema_version)
- Design note: both sub-checks share code `plugin_ref_pin_mismatch` with message+location discriminators (finer split = council decision, not a defect)

## 4RV-4 — Docstring truth: PASS (with calibration note)
- Documented 23-code closed enum (plugin_skill.py:44-121) vs actual emission sites (39 sites: 38 plugin_skill.py + 1 plugin_registry.py:529, programmatically extracted): **exact bidirectional match** — zero phantoms, zero undocumented.
- **Phantom `schema_version_missing` GONE from plugin_skill.py (0 hits)** — the council finding is closed.
- ⚠️ Calibration: dir-wide grep returns 2 legitimate hits in `manifest_reader.py` (:36 docstring, :617 emission) — that is a REAL `ManifestRefusal` code at the manifest layer (different exception class, own docstring). The commission's "0 hits in daemon/plugin_subsystem/" expectation was over-broad; the phantom-in-skill-docstring specifically is verifiably eliminated. (1 stale `.pyc` binary hit = gitignored artifact.)
- Cosmetic nit: :119 docstring cites `_scan_plugins_root`; actual is public `scan_plugins_root` (plugin_registry.py:419).

## 4RV-5 — Lineage + tree state: PASS
- HEAD `094eae129` ✓; `HEAD~1` = `fc3ee0da8` — DIRECT parent ✓; `e155d5ed4` exists as a commit object but is **NOT an ancestor** (`merge-base --is-ancestor` exit 1; log grep 0) ✓
- Porcelain verbatim: `UU .agents/approver/active.md` + `UU .agents/tester/PACKS.md` — exactly the KNOWN residue (pre-existing merge conflicts preserved across the branch ff; `fc3ee0da8..094eae129 -- .agents/` diff empty; stash-named "pre-ff-sync"). Nothing beyond. Untouched.

## 4RV-6 — Sentinels (unchanged set): PASS ×3
- (a) Vocabulary: all 5 terms — zero violations, zero unclassified; skills/** still vocabulary-silent (held from fc3ee0da8); OTHER = known-benign .agents planning docs only
- (b) Runtime loading: 5 hits all negative-assertion docstrings; zero actual usage (entrypoint_tripwire real imports = stdlib + plugin_declaration only)
- (c) Tier-1: `plugin_subsystem` refs outside the package in daemon/ = **0**

---

## Notes for the leader
1. ✅ Council findings W1 + C1 both closed and independently probe-verified; suite composition claim (232+10) exact.
2. ℹ️ The commission's 4RV-4 grep expectation ("0 hits dir-wide") was over-broad — `schema_version_missing` legitimately lives at the MANIFEST layer (manifest_reader ManifestRefusal). The skill-layer phantom is gone. No action.
3. ℹ️ The 2 UU porcelain lines are the known .agents residue — one of them is `.agents/tester/PACKS.md` (tester's own doc): pre-existing conflict carried across the ff, giter resolves at merge. No tester action before merge.
4. Cosmetic: :119 docstring function-name nit (future commit opportunity, not blocking).

## Boundary compliance
No boots · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · port 8088 untouched · `/home/nea/opt/open-design` untouched · no key changes · zero modifications/commits · scratch /tmp/slice04rv/ · `-p no:cacheprovider` + PYTHONDONTWRITEBYTECODE=1.
