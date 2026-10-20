# INDEPENDENT STAGED-PAYLOAD VERIFICATION (r2 / release gate) — agent-restart → v0.25.6

- **Date:** 2026-10-20 · Tester instance c08c503a · 6 worker dispatches (ce7c6ca0 payload · 068a923d isolation · c6b02de9 bootstrap · d7dbcdf4 functional · 1a586452 sim · closure), 0 re-dispatches, 0 timeouts
- **Release chain (independently verified):** micro-fix `cefd0f2e1` (test-only, proven) → merge `e06974c30` (--no-ff, parents `17befa5ad`+`cefd0f2e1`; tester builder evidence `5742d8605` is an ancestor, rc=0) → seal-carry `361c72c7e` (sha-swap only, 7 rows) → bump `5988220d1` (house 4-file set) → tag `v0.25.6` = annotated `7954e33cf` → commit `5988220d1`, **local-only, not pushed** (ls-remote EMPTY); `latest == origin/latest == 5988220d1`; feature branch pushed @ `cefd0f2e1` as audit trail
- **Staged payload:** `/home/nea/agents-ensemble/releases/v0.25.6` · binary_sha256 `c750aa3c10be219e8915071da1a33bb22809e706770aa22c88545a1a5363c878` (recomputed == manifest == sidecar, 3-way)
- **NO flip, NO restart by our stage:** `current → releases/v0.25.5` unchanged; journal last commit = the USER's v0.25.5 promote (03:51:58Z); unit ActiveEnter 03:49:35Z PREDATES stage (04:04–04:11Z); live serves 0.25.5 (PID 2020780 daemon / 2020522 launcher-wrapper MainPID; both predate stage)

## VERDICT: 🟢 PASS — all 5 dispatch items verified; **v0.25.6 PROMOTE-READY** (promote itself = user's 3-factor nonce ceremony; we do NOT promote)

---

## Item 1 — Manifest/sidecar cross-check on the staged payload: ✅ PASS 9/9

| Check | Verdict | Evidence |
|---|---|---|
| Binary sha | ✅ | `sha256sum ensemble-prod` = `c750aa3c…c878` == manifest == sidecar `artifact_sha256` |
| Provenance | ✅ | Sidecar (kept staging worktree `/home/nea/ensemble-src-wt-stage-v0256/dist/`): `git_head=5988220d178a…374`, `git_dirty=false` — corroborated out-of-band (staging worktree clean @ same HEAD). FE sidecar 3-way match too |
| rollback_safe | ✅ | `rollback_safe=true source=explicit=true` verbatim stage-log line. First attempt refused on heuristic (destructive-DDL pattern); explicit re-run justified by Item-1 migration proof (delta EMPTY — matches the v0.25.4 adjudicated pattern) |
| Migration delta EMPTY (re-derived) | ✅ | git `diff --stat b85804a15..5988220d1` and `630cefebb..5988220d1` on migrations → EMPTY; worktree `diff -r` v0.25.6↔v0.25.5↔v0.25.4 EMPTY; 105==105==105; known_schema_gen `20261019_120822_alter_resource_monitor_booleans.sql` newest in all three |
| fe_index | ✅ | `a017dd2135…af1` recomputed; `cmp` byte-identical vs v0.25.5 AND v0.25.4 (daemon-only release) |
| Tree shas | ✅ | agents `15d00226…` / frontend `8d6afeaa…` / plugins `4715cf64…` / config `666a70d9…` / launcher `a3a4446…` — ALL reproduce exactly under the manifest's algorithm; per-file map 0 differing entries (339+92+4930) |
| agents delta confinement | ✅ | whole-payload diff vs v0.25.5 = **exactly the commission's 8 agents files + the binary**; plugins byte-identical across 6/5/4 |
| ENSEMBLE_SELF_ENV marker | ✅ | `/home/nea/agents-ensemble/.env:112 ENSEMBLE_SELF_ENV=live` (marker at install root by design) |
| No-flip / no-restart | ✅ | see header; livez `{"version":"0.25.5"}` read-only, uptime consistent with 03:49:33Z start |

**Disclosed caveats (both favor the payload):** (a) my dispatch premise "frontend MUST differ" was wrong — frontend is byte-identical (daemon-only release; payload self-consistent with the giter report); (b) payload is **source-less by design** (no `scripts/`, no `daemon/`) — see Item 4.

## Item 2 — Merged-tree functional re-check @ 5988220d1: ✅ PASS

Fenced temp worktree `/tmp/agent-restart-merged-5988220d1` (detached @ `5988220d1`, `uv sync --frozen`, Python 3.14.7 / pytest 9.0.2 — byte-matched to the builder-stage runs; `daemon` 0.25.6 resolved inside).

