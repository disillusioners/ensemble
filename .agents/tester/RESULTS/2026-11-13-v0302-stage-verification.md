# Stage Verification — v0.30.2 (sonic team roster & support-parity amendment)

**Lane:** STAGE-VERIFICATION (independent verifier = tester; every value re-derived by worker legs, zero relayed numbers trusted)
**Date:** 2026-11-13 (UTC) · **Verdict: ALL 5 LEGS PASS — promote-ready evidence complete; NO promote performed (Emperor's ceremony)**
**Scope ruling (leader):** TARGETED — declarative meta/grant delta; no runtime code, no migrations; v0.30.1's V0–V14 dry-run remains valid for unchanged machinery. No full suite, no dry-run re-executed.
**Workers:** S1 `1866da43` · S2 `f6b88c4e` · S5a `38c269d7` · S5b `8eca52f4` · S3 `ef547f32` · S4 `eca3b89e` · hygiene `81b11e0a`

---

## S1 — Stage identity: PASS (2 claim-text refutations, both benign; 1 system-defect disclosure)

Identity chain re-derived end-to-end:

| Link | Derived value |
|---|---|
| Tag object `v0.30.2` | `01c7a4fc65ecb3f8fae5ce99c8a719374aecbf73` (annotated; pushed — ls-remote confirms) |
| Tag commit | `afc172de77deaea9ca383071bf4edfc9f77881e5` |
| Stage worktree HEAD | `afc172de77de…` == tag commit; `describe --exact-match` = v0.30.2; porcelain CLEAN |
| Sidecar (stage-wt `dist/ensemble-prod.build-provenance.json`) | git_head `afc172de77de…` == tag commit; git_dirty=false; artifact_sha256 = 4-way sha |
| **4-way binary sha256** | `ca1a9cfee7de7c1570a2c364ab65bc5ded875ed1a74d8afb039cf2c0f13d2edd` — release binary == stage-wt dist == manifest `binary_sha256` == sidecar (EXACT match to claim) |
| manifest | version `v0.30.2` / binary_version `0.30.2` / rollback_safe **true** (source=explicit via receipt line) / staged_at `2026-11-13T13:24:39Z` (exact) / known_schema_gen `20261112_124200_add_snapshot_agent_space_key.sql` (unchanged vs v0.30.1 → zero migrations) / contains_contract_phase true |
| Version files | pyproject.toml, daemon/__init__.py, tests/test_api.py:227, uv.lock — all `0.30.2` |
| Baseline | release tree `agents/.prompt-artifacts.sha256` = **201 lines**; generator `--check --tree <release>` rc=0 |
| Branch | `feature/sonic-team-v0302`: 10 commits on base `c1d81593e`; tip `c50a7631a` (see refutation 1) |
| Neighbor tags | v0.30.1 `0a4c8b6f63…` → `00b0af633…`; v0.30.0 `3a2444804…` → `bb4daa596…` — UNTOUCHED on origin |
| sonic-tester parity | `diff -r` v0.30.1↔v0.30.2 `agents/sonic-tester` → EMPTY (byte-identical) |

**Refutations (claim text, not release defects):**
1. Branch tip is `c50a7631a` (docs-only ceremony record), ONE commit ahead of tag commit `afc172de7` — dispatch said "post-fold tip afc172de7". Stage identity unaffected (stage worktree sits exactly at the tag).
2. `scripts/` IS present in the release payload — v0.30.1-parity, manifest-covered (`scripts_manifest` + tree sha). The "NO scripts/" expectation was stale claim text.

**System-defect disclosure:** stage took the FRESHNESS OVERRIDE (tip-identity mismatch vs `c1d81593e`) and the override's journal append FAILED best-effort (`reason=non-tip-tree`) — the SAME known defect class as v0.30.1 (anomaly then filed by leader). Audit trail for the override is stdout-only (`/tmp/stage_v0302.txt` line 2-3). Artifact integrity is independently proven by the 4-way sha + sidecar git_head==tag-commit, matching the v0.30.1 acceptance rationale.

## S2 — Counter-mutation fresh run: PASS

- Self-test (stage worktree, `--self-test`, exit 0, <1s): **55/55 counter-mutation legs RED, 0 vacuous**; good fixture 14/14 GREEN; new `A3-tester-add-image` leg present and RED (adding `image` to sonic-tester's allow is caught). Per-leg attribution clean — every RED names its own assertion family + agent/file (the 1fe263aaf harness-isolation fix holds). Pack lines verbatim: `SELF-TEST: PASS` / `CHECKS: PASS`.
- End-to-end counter-mutation OUTSIDE the harness: scratch worktree `git worktree add --detach /tmp/v0302-e2e-mut v0.30.2`; removed exactly `"image"` from sonic-reviewer allow → static layer RED with single-failure attribution (`actual=[… 'pok' …] expected=[… 'image', 'pok' …]`, other 13 assertions green) → `git restore` → sha256 `5146ad5280da764ab2e0a6ceb9031c46a85842b1f934fe99569864df5debe376` byte-exact before==after → scratch worktree removed. The new support-parity grant IS pinned.
- Disclosure: the checks script carries no internal timer (wrapper `timeout 300` was the sole layer); runs complete <1s so the dual-layer intent is moot. Evidence: `/tmp/v0302-s2-selftest.txt`, `/tmp/v0302-s2-e2e.txt`.

## S3 — Fenced boot-scan (staged release binary): PASS

- Boot: frozen release binary via fd-fence launcher mirroring `fenced-launch.py` (`os.closerange(3,4096)` + execv); ready ~18s; `/livez` → `version 0.30.2`; `/readyz` all 4 components true.
- Fences held: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly; `PORT=8079` explicit (verified free first; no fallback); `env -i` + five `POSTGRES_*` per-variable (never the glob form); `ENSEMBLE_SELF_ENV=dev`; scratch data/log/HOME under `/tmp/v0302-scan` (umask 077).
- **Registration table (runtime, from the release tree):**

| Agent | Version | Team/spawn set | Image grant |
|---|---|---|---|
| sonic-leader | 1.1.1 | sonic-developer, sonic-coder, sonic-reviewer, sonic-tester, **giter, devops** | allow `image` + deny `image_save` ✓ |
| sonic-developer | 1.0.1 | sonic-coder, **giter** | allow `image` + deny `image_save` ✓ |
| sonic-coder | 1.1.1 | (none) | allow `image` + deny `image_save` ✓ |
| sonic-reviewer | 1.1.1 | (none) | allow `image` + deny `image_save` ✓ |
| sonic-tester | 1.1.0 | (none) | NO image grant ✓ (v0.30.1 parity) |

- `/api/agents` exposes basic info only — effective tool-NAME resolution attributed to S5a's real-resolver layer (production auth gate); attribution split, not a gap.
- FD-fence proof: scratch pid appears **0 times** on live API/metrics/demo ports (unfiltered ss).
- Teardown: SIGTERM by exact pid → graceful ~4s; ports freed; children reaped; scratch DB dropped (count 0); claim removed; live pids continuous. Release dir byte-identical pre/post (binary sha + top-level inventory).
- Disclosures: (a) venue adaptation — throwaway scratch DB `ensemble_v0302_scan` per the lane recipe instead of the dispatch-letter shared dev DB (strictly safer: zero shared-state writes; satisfies scratch/dev-only + SELF_ENV=dev); (b) scratch claim `install_dir=null` (frozen-binary residual, identity proven via /proc); (c) webfetch MCP degraded in scratch env (benign, no MCP config).

## S4 — Post-run fence proof: CLEAN

All 8 checks MATCH the S1 before-state (captured 13:40:30Z, before any leg): `current` → `releases/v0.30.0` unchanged; state.json byte-identical (size 63475 / mtime 1794574200 / sha `adbf8fad…`, history 330, no new rows); no promote/upgrade lane processes (only the OS unattended-upgrades service, 5d old); live pid 2579736 etime 08:00:13→08:21:15 and demo 3547188 strictly increasing (no restart); tags v0.30.2/v0.30.1/v0.30.0 unmoved on origin; stage worktree clean @ tag commit, branch worktree clean @ `c50a7631a` (tip did not move); release dir = exactly 4 top-level files with binary sha `ca1a9cfe…`; scratch fully gone (worktree, DB, claim). **ZERO live contact across the entire lane.**

## S5 — Targeted regression: PASS

- **S5a** registered pack `sonic_agent_defs_unit_test.sh` (static + real through production auth gate), run at HEAD `c50a7631a` under test-leader adjudication (docs-only path proof: `afc172de7..c50a7631a` = 1 path `.agents/shared/planning/sonic-team/V1-1-AMENDMENT.md`; 0 under `agents/`, 0 under `test/`): exit 0 in 12s — static 14/14; self-test 55/55 RED + fixture 14/14; `A2-real` (discovery + spawn positives + coder deny-by-default) and `A3-real` (high-risk spawn negatives denied) green; pytest phase 23 passed.
- **S5b** enumerated unit legs (stage worktree @ tag): grant pins 15 passed; pok pins 10 passed; duty prompts 4 failed / 15 passed = EXACTLY the 4 documented pre-existing canary-rooted reds (byte-attributed to `agents/canary/rule.md`; zero net-new); aegis security family 556 passed (baseline 201); `tests/test_api.py -k "health or version"` → 1 node (`test_health_check`) passed. Disclosure: the "health or version" universe in that file is a single node; version-string pins live in the bump files (verified in S1).
- ensure.md scoping: Core gates taken as the changed-pack set (sonic pack + grant-pin files + test_api health) — ALL PASS. `concurrency_atomic_unit_test` / dev.sh static gate OUT of blast radius (declarative content-only delta; zero daemon/ code, zero migrations — known_schema_gen unchanged vs v0.30.1). No Release Gate (not a promote-blocking judgment trigger; promote is the Emperor's ceremony).

## Defects & disclosures register (this lane)

1. **Malformed worktree `.env`** (`/home/nea/ensemble-src-wt-sonic-team-v0302/.env` line 9 = bare POSTGRES_PASSWORD value, no key): sourcing executes it and echoes the value. Two one-time dev-credential echoes into worker transcripts (sha256 prefix `abbb408e73d5` — DISTINCT from the F7 live-key `e33c34f5…`). Anomalies `e5bc2dc4`, `86925d60`. File left byte-unmodified; **repair routed to giter/leader** (add `POSTGRES_PASSWORD=` prefix to line 9). All legs switched to non-sourcing per-line extraction; safe pattern recorded as POK `env-nonsource-wt-sonic-v0302`.
2. **stage.sh freshness-override journal append fails for non-tip trees** (recurrence of the v0.30.1 defect; stdout-only audit trail) — owner: upgrade/staging-pipeline lane.
3. **RAM-guard envelope false-positives** (`ram_breach_observed=true` on trivial read-only calls; guard staged-not-live v0.29.21): anomalies `2e5ca405`, `29c71561`, `ff194f3f` (clustered). No output/exit-code impact.
4. **Non-sonic resolver warning:** `Agent 'maintenancer': deny entry 'git_commit' is neither a known category nor a known tool` — pre-existing, non-blocking, surfaced by S5a; routing to leader for disposition.
5. 4 evidence scratch files at mode 644 (secret-free) — chmod 600 hygiene fix applied post-proof (micro-dispatch).

## Fence verdict

**ZERO live contact · NO promote · NO tag moves · steady team untouched · worktrees clean · release payload byte-stable.** Promote remains the Emperor's ceremony.

Evidence files (mode 600): `/tmp/v0302-s1-evidence.txt`, `/tmp/v0302-s2-{selftest,e2e}.txt`, `/tmp/v0302-s3-evidence.txt`, `/tmp/v0302-s4-evidence.txt`, `/tmp/v0302-s5a.txt`, `/tmp/v0302-s5b-{1,2,3,4,5,5-collect}.txt`.
