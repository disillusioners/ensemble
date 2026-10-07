# FINAL TEST PASS — upgrade-resilience fix branch (Stage 2.3 + deferred regressions)

**Date:** 2026-10-07 17:02–19:05 UTC
**Worktree:** /home/nea/ensemble-src-wt-upgrade-resilience (branch feature/upgrade-executor-resilience)
**Commissioned tip:** dc17a9142 · **Final tip at gate close:** b78c9fbba (= dc17a9142 + 16 authorized test-lane commits + 2 concurrent-actor production commits dc01af6a1/83f0b53a5 — see §5)
**BASE discrimination pin:** 2753ee78d (scratch worktree, created + removed this session; cwd-import-verified)
**Workers:** 30 spawned (4 replaced after dispatch-loss), 7 investigation riders, all evidence commit-pinned
**Live system:** READ-ONLY throughout — /home/nea/agents-ensemble mtime 2026-10-07 13:08:14 and demo 2026-10-04 09:30:40 unchanged at every check; port 8088 never touched; ensemble-main.service/ensemble-demo untouched; zero stray units/processes at final sweep.

---

## RELEASE TEST VERDICT: **GO** — blockers: **none**

Scope note (full suite warranted): final pre-promote gate, all lanes deferred regression to this pass; broad sweep + release-gate class evidence commissioned explicitly.

---

## §1 Commissioned kill-scenario tests (a)–(e) — ALL PASS + ALL DISCRIMINATED

| Scenario | FIX-side evidence | Base discrimination | Verdict |
|---|---|---|---|
| **(a) unit-stop cleanup sweep** (daemon shutdown kills ceremony pgroup, cause ③) | systemd-service pack 32/32 PASS (1.10s, hermetic) + REAL kill R1 | PROVEN-BY-TEST-LOGIC: pack `:433-436` "THE discriminator" (old = silent legacy-setsid fallback; fix = `ExecutorSystemdUnavailable` raise + zero Popen). Structural: BASE `upgrade_journal.py:1823-1924` `--scope` + `start_new_session=False` keeps payload in caller pgroup; BASE grep for Option D symbols = 0 | **PASS** |
| **(b) bash-tool killpg sweep** | same pack (never-setsid pins `:470-479` + `Restart=no`) + REAL kill R1 | PROVEN-BY-TEST-LOGIC (same mechanism — pgroup lineage reaches payload at BASE) | **PASS** |
| **(c) KillMode=mixed stragglers** (④ mechanism) | pack survivorship tests + REAL kill R2 | PROVEN-BY-TEST-LOGIC + STRUCTURAL: BASE broad-except → legacy setsid (escapes pgid, NOT cgroup — BASE comment "known-bad under KillMode=control-group"); FIX 3-tuple detector → UNAVAILABLE classification | **PASS** |
| **(d) post-flip hang → bounded recovery** (§9 class, 7 sites) | bounded_waits **57/57** at CURRENT tip (original 29 under new derived-budget lib.sh + D1-D8 derivation + child≡parent sync-guard); sites 3-7 ran REAL (host systemd; first host ever) | PROVEN-BY-BASE-RUN (site-3 repro: BASE wedged 30s→exit-124 hang-class vs FIX bound 2s) + STRUCTURAL all 7 sites (BASE `_run_bounded` grep = 0) | **PASS** |
| **(e) boot-sweep stale-txn → non-halting** | boot_sweep **27/27** (C1-C7 incl. live GNU cooldown dispatch) | PROVEN-BY-BASE-RUN (C1-fixture: BASE → `sweep-halt`, in_flight stuck, boots on prev; FIX → `boot_sweep_commit_and_continue`, commit at target) + STRUCTURAL (BASE launcher.sh:944-961 halt vs FIX :1007-1031; `intent_flip` absent at BASE) | **PASS** |

