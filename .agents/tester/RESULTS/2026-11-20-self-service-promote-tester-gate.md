# Tester Gate — self-service-promote (board wi-aa89581b family)

| | |
|---|---|
| **Branch under gate** | `fix/self-service-promote` @ `ce9b45525b2a434bcf04859c289d1b6cc2103376` (worktree `/home/nea/ensemble-src-wt-selfservice-promote`) |
| **Implementation base** | `2a0e69b37a72789ce42f25099faf5cb5d7451f77` (the design-doc commit; spec = `.agents/shared/planning/self-service-promote/architecture-recommendation.md`) |
| **Gate date** | 2026-11-20 |
| **Dispatcher** | tester (this instance); 38 worker legs dispatched, 38 reported/adjudicated (2 legs terminated-stalled and re-dispatched once per the escape valve) |
| **VERDICT** | 🔴 **RED — NOT merge-ready.** See §V. |

---

## 0. Preflight (verified, not taken on trust)

- `git rev-parse HEAD` == `ce9b45525…` ✓ · branch `fix/self-service-promote` ✓ · `git status --porcelain` EMPTY at start ✓
- `git log --oneline 2a0e69b37..HEAD \| wc -l` == **39** ✓
- Lane file set = **44 files** (+10,520/−204), incl. `daemon/manager.py` (+91), `daemon/services/bind_retry.py`, `daemon/services/rollback_target_chain.py` (new), `daemon/services/upgrade_journal_sweep.py`, `daemon/tools/upgrade_journal.py`, `daemon/tools/upgrade_tools.py`, `launcher.sh` (+615), `scripts/upgrade/*`, `tests/**` (26 test artifacts)
- merge-base(origin/latest, HEAD) = `43e8d8db6`; origin/latest = `3506a326d` (180-file divergence — the branch is NOT a fast-forward of latest)

## Scope Decision

- **Tier chosen mechanically BEFORE any run:** the lane touches `daemon/manager.py`, which is on `ensure.md` §Gate Tiers' core-seam path-prefix list ⇒ **Tier 2 (whole-tree dual-side)**. Escalation was never a judgment call. The first full-tree attempt breached the 90-min advisory budget without reporting (worker terminated at 1h44m); the sanctioned ONE re-scope ran as a daemon-coupled corpus dual-side leg (see §T2).
- **Gate map** = the 62 spec ACs (§5 of the spec) × the PACKS.md registry. The lane registered **31 rows / 24 pack names**; all **6 lane-added test scripts are registered** (no coverage hole from registration); every script the lane registers exists on disk.
- **Baseline correction adopted gate-wide:** the commission's "base" `2a0e69b37` is the *spec commit*. For the lane's registered packs the *true pre-fix base* is the **registered baseline tip recorded in each PACKS.md row** (e.g. `fec3f8982` for serving-green, `6257d50f1` for executor-family). Diffing those packs against `2a0e69b37` mis-attributes intended Wave-3 behaviour changes as net-new; conversely the launcher pack's `2a0e69b37` leg is **green**, so its HEAD reds are NOT base-attributable under any reading.

---

## I. Per-pack results (authoritative standalone runs)

