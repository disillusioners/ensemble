# Plugin Subsystem Slice ③ — Independent Test Pass (sync-runner MVP + 3-class manifest + §8.5 dry-run)

- **Date**: 2026-10-06, completed 21:01 UTC
- **Commission**: Overnight build — slice ③ independent verification (report-only, no modifications, no boots)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-03`, branch `feature/plugin-subsystem-03`, HEAD `b76938e3d6a1654033d123e480577dd91bba2067`, base `ea1242201` (10 commits), 47 files +14,660/−97
- **Workers**: `slice01-suite-run` (1c19528f) · `slice01-git-state` (7b886250) · `slice01-negcheck-greps` (afa7f0db) — all reused
- **Overall verdict**: 🔴 **RED — fix-before-merge** — 7/8 checks PASS; **check 7e FAILED**: all 4 divergence-register `pinning_test` pointers dangle (cite tests that were never implemented). One 🟡 doc-code drift companion finding. Everything else, including both of my prior cross-slice findings (HASHES placement; `..` marker), verified closed.
- Code changes by tester: NONE

---

## Per-check verdicts

| # | Check | Verdict |
|---|---|---|
| S3-1 | Scoped suite | ✅ PASS — **221/0/0/0** exact (② 187 + ③ 34; **0 skill-test ids** → sibling isolation); pytest 9.25s; Py 3.13.15 (rot not triggered) |
| S3-2 | Refusal enum | ✅ PASS — 7/7 codes verbatim (sync_runner.py:131-137) each pinned by dedicated tests; 2 removed codes (`upstream_git_unavailable`→`tag_missing_upstream` fail-closed; `invalid_target_class`→`own_outright_mutation`/ValueError) **zero residue** (0 hits daemon + convention). Note: "CON §5" lives in planning-contract doc + sync_runner docstring, NOT CONVENTION.md |
| S3-3 | Atomicity | ✅ PASS — `TestSyncAtomicity::test_mid_pull_failure_leaves_target_intact` (:627): REAL kill (`_SimulatedCrashError` on 6th `cat_file_blob`, non-RuntimeError deliberately bypassing per-file swallow) + STRICT whole-or-nothing (`post_state == pre_state` full-dict equality + zero leaked `.sync_stage.*`) + tmp_path fixture; targeted run PASS 0.73s; real tree intact after |
| S3-4 | HASHES relocation | ✅ PASS — **my ② finding CLOSED**: `cd copy_freely && sha256sum -c HASHES.sha256` → exit 0, **4881 OK / 0 FAILED**; old sibling GONE; per-class `snapshot_with_drift_alarm/HASHES.sha256` (27 lines) coexists by design |
| S3-5 | Dry-run artifacts | ✅ PASS — both docs present (393 + 264 lines); **3/3 concrete claims verified** against read-only OD git: `od-next-intent-resolution.ts` NEW@v0.24.1 (120 ln, A — exact), `od-next-strategy.ts` MODIFIED (11 ln 9+2, M — exact), delta contains nothing else. 🟢 nit: doc path shorthand `prompts/contracts/` vs real `packages/contracts/src/prompts/` |
| S3-6 | OD boundary | ✅ PASS — porcelain clean; HEAD `53231d40b` dated **2026-09-30** (predates 10-06 window by ~6 days). Boundary HELD |
| S3-7 | Sentinels + manifest | ⚠️ SPLIT — (a) vocab clean ✅ (skills/ absent ✓, corpus 0 hits; benign-note: `tools/vendor/od_vendor_snapshot.py:20` docstring vocab mention — tooling, consistent with ② sanction); (b) W4 root-depth carve-out ✅ — mechanism = **sentinel-suite** (root-depth-only allowlist `len(parts)==3`, self-test guard, rglob ban in class subtrees); ad-hoc nested `docs/MANIFEST.yaml` probe → sentinel FLAGS it, runtime reader correctly blind. **🟡 DOC-CODE DRIFT**: `plugin_registry.py:218-220` docstring claims a scan-time nested-manifest check that does NOT exist; (c) 3-class manifest validates through ① reader `validate_tree=True` → ok=True (own_outright = 3 declared-not-authored paths — manifest-level 3-class model, no dir needed); (d) `schema_version: "1.0.1"` @ MANIFEST.yaml:14 ✅; **(e) 🔴 FAIL — see below** |
| S3-8 | Branch state | ✅ PASS — HEAD exact, branch ✓, clean, **10 commits**, 47 files all in-prefix incl. `tools/vendor/` (adjudicated: expected by slice-② precedent — sanctioned tooling home; my instruction list was narrower, worker flagged correctly); pyproject/uv.lock untouched |

---

## 🔴 The finding: divergence register's pinning teeth are promises, not enforcement (S3-7e FAILED)

- Register: **4 entries present** (MANIFEST.yaml:116-143) — ids 1-4 (daemon/system.ts full-vs-mirror; media-contract.ts; core-slim.ts no-mirror; {directions,discovery,official-system}.ts two-tree drift).
- **All 4 `pinning_test` pointers DANGLE**: each cites `test_opendesign_vendoring.py::TestSnapshotClassByteFidelity::{test_daemon_system_ts_hash_matches_upstream | test_daemon_media_contract_ts_hash_matches_upstream | test_daemon_core_slim_ts_has_no_contracts_mirror | test_two_tree_drift_files_have_distinct_hashes}` — **none exist**: grep over tests/ = 0 hits; per-commit grep across all 10 commits = 0; `git log -S` shows the strings entered the tree at `e7623e333` only inside MANIFEST.yaml itself.
- The snapshot-class `HASHES.sha256` exists (27 entries incl. all 4 register-referenced files) **but no test consumes it**; entry 4's "mirror tests" also don't exist.
- What IS pinned (bounds the gap): register SHAPE (4 entries × 5 fields, `TestRealPluginSync::test_real_full_3_class_manifest_validates`), the LIVE alarm mechanism vs upstream v0.24.1 (`::test_real_snapshot_dry_run_against_v0_24_1` — expects alarmed on exactly the two dry-run-verified files), and synthetic drift flow (`TestSnapshotClassDrift`). **Per-entry file-level drift teeth — the register's stated purpose — are absent at HEAD.**
- Why it matters: a provenance manifest whose `pinning_test` fields point at nonexistent tests misleads every future auditor; the 3-class provenance model's enforcement story has a hole exactly where the register claims its teeth.
- **Fix (cheap)**: implement `TestSnapshotClassByteFidelity` (re-hash `snapshot_with_drift_alarm/HASHES.sha256` mirroring the copy_freely byte-fidelity test + the 3 no-mirror assertions), or rewrite the 4 pointers to tests that exist. Also amend the `plugin_registry.py:218-220` docstring (🟡 companion).

## Minor notes
1. 🟢 CON §5 enum's canonical home: planning contract (`v1-interface-contracts.md:190-196`) + sync_runner docstring; CONVENTION.md has no §5 — citation hygiene for future commissions.
2. 🟢 `upstream_paths` additive field accepted structurally but not surfaced on PluginDeclaration (no consumer yet — fine, note for slice ⑤+).
3. 🟢 Zone-model suggestion: add `tools/vendor/**` explicitly (docstring-only vocab mentions are benign today; make it explicit).

## Boundary compliance
No boots · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · port 8088 untouched · `/home/nea/opt/open-design` READ-ONLY (log/diff/show/status only — boundary verified HELD) · no key changes · zero modifications/commits · scratch /tmp/slice03/.
