# LCA Gate 2026-09-06: Sealed-baseline coverage gap — ~90 undocumented pre-existing failures surfaced by a full-suite sweep

## What happened
The 2026-09-05 gate sealed pre-existing failure fingerprints for the partitions it ran (loose_s_z 58F+2E, loose_a_d 10F+21E, core 4F, api 2F, injection_compaction 1F + quarantine families). This gate's full 12-partition sweep of ~15.6k tests surfaced **~90 additional failing nodes that were pre-existing at base e866c116 but absent from every sealed list** — because their files had not been executed in any recent gate (root h–q/m–r/r–z partitions, unit e–l, U5a subdirs, and non-attestation tests/integration had no recent run ledger).

All were adjudicated pre-existing by three converging layers (git forensics: zero branch-plausible clusters; base worktree A/B: 79/79 suspect nodes FAIL-AT-BASE node-for-node; env adjudication: 23 SSL-env artifacts cleared by unset). They are now registered in QUARANTINE.md (2026-09-06 addendum rows).

## Root causes of the gap
1. **Partition-scoped ledgers**: sealed fingerprints cover only partitions actually run. Un-run partitions carry silent debt.
2. **latest-side drift between gates**: base e866c116 is 6+ commits of latest past the 09-05 gate's base (slash-commands WS-1 f9d377b9 introduced the messages.py:258 await seam; config/job_processor/work_status touched by pre-LCA commits) — new defects entered through the base, not the branch.
3. **Env artifacts masquerading as failures**: 23 httpx-TypeError nodes from stale PyInstaller certifi SSL env paths (documented only as a dead_letter-file-scoped row; actually a class affecting any httpx-client-constructing integration test).

## Lessons
- A "0 new deltas vs sealed fingerprint" verdict is only as good as fingerprint COVERAGE. For merge gates on big branches, run the full partition set, not just previously-run partitions — or explicitly scope the verdict to covered partitions.
- Sealed fingerprints should record the CLASS (signature + mechanism), not just the file list — the httpx class spilled to 4 files while its row named 1.
- Always `unset SSL_CERT_FILE SSL_CERT_DIR` before integration runs (ensure.md prerequisite — the user shell carries PyInstaller temp paths after any PyInstaller-launched tool).
- Base-worktree A/B remains the cheapest decisive attribution: one worktree + uv sync + ~10 scoped packs converted ~90 suspect failures into sealed pre-existing baseline in a single pass.