### Real-systemd kill evidence (this host, live systemd; disposable `ensemble-test-<uuid>` units; zero-stray proof)
- Production spawn helper replicated: `upgrade_journal.py:1974-2039` `build_service_argv` + `:2148-2296` `spawn_executor`; manager = SYSTEM bus (user bus unavailable — matches production detector fallback).
- **R1 pgroup immunity (a+b):** transient unit MainPID (PPID=1, own cgroup) SURVIVED killpg of the sacrificial systemd-run client's pgroup (+60s verified); journal: unit unaware of client death.
- **R2 KillMode=mixed (c):** straggler child SIGKILLed on unit stop — journal smoking-gun `Killing process 1822514 (sleep) with signal SIGKILL`; cgroup auto-cleaned.
- **R6 loud refusal:** all 3 production failure signatures reproduced (user-bus denial, invalid Restart, duplicate unit) matching `_is_systemd_run_failure` `:2105-2118`; in-process raise pinned by pack `:397-468`.
- Evidence: /tmp/ensemble-kill-evidence/ (SUMMARY.md + R1/R2/R6 + argv logs).

## §2 Full regression suites — totals + triage

**Every residual below is BASE-verified pre-existing, in-file-quarantined, or owner-deferred. ZERO fix-range-caused failures remain.**

| Suite | Result | Triage |
|---|---|---|
| upgrade_journal 117 | 116P/1 env-skip | clean (`ENSEMBLE_UPGRADE_E2E_SERVICE` real-smoke gate) |
| upgrade_tools 173 (incl. **drain 13** = `TestManagerDrainPendingExecution` @ :3529) | 173/173 | clean |
| upgrade_registration 21 | 21/21 | clean |
| executor_systemd_service 32 | 32/32 | clean |
| post_restart_arm_notify_journal 40 | 40/40 | clean |
| reconcile/boot-sweep classes (journal :771/:882/:1095/:1790) | 27/27 | clean |
| bounded_waits (re-run @ c140f6053, post-dc01af6a1) | 57/57 | clean |
| boot_sweep | 27/27 | clean |
| stage_plugins 31 / stage_freshness 118 / atomic_flip 36 / staleness 61 | all PASS | clean |
| release_journal | **316/18** (post `dbd37dbac`) | 86-node cascade CLOSED (fixture gap from `f7db75ed4`); 18 pre-existing ×3-base-attributed (QUARANTINE row added) |
| supervision e2e/journal (K1) | **52/0 + 39/0** | fixed via `b78c9fbba` (was 12/43, 11/26) |
| plugin_subsystem 563 | 559 eff. | 4 pre-existing (hash-count literal 37→38; allow-stale `no_change` policy; MANIFEST duplicate pinning_test ×2 — vendored-data defect → plugin owner) |
| drift parity 11 | 11/11 | clean |
| **unit sweep (12 packs, 15,269 collected)** | **~14,900 effective-pass** | all residual families base-attributed (see QUARANTINE.md 2026-10-07 rows): prompt-content debt ×9, `service_tool` fixture backlog ×21, opendesign-template retired ×15 (slice-⑦ ancestor of BASE), governor vision-fixture ×10, source_configs fixture ×6, time-bombed scheduling ×1, zombie-race ×1, order-flake ×1, KB re-pin ×1, terminal_reason registry ×1 (production-coordinated, pre-BASE commits), knowledge-explore-caller ×2 (standing owner-decision) |

Sweep quick-fix commits (authorized test-lane): ff967acb6, a2a3691f7, 61b8802a0, 99de2756d, 81962baf9, 29fa31bbd, 3c3b602f6, c9c33e064e, 570145f3c, 94cce8c30, 875770b0d, c5c5dc2fe, c140f6053.

## §3 Known issues assigned to tester — ALL CLOSED

- **K1 supervision fixture gap:** FIXED — `b78c9fbba` (mirrors `dbd37dbac` pattern: dict-style MANIFEST + predicate stub). e2e 12/43→**52/0**, journal 11/26→**39/0**; stop_handback 152/0 unaffected (never stages).
- **K2 TestTerminalStateGating ×2:** PRE-EXISTING CONFIRMED at BASE (identical 2/33); root cause = time-bombed fixtures (hardcoded 2026-10-04 `expires_at`; ADR-042 `_grace_pass` abandoning correctly by design). Fixture-only fix `7166979dd` → **33/33**.
- **K3 FE spec :685:** static confirmation — FIX=20000 vs BASE=10000, single-line hunk in `6e17c08f8`; QUARANTINE row-14 wording MATCHES reality; **not e2e-verified** (harness needs npm install + dual webServer, 5-15 min cold — not cheap per commission); NOT un-quarantined (needs 3× clean run).

