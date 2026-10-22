# DF-4 INDEPENDENT VERIFICATION — provenance-gate closure (commission verdict record)

Date: 2026-10-22 (verification window 08:30–09:45Z)
Worktree: `/home/nea/ensemble-src-wt-df4` @ HEAD `9959f9f4b39d59fd80e1e21e67a8265a11dda775`, branch `fix/df4-provenance-gate`, base `60d7daaf4338b8856c923bc2d7b4ba3a68f75308` (6 commits: c8c8df51c P1 / ea719d372 P2 / e5a273537 P3 / 9da51b2e1 P4 / 9959f9f4b tidier + P4 docs)
Workers: W-A `ea895de0` (item 1) · W-B `b6a9e0cb` (items 2/4/5) · W-C `b42fddc8` (item 6) · W-D `39671e13` (item 3)

## VERDICT: **PASS-WITH-NOTES**

The implementation lane's claims reproduce under independent verification. Zero DF-4 defects found; zero vacuous pins; zero net-new reds. The notes are routed pre-existing findings and disclosures — none blocks DF-4 stage readiness.

---

## Item 1 — ORIGINAL-SYMPTOM CLOSURE: **PROVEN both directions**

Method: byte-identical (md5-checked) defect-shape harness run at throwaway worktrees of BASE and HEAD; real chain, zero mocked provenance seam (real `sweep_wake_records` → real funnel stamp → real `_inherit_provenance_window` → real `_protected_gate`; assertions read the real window dicts); real PG on private scratch DB.

