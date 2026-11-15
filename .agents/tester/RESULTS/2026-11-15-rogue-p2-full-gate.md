# Rogue-Containment P2 — FULL TESTER GATE PASS (wi-eb4e5059)

- **Date:** 2026-11-15 (UTC), run window 00:26Z→(closing)
- **Commissioner:** leader (Ari) — full gate pass, tester-owned legs only (S5b staged-identity-probe + S9b operator live-unit check EXCLUDED by commission — giter/operator owned)
- **Worktree/branch:** `/home/nea/ensemble-src-wt-rogue-contain` · `feature/rogue-port-containment` · commissioned tip `ccb35112b` → gate-time tip `a7aec8582` (gate-8 commit, mine) · TRUE pre-fix base `bf4bfde6e`
- **Authoritative spec:** `.agents/shared/planning/rogue-containment-p2/component-E2-pins-verification-measurement.md` (+ T9 note)
- **Landing disposition (IMPORTANT):** the branch entered a finalize-owned freeze mid-gate (see INCIDENT). ALL evidence files live in **/tmp rescue packs** with ready-to-apply, ancestry-gated commit commands (unexecuted). The only landed commit is `a7aec8582` (gate 8). Finalize/leader owns applying the rest.

## VERDICT: ALL 8 GATES PASS — lane is tester-clear for finalize

| # | Gate | Verdict | Key evidence |
|---|---|---|---|
| 1a | BF+HY mutation matrix re-run | ✅ PASS | 13/13 mutants RED (red-count sets exact-match matrix), 13/13 sha256-verified restores, clean control 73P = recorded baseline, spawn-seam 27/27 OBSERVED_CHILDREN=0. Pack: `/tmp/rogue-p2-g1a-evidence-rescue-20261115T011055Z/` |
| 1b | AH/LH mutation matrix re-run | ✅ PASS | 22/22 legs RED, 22/22 restores sha-verified, green controls 42P, 0 spawn-seam hits across 44 invocations; F-1/F-2/F-3 confirmed RESOLVED; gate-8 handoff verified (AH-M16 red set grew exactly as dispatched). Pack: `/tmp/rogue-p2-g1b-evidence-rescue-2026-11-15T0057Z/` |
| 2 | Pin families green | ✅ PASS | 141/141: BF 44 (26+18), AH 68 (26+31+11; pins+holder pair = 42 exactly), HY 29 (16+13). Metrics/neighbors leg: see §2b. Pack: `/tmp/rogue-p2-gate2-evidence-rescue-20261115T0053Z/` |
| 3 | Harness legs (T9 contract) | ✅ PASS | Gap-fixes #2–#5 + legs (a)/(b)/(c) implemented; ALL 6 legs PASS ×2 runs (v1 pre-incident, v2 reconstruction); markers = shipped vocab (`tier=resolved-dsn-targets-prod` @ ensemble_config.py:346-349; holder pid/exe/cwd tokens); T9 = same-parent + v2-claim takeover. Pack: `/tmp/rogue-p2-g3-evidence-rescue-20261115T005850Z/` |
| 4 | (D) telemetry verify (no re-run) | ✅ PASS | Venue `9b59bafcd` proven pre-A2 (A2 `cc154c308` = 3 commits later); median U=34.472s ≤ 58s arithmetic verified; attempt-1-78 correctly quarantined as leg-failure (root cause: missing `ENSEMBLE_DB_AMBIENT_ALLOW=1`); fast-refuse-ON U=5.111s/exit-78 + full holder marker; evidence committed `1d13ec5ea`+`0e9152869` |
| 5 | Scratch-port boot E2E (full house) | ✅ PASS | 3/3 shapes green through fd-closerange(3,4096)+execv wrapper; **fd-verify: scratch pid in NO pid token on any live row (9797/9798/7979); positive control = binder on own scratch port**; PG 15433 torn down; live-untouched identical pre/post |
| 6 | Live-probe correction | ✅ PASS | Listener-only (systemctl show + ss -tlnp), 230s dwell: MainPID 4119088 STABLE; binder 305455 STABLE on 9797+9798 (audit-saw value still live); 7979 owned by second stable ensemble-prod 304612 (demo topology). Evidence: `/tmp/rogue-p2-g6-live-probe-evidence.txt` |
| 7 | Quarantine + regression vs base | ✅ PASS | 213 nodes HEAD: 204P/9F — all 9 in test_api.py, **byte-identical at base** (FAILED diff EMPTY; 6/9 standing quarantine rows, 3 base-identical). NEW files 3× = 12/12 stable. **Net-new failing list vs base = EMPTY.** Pack: `/tmp/rogue-p2-g7-evidence-rescue-20261115T005835Z/` |
| 8 | AH8/AH9 pin robustness | ✅ PASS | Both REAL, both fixed cheap (test-only, commit `a7aec8582`): AH8 = 2 vacuous terminal pins re-aimed at positive shapes (AH-M15/AH-M16 counter-mutation re-proven); AH9 = lineage sha made deterministic (synthetic tuple, zero /proc reads; REDDENS under AH-M16 now). 42P before/after, env-perturbed 10/10 green |