| # | Pack / leg | Result | Counts | Net-new vs declared base | Verdict |
|---|---|---|---|---|---|
| 1 | `promote_handback_halt_gate_legs_test` | rc=0 | 6/6 legs, **86/86** | — | ✅ PASS |
| 2 | `promote_handback_halt_serving_green_test` | rc=1 | **136P/4F** | **0** (diff vs registered tip `fec3f8982` EMPTY; both legs 136P/4F) | ✅ PASS (declared base set confirmed) |
| 3 | `handback_verify_budget_test` | rc=0 | **120/120** (168 s) | — | ✅ PASS |
| 4 | `watchdog_hold_test` | rc=0 | **30/30** (default) / 31/31 (opt-in) | — | ✅ PASS (stub-path only — see RR1) |
| 5 | `executor_cgroup_selfcheck_test` | rc=0 | **12/12** | — | ✅ PASS |
| 6 | `test_bounded_subprocess_waits` | rc=1 | **74P/1F** | **0** (the 1 = declared BASE `R happy-path`; file byte-identical at base) | ✅ PASS (base set confirmed) |
| 7 | `test_supervision_stop_handback` | rc=1 | **232P/33F/1S** | **0** — a real base leg was EXECUTED (git-archive `2a0e69b37`): 211P/33F/1S, failure-name diff EMPTY | ✅ PASS (base set confirmed by execution) |
| 8 | `test_boot_sweep_commit_and_continue` | rc=0 | **78/78** (40 s) | — | ✅ PASS |
| 9 | `test_pipeline_scripts_floor_stamp` | rc=0 | **48/0** (3 s) | — | ✅ PASS |
| 10 | `test_executor_family_payload` | rc=1 | **85P/1F** (stable ×2) | **0** vs `6257d50f1` AND vs `2a0e69b37` (3-leg proof; the 1 = `boot_smoke.sh` family-list gap, base-red at all 4 refs) | ✅ PASS (base set confirmed) |
| 11 | `test_upgrade_overhaul_all` (aggregator) | rc=1 | 18 pass / **3 fail** / 1 skip (365 s; 300 s cap ⇒ 124, re-run 600 s) | sub-pack counts inside the aggregator are mode/contamination-distorted (rollback_target 17F vs standalone 2F) — **standalone runs are authoritative** | ⚠️ aggregator verdict not usable as gate evidence |
| 12 | `test_release_journal` | rc=1 (clean leg) | **382P/24F** (1549 s) | **10 failures beyond the declared 14-name base set** | 🔴 RED (see §III.B) |
| 13 | `test_rollback_target` | rc=1 | **32P/2F** | **1 NET-NEW** (`C7 promote.sh 8b resolves via the chain` — stale line-range anchor) | 🔴 RED (1 net-new, test-side) |
| 14 | `test_launcher` | rc=1 | **399P/36F/1S** (stable ×2) | **36 NET-NEW vs `2a0e69b37`** (base leg EXECUTED: **421P/0F/1S**; one-variable ablation decisive) | 🔴 RED (see §III.A) |
| 15 | `rollback_target_chain_parity_test` ×3 | rc=0 ×3 | **97/0 over 18 cases**, byte-identical ×3 (md5 `d6ef5729…`) | — | ✅ PASS (STABLE) |
| 16 | `tests/unit/tools/test_upgrade_tools.py` | rc=0 | **228/228** (ScriptsPinSelfAdvance+ArmPath = 23 nodes) | — | ✅ PASS |
| 17 | `tests/unit/tools/test_upgrade_serving_green_closer.py` | rc=0 | **38/38** | — | ✅ PASS |
| 18 | `tests/unit/tools/test_upgrade_preflight_daemon_side.py` | rc=0 | **54/54** | — | ✅ PASS |
| 19 | `tests/unit/services/test_executor_reaper_classifier.py` | rc=0 | **17/17** | — | ✅ PASS |
| 20 | `tests/unit/test_watchdog_hold_event_kinds.py` | rc=0 | **8/8** | — | ✅ PASS |
| 21 | `tests/unit/test_watchdog_probe_contract.py` | rc=0 | **20/20** | — | ✅ PASS |
| 22 | `tests/test_bind_retry.py` | rc=0 | **28/28** | — | ✅ PASS |
| 23 | `preflight_mutation_matrix --legs M47` (registered) | rc=0 | 1/1 green-and-red-proven | — | ✅ PASS |
| 23a | `preflight_mutation_matrix` default (all 47 legs) | rc=124→FAIL | 6 ok / 41 `ERROR-plant` | — | ⚠️ harness defect (outer timeout DEFEATED + false cascade) — not a defect signal; default no-arg invocation is not a valid pack shape under the house cap |
| 24 | `concurrency_atomic_unit_test` (ensure.md Core) | rc=1 raw | 94P/**5F**/74S | **0 non-quarantined** — all 5 = QUARANTINE row 153, byte-identical shape | ✅ PASS (quarantine-adjudicated) |
| 25 | ensure.md Core-static ×3 | — | PASS/PASS/PASS | — | ✅ PASS |
| 26 | Tier-2 dual-side (re-scoped: daemon-importing corpus, 32,333 nodes = 82% of tree, mechanically derived) | both legs 32,333/32,333, rc=1 | BASE 31753P/346F/24E · HEAD 31751P/348F/24E | **4 NET-NEW** (3/3 fail at head, 3/3 pass-or-flaky at base) | 🔴 RED (see §III.D) |
| 27 | RR3 non-vacuity spot-checks (replacement, 13 min) | 3 LOAD-BEARING / 1 VACUOUS | SC1 D5-3: RED 10F→GREEN 78/0 · SC2 parity: RED 93/4 (4 rows incl. the rung-ORDER property)→GREEN 97/0 · SC3 D5-2g: RED GUARD-RC=1 (all 15 laundered)→GREEN · SC4 steal: **no pin exists** | ✅ 3/4 non-vacuous; 🔴 SC4 VACUOUS |