| Leg | Result | Key captured evidence |
|---|---|---|
| BASE defect harness (asserts the BAD shape) | **1 passed** = defect reproduced | delivered source `'api'`; `classify ok=True detail='exact:api'`; DIRECT window minted on woken instance (`message_id` = the funnel's id, proving mint site); INHERITED window propagated to delegated child (`depth 1`); **gate ADMITS** (`terminated: true`, audit `class=inherited, depth=1, root_source='api'`) |
| HEAD, identical harness | **1 failed @ :434** = defect gone at the predicted first divergence | delivered source `'system:sweep_no_user_origin_wake'` ≠ `'api'` |
| HEAD shipped pin (iii) | **2/2 passed, 0 skipped/deselected** | reserved literal + classify fails closed (`reserved-internal`) + sweep re-stamp not fired + no direct window + no inherited mint + `reason=no-user-provenance` + audit refusal row `root_source=None / provenance_depth=None` |
| Genuine path (B), BASE vs HEAD | BASE `test_direct_window_on_acting_instance_refused` passes on the verbatim `direct-provenance-insufficient` refusal; HEAD renamed `test_genuine_direct_window_admits_protected_act` asserts ADMIT + pin (ii) inherited ADMIT — **2/2 each side** | symptom B closed |

## Item 2 — SIX PINS + MUTATIONS: **all pass, 4/4 legs meaningful, no vacuous pin**

| pin | node (actual) | @HEAD | RED under mutation | vacuous? |
|---|---|---|---|---|
| (i) | `test_instance_ops_behaviour.py::TestProtectedGateLadder::test_genuine_direct_window_admits_protected_act` | PASS | M2 → RED (refused again) | NO |
| (ii) | `…::test_genuine_inherited_window_admits_protected_act` | PASS | M3 (`provenance.py:240` inherited `ok=False`) → RED; **GREEN under M2 (expected continuity pin)** | NO |
| (iii) | `tests/postgres/test_df4_provenance_closure.py::test_sweep_forged_wake_is_refused_real_chain` | PASS (real PG) | M1 → RED @ `:417` (`'api' == reserved` on the delivered queue row) | NO |
| (iv) | `test_instance_ops_registration.py::TestNoGateKeysOnPclass::test_no_gate_predicate_keys_on_pclass` (path correction vs plan) | PASS | M2 → RED (`pclass` reads found at gate lines) | NO |
| (v) | `test_df4_sweep_reserved_fallback.py::TestSweepReservedLiteral::test_sweep_reserved_fallback_is_reserved` | PASS | M1 → 4 RED incl. both-sites + `or "api"`-absent | NO |
| (vi) | `…::TestSweepWakeTakesTheIsSystemOriginLane::test_the_reserved_literal_takes_the_system_origin_lane` (name correction vs plan) + `injected_message` stamp extension | PASS | M4 → canonical RED @ `:257` (3 sibling sub-tests stay green by design — they pin AST shape/stamp shape; disclosed) | NO |

Mutation legs: M1 both sweep sites → `"api"`; M2 pre-P2 `pclass != "inherited"` branch transplanted from base; M3 inherited-branch `ok=True→False`; M4 `is_system_origin` selector forced to `"api"` prefix (M4 caveat: P1 never touched `instance_messaging.py` — diff base..HEAD empty; the leg is the behaviorally-equivalent forward reversal). **Every restore proven**: sha256 pre-mutation == post-restore, `git diff` empty, tree returned to start state. Consolidated green after restores: 111/111 collected+passed on the df4-relevant unit files.

## Item 3 — REVIEWER TICKET (ari-fallback arm): **CLOSED with a new mutation-proven pin**

New file `tests/postgres/test_df4_ari_fallback_closure.py` (**UNTRACKED** — must be committed before the worktree is used as a merge source; see Routed): 2 node ids, **2/2 passed** on the mandatory `-m postgres` form (private scratch DB `df4v_ari_scratch`, created+dropped+proven gone). Proves through real repo code: ari-target selection (`sweep :1048-1101`), `row.source == SWEEP_NO_USER_ORIGIN_WAKE_SOURCE ≠ "api"`, classify fails closed, no re-stamp, `injected_message` stamp shape, **no window on the ari target, nothing inherited by its child**, plus a genuine-`"api"` negative control (differential = the source value, not plumbing). Mutation leg (ONLY the `:1217` fallback ternary → `"api"`): new pin **RED @ :393**; **pin (iii) GREEN** (arm-exclusivity proven); pin (v) RED cross-check; restore byte-exact → re-GREEN.

**⚠️ Finding (routed, pre-existing, NOT a DF-4 regression): the ari-fallback arm's stated trigger is DEAD on the current enqueue contract.** A missing arming row does not raise `KeyError`: `_prepare_enqueued_message` (`instance_messaging.py:2458-2461`) warns and writes MessageQueue+Task rows unconditionally → the wake is silently absorbed by the direct arm with the queue row landing on a **ghost instance id** (no `arm_notify_no_instance`, no ari delivery; probe-proven on scratch PG). Sweep docstring `:1006-1017` is stale (that KeyError belongs to the graph cold-load path enqueue never calls; the existing unit pack enters the arm only via a stub). The new pin exercises the arm under its documented gone-row contract (real `KeyError` shape replayed for the gone id only — disclosed in the file docstring); everything else real.

## Item 4 — REAL POSTGRESQL: **CONFIRMED**

Mandatory form `--override-ini="addopts=" -m postgres tests/postgres/test_df4_provenance_closure.py`: **2 collected / 2 passed / 0 skipped / 0 deselected** (24.5–26.0 s, serial, per-variable scrub + explicit SET, collection-time + in-test resolved-not-prod fences fired). Two-tier ledger on real PG, line-cited: refusal row `root_source is None` / `provenance_depth is None` (`:481-482`); genuine-chain committed row `provenance_class="inherited"`, `depth=1`, `root_source="api"` (`:560-562`). Note: 24.5–26 s wall vs the 30 s/test ini timeout — near ceiling (see Routed).

## Item 5 — DRIFT PINS + FREEZE: **GREEN / SATISFIED**

- `tests/unit/test_instance_ops_registration.py`: **26 passed** — AC-10a wiring (:235/:245), alias identity (:256), T0a slice (:269), AC-10b vocabulary (class drifted to `:436+` under tidier — located by content), token-map re-pointed via two-halves reconstruction (:473) with **zero** retired-token literals anywhere in the file.
- T0a slice pin `tests/unit/tools/test_agent_restart.py::TestT0Preconditions::test_t0a_prime_hardened_fallback_is_reserved`: **PASS**.
- Freeze: `daemon/manager.py` **empty diff** (blob `9a29e074…` both revs); 6-name alias block sha-identical; restart gate (:2720-2730) and factor-2 (:3318-3366) byte-identical after the uniform +3 line shift. **No freeze violation.** Disclosure: `upgrade_tools.py` carries **two** comment-only hunks in `:1259-1280` (the sanctioned FIVE→SIX `:1275` comment **plus** a stale "falls back to 'api' in Phase 2" comment fix) — zero code lines, outside all protected regions; for the reviewer to judge the allowance wording.
- Changed-file surface (20 files, +1653/−174): nothing outside the expected set; `daemon/manager.py` absent; `models.py` docstring-only; A1 build-breaking amendment landed (`tests/job_queue/test_post_restart_arm_notify_routing.py:187`, file 7 passed).

## Item 6 — NET-NEW REDS: **0, independently proven**

Throwaway worktree pair, own venvs, identical commands both sides, chunking computed once from HEAD and applied to both. Ambient env on this box is **live-shaped** (`POSTGRES_DB=ensemble_prod` @ 10.44.0.2, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`) — scrubbed per-variable every run.

| Scope | BASE | HEAD | net-new | base-red→head-green |
|---|---|---|---|---|
| S1 `tests/unit/tools/` | 3980P/13F/6S (3999 coll.) | 3997P/13F/6S (4016 coll.) | **0** | 0 |
| S2 `tests/job_queue/` | 2179P/16F/38S/3D (2236 coll.) | identical | **0** | 0 |
| S3 `tests/unit --ignore=tests/unit/tools` | 13824P/72F/21E/57S (13974 coll.) | 13827P/72F/21E/57S (13977 coll.) | **0** | 0 |

Pre-existing reds identical both sides: **122** (13+16+93). S3 HEAD = BASE **+3 passed exactly** (the new `TestNoGateKeysOnPclass` tests). Chased candidates — both execution artifacts, neither a DF-4 defect: (a) 9 transient UniqueViolation setup-ERRORs from running both legs **concurrently** against one shared scratch PG DB (symmetric race; re-proven strictly serial → identical both sides); (b) 1 load-sensitive flake `test_upgrade_journal.py::TestExecutorSpawn::test_real_spawn_env_and_process_group_independence` (3× isolated PASS on both sides; chunk-context-sensitive; pre-existing; quarantine-candidate for the owning lane, not unilaterally quarantined). New/re-pointed DF-4 tests: 17 new + 6 re-pointed **all green** at HEAD; 3 base counterparts green at base; 23 added / 3 removed mapped 1:1, none red either side.
**Population caveat:** S3 absolute reds (93) differ from the implementation lane's ~11 because throwaway worktrees lack the fenced gitignored `.env` (config/DB-dependent unit tests fail identically on BOTH legs) — verdict unaffected, populations not 1:1.

## Routed (owner lanes, none DF-4-blocking)

1. **Developer lane — candidate defect**: ghost-arming wake absorption (dead `KeyError` seam, queue row on ghost iid, stale sweep docstring `:1006-1017`, unit pack arm only via stub). Reproduce: W-D probe pattern; see LESSONS/2026-10-22-df4-ari-fallback-dead-seam.md.
2. **Stage lane — W1 handoff extension**: commit the untracked `tests/postgres/test_df4_ari_fallback_closure.py` WITH the planning dir (targeted adds only; `git add -A` is banned by W3). Until committed, the arm's behavioural cover exists only in the worktree.
3. **Stage lane**: pin (iii) real-PG wall 24.5–26 s vs the 30 s/test ini timeout — near ceiling; consider an explicit `--timeout` override in the promote-time gate invocation.
4. **Stage lane**: future A/B briefs must state the verification worktree's `.env` shape (population, not verdict, depends on it).
5. **Reviewer**: two comment-only hunks in `upgrade_tools.py:1259-1280` vs the one sanctioned in the plan (wording of the allowance).
6. **Owning lane**: flaky-candidate `test_real_spawn_env_and_process_group_independence` (quarantine evaluation).

## Housekeeping

- Worktrees `/tmp/df4v-{base,head,reds-base,reds-head}` removed; scratch DBs `df4v_scratch`, `df4v_ari_scratch` dropped + proven gone; DF-4 worktree tracked tree byte-identical to HEAD at hand-off (start==end porcelain; pre-existing foreign-lane dirt only: tracked-M `install-audit.jsonl` [self-growing telemetry, not ours], untracked planning dir). Port 8088 / live daemons untouched throughout. Nothing committed by the tester lane.
- Evidence bundles retained: `/tmp/df4v-reds-evidence` (W-C, 84 run outputs), `/tmp/df4v-STEP-{A,B,C}*.log` (W-A), W-B/W-D evidence embedded in their reports (this file + EVIDENCE/).
- Docs updated by tester: this file; PACKS.md DF-4 commission section (pack registration); LESSONS ×2.