## 2b. "+39 metrics pins" adjudication

Census (bf4bfde6e..a7aec8582): ZERO metrics pin files in the diff; the only metrics-touched file is `daemon/services/metrics_listener.py` (+4 docstring lines, source). The arithmetic 13+11+8+7 = **39 exactly matches the NEW port-gate pin adds** (port_ownership_pins +13, holder_identity +11, prod_dsn_fence +8, stgh_sweep_truth +7). The claim as worded ("metrics pins") is a **wording defect in the commission/claim**; the defensible readings are BOTH true and describe different file sets (no tension remains):
- **Arithmetic reading:** +39 = the lane's NEW port-gate pins — all green via gates 2/7.
- **Literal reading:** 8 pre-existing, lane-untouched metrics pin files (9/12/15/21/24/41/56-node spread, incl. `tests/unit/test_metrics_port_derive.py`), **190 nodes ALL GREEN at HEAD `a7aec8582`** (7.72s) — the "untouched-green metrics pins" literally exist as an 8-file family, not a 39-node file.
Regression neighbors green: bind_retry family 92P (incl. `tests/test_bind_retry.py` 26P untouched); `tests/test_main_entry.py` **29P** (27 test functions — expectation was a function count; one parametrize ×3 SQLSTATEs yields 27−1+3=29 nodes; file byte-identical base→HEAD). Pack: `/tmp/rogue-p2-g2b-evidence-rescue-20261115T011840Z/`.

## INCIDENT — worktree deletion mid-gate (root-caused, contained)