---

## II. Riders

### R1 — MINOR-⑥/⑦ disclosure row in PACKS.md → **PASS**
`PACKS.md:1453` carries `selfservice_promote_cycle2_minor_disclosures` verbatim, including the MINOR-⑥ disclosure (JSON-object capture divergence Python-vs-shell; writer guard at `lib.sh:2115`; the amended parenthetical) and the MINOR-⑦ disclosure (the `8w→8v2` renumbering + the stale `EVIDENCE-p21-sandbox-drills.md:23` label re-point + the `_efa_stat_mtime` fix). Verified in an artifact, not chat. Note: the spec itself uses NUMERIC forms (`MINOR-6/-7`, lines 196/406/477); the circled forms exist only in PACKS.md/commit text.

### R2 checklist
- **release_journal 1200s-class run → EXECUTED, FAIL 382P/24F** (§III.B). Registered 900 s budget is STALE (measured 1549–1577 s); the commission's "1200s-class" is also under-measured.
- **launcher 36✗ HEAD ≡ BASE, empty diff → CLAIM IS FALSE** (§III.A). Base `2a0e69b37` is **421P/0F**; HEAD is 399P/36F; ablation isolates `launcher.sh`.
- **parity ×3 → VERIFIED**: 97/0 over 18 cases ×3, byte-identical.
- **three new pytest legs → VERIFIED PASS**: 17/17, 8/8, 20/20 (collected==executed on each).
- **executor-family 85P/1F re-measure → VERIFIED BASE-red**: 3-leg proof (HEAD 85/1, `6257d50f1` 85/1, `2a0e69b37` 69/1 — same failure name on all three; `boot_smoke.sh` gap landed in `d3051ad894`, a different lane). Two commission premises corrected: the pack was NOT added by this lane (`2e19a812c`, 2026-11-11), and the commission-base leg is NOT N/A.

### R3 checklist (final-polish, "settled before merge, now verifying")
| item | verdict | evidence |
|---|---|---|
| prerelease grammar widened | 🔌 **PRESENT but the shipped function ACCEPTS what it claims to refuse** | regex byte-identical at `lib.sh:2119`; behavioural proof against the REAL `journal_mark_captured_previous`: `v1.2.3e10` **ACCEPT** (exponent re-admitted — contradicts `lib.sh:2109` and PACKS.md:1453), `v1.2.3..4` **ACCEPT**, `v1.2` ACCEPT; GUARD-RC=0 reproduced but its bad-list has NO v-prefixed exponent form so it cannot catch this |
| atomic rename-aside steal | 🔌 **PRESENT; single-winner claim FALSE** | shell 12-stealer ×7 races: winners 1,2,2,3,2,2,1; deterministic at 1.5 s delay: WINNERS=2/LOSERS=0; daemon lane: 2–3 workers ALL returned `advanced` in separate races; mechanism = read-once-then-rename of whatever sits at the path |
| `$BASHPID` owner | ✅ **PRESENT + PROVEN** | shipped code records the command-substitution sub-shell pid (BASHPID=3096621 vs enclosing $$=3096616); POK `advance-lock-01-owner-pid-is-parent-shell` now STALE (the lane implements that row's own prescription) |
| owner-parse hardening | 🔌 **PRESENT; one escape** | `'²'/'0'/''/'-1'/'12a'` all REFUSE; `'99999999999999999999'` ⇒ **uncaught `OverflowError`** escapes `scripts_pin_self_advance` (docstring: "never raises") — crashes instead of failing closed |
| ruling parenthetical | ✅ PRESENT | spec `:192` verbatim |

### RR1 — real systemd path → **REAL PATH LIVE** (with two caveats)
systemd 255; `systemd-run` present and non-root transient minting works on the system bus. The `--collect` caveat REPRODUCED: a terminated `--collect` unit is `LoadState=not-found` with status FLUSHED (`Result=success, ExecMainStatus=0` for a payload that exited 1). `systemctl show` returns rc=0 for a dead unit, so the probe's `load == "not-found"` branch takes the journal-truth fallback — correct. **The lane's `CollectMode=inactive` is empirically the RIGHT remedy** (failed unit stays `loaded/failed` with `ExecMainStatus=1` ⇒ the `unit_terminal` branch IS reachable); the spec's `inactive-or-failed` suggestion is WRONG (it equals `--collect`'s implied value and flushes the status). Caveats: (1) the probe (`executor_unit_state_probe`, `upgrade_journal.py:2968`) exists ONLY on this branch — the live daemon has no such reaper; (2) the watchdog pack's privileged leg is PRESENCE-ONLY (`command -v systemd-run`, mints nothing) — a green 31/31 is NOT real-systemd coverage, and **no default-on leg anywhere proves the reaper against real systemd** (all lane tests are hermetic).