**Functional pack (6 files, 348 selected): PASS 348 / 0 FAIL / 4 D, 15.34s, exit 0.** Per-file: test_agent_restart 32/0 (+1 micro-fix pin) · test_upgrade_tools 173/0 · arm_notify_journal 40/0 · arm_notify_sweep 33/0 · chart audit 46/0 · chart e2e 24/0/4D. Builder-stage reds (9× memory.md, 1× stale-seal) all absent — the merged tree behaves like the verified branch HEAD.

- **Seal formal test GREEN (verbatim):** `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts PASSED [100%]` — seal-carry `361c72c7e` proven ancestor of HEAD; the 7 re-pinned rows equal actual blob shas at `5988220d1`; full static sweep **35/35 match, 0 drift**.
- **G-5.1 pin verified:** `test_g51_none_vs_zero_dict_distinct` (test_agent_restart.py:810) — three-arm discriminator: None → `"unavailable — boot resolution record absent"` AND `"(applied)" not in body`; populated+db → `limits.worker_pool_size: 5→7 (applied)` AND `"unavailable" not in body`; non-db tier → `"not-applied — resolved tier=env"`.

**Hardened restart-sim: PASS 26/26 runtime assertions, exit 0, 39s.** Static call-sites 42 → runtime 26. SF/G mapping, all runtime-verified: SF-1 stop-stub now derives root from its own script location (`stub-stop: SIGTERM /tmp/ens-restart-sim.*/agents-ensemble 39713` INSIDE the fixture; no root-level `/stub-stop.log`); SF-2 fail-closed FAKE_HOME validation + literal `/tmp/ens-restart-sim.??????` guard (both negative fixtures refuse, rc=99); SF-3 `8088` in the refusal set (line 84) and never bound; SF-5 REAL settings resolver → receipt `overrides_pending: limits.worker_pool_size: 5→7` + audit row `{"key":…,"live_before":5,"db_value":7,"status":"pending"}` (populated render, not "none"); SF-7 honest readyz (FAIL leg names `readyz=pass(one-shot` with real-green ABSENT; PASS leg `livez=pass latency=0.0s budget=8s readyz=pass latency=1.0s … version=identity-ok`; `restart_gate` BEFORE terminal in BOTH directions); **G-4.2 budget survives a real process boundary** (fresh interpreter → `restart_budget_count = 1`; journal persists `restart_window.events`). Live-system safety before/after: journal sha unchanged, live PID/symlink/ports unchanged, all PG conns ensemble-prod-owned, zero sim leftovers.

## Item 3 — Absolute constraint on the MERGED/STAGED tree: ✅ PASS (8/8)

- `require_live_guard` **byte-identical** base `1fecf7c3f` ↔ merged `5988220d1` (sha `b434b88c01c54c2890c56b6a56591f18e45aa2b3863330f44fbb73a1d3d0c51f`, 14 lines, cmp rc=0) — same sha as builder stage.
- Sibling `require_live_guard_restart` sha `6d4b46420832f520…` **identical builder-stage ↔ merged**; absent at base; lib.sh delta base→merged = exactly ONE +26-line hunk; sibling called ONLY at restart.sh:99 (promote/stage/rollback/status all call the untouched original).
- `classify_user_origin` `fc220f3b…` (53 lines) and armed 3-factor gate block `67facda3…` (167 lines) byte-identical base ↔ merged.
- `ENSEMBLE_UPGRADE_LIVE`: ZERO hits in restart.sh / stop-ensemble.sh / upgrade_journal_sweep.py; every repo hit promote-lane-gated or doc/test-asserts-absence; sole `ENSEMBLE_RESTART_LIVE` mint = manager.py:5122-5123 (`if spec.get("provenance_ok")`).
- `_TERMINAL_EVENTS` 9 members, tuple text byte-identical base↔merged (`7fc409ea…`); `RESTART_TERMINAL_EVENTS` = 3; kind-aware reader present. C-3 trap avoided.
- Migration delta (git, both ranges) EMPTY; ls-tree 105. meta.json allow-lists correct (maintenancer + ari). Bump commit = exactly `daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock`. Micro-fix `55c45ab2a..cefd0f2e1` = tests/ + `.agents/` only — **production/agent/script diff = 0, confirmed**.

## Item 4 — Staged payload restart-machinery carriage: ✅ ESTABLISHED (source-less payload disclosed)