- **Root cause (proven):** gate-3 harness leg-(c) teardown — `rm -rf "$HOLD_DIR"` fired with HOLD_DIR = the worktree (T9 same-install holder cwd = worktree). Same defect class as the 2026-10-12 live-install deletions. Anomaly `b740d48c`. **FIXED in-rescue:** OWNED_RM_DIRS allowlist (harness rm's only paths it minted; HOLD_DIR never rm'd; ⛔ banner). Empirically verified — the reconstruction survived its own cleanup.
- **Anomaly cluster:** `6a08060b` (G2, "external actor" — attribution SUPERSEDED), `b740d48c` (root cause), `527394b1` (G7, lost-uncommitted-deltas inventory), `f6493f80` (G1a), `2c52097b`+`ad9184ac` (G1b). One event, five filings, one provable mechanism.
- **Casualties:** uncommitted `.agents/tidier/notes.md` edits (tidier lane's WIP — unrecoverable, owner must re-write); G2's HY leg venv crash (re-run clean on reconstruction); no signal lost anywhere (double-run where affected).
- **Containment:** all workers halted branch writes; branch tip `a7aec8582` + `origin/latest` `bf4bfde6e` verified unmoved at every close; finalize drift-guard intact. Reconstruction = detached HEAD @ a7aec8582 + uncommitted harness/evidence (finalize to sweep or land).

## Safety sweep (b740d48c class, rider)

- Buggy `rm -rf "$HOLD_DIR"` variant: **EXTINCT on disk fleet-wide** (11 worktrees carry the OLD 105-line harness — no HOLD_DIR legs in it).
- **1 unbounded hazard found (main checkout, NOT this lane's tree — write-forbidden):** `tests/test_stop_ownership.sh:78` — `${CLEANUP_DIRS:-}` never initialized in-script, unquoted → env-injectable arbitrary-path `rm -rf`. Recommended quick-fix (2 lines): init `CLEANUP_DIRS=""` at script top + quote the expansion.
- Bounded/convention hazards: `tests/test_supervision_e2e.sh:151` (real-HOME rm -f, self-installed unit names — recommend name gate `ensemble-e2e-*`), `scripts/test_builtin_mcp_servers.sh:66` (repo-rooted scratch — recommend /tmp mint), `test/packs/notify_user_mutation_legs.sh:168,195` (repo-rooted __pycache__ purge). ~40 mktemp-minted-but-ungated rm sites (safe by construction; gate convention gap — exemplar: `hard_reboot_drill_e2e_test.sh:242-245`).

## Deviations & disclosures

1. **No branch commits after `a7aec8582`** (incident freeze) — all subsequent evidence in /tmp rescue packs with ready-to-apply commands. /tmp is reboot-volatile: recommend the leader/finalize relocate packs at landing.
2. Matrix re-runs executed at gate-time tip `a7aec8582` (not historical per-family tips) — includes the gate-8 re-aims; disclosed in both matrix evidence files.
3. G1b AH-M16 honest blast radius: mutant also reddens AH-12 via `TypeError: join(None)` (normalizer None-contract break) — observed ⊇ named, not vacuous.
4. Gate 5 "claim cleaned": shipped B3 design RETAINS the claim post-shutdown (liveness self-heal on next boot) — E2 plan line is stale vs shipped reality; evidence recorded both directions. Spec-drift note, not a failure.
5. Gate 7 QUARANTINE overlap existed where none was expected: test_api.py ×6 nodes under standing rows 13/113/124 + 1 row-181 flake-watch node — pre-existing, excluded from adjudication; signature-drift observation recorded for row maintainers.
6. G7 KB note: partial `ENSEMBLE_AUTH_*` scrub breaks test_api collection (rc=2) — full-family scrub required.
7. G1a real-world fence confirmation: the pkill overlay guard refused a worker's own command merely embedding a live-shaped kill literal in sed text — pre-execution refusal, production behavior matches pins.
8. S5b/S9b NOT run (commission exclusion). C2 (live-unit RestartPreventExitStatus=78) rides S9b with the operator — (D) closure stays CONDITIONAL on it per plan.

## c4d8cc2c closure recommendation

**CLOSE AS PROBE ARTIFACT.** Listener-only re-probe shows a single stable generation (MainPID 4119088 across 230s; binder 305455 — the audit-saw pid — still alive and owning 9797/9798; 7979 = stable demo daemon 304612). The churn was fd-inherited transient pid tokens on the :9797 listener row (probe pipelines + MCP child), read as identity by the artifact-prone method (pgrep -f / ss -tnp client rows — POK pid-numeric-band-not-identity). Corrected method recorded; POK `identity-01-mainpid-vs-binder-pid` minted. No live-side action warranted.

## Rescue pack index (never-delete; reboot-volatile /tmp)

| Pack | Contents |
|---|---|
| `/tmp/rogue-p2-g1a-evidence-rescue-20261115T011055Z/` | BF+HY matrix evidence + 27 leg logs + APPLY-COMMAND.sh |
| `/tmp/rogue-p2-g1b-evidence-rescue-2026-11-15T0057Z/` | AH/LH matrix evidence + 66 raw files + leg_runner |
| `/tmp/rogue-p2-gate2-evidence-rescue-20261115T0053Z/` | pin-families evidence + 4 leg outputs |
| `/tmp/rogue-p2-g3-evidence-rescue-20261115T005850Z/` | FIXED harness + template + gate-3 + gate-5 evidence + 12+3 logs + STATE.md |
| `/tmp/rogue-p2-g7-evidence-rescue-20261115T005835Z/` | regression evidence + 26 raw files + PROVENANCE.md |
| `/tmp/rogue-p2-g6-live-probe-evidence.txt` | live-probe raw readings |

*(aggregate file — the per-scope evidence files named in each worker's pack mirror the intended `.agents/tester/RESULTS/2026-11-15-rogue-p2-<scope>.md` paths)*