### RR2 — pin-the-unpinned → **1 GAP + 1 PARTIAL GAP + 2 PASS**
- wrongful-rollback race leg: pinned ONLY in `test_launcher.sh` 8w/8w-2; **0 of 47 mutation plants can reach it** (none target `launcher.sh`); the pin itself IS load-bearing (scratch mutation of `launcher.sh:3293` flips 3 assertions RED). The spec's named home does not carry the wrongful clause.
- arm-time dirty-pin refusal: PASS (`upgrade_tools.py:5087-5109` + `test_d11_…` green). **Stage-time shell twin `executor_family.sh:523` has NO dirty-tree guard** (asymmetric).
- privileged watchdog marking: PASS (`PACKS.md:2621` carries `[privileged-systemd]` + the skip disclosure; spec line 380 MET).
- R0 typing-seam rows: PASS (TSV 18 cases: numeric 2 / exponent 1 / bool 1 / none 1 / absent 7 / string 7; all three resolvers fold non-string→ABSENT, direct-probed with the `vT` invalid-string control still halting `capture_invalid`).

### RR3 — non-vacuity → **3 LOAD-BEARING / 1 VACUOUS** (replacement leg, 13 min, scratch-copy mutations only)
Corroborated by other legs: M47 green-and-red-proven; census rename→RED→restore→GREEN; the wrongful 8w leg load-bearing under a scratch mutation.
- **SC1 · D5-3 4-distinct R0-wins (AC-D5-3): LOAD-BEARING.** Mutation = R0 rung demoted in `launcher.sh`'s resolver (`cand="$cap"; src="captured"` → `: # MUTATION`). RED: `PASS=68 FAIL=10` — all 6 D5-3 assertions flip (repoint→v-target, `rollback_target_ambiguous` halt instead of resolve, no tierB tag, current not the capture, txn not closed, 24h=0) + 4 collateral D5-7 reds. GREEN (restored): 78/0.
- **SC2 · parity TSV rung-order (AC-D5-12): LOAD-BEARING.** The pack's own M-LAU sed. RED: `PASS=93 FAIL=4` — 4 rows flip, incl. `[r1-valid-conflicts-r0]` where the **rung-ORDER property itself inverts** (R1-wins replaces R0-wins). GREEN: 97/0 ×2.
- **SC3 · D5-2g bad-value list: LOAD-BEARING.** All four refusal arms → always-accept. RED: `GUARD-RC=1`, **all 15** bad values laundered; GREEN: `GUARD-RC=0` ×2. (Controlled reproduction of the accidental foreign plant, which laundered a subset.)
- **SC4 · concurrency steal leg: VACUOUS — NO PIN EXISTS.** `tests/test_executor_family_payload.sh` has zero references to `steal`/`.stale.`/`heartbeat` on the steal path (its only lock assertion is cleanup-after-success). The repo's only concurrent-steal pin (`test_upgrade_tools.py:5216`) monkeypatches `os.rename` on the **daemon-side Python** — not the bash `mv`. Mutating `executor_family.sh`'s `mv` → `cp -r`+`rm -rf` produced **byte-identical** results (85/1 both runs). Consistent with R3's measured 2–3 concurrent winners. Filed as POK `coverage-01-bash-steal-atomicity-unpinned`.

---

## III. The three RED packs (failure-name diffs)

### III.A `tests/test_launcher.sh` — 36 NET-NEW reds (blocks)
- HEAD ×2: **399P/36F/1S**, byte-identical name sets. BASE (`2a0e69b37`) ×2: **421P/0F/1S**. `comm -23` (base-only) = 0; `comm -13` (head-only) = **36**.
- **Ablation (decisive):** base tree with ONLY `launcher.sh` swapped to HEAD ⇒ **385P/36F/1S**, name set byte-identical to HEAD's. The lane's `launcher.sh` alone is sufficient and necessary.
- **Mechanism:** Wave-3/AC-D5-9 replaced launcher.sh's under-request `*` arm (fire the rollback chain immediately) with a Tier-B bounded absent-ceiling (absent/unready probe defers and counts toward `SWEEP_DEFER_CEILING=3`). All 36 cells pin the OLD immediate-rollback semantics (8a/8e/8f/8g/8j/8l/8o/8p/8q/8r/8u2/8v/8b-c5/14d); each fixture is a FIRST observation against a fresh defer counter ⇒ the sweep journals `sweep_deferred … DEFERRED (request-absent) … Tier-B` instead of rolling back. The lane ADDED 8w/8w-2 (both PASS) but never re-anchored the 36 pre-existing cells (`test_launcher.sh` grew only +57/−6, all additive).
- The 36 names are listed in full in the launcher leg's evidence log (`/tmp/ssp-names-head-3078820.txt.sorted`).
- Quarantine check: the ONLY launcher row is 8b-c2 (ONE node, DOCUMENTARY) — it does NOT cover these 36, and 8b-c2 itself PASSED on all four legs.
- Registry conflict to reconcile: `PACKS.md:4979` records `385P/36F/1S … ZERO branch-caused` at an unstated tip — that tip cannot be `2a0e69b37` (green there); the row's baseline must be identified and its attribution re-stated.
- **Owner decision required:** re-anchor the 36 cells to the Tier-B contract, or revisit the launcher.sh change. Either way the pack does not return PASS at HEAD.