The payload carries NO `scripts/` and NO `daemon/` (5,365 files: agents 339 + frontend 92 + plugins 4,930 + binary + config.yaml + launcher.sh + manifest). `strings` on the binary is **non-probative** (control test: `readyz`/`livez`/`POSTGRES_URL`/`"Starting Ensemble"` all 0 hits) AND shell-lane guards are not binary artifacts (category error) — labeled INCONCLUSIVE-by-design, not failure. Carriage is established transitively and at the seams that matter:
1. binary sha 3-way match to a build whose sidecar pins `git_head=5988220d1, git_dirty=false`;
2. that git tree (independently executed, Item 2) contains and passes all restart machinery;
3. the STAGED `agents/` tree carries the flipped policy: `maintenancer/meta.json` allow ∋ `system_restart`, deny ∌ (staged bytes, quoted), ari same; `agents/maintenancer/rule.md` staged sha = `66e91a67357d…` == builder-stage pin;
4. `daemon/constants.py` @ staged git tree sha = `df41a67f…` == pin;
5. the shell lane (restart.sh/lib.sh) is live-sourced from `ENSEMBLE_UPGRADE_SCRIPTS_DIR` — the live checkout already carries `require_live_guard_restart` (restart.sh:99, lib.sh:730-740), byte-identical to the merged tree.

## Item 5 — Known non-blocking follow-ups: ✅ confirmed non-payload

- **PROMPT-DRIFT (3 files):** `agents/ari/tools_note.md`, `agents/maintenancer/rule.md`, `agents/maintenancer/tools_note.md` — last-touch `74e9168a2`/`55c45ab2a`, both INSIDE the commission range and ancestors of the merge (no foreign edit). Stage journaled the warn (`prompt-artifact-drift at stage of v0.25.6`, 2026-10-20T04:10:28Z) and proceeded allow-stale. Payload impact: none — the files ARE the commission's intended content (verified by the agents-tree confinement to exactly these 8 files).
- **No CHANGELOG entry:** confirmed absent from bump commit; CHANGELOG is not part of the staged payload — docs-only gap, non-blocking.

## Findings (report-only)

- **F-r2-1 🟡 REPO_ROOT path-glob pin in the sim** (`tests/test_agent_restart_sim.sh:126-130` hard-fences the READ side to `*/ensemble-src-wt-agent-restart`): after the branch worktree teardown the pack is unrunnable from ANY merged-tree path — first canonical attempt exit 99 with zero real coverage. Resolved verification-only via a faithful external alias (same commit, byte-identical scripts, daemon resolves to the real merged worktree; read-side pin cannot redirect writes — verified). **Recommend: relax to a content-based check (git-common-dir / commit identity).** This is a worktree-lifecycle hazard for every future merged-tree leg.
- **F-r2-2 🟢 provenance sidecar lives outside the payload** (kept staging worktree — recoverable today; the v0.25.2 unrecoverable-sidecar gap class, avoided this time by keeping the worktree).
- **F-r2-3 🟢 prompt-artifact stamps unstamped** for the 3 intended edits (allow-stale; housekeeping).
- **F-r2-4 🟢 no CHANGELOG entry** (cosmetic).
- Harness note: the sim worker's first evidence-capture shim broke `lock_release` (21/26) — root-caused as harness-induced, corrected, canonical run 26/26; disclosed for evidence integrity.
- Main checkout `latest` carries unrelated dirty `.agents/` files from concurrent sessions (approver/tidier/other-tester docs, all pre-dating this round) — untouched; our evidence commit uses targeted adds only.

## Promote-readiness statement — v0.25.6

**GO for the user's promote ceremony.** The staged payload is byte-verified against a clean, functionally-proven git tree; the upgrade nonce-gate isolation is byte-proven base↔merged; migration delta vs the LIVE release (v0.25.5) is EMPTY (rollback_safe=true explicit, substantively justified); the restart feature is green at every level (unit 348/0, seal 35/35, sim 26/26 with all builder-stage findings fixed and both coverage gaps pinned); no flip and no live interaction occurred at stage time. Promote-time gates (livez/readyz/version-verify/300s soak, auto-rollback) run inside the user's ceremony. **No promote performed or recommended by this round — the 3-factor nonce ceremony owns it.**

## Scope decision

Dispatch-defined r2 scope (5 items) fully covered. ensure.md Release-Gate E2E (dev-daemon LLM flows) not run — not requested for r2 and orthogonal to the staged-payload gate; the restart-sim + functional packs are the release-relevant functional evidence. Builder-stage baseline parity (47/47) is not repeated here: the merged tree's only deltas vs the verified branch are the seal-carry (proven sha-swap-only) and the 4-file version bump (proven), so the builder-stage parity carries.

## Worker roster

| Worker | Instance | Result |
|---|---|---|
| v026-payload | ce7c6ca0 | 9/9 PASS (2 disclosed caveats) |
| v026-isolation | 068a923d | 8/8 PASS |
| v026-bootstrap | c6b02de9 | READY (3.14.7 match; sim 26 call-sites; 8088-in-refusal-set observed) |
| v026-functional | d7dbcdf4 | 348/0 PASS, seal GREEN, G-5.1 verified |
| v026-sim26 | 1a586452 | 26/26 PASS, SF/G runtime-verified, live-safety green |
| closure | (post-report) | evidence commit + worktree cleanup |
