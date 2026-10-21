# Staged Verification — v0.26.3 (ari-tools D-V2 doc-fix release)

**Commission:** ruling-e2e scoped re-verification — the D-V2 fix release (the user's v0.26.2 ceremony ran mid-flight, so the doc fix rode a new version)
**Date:** 2026-10-21 (UTC, ~10:00–12:0x)
**Staged artifact:** `/home/nea/agents-ensemble/releases/v0.26.3` (staged_at 2026-10-21T09:32:26Z)
**Build tree:** `efb8158ee8f9132ce5df50ac6535233a30b65781` (== bump commit; fix `dfe50e6b7` + merge `f5f96bb1c` are ancestors)
**Binary sha256 (3-way):** `017935589ec81aadf2f05c26da7a7b7fc0268cee80f59b063c158ae46f161f39` (disk == manifest == sidecar, recomputed)
**Tag:** `v0.26.3` = annotated object `6eca3b5e8c0234bd15ec4d01c06b1bf611934189` → `efb8158ee` — **LOCAL-ONLY** (origin carries NO v0.26* refs)
**Mode:** read-only + sanctioned scratch only. 5 lanes (one skill each where applicable) + 1 docs commit lane. 0 promotes, 0 production-code changes.

---

## VERDICT: 🟢 **STAGED-VERIFIED (PASS)** — v0.26.3 is artifact-intact, daemon-only, and clears D-V2 at binary level

| # | Mission item | Verdict | Lane |
|---|---|---|---|
| A1+A2 | Artifact integrity + daemon-only proof | ✅ PASS 8/8 | `180ea66b` skill `independent-staged-release-integrity-verification` (+ `release-build-provenance-recovery`) |
| A3 | Doc surface in the SHIPPED binary (co_consts) | ✅ PASS | `d19bcbc2` (infra; new skill `pyinstaller-co-consts-doc-surface-proof`) |
| A5a | Gate packs + new pin + mutation cycle | ✅ PASS (7/7 packs) | `5072af47` skill `test-pack-execution` |
| A5b | Isolated boot smoke (v0.26.3) | ✅ PASS 8/8 | `0e7d0c1e` skill `staged-binary-boot-smoke` |
| A7 | Housekeeping — GT-b base-diff harvest | ✅ HARVESTED-COMPLETE | `9d29c617` (infra; new skill `base-vs-head-red-set-adjudication`) |

**Ceremony path:** promote the staged artifact **AS-IS**. A re-stage is NOT possible nor needed — `origin/latest` has advanced past the build tree (docs-only, see D2), so the L1 tip-identity guard would refuse a re-stage (exit 78 `non-tip-tree`). Same precedent as v0.26.1.

**Live state (read-only, unchanged by this commission):** `:9797` = **v0.26.2** (MainPID 132830, NRestarts 0, epoch 2026-10-21T07:21:45Z), `current → releases/v0.26.2` NOT flipped, `releases/state.json` sha unchanged.

---

## A1+A2 — Artifact integrity + daemon-only proof: PASS 8/8

1. **sha 3-way**: disk == manifest `binary_sha256` == sidecar `artifact_sha256` == expected `017935589e…`.
2. **Sidecars** (both present + parse): `git_head=efb8158ee8f9132ce5df50ac6535233a30b65781`, `git_dirty=false`; FE sidecar `artifact_sha256=a017dd21…` == fe_index == manifest == on-disk == live v0.26.2.
3. **Tag**: `refs/tags/v0.26.3` is an annotated object `6eca3b5e8…` peeling to `efb8158ee`; `git ls-remote origin | grep v0.26` → **EMPTY** (no v0.26* refs on origin at all).
4. **Manifest**: `version=v0.26.3`, `binary_version=0.26.3`, `rollback_safe=True`, `staged_at=2026-10-21T09:32:26Z` (manifest mtime matches to the second), `known_schema_gen=20261020_060000_create_board_tables.sql` (== v0.26.2); config `7bab75e9…` / launcher `a3a44461…` re-hash == manifest.
5. **Daemon-only proof**: (a) manifest tree fields == published live v0.26.2 values (agents `2990c41160cd…`, frontend `8d6afeaa8c7f…`, plugins `4715cf64dad6…`); (b) house-algorithm recompute — **self-validated first against live v0.26.2 (reproduced 3/3)**, then matched v0.26.3 exactly; (c) `diff -rq` on agents/frontend/plugins → **0 differing entries each**; (d) full re-hash — agents 339/339, frontend 92/92, plugins 4930/4930, **0 mismatch / 0 missing / 0 extra**; (e) `diff -rq` of the two RELEASE DIRS → **exactly 2**: `ensemble-prod` + `manifest.json` (config.yaml + launcher.sh byte-identical); release-root file set exactly `{config.yaml, ensemble-prod, launcher.sh, manifest.json}` (no sidecar leak, no anomaly); manifest scalar delta vs v0.26.2 = **exactly 4 fields** (binary_sha256, binary_version, staged_at, version), all 3 per-file maps IDENTICAL.
   *Source corroboration:* `33e1ef513..efb8158ee` = version bump + `daemon/sources/adapters/scheduler.py` **comment-only** (4 lines) + `daemon/tools/self_alarm.py` **docstring-only** (the D-V2 text) + `tests/unit/tools/test_self_alarm_tools.py` (+126 doc-text pin) + 4-file bump + tester docs → **no code line changed**, consistent with a doc-fix release.
6. **In-payload seal**: `sha256sum -c agents/.prompt-artifacts.sha256` from the release root → **161 OK / 0 FAILED**; seal file byte-identical to v0.26.2.
7. **Migration delta EMPTY**: `git diff --name-status 33e1ef513..efb8158ee -- daemon/migrations/versions/` → **0 entries** (rename-detection-off control → 0); 106/106 at git-tree level and 106/106 in the worktree FS (both revs); `cut -c1-15 | uniq -d` → empty (106 distinct `date_sequence` prefixes).
8. **Zero-writes**: main checkout porcelain 26 → 26 byte-identical; `find releases -newermt` → 0; stage/verification worktrees porcelain 0; tree-loss gate (`journalctl -t ensemble-treecheck --since "-10 min"`) → no entries (green).

## A3 — Doc surface in the SHIPPED binary (co_consts, not source grep): PASS

Method (blueprint-decreed): build-venv PyInstaller `CArchiveReader` → `extract('PYZ.pyz')` → `ZlibArchiveReader` → `extract('daemon.tools.self_alarm')` → real code object → recursive `co_consts` walk. `strings` never used (it cannot see compressed module constants).

Needles derived with `ast` from BOTH git revs (so concatenation + embedded newlines match `co_consts` exactly):

| Needle | staged v0.26.3 | live v0.26.2 |
|---|---|---|
| OLD field desc — "…to link — the fresh instance is told to board_get it before acting." | **ABSENT (0)** | **PRESENT (2)** |
| OLD `_full_doc_` — "…a FRESH Ari instance — an instance with no\nmemory of this request…" | **ABSENT (0)** | **PRESENT (1)** |
| short `FRESH Ari instance` | ABSENT (0) | PRESENT (1) |
| short `no\nmemory of this request` | ABSENT (0) | PRESENT (1) |
| NEW field desc — "…when the alarm fires into THIS session you are told to board_get it before acting." | **PRESENT (2)** | ABSENT (0) |
| NEW `_full_doc_` — "the ALARM INTENT to THIS session — the same instance that armed it, with\nits context intact." | **PRESENT (1)** | ABSENT (0) |
| NEGATION "The alarm does NOT spawn a fresh instance." | PRESENT (1) | PRESENT (1) |

- **One-way positive (live exposure)**: 4/4 old retracted forms PRESENT in the LIVE v0.26.2 binary → the D-V2 doc-only exposure statement is proven at binary level, both directions.
- **Tightness**: unique consts **232 == 232**, exactly **2 only-staged / 2 only-live** (the two old constants replaced by the two new ones; the other 230 byte-identical).
- **Controls**: version anchor — the `daemon` pool yields `0.26.3` (staged) / `0.26.2` (live), proving each PYZ belongs to the right binary; **negative control** — `daemon.sources.adapters.scheduler` pools IDENTICAL (0 delta) in the same run, proving the 2+2 delta is a property of the change, not of the extractor.

## A5a — Gate packs + the new pin (mutation-verified): PASS 7/7

| Pack | Result | P/F/E | Duration |
|---|---|---|---|
| Seal repo `generate_prompt_artifact_baseline.py --check` | PASS | rc 0 | 0.23s |
| Seal payload `sha256sum -c` (releases/v0.26.3, read-only) | PASS | 161 OK / 0 FAILED | <1s |
| Tripwire `tests/test_chart_image_delivery_e2e.py` | PASS | 24P / 0F / 4 deselected | 2.62s |
| Tripwire node (PhaseD sealed-artifacts) | PASS | 1P | 1.56s |
| Chart audit `tests/test_chart_image_delivery_audit.py` | PASS | 46P / 0F | 1.80s |
| `tests/unit/test_self_alarm_service.py` | PASS | 65P / 0F | 3.06s |
| `tests/unit/tools/test_self_alarm_tools.py` | PASS | 61P / 0F | 3.99s |
| 3 remaining self-alarm corpus files (registration + literal-allow pins + privilege pins) | PASS | 30P / 0F | 11.14s |
| Integration pin `test_service_tool_flag_off_byte_identical.py` (`-m integration`) | PASS | 9P / 0F | 10.51s |
| `TestDocSurfaceWakeTheCaller` | PASS | 6P / 0F | 2.64s |

- **Count reconciliation**: the brief's "~156" = **156 exactly** = 65 + 61 + 30 across FIVE corpus files (no separate new doc-surface file exists — the pin lives inside the tools file; `git diff -- tests/` = that file + the version bump).
- **Tripwire recount (independent)**: imported `SEALED_SHA_BASELINES` and re-hashed every pin → **35 pins, 35 green, 0 stale, 0 missing**.
- **Mutation spot-verify (sanctioned)**: planted the verbatim pre-fix Field description at ONE location (args_schema `link_board_item`, :483-485) → **RED leg: 2 failed** — `test_the_doc_surface_carries_no_retracted_fresh_instance_framing[the fresh instance is told to board_get it]` + `test_the_link_board_item_description_teaches_this_session` — exactly the fix commit's own prediction. Restore via `git checkout --` → porcelain 0, `git diff --quiet` rc=0, sha256 identical to pre-mutation; **GREEN leg** 6P (and the full tools file 61P).

## A5b — Isolated boot smoke: PASS 8/8

Boot inside its own systemd transient unit (`/user.slice/user-1000.slice/user@1000.service/app.slice/ensemble-tv63smoke-ce5da0.service` vs live `/system.slice/ensemble-main.service` — cgroup-proven; the worker's own shell was inside the live cgroup, hazard avoided). Both kill-switches asserted pre-exec. Port **19120** with **both** `PORT` and `DAEMON_PORT` exported (staged `config.yaml` uses `${PORT:-8088}` — a PORT-only override would have silently bound the forbidden 8088).
Banner `Starting Ensemble v0.26.3` ×2 · livez 200 `version 0.26.3` · readyz 200 (`database`+`checkpoint_saver` true) · 3 board tables scratch-role owned (77 tables) · `Prompt-artifact baseline clean: 161` at boot · zero 42804/DatatypeMismatch/startup-failed/DuplicateMigrationVersionError/InsufficientPrivilege/CRITICAL · 7 ERROR lines ALL known-benign computer-use MCP · teardown SIGTERM-only (3-marker ownership proof), port freed, DB+role catalog counts 0/0, transient unit collected · staged payload **0 modifications** (sha re-verified) · **live fence**: `:9797` v0.26.2 / MainPID 132830 / NRestarts 0 / `current → releases/v0.26.2` NOT flipped / `state.json` sha unchanged / demo :7979 untouched. 10.44.0.2 never contacted (socket- and PG-session-level proof).

## A7 — Housekeeping: GT-b base-diff HARVESTED-COMPLETE (nothing killed)

The orphaned `/tmp/gtb2/run_base.sh` driver had **already completed** (base_driver.log DONE 09:01Z, progress 21/21; resolve phase 09:21Z). 0 live processes at census → **no signal sent**. Deliverable: of the 297 HEAD reds — **292 also red at BASE (pre-existing)**, **5 BASE-green** (dequeue/instance_filter, chart_tools T3 error-policy, persistence ×2, worker_notification) — all **demonstrably flaky on BOTH legs** under the in-tree 3× isolation data (iso3_BASE/iso3_HEAD, 09:25Z) with comparable rates; independently corroborated by the HEAD2 leg (13 ids red at HEAD1 but green at HEAD2, incl. 3 of the 5). **Net-new regressions attributable to 33e1ef513 = 0.** Residual: 4 BASE-NOVERDICT ids outside the 297 set (2 of them perf cells — timeout false-positive generators) — **accepted as disclosed** (non-blocking). Full record: `/tmp/gtb2/housekeeping/GT-B-BASE-DIFF-RESOLUTION.md`.

> **Carried-over correction (supersedes the v0.26.2 doc's "base-diff NOT run" line):** GT-b is **CLOSED — PASS** — full whole-tree base-proven diff delivered, 28,931 nodes at HEAD (28,290P/297F/25E/278S/5xf, accounting closes exactly), BASE re-ran all 330 HEAD-red ids plus the identical shard files at HEAD for same-context comparison, `comm` diff NET-NEW = 0 (`NETNEW.txt` empty), 1 RESOLVED = BASE-side flake, 5 TIMEOUTs + 25 ERRORs symmetric both legs. The red mass is environment-shaped (36× the known SQLite-migration defect `20260714_000001`, 46× ambient `VisionModelNotAllowedError`) — env-symmetric on both legs, so the DIFF is the deliverable. The lane also retracted its own earlier "8 timeouts" figure (its concurrent-tempfile bug) → 5.

## Carried-over behavioral verdicts (v0.26.2 → v0.26.3)

The three ruling verdicts (R1 ari-only allow-lists · R2 wake-the-caller · R3 delivery mechanism + narrowing + census) were verified on v0.26.2 and **carry into v0.26.3 unchanged** — proven, not assumed, by A2: agents/plugins/frontend payload trees are **byte-identical to the live v0.26.2**, the only payload delta is the compiled binary, and the source delta contains **no code line** (comment-only + docstring-only). The v0.26.2 whole-tree base-proven diff (net-new 0) therefore holds for v0.26.3 as well.

## Live D-V2 exposure statement (confirmed)

**The live v0.26.2 binary DOES carry the retracted fresh-instance strings** (4/4 forms present, A3 one-way positive) — the exposure is real, **doc-only** (agent-visible via `tool_help`/`args_schema`, no behavioral effect), and **cleared in staged v0.26.3** (all 4 forms absent, 232-const pool diff proving nothing else moved). It remains live until the v0.26.3 ceremony.

## Disclosures (no product defects; nothing fixed)

- **D1 — commissioned staging-worktree path does not exist**: `/home/nea/ensemble-src-wt-stage-v0263` is absent; the v0.26.3 sidecars live in the **reused, stale-named `wt-stage-v0262`** (HEAD `efb8158ee`, porcelain 0, both sidecars with correct v0.26.3 attestation). Attribution was by `artifact_sha256 == manifest.binary_sha256`, not path → evidence sound, no recovery gap. **Commission record should carry `wt-stage-v0262` as the actual path.**
- **D2 — `origin/latest` drifted past the build tree (docs-only, benign)**: `efb8158ee → 71d7c8fa1` (2 commits: the v0.26.2 ruling-e2e docs merge `772d2ac44` + one more docs commit). Drift touches **3 paths, all `.agents/tester/**`**; shipped tree-objects (daemon/agents/frontend/plugins/scripts) **IDENTICAL at both revs** (`git diff` over them = 0 files). ⇒ promote the staged artifact **AS-IS**; a re-stage would be REFUSED (exit 78 `non-tip-tree`); no override.
- **Census-comment half of the fix is out of reach of the co_consts method by construction** — `daemon/sources/adapters/scheduler.py:179` is a source COMMENT (comments never compile) and is not an agent-visible surface; not proven by A3, covered by A2's source-diff read.
- **W2 run 1 discarded** (harness deviation, self-corrected): under `systemd-run --user` the unit inherits the USER-MANAGER env, not the shell's — `.env` must be sourced **inside** the unit; run 1 otherwise created the webfetch built-in and tried to exec `uvx` (not on the daemon PATH → 4 extra ERROR lines). Run 1 torn down cleanly (DB+role dropped, catalog counts 0), run 2 re-provisioned fresh and is the authoritative result (7 ERROR lines, all known-benign).
- **W3 count reconciliation**: brief's ~156 = exactly 156 across 5 files (65+61+30) — no separate new test file; the pin lives inside `tests/unit/tools/test_self_alarm_tools.py`.
- **4 BASE-NOVERDICT ids** in GT-b (outside the 297 set, 2 of them perf timeout-generators) — accepted as disclosed.

## Worker roster

| Lane | Instance | Flavor | Result |
|---|---|---|---|
| W1 artifact + daemon-only | `180ea66b` | integrity skill (+ provenance-recovery) | PASS 8/8 |
| W2 boot smoke | `0e7d0c1e` | boot-smoke skill | PASS 8/8 |
| W3 gates + mutation | `5072af47` | test-pack-execution | PASS 7/7 packs |
| W4 doc-binary (co_consts) | `d19bcbc2` | infra (new skill `pyinstaller-co-consts-doc-surface-proof`) | PASS |
| W5 housekeeping | `9d29c617` | infra (new skill `base-vs-head-red-set-adjudication`) | HARVESTED-COMPLETE |

Fences: prod `:9797` read-only (v0.26.2, MainPID 132830 untouched) · `current → releases/v0.26.2` untouched · `releases/v0.26.3` read-only (0 modifications) · stage + verification worktrees porcelain 0 · scratch PG per lane, all dropped with catalog-count proofs · no pipeline script executed · no systemctl mutations · no promote.

**Bottom line:** staged v0.26.3 is **STAGED-VERIFIED (PASS)** — artifact-intact and daemon-only, D-V2 cleared at binary level with mutation-proven pin, all gate packs green, isolated boot green. Promote the staged artifact **AS-IS** at the user 3-factor nonce ceremony; the live v0.26.2 doc-only exposure is confirmed and ends with that promote.