### III.B `tests/test_release_journal.sh` — 24F vs declared 14
- Clean authoritative leg (lib.sh sha-pinned before AND after): **382P/24F**, 1549 s. The declared Wave-3 base set = **14** names (cap-boundary ×3, manual-rollback ×2, 12c/12d/12g, 12h-A/B/D ×2). The extra **10**: `12h-C ×2`, `12h-E ×3` (absent from the enumeration; likely the Darwin-only launchctl host-arch class but UNPROVEN — need a base leg), `B2b` (documented contention flake), and the **4 D5/LD-1 cells** in the lane's own Wave-3 section:
  - `D5-1 the txn carries captured_previous=vY` — **test-side**: the `case` pattern collapses under bash quote-stripping to a match that can never hit valid JSON (the journal DOES carry the value; the resolver leg passes).
  - `D5-2c the capture block is BEFORE the stop` — **test-side**: greps `stop_via_stop_script$` but the call site is `if ! stop_via_stop_script; then` (0 end-of-line hits).
  - `LD-1d` / `LD-1e` — journal reads back empty after `_journal_sweep` defers (`ambiguous-absent`) — needs investigation (fixture shape vs behaviour).
- D5-2g: **CONFIRMED GREEN at HEAD** (zero D5-2g failures; GUARD-RC=0; full bad-list + 3-version control).
- Cross-actor contamination incident: a concurrent lane flipped lib.sh's accept arm (`# MUTATION: accept bad value`) 16 s before leg 2's D5 cells ⇒ a FALSE D5-2g red, reverted 9 min later. De-facto proof the D5-2g pin reddens under its mutation.

