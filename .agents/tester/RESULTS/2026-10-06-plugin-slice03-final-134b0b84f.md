# Plugin Subsystem Slice ③ — FINAL Consolidated Test Pass @ 134b0b84f (RED findings closure)

- **Date**: 2026-10-06, completed 21:38 UTC
- **Commission**: slice ③ FINAL — verify closure of tester RED finding S3-7e (dangling pinning_test pointers) + council critical #2 (fail-closed pull). Developer DONE (no concurrent writes). Report-only.
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-03`, branch `feature/plugin-subsystem-03`, HEAD `134b0b84fc2cbcfc2f21b4b6d53e4cf2ed7330f1`, base `ea1242201` (**12 commits** — 2 fix commits above my prior verified `b76938e3d`: `b82a88b1c` pinning teeth + registry docstring; `134b0b84f` fail-closed pull + W3/W5 dispositions; 6 files +1,564/−81, all in-scope)
- **Workers**: 1c19528f (suite + structural targeted runs) · 7b886250 (enum surface + branch/OD) · afa7f0db (7e re-check + critical #2 probes)
- **Overall verdict**: 🟢 **GREEN — 7/7 checks PASS; both RED-surface findings independently verified CLOSED. Merge gate satisfied from the tester side.**
- Code changes by tester: NONE

---

## Per-check verdicts

| # | Check | Verdict | Evidence |
|---|---|---|---|
| S3F-1 | Suite | ✅ PASS | **244/0/0/0** exact (claim match; pytest 15.25s, 0 retries; Py 3.13.15). **Authoritative resolution of the 221-vs-231 discrepancy: 244** (= my verified 221 @ b76938e3d + 23; 231 was stale). Tree clean (developer-done confirmed) |
| S3F-2 | **7e re-check** | ✅ PASS — **RED→GREEN** | (a) `TestSnapshotClassByteFidelity` REAL (test_snapshot_class_byte_fidelity.py:126-338): **exactly 10 tests = 6 offline + 4 upstream-dependent** (`@requires_real_upstream`, skip guard on `/home/nea/opt/open-design` presence, env-overridable); **live run 10/10 in 0.50s**. Offline tooth re-hashes every snapshot file vs the **27-entry HASHES (now CONSUMED**, count pinned :178-181, self-listing ban); upstream teeth assert the honest two-tree semantics (vendored == daemon blob AND daemon ≠ contracts mirror; entry 3 = absence assertion; entry 4 loops 3 files) with module docstring explaining why naive "must differ from upstream" cannot pass. (b) **4/4 MANIFEST pinning_test pointers resolve** (:122/:128/:134/:143 → the 4 `test_register_entry_*` ids, collected-verified). (c) `TestPinningTestReferentialIntegrity` (test_sync_runner.py:1689+, 3/3 pass): genuinely WALKS every pointer at any depth (`_walk_pinning_test_pointers` :1664+) and resolves against a collect-only test-id set; anti-vacuous `>=4` sanity. **Negative probe: deliberately dangled pointer in /tmp copy CAUGHT at `snapshot_with_drift_alarm.divergence_register.0`** with full diagnostic; positive control 4/0 |
| S3F-3a | Blob-error injection | ✅ PASS | Test `TestFailClosedNonDryPull::test_blob_error_aborts_non_dry_pull` (:1106) + **two ad-hoc re-fire seams**: (1) REAL on-disk corruption (loose object deleted post-tag) → refused / `tag_missing_upstream` w/ cause "blob … missing or unreadable" — caught at the **listing-level parity gate BEFORE any write**; tree byte-intact (full-dict equality); NOT clean_pulled; no `.sync_stage.*`. (2) faithful git-IO shape (`subprocess.CalledProcessError` rc=128 through the production wrapper :1041-1053) → converted + refused, message carries injected error |
| S3F-3b | Subdir-error injection | ✅ PASS | Test :1168 + re-fire (ls-tree RuntimeError → wrapped content-incomplete) → refused / `tag_missing_upstream` "subdir 'data' unreadable"; tree intact; no leak |
| S3F-3c | Structural guard | ✅ PASS | `test_no_silent_skip_in_pull_path` (:1574) reads sync_runner.py as text; asserts forbidden silent-skip patterns (`except (CalledProcessError, RuntimeError): continue`, `except RuntimeError: continue`) = **zero matches**; targeted run PASS |
| S3F-3d | Dry-run parity | ✅ PASS | `_assert_blobs_present` (:999-1032, `cat-file --batch-check`, fail-raise on error/missing) called from `list_subdir_blobs` (:995-996) — **shared by diff (dry-run) AND pull paths**; :952-966 comment documents the rationale. Pinning tests :1226/:1251. Re-fire: dry_run=True vs the REAL corrupted repo → refused/tag_missing_upstream — **NOT a clean dry-run** |
| S3F-4 | W3 swap window | ✅ PASS | `TestRenameAsideAtomicity::test_target_present_during_successful_pull` (:1360; rename-aside→rename-in→delete-old; target NEVER absent) + companion `::test_target_restored_on_stage_replace_failure` (:1402; 2nd `os.replace` fault-injected ENOSPC/EACCES shape → old tree restored). Targeted runs PASS |
| S3F-5 | Enum surface | ✅ PASS | Inline wrap @ sync_runner.py:372-422; `_SYNC_ENUM_7` frozenset (:186-194) gates at :411; **7 overlap codes verbatim** passthrough (pinned `test_overlap_code_passes_through` :1476); reader-only codes namespaced `manifest_reader:<code>` (pinned `test_reader_only_code_namespaced` :1522); closed enum held on both seams (sync-initiated codes = module constants only, no f-string path); zero unprefixed leaks (lone `manifest_unparseable` hit = defensive comment :383) |
| S3F-6 | Sentinels + branch + OD | ✅ PASS | HEAD exact; 12 commits; clean; fix = 2 commits, 6 files all in-scope; pyproject/uv.lock/manager untouched. **OD upstream byte-exact unchanged**: `53231d40b… 2026-09-30` — boundary held across the entire slice lifecycle |

---

## Notes for the leader

1. 🟢 **Merge gate**: GREEN from the tester side (with reviewer APPROVED, ③ merges as the second of the pair; MANIFEST union mechanical per giter's conflict-watch — ④'s skills.entries + ③'s W5/class sections).
2. 🟢 **Hardening suggestion (non-blocking, cheap)**: one additional test injecting at the `subprocess.run` seam (worker's probe shape) would pin the CalledProcessError→`UpstreamContentIncompleteError` wrapper itself — current tests inject the wrapped exception directly, so a future refactor moving the wrapper out of `cat_file_blob` would go undetected. Nice-to-have for a later slice.
3. 🟢 Refactor candidate (informational): the reader-refusal wrap is an inline `except` arm; extract to a named helper if more namespaced surfaces appear.
4. Cross-pass trajectory for slice ③: 221 (b76938e3d, RED on 7e) → **244 (134b0b84f, GREEN)** — +23 tests, all accounted for by the two fix commits (byte-fidelity 10 + referential-integrity 3 + fail-closed class 5 + namespacing 2 + structural/W3 +1 each, etc.).

## Boundary compliance
No boots · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · port 8088 untouched · `/home/nea/opt/open-design` READ-ONLY (cat-file/ls-tree reads only; still clean, HEAD unchanged) · no key changes · zero modifications/commits · scratch /tmp/slice03f/ · worktree byte-identical at end.