## §4 Stage dry-run evidence (dev3's gap — CLOSED)

Full real-worktree pipeline EXIT=0 @ `b78c9fbba` → disposable INSTALL_DIR (never live/demo):
- **plugins/opendesign: exactly 4929 files** (source == staged cross-verified); top-level = opendesign only.
- **manifest** (730 KB): `plugins_tree_sha256=25511e4b129cf18366e8bd86d2e1d118fb905d0779da9213bd134016c3425d49` + 4929 per-file entries; `agents_tree_sha256=4b69dc28…` (318); `frontend_tree_sha256=753ff312…` (82-vs-83 — G3 sidecar exclusion PROVEN LIVE); `binary_sha256` == borrowed pristine v0.18.0 artifact.
- **Manifest+integrity block: 12m58s** (vs ~6-7 min estimate — includes integrity re-hashes; ~15k sha256 spawns; per-file spawn cost dominates). TTQA note for promote pipeline: consider xargs-parallel sha256sum.
- Bonus: L1 `non-tip-tree` exit-78 refusal captured verbatim + durably journaled before the `--allow-stale-stage` retry (guard proven firing on feature-branch staging). Ambient `POSTGRES_DB=ensemble_prod` leak correctly sandbox-guarded → `ensemble_sandbox`.
- Disclosed adaptations: temp tag `v9.9.9-dryrun` (deleted post-run), borrowed artifacts from the v0.18.0 STAGING WORKTREE (not live dirs), `ENSEMBLE_ROLLBACK_SAFE=0`.

## §5 Concurrent-actor event (caller must know)

The giter/reviewer (claim heartbeat stale at 16:40Z "READ-ONLY audit done") wrote uncommitted production edits mid-gate, committed ~80 min later as **`dc01af6a1`** (+`83f0b53a5`): STOP_SCRIPT_BUDGET_S derivation (`_resolve_wait_s_mirror`/`_derive_parent_stop_budget`/`_effective_stop_script_budget`) + D1-D8 tests. Response: read-only dirt attribution (every file accounted: mine/sibling/foreign/daemon-audit), evidence stayed commit-pinned, and the affected pack **re-run at the new tip — 57/57 green incl. the actor's own new cases**. The reviewer owns code-level review of those 2 commits; runtime evidence is green. LESSONS/2026-10-07-concurrent-actor-mid-gate-hygiene.md.

## §6 Observations (non-blocking)

1. Cosmetic: `lib.sh:2390` refusal-message builder errors (`branch: No such file…`) and mangles inline-code spans — refusal + journaling still correct.
2. `sweep_top_hm` ran 288s ≈ 96% of the 5-min cap — **split before any re-run** (TTQA).
3. Port 8088 had no listener at final sweep (18:44Z) — flagged read-only; nothing touched.
4. Stage manifest block 2× estimate (see §4) — promote-time budget should assume ~13 min for the hash block on this host class.
5. 4-of-30 workers hit a dispatch-loss never-started state — detected via `last_activity_at=null` probe, replaced 1× each (LESSONS/2026-10-07-dispatch-loss-idle-workers.md).

## §7 Documentation updated

- PACKS.md — full commission section + 26-row pack table
- QUARANTINE.md — 2 new consolidated rows (sweep residuals; release_journal 18-node extension)
- LESSONS/ — 3 entries (f7db75ed4 fixture-contract cascade; dispatch-loss; concurrent-actor hygiene)
- RESULTS/ — this file
- COVERAGE — no structural change beyond fixtures; covered by PACKS.md table.

## §8 Code changes summary (ALL committed; branch = b78c9fbba)

16 authorized test-lane commits: dbd37dbac + b78c9fbba (fixture seeding, K1-class) · 7166979dd (K2 time-bombs) · ff967acb6 / a2a3691f7 / 61b8802a0 / 99de2756d / 81962baf9 / 29fa31bbd / 3c3b602f6 / c9c33e064e / 570145f3c / 94cce8c30 / 875770b0d / c5c5dc2fe / c140f6053 (drift/mock/fixture quick-fixes, each pathspec-limited, each re-verified green). Production code: ZERO changes by tester lane. Foreign production commits dc01af6a1/83f0b53a5: actor-owned, runtime-verified.