### III.C `tests/test_rollback_target.sh` — 1 NET-NEW (test-side)
- Standalone: **32P/2F**. `C6` = the declared BASE red (confirmed). `C7 promote.sh 8b resolves via the chain` = **NET-NEW**: the test greps `promote.sh` lines **555–680** but `_rollback_target_resolve` moved to **:1266** when the lane grew promote.sh by 426 lines. The feature IS present; the anchor is stale. Lane-caused (the lane's own growth broke the window) ⇒ must be fixed before merge (quick fix: anchor on the symbol, not a line range).
- The aggregator (`test_upgrade_overhaul_all`) showed 17F for this sub-pack — a mode/contamination artifact; standalone is authoritative.

---

### III.D Tier-2 dual-side regression — 4 NET-NEW pytest nodes (blocks)
- **Re-scope (doctrine-sanctioned, disclosed):** full-tree serial projection ≈299 min ≫ 60 ⇒ the declared set = every pytest file importing the daemon package (mechanical `grep -rl '^(import daemon|from daemon)' tests/` minus the 3 pre-exclusions) = **1,437 files / 32,333 nodes = 82% of the 39,469-node tree**. Both legs ran the **identical 32,333-node set**, identical command shape, BASE first, never concurrent. xdist `-n 12 --dist worksteal` within legs; serial shard for `no_xdist` + shared-scratch-DB files. Accounting closure **100%** on both legs (31,753+346+24+205+5 = 32,333 base; 31,751+348+24+205+5 = 32,333 head). Not covered by this leg (disclosed): the 99 head-only new nodes (covered by the lane's own pack legs above) and the shell/pack surfaces (also covered by the pack legs).
- `sweep_digest_verdict.sh` **exit 1** (non-vacuous): BASE 370 unique ids / HEAD 372; base-only 9; head-only 11. Digests: base `ab031fc0…`, head `2eafe460…` (identical in both forms).
- **3× isolation adjudication (diff nodes only):** 7 of the 11 head-only ids did NOT reproduce in isolation (parallel-shard composition artifacts — the wtgate-01 lesson holds). **4 are NET-NEW** (3/3 fail at head vs F/F/P at base), each attributable to the lane's `daemon/manager.py`:
  1. `test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_decision_lane_manager_drift_is_confined_to_decision_hunks`
  2. `…::test_the_group_chat_lane_manager_drift_is_confined_to_sanctioned_lane_hunks`
  3. `…::test_the_memoir_lane_manager_drift_is_confined_to_sanctioned_lane_hunks`
  4. `tests/unit/test_agent_restart.py::TestT8Extras::test_restart_kind_argv_lock_adoption_and_unit_prefix`
- **Root cause (hard evidence):** the lane's 3 commits add **92 lines to `daemon/manager.py`** inside the P11 guard span, and **0** of them carry a sanctioned-lane marker ⇒ the three drift-confinement pins red **by design**; the 4th pins the restart-kind argv lock / unit prefix, which the lane changed (`UNIT_NAME_PREFIX_RESTART` branch + the new `identity_stamper`). **Fix is lane-side:** add the sanctioned marker to the manager.py hunks (scoped source-gc sanction, `c81dd0dea` precedent) and reconcile the restart-unit-prefix pin.
- No quarantine row covers any of the 4; the failing files' diffs vs base are EMPTY (causality is the lane's manager.py, not the test files).

## IV. AC-D5-13 adjudication — **BUILD (executed)**
`tests/upgrade_refusal_scan.py` is a substitution-shape scanner, not a vocabulary census; no census existed. I built one: **branch `tester/self-service-promote-gate` @ `5ee625706c9d51dc4b5594fdb02dd798afca005d`** (3 files: `tests/test_upgrade_refusal_vocabulary_census.py`, `tests/packs/upgrade_refusal_vocabulary_census_test.sh`, PACKS.md row). 23 tokens, each with **spec-line provenance** (never derived from HEAD). Pack verdict **PASS** (3/3 + scanner rc=0). Non-vacuity: renaming `handback-budget-exhausted`→`…-gone` in lib.sh ⇒ RED; restore ⇒ GREEN. The lane branch is untouched (HEAD == `ce9b45525`, clean). Rationale: AC-D5-13 is a literal AC; "no token renamed" is a token-PRESENCE requirement; the lane adds ~10 new refusal tokens currently unguarded. Correction: `tests/upgrade_refusal_scan.py` **was already registered** (2026-11-19 block) — POK `g5scan-02-scanner-packs-registration-gap` is STALE.

## V. Quarantine discipline
- **Only 2 of the 7 declared "known BASE sets" have a ledger row**: release_journal-14 (row L38, exact match) and launcher-8b-c2 (ONE node, documentary — does not cover 36).
- **No row at all** for bounded_waits-1, supervision-33, rollback_target-C6, serving_green-4, executor-family-1. No lane keyword appears anywhere in either copy; nothing covers a refusal census. `2a0e69b37` appears nowhere; all recorded base shas predate the lane base.
- The base-diff evidence THIS gate produced is the only attribution those five sets have ⇒ **they must be ledgered** (supervision-33 and executor-family-1 now have executed-base-leg proofs; bounded_waits-1/serving-green-4/rollback_target-C6 have registered-row disclosures).
- Drift: main copy is 3 rows behind the worktree copy (resolved sections byte-identical).

## VI. AC coverage table (62 ACs)
| AC | Pin file | Status |
|---|---|---|
| D6-1/-2 | `test_bounded_subprocess_waits.sh` S4b/S4c | ✅ green |
| D6-3/-5/-9/-12 | `handback_verify_budget_test.sh` L16–L21 | ✅ green |
| D6-4 | `test_bounded_subprocess_waits.sh` S5 | ✅ green |
| D6-6 | halt-gate-legs `d6` + serving-green `P-38` | ✅ green (both arms) |
| D6-7/-8/-10 | existing L6b/L6b′/L7/L8 legs | ✅ covered by handback-budget (green) |
| D6-11 | `test_pipeline_scripts_floor_stamp.sh` P3-T08 | ✅ green (constants EQUAL today; the *merge-commit* naming is Wave-6's BUILD-ONLY obligation — not yet executed, correctly disclosed) |
| D1-1 | supervision B7s + independent lsof census (3 bare sites file-wide, DUAL_FIGHT body 0; base had 4/1) | ✅ green + base→HEAD proof |
| D1-2/-3 | `test_bind_retry.py` :552/:531 (real-exec, close_fds=False) | ✅ green, non-vacuous |
| D1-4 | supervision M1/M18b (literal preserved, append-only; base↔HEAD byte-identical) | ✅ green |
| D1-5 | supervision B7c/B7d (BOTH paths) | ✅ green |
| D1-6 | "new legs" — **NO dedicated pin found** | ⚠️ partial (three signatures distinguishable via B7 family, no named leg) |
| D2-1/-2/-3/-10 | `watchdog_hold_test.sh` | ✅ green — **stub-path only** (privileged leg presence-only) |
| D2-4 | handback-budget L6r + serving-green P-27 | ✅ green |
| D2-5 | halt-gate-legs d7 + supervision P5 | ✅ green |
| D2-6/-7 | watchdog event kinds | ✅ green |
| D2-8 | probe-contract (source-token) + promote wiring | ✅ green (runtime probe itself NOT executed by any default leg — RR1) |
| D2-9 | `test_watchdog_probe_contract.py` | ✅ green (predicate executed for real; probe body source-token only) |
| D3-1..-4 | `test_executor_reaper_classifier.py` | ✅ green 17/17 |
| D3-5 | `test_upgrade_preflight_daemon_side.py::TestExecutorUnitIdentity` | ✅ green |
| D3-6 | `executor_cgroup_selfcheck_test.sh` | ✅ green 12/12 |
| D5-1 | release_journal D5-1 cell | 🔴 **test-side red** (quote-stripping) |
| D5-2 | release_journal | ✅ green except D5-2c anchor |
| D5-3/-4/-5/-7/-9 | boot-sweep D5 legs | ✅ green |
| D5-6/-11 | serving-green-closer | ✅ green (D5-11 order documented, not pinned) |
| D5-8 | halt-gate-legs D5-C + serving-green P-15/P-24 | ✅ green |
| D5-10 | halt-gate-legs (halt/green/tierB) + launcher 8w/8w-2 (wrongful) | ✅ pins green — but the wrongful leg is NOT mutation-reachable (RR2 gap) |
| D5-12 | parity pack + TSV | ✅ green 97/0 ×3 |
| D5-13 | **NEW census (this gate)** | ✅ built, PASS, non-vacuous |
| D5-14 | `TestArmtimeRollbackTargetInvariant` | ✅ green (3 refusal reasons + control, pre-nonce) |
| D4-1/-2/-3/-8 | `test_upgrade_tools.py` | ✅ green (23 nodes) |
| D4-4 | — | 🔴 **NOT PINNED** (advanced-tree gate is existence-only; no sha comparison vs `scripts_manifest`) |
| D4-5 | executor-family §7 | ⚠️ wiring only ("both sha-shaped" enforced at runtime, never pack-asserted) |
| D4-6/-9 | floor-stamp D4-6 section | ✅ green |
| D4-7 | executor-family §4/§7c | ✅ green |
| LD1-1 | release_journal LD-1 cells | 🔴 LD-1d/-1e red (needs investigation) |
| CER-1 | overhaul R section (9/9) | ✅ green |
| OBS-1 | `TestArmJournalctlCapture` | ✅ green |
| OBS-2 | upgrade_tools legs | ✅ covered |
| OBS-3 | "new leg (privileged)" | ⚠️ the payload-log narrative pin is NOT present as a default-on leg |

**16 AC rows in the spec name no test path at all** (D6-7/-8/-10, D1-1/-2/-3/-6, D2-7/-8, D3-1..-4/-6, OBS-2/-3) — of these, D1-2/-3/D3-1..-4/OBS-2 gained real homes in the lane's new files; **D1-6, D2-8-runtime, OBS-3 remain without a named default-on pin.**

## VII. ensure.md
- **Core-static: PASS 3/3** (dev.sh graceful-shutdown grep PASS; async-await callers PASS — 10 production + 3 test sites all awaited, lane diff references those names 0 times; dead-code PASS — 0 deleted defs, import smoke rc=0).
- **Core pack (concurrency_atomic_unit_test): PASS** quarantine-adjudicated (5F all = QUARANTINE row 153; `daemon/repositories/` diff vs base EMPTY).
- **Core critical "every pack in the blast-radius change set returns PASS": FAIL** — `test_launcher.sh` (36F), `test_release_journal.sh` (24F, 10 beyond declared), `test_rollback_target.sh` (1 net-new).
- Contradiction notices: none — ensure.md's pack-mapped, dual-layer-timeout doctrine was followed as written. Two registered invocations structurally exceed the 5-min pack cap (release_journal 900 s registered vs 1549 s measured; overhaul 900 s) — re-registration needed, not a doctrine breach by the gate.

## VIII. Defects found that the commission did NOT list
1. 🔴 **In-place mutation planting contaminates the shared worktree** (parity pack, preflight-mutation-matrix, release_journal D5 blocks): 4 separate incidents observed; one produced a FALSE D5-2g red; one blocked Wave-6 (`bump-pipeline-floor.sh` refuses a dirty lib.sh); one was still planted at 10:44Z and had to be restored by a gate worker. **A plain `git add -A`/`git commit -a` at finalize would sweep a plant into the release line.**
2. 🔴 **`preflight_mutation_matrix_test.sh` defeats the outer `timeout`** (trap defers TERM; inner pack in its own process group; matrix bash alive at 429 s under a 300 s cap) and the deferred TERM's `rm -rf "$SB"` manufactures a false 41-leg `ERROR-plant` cascade. Its default no-arg invocation is not a valid pack shape under the house cap.
3. 🟠 `test_executor_family_payload.sh` anti-drift leg counts DIRECTORIES (`ls scripts/upgrade \| sort`) — a stale `__pycache__/` (untracked, cpython-312) is a second undisclosed cause of its red.
4. 🟠 Registry: `executor_family_payload` lane row bypasses the existing wrapper (single-layer timeout, no `RESULT:` contract); `test_pipeline_scripts_floor_stamp` is registered under TWO names at the same path (post-merge duplicate); `upgrade_family/tools` row name contains `/` (invisible to the house extraction regex).
5. 🟠 `tests/unit/tools/test_upgrade_preflight_daemon_side.py`'s +339 lines of new coverage have NO PACKS.md row; the covering corpus row's recorded count is stale ⇒ that coverage is not passing coverage.
6. 🟠 Anchor drift (docs/test-side): spec `:363` cites base coordinates (`:1667,:1874` → now :1936/:2143); `lib.sh:8061`→`:8978`; `upgrade_tools.py:4145`→`:4509`; `bind_retry.py:486-513`→`:524-525`; `bind_retry_mutation_matrix.md:42`→`:43 (M3b)`.
7. 🟢 `concurrency_atomic_unit_test.sh` cannot emit its `RESULT:` line on a red run (`set -euo pipefail` + unguarded `EXIT_CODE=$?`).
8. 🟢 Pack hygiene: `handback_verify_budget_test.sh` leaves ~30 `hbv-*` mktemp dirs per run; `test_supervision_stop_handback.sh` leaks 1 `suphb-curl.*` per run; `test_release_journal.sh` leaks 6 `d5rj*` per D5-reaching run. The serving-green base-revision extraction leaks 19 fake listeners (old pack predates the `$$` registry).
9. 🟢 POK staleness: `advance-lock-01-owner-pid-is-parent-shell` (the lane implements its prescription) and `g5scan-02-scanner-packs-registration-gap` (the scanner IS registered).
10. 🔴 **The bash atomic-steal path has NO test pin** (RR3-SC4): `tests/test_executor_family_payload.sh` never enters the steal path; the repo's only concurrent-steal pin exercises the daemon-side Python with `os.rename` monkeypatched. Mutating the bash `mv` → `cp -r`+`rm -rf` yields byte-identical pack output. This is the pin gap behind the measured 2–3-winner defect (§R3 ITEM 2).

## IX. Gaps
- ~~Tier-2 dual-side regression~~ — **DELIVERED (re-scoped)**: see §III.D. Residual coverage not exercised by the re-scope (disclosed): the 99 head-only new pytest nodes and the shell/pack surfaces — both covered by the lane's pack legs in §I.
- ~~RR3 non-vacuity~~ — **DELIVERED**: 3 LOAD-BEARING / 1 VACUOUS.
- No default-on leg proves the daemon-side reaper against real systemd (RR1) — needs a privileged-systemd pack that actually mints a transient.
- AC-D4-4 has no pin at all; AC-D1-6, AC-D2-8-runtime, AC-OBS-3 lack named default-on pins; the bash atomic-steal has no pin at all (RR3-SC4).
- The 12h-C/12h-E release_journal arms need an executed base leg to attribute (presumed host-arch, unproven).
- Artifacts left in the lane worktree (untracked, disclosed): `.agents/tester/RESULTS/2026-11-20-self-service-promote-tester-gate.md` (this report) and `.agents/tester/RESULTS/2026-11-20-self-service-promote-tester-gate-rescope.md` (the Tier-2 worker's evidence). Tracked files: clean; HEAD `ce9b45525`.
