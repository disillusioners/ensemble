# P5 LEG A — Full Verification Matrix — Central Maintenance System (maintsys-p5a)

- **Date:** 2026-11-22 (17:37Z–20:0xZ)
- **Lane:** `feature/central-maintenance-system` @ worktree `/home/nea/ensemble-src-wt-maintsys`
- **Leg span:** base `7fd7af010` (staged v0.31.31 source = origin/latest tip) → final HEAD `475884dd4` (authoring entered at `a882de317`; +8 P5A commits)
- **Binding spec:** `.agents/shared/planning/maintenance-system/phase5-plan.md` (T5.1–T5.7, AC1–AC8) + `decisions.md` §A (incl. P1/P5 TEST-PATH ADOPTION) + dispatch envelope (AC8 four-surface amendment, F1/Y1/Y2/G3/F3/G5 carried items)
- **Tester:** leader instance `2cf25411` (dispatch only); worker roster below
- **Gate tier:** **Tier 2 whole-tree dual-side** (MECHANICAL — the branch touches `daemon/manager.py`, core-seam list; ensure.md Gate Tiers §)

## Scope Decision

Full matrix requested AND warranted (cross-module branch: scheduler adapter, manager, repositories, routers, agent seat, FE; release-gate-adjacent). Whole default pytest set dual-side (39,497 base / 39,794 tip nodes) + registered packs + FE evidence + ensure.md Core. After the 7 carried cures, re-verification was **scoped** to the cured files + blast radius (cures touched 7 test files, 1 FE comment, 1 agent skill body; `daemon/manager.py` needed **zero** byte changes) — a full 2×39.8k re-run was judged disproportionate; the scoped re-runs + 3× determinism matrix (21/21) + base-parity proofs carry the re-certification. Disclosed here per the scope-decision rule.

## Worker roster (positional map)

| Role | Instance | Deliverable | Commit |
|---|---|---|---|
| W0 preflight | `9167f493` | tree/fence/census/baseline/recipes (read-only) | — |
| W2A T5.1 | `016aa1b6` | `test_maintenance_no_idle_trigger.py` (7 nodes) | `08f0cad97` |
| W2B T5.2 | `c1733a8f` | knobs file 30→63 nodes | `b54d3d8fe` |
| W2C T5.3+G2 | `259c7e57` | rearm file 15→16 nodes | `ce17d4008` + `743332d57` (leg-1 correction) |
| W2D T5.4 | `ac12ae11` | seed-guard file 27→37 nodes | `a3ab12827` |
| W2E T5.5 | `5c1df297` | acceptance-map file (72 nodes) + 3 pin-file extensions (+5 nodes) | `7fc08fe27` |
| W2F T5.6+AC8 | `4f09108b` | `manual_trigger_provenance` (12) + `results_root_equivalence` (9) | `57a2fe6ff` + `82cc7da0c` |
| W2G F1+Y1 | `7abbad8b` | router `\Z` pin file (15) + Y1 pack fence | `b44d5d784` |
| W2H G3 | `70b117dc` | FE fail-closed fix + 7 spec pins + fenced npm ci | `b5488e4ef` |
| W3A Tier-2 | `045797fe` | whole-tree dual-side gate (evidence `/tmp/p5a-reg-scratch/`) | — |
| W3B packs+Core | `1e0f9377` | packs 177/181/51 + Core a–d + Y2 ledger | `d7656190f` |
| W3C FE evidence | `a0c37faf` | jest 93/93 + tsc + AOT | — |
| W4C cures | `d94c0ac8` | 7+2 carried cures + scoped re-verify | `475884dd4` |
| W4M mutation | `dd2ac9ed` | T5.1 dual-leg mutation proof + quartet 58 | — |

## 1. Authoring deliverables (per-task verdicts)

### T5.1 — R2 no-idle two-legged pin ✅
- NEW `tests/unit/test_maintenance_no_idle_trigger.py` (7 nodes: 2 behavioral + 5 structural). Canonical predicate string (plan:54) quoted verbatim in the module docstring.
- Behavioral: busy fixture + due one-shot chain row ⇒ `enqueue_message_job` awaited with `idempotency_key == f"scheduler:{source_id}:{run_at.isoformat()}"`; idle-probe never awaited; one-shots force NEW_INSTANCE so the busy-CALLER deferral is unreachable. Anchor drift verified: key derivation at `scheduler.py:2958-2960` (plan said :2728), `_emit_scheduled_message` :3063, new_instance force :374-381, deferral :3126-3160.
- Structural: real AST (`ast.parse`+`ast.walk`, Name/Attribute, docstring Expr skipped) over EXACTLY the four BR-6 files; non-vacuity via synthetic-snippet detector check + per-file visited>0 + existence.
- Keep-green: `test_maintenance_idle_starvation.py` 35P untouched.
- **Mutation proof (W4M, serialized last):** ONE 17-line idle-gate inserted at the real dispatch seam (`_route_via_job_queue`, after `get_or_create_instance`, before the D4 idempotency block; realism anchors: real `MaintenanceService` + real in-flight registry) ⇒ **both legs RED** in one attempt: behavioral `Awaited 0 times` + structural `scheduler.py:2944 Attribute ._is_idle`. sha256 before `161b8cac…` == restored `161b8cac…` (byte-identical); post-restore 7/7 green; `git status` clean. (One behavioral node — busy-CALLER deferral — stayed green BY CONSTRUCTION: its seam is stubbed above the insertion point; disclosed, not papered over.)

### T5.2 — picker + BR-5 DST ✅
- EXTEND `test_maintenance_schedule_knobs.py` 30→63 nodes (append-only, 623+/0−). Uniformity = fixed seeds + fixed ±4% band (cannot flake; 6× green). `[start,end)` 240 candidates; weekday +7d re-anchor (Monday-reference leg); ICT via real `anchor_local_to_utc`; invalid-window fail-closed (names both knobs, refuses to arm); no-croniter = AST pin + runtime tripwire + control.
- **BR-5 DST:** gap 2026-03-08 02:30 → `07:00:00Z` (inside window, warning surfaced in `picked.warnings` AND the module log); fold 2026-11-01 01:30 → `05:30:00Z` (fold=0/EDT, inside); whole-window-in-gap ⇒ every draw refuses + "shifted-forward" logged.
- Mutation evidence (4 mutations, all restored byte-identical): inclusive-end (7F), swallowed warning (3F), removed window re-check (2F), injected croniter import (1F AST).
- **Disclosed adaptation:** the fold path returns an EMPTY warning by helper design (`tz.py:482` returns `(pre, "")` on fold) — the fold leg pins the mirror form (`warnings == (helper_warning,) if helper_warning else ()`), catching both swallowed and invented warnings; the non-empty-warning assertion lives on the gap legs. Intent-faithful, not a weakening. POK `maintenance-dst-fold-warning-empty`.

### T5.3 + G2 — re-arm ✅ (one spec correction, adjudicated)
- EXTEND `test_maintenance_rearm.py` 15→16 nodes. **Adjudication:** the plan's T5.3 sentence "a run that fails still mints the next run_at" conflated run-failure with dispatch-failure. Binding docs (phase1-plan.md:15, D-G; decisions R1) are two-sided: consuming fires mint regardless of run success; FAILED DISPATCHES never advance (same-episode 5s re-arm). First cut RED on the wrong side → corrected to BOTH legs: (1a) failed run keeps the mint; (1b) failed dispatch never advances (cites phase1:15). 16/16 green. **Plan-wording ambiguity disclosed to the leader.**
- BR-4 complete-config RMW (agent/message/timezone/recurrence/instance_mode/project_id/chain survive; only run_at changes) ✅. BR-2 expired-key ⇒ second JobItem + 24h TTL pinned un-widened ✅. Persistence across adapter reconstruction ✅ (P1 open-question #8 formally proven on the light write). Weekly vs 5s `rearmed` distinguished ✅. **G2** force-set pin (`refresh_chain_keys` force-sets `chain.mode → CHAIN_MODE="weekly-random"`, `maintenance_seed.py:135/:373-380`) ✅.

### T5.4 — guard legs ✅
- EXTEND `test_maintenance_seed_guard.py` 27→37 nodes. (a) cap unset + past run_at ⇒ UNTOUCHED (`past_run_at_left_for_auto_fire`; re-mint forbidden); (b) cap exceeded ⇒ re-mint to verified FUTURE instant, chain lockstep, strict-`>` boundary sub-leg; (c) cancelled-archived NOT resurrected — driven through REAL `gc_archive_and_delete`; (d) stopped+chain revived (same source_id, verbatim snapshot; contrast leg: no chain ⇒ created); (e) no-archive ⇒ created with deterministic uuid5; second boot ⇒ noop. Plus present-cancelled/paused respected + `archive_id_collision` no-overwrite. **NO resurrection legs** (P1's file already had the correct BR-3 shape — nothing replaced; disclosed). Real anchor drift: auto-fire/cap live at `_get_next_trigger_time` `scheduler.py:1147-1185`. Teeth spot-checks: boundary + status-flip discriminators proven.

### T5.5 — acceptance map ✅ (18 rows, all green)
- NEW `test_maintenance_acceptance_map.py` (72 nodes) + extensions: team_members 21→22 (spawnable==4), report_scrutiny 6→7 (report-ALWAYS), fan_in_valve 7→12 (BR-11 valve). `skill_versions_consistent` NEVER edited (P2 T2.7 ownership; 17P keep-green). Combined final run 130/130 @ `7fc08fe27`.
- Rows: BR-10 four-literal + deny byte-equality + `resolve_tool_filter` exclusions (category map AST-derived, never hand-mirrored; granting "instance"/"council" proven to leak terminate/spawn_councilor) + spawnable 4; POK prohibition (pok_upsert/pok_curate ABSENT, pok_query present); BR-9 containment; BR-12 mapping table (mutation: failed→degraded REDs); report-ALWAYS (mutation: phrase removal REDs); BR-13 L0→L3 ladder; BR-11 event-driven deadline (initial + single re-dispatch, watchdog/long-tool-nudge sources, valve once, NO polling); BR-14 (named venv interpreter, bash fallback binding, run_token reuse); BR-15 (detector + lane-4 orphan listing); PD-4a frozen schema (parsed from decisions.md, citation-by-reference asserted); disk-lane fence recipe; lane-4 no-write; BR-16b INDEX mechanics; receipt cross-ref; P1 AC1/AC6 failure-mode ledger; **BR-18 added** (assigned to T5.5 by the plan's map but previously unpinned).
- CITED not duplicated: BR-15 detector half (seed_guard), skill-versions consistency; BR-16/17/19 recorded as pointer rows with owner-file existence asserted.
- Disclosures: AC6 ledger↔body delta (9/10 vs 8/10 anchored rows; pin encodes the AC6 floor ≥8 + pins the ledger text separately; POK `p1-ac6-anchor-ledger-delta`); disk-lane baseline citation asserted via phase3-plan + fence vocabulary (lane body is P3-owned, not edited); bare-python3 leg SKIPS (not REDs) where ambient interpreter can import sqlalchemy.

### T5.6 + AC8 ✅
- NEW `test_maintenance_manual_trigger_provenance.py` (12): token round-trip over the artifact chain — chain literal `trigger: scheduled` / manual `trigger: manual` → orchestrator workflow "Parse the trigger" → writer envelope (`FROM THE ENVELOPE`, never-read-DB asserted) → receipt `_HEADER_FIELDS` carries `trigger_type`. AUDIT sub-check (marked secondary): creator_instance_id provenance, schedule_executions, fire-notify identity-gate skips. Fallbacks documented (REST trigger 409/503 constraints; direct send_message weakest).
- NEW `test_maintenance_results_root_equivalence.py` (9): **four surfaces identical** — seeder helper `maintenance_results_dir()` (`maintenance_seed.py:308`, promote-stable `resolve_live_install_root()`), BE route `RESULTS_ROOT`, api live-views injection (`daemon/api.py:495`/`:2386`), writer skill documented derivation (single-source, no independent recipe). Fresh-root guard = OWN finding kind `results_root_empty` (never `reportless_week`), loud log + ONE dedup'd board item across repeated boots.
- 20/20 green @ `82cc7da0c`.

### F1 + Y1 ✅
- NEW `tests/unit/routers/test_maintenance_reports_router.py` (15 nodes): production pattern `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}\Z` (`maintenance_reports.py:118`, `.match` at `:306`) — trailing-newline id REJECTED today; pin proves the `\Z` property + `$`-mutant bites (load-bearing proof) + `_collect_runs` end-to-end leg. Commit `b44d5d784`.
- Y1: two-line `ENSEMBLE_AUTH_ENABLED=false` fence inside `tests/packs/scheduled_tasks_acceptance.sh`, byte-identical to the sibling idiom (`scheduler_fire_notify_acceptance.sh:12-13`).

### G3 ✅
- `report.component.ts:60-71`: whitespace-only/empty wrapper textContent ⇒ MISS (fail-closed), hit path byte-identical; +13/−4. 7 new spec pins (pure seam + component posture); mutation proof: revert ⇒ exactly 3 pins RED. Scoped report jest 17/17. Commit `b5488e4ef`.

## 2. Tier-2 whole-tree dual-side regression (W3A)

- **Census/slicing proof:** BASE 39,497/39,497 executed (Δ0); TIP 39,794/39,794 (Δ0; 39,564 symmetric + 230 tip-only). Identical ignore set both legs (integration/postgres/slow/conformance/performance/packs + `tests/e2e/test_context_injection_hybrid.py`); pre-exclusion registry honored with named compensating pins.
- **Shards:** BASE 13 shards 48.0 min (T39497 F491 E28); TIP 14 shards 55.2 min (T39794 F489 E29); tip-only shard (8 new maintenance files, 230 nodes) fully GREEN.
- **Digests:** BARE-ID + GREP-FULL sha256 both legs; raw head-only 14/15 → after 3× isolation on the 29 diff nodes: **7 deterministic net-new; 7 resolved non-net-new** (PPP/FPF/pre-existing incl. 2 `bash_spawn_stamp` pre-existing).
- **Base-only column (15):** 9 provisioning artifacts (.venv-path/checkout-name/worktree-location classes per the `ab-base-leg-provisioning-artifacts` recipe) + 6 sweep flakes (3/3 PASS isolated both legs). Zero genuine branch fixes.
- **Recipe compliance:** all four recipe bodies read first; import proof + venv gate; single fence script (4-case self-test PASS); identical file lists; tip-only dedicated shard; canon comparator (fail-loud vacuous guard); main-checkout sha byte-identical before/after; base worktree force-removed.
- **Budget:** shard execution 103.2 min — **90-min advisory BREACHED, disclosed** (fence-file split + ~15% host load + two background-driver tree-kills). Fail-loud honored.
- **Net-new disposition:** 7 deterministic failures, ALL in branch-untouched test files pinning branch-changed subjects ⇒ **carried cures** (§3), then scoped re-verification. **Final: net-new = 0.**

## 3. Carried cures (W4C, commit `475884dd4`)

| # | Finding | Cure | Re-verify |
|---|---|---|---|
| 1–3 | P11 manager-drift pins: 31-line maintenance block markerless | Guard `SANCTIONED_LANE_MARKERS` + ONE entry `'central maintenance'` (hunk natively carries the lane noun — the guard forbids courtesy markers); **manager.py zero byte changes** | 3/3 each (9/9) |
| 4 | FE lane sanction: 13 files outside allowlist | +11 paths (2 already sanctioned) = plan-ratified P4 report slice (PD-2/BR-19); + marker token `'report'` + one marker comment in `models/index.ts` (tool/tab precedent) | 3/3 |
| 5 | Duty-prompt baseline stale | `generate_prompt_artifact_baseline.py` regen: 2/217 rows, count unchanged, `--check` OK | 3/3 |
| 6 | Grants append-point tail | Re-pinned at new legitimate four-literal tail; invariant asserted via per-agent `expected_tails` map | 3/3 |
| 7 | 2 bare `INDEX.md` tokens in `run-report-writer.md` | Reworded to operational-path form (§12.5 #0 exemption) — literals REMAIN so BR-16b pin stays green untouched | 3/3 |
| +8 | `SEALED_SHA_BASELINES` 3rd stale row (chart delivery e2e) | Row re-pinned to real bytes w/ provenance; lives in `SEAL_REPINNED_BASE_VALUES`; 35-row count unchanged | tip == base |
| +9 | Sibling `test_anomaly_report_grant_pins.py` same BR-8 tail | Four literals named in `GRANT_POST_TAIL` w/ BR-8 provenance comment | tip == base |

Blast radius re-verified green: acceptance map 72/72, root-equivalence 8/8, manual-trigger 12/12, no-idle 7/7 (manager.py untouched — proven by `git show --name-only 475884dd4`), quartet + 10 maintenance/maintenancer files, `tsc --noEmit` RC=0. **Determinism matrix: 21/21 PASS.**

**Deliberately NOT cured (STOP-threads, all base-proven at exact parity):** FE tree-level allowlist compensator (58 offenders on 13 foreign paths, tip set == base set — recorded upstream debt); `GRANT_POST_TAIL` missing `project_future` for 13 agents (1-token owner decision); 2 remaining seal rows (`daemon/constants.py`, `agents/leader/rule.md` — other lanes' files); `memory.md` absent ×3 + `EXPECTED_RELEASE_TAG v0.12.4` ×1 (upstream pre-existing, red at base AND tip).

## 4. Registered packs + ensure.md Core (W3B)

| Pack | Result |
|---|---|
| `scheduled_tasks_acceptance` (Y1-fenced) | ✅ 177P/0F, rc=0, 46s wall @ `a3ab12827` (commit-pinned; later P5A commits touch none of its suites) |
| `scheduler_fire_notify_acceptance` | ✅ 181P/0F (177+4 legs), rc=0, 78s @ `a3ab12827` (refreshed row; prior 2026-11-21 @ `5ec666e3e`) |
| `maintenancer_pin_quartet` | ✅ 51P @ `a3ab12827` → **58P @ final HEAD `475884dd4`** (17+22+7+12; legitimate T5.5 growth) |
| `concurrency_atomic_unit_test` (Core) | ✅ PASS quarantine-adjudicated — raw 5F are QUARANTINE row-154 members node-for-node (base-proven `f49006b92`, re-confirmed 2026-11-17); 0 non-quarantined failures |

ensure.md Core: (a) changed packs PASS ✅; (b) concurrency ✅ (above); (c) sync-DB-calls ✅ by mapping (ensure.md:38/:47); (d) `dev.sh:159` `--timeout-graceful-shutdown 10` ✅. Y2 ledger: branch-copy rows landed/refreshed (lane block `central-maintenance-system-p5a-pack-registration`), commit `d7656190f`; main-copy mirror applied by the tester (runtime-state, uncommitted by design).

## 5. FE execution evidence (F3/G5, W3C)

- Scoped jest (report + mcps-gateway.pins + metrics-gear-menu + app.component): **93/93, 5/5 suites** (report dir 17/17 = G3 baseline; JSON-authoritative).
- `./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`: **RC=0**.
- ONE AOT dev build (`npm run build -- --configuration development`): **exit 0**, 24s, 289 files; **zero** NG8002/NG0303/NG8001 hits (R-P5-6 gate). FE tree byte-stable across concurrent Python-lane commits (evidence valid).

## 6. AC1–AC8 verdict table

| AC | Verdict | Evidence |
|---|---|---|
| AC1 R2 two-legged pin | ✅ PASS | §1 T5.1 + W4M mutation proof (both legs RED under ONE mutation; byte-identical restore; green at final HEAD) |
| AC2 picker/re-arm/guard/seed + keep-green trio | ✅ PASS | §1 T5.2–T5.4; keep-green trio green in the Tier-2 symmetric set; `idle_starvation` 35P untouched; UN_GATED backstop did NOT land (BR-7 conditional update not triggered) |
| AC3 every P2/P3/P4 AC mapped + BR-8 four-literal | ✅ PASS | §1 T5.5 — 18 rows green incl. BR-10 four-literal + deny byte-equality + spawnable=4 |
| AC4 manual-trigger token round-trip | ✅ PASS | §1 T5.6 — round-trip green; audit sub-check green; fallbacks documented |
| AC5 staging artifact | ◻️ N/A this leg | Staging = leader Leg 2 (KV: rebase complete, slot `v0.31.32` determined; cut awaits THIS verification). No-promote honored; no release-dir/tag mutation from this lane |
| AC6 no live-shaped paths / fences pinned | ✅ PASS | Every leg fenced (temp DBs, per-var scrubs, ensemble_dev assertions, FAKE_HOME rules honored); W3A fence script 4-case self-test PASS; main checkout sha byte-identical before/after |
| AC7 BR-11/BR-14/BR-15 | ✅ PASS | §1 T5.5 rows 8/9/10 |
| AC8 promote-time root equivalence (FOUR surfaces) | ✅ PASS | §1 T5.6/AC8 — identical path through the ONE helper; writer derivation pinned to helper semantics; fresh-root guard = own finding kind |

## 7. Pre-existing red ledger (base-proven, NOT lane-attributable, NOT net-new)

Quarantine grep: **no** maintenance/maintenancer/scheduler rows in QUARANTINE.md; no lane pack reddens from these (documented here, no new quarantine rows needed — nothing in the lane's pack set contains them):
1. `test_maintenancer_agent.py` ×3 — `memory.md` absent (DB-versioned artifacts).
2. `test_maintenancer_kb_coverage.py` ×1 — `EXPECTED_RELEASE_TAG v0.12.4` vs pyproject v0.31.31.
   *(The dispatch envelope's carried baseline named these 4 — measured red at BOTH legs ⇒ pre-existing, carried.)*
3. FE tree-level allowlist compensator — 58 offenders/13 foreign paths, tip == base parity.
4. `GRANT_POST_TAIL` missing `project_future` (13 agents) — 2 nodes.
5. Seal tripwire residual rows — `daemon/constants.py` + `agents/leader/rule.md`.
6. `test_prompt_section_reference_integrity` — 16 base-red nodes (tip post-cure == base exactly).
7. Concurrency pack's 5 quarantined members (row-154) — already registered.

**Baseline note:** the envelope's other carried items (2 live_views video reds; router-manifest ambient-auth shape) measured **GREEN at HEAD** under the scrubbed fence (cured in-branch before this leg) — env-shape attribution re-proven (ambient auth changes outcome; scrubbed runs pass).

## 8. Commits (this leg, all pathspec-limited, in-branch; no push/merge/tag)

`b44d5d784` F1+Y1 · `b54d3d8fe` T5.2 · `ce17d4008`+`743332d57` T5.3 (+correction) · `a3ab12827` T5.4 · `b5488e4ef` G3 · `08f0cad97` T5.1 · `57a2fe6ff`+`82cc7da0c` T5.6+AC8 · `7fc08fe27` T5.5 · `d7656190f` Y2 ledger · `475884dd4` carried cures · (+ this RESULTS commit + quartet-row refresh — see sweep).

## 9. Disclosures & open items for the leader

1. **P5 T5.3 plan wording** conflates run-failure with dispatch-failure (binding phase1:15/D-G is two-sided; pins encode the two-sided semantics). Suggest a one-line plan amendment.
2. **BR-5 fold-warning** is empty-by-helper-design; fold leg pins the mirror form (POK entry recorded).
3. **Quartet 51→58** legitimate growth (T5.5 extensions) — row carries both measurements.
4. **Tier-2 advisory budget breached** (103.2 min vs 90) — cause + recipe compliance disclosed (§2).
5. **FE tree-level compensator + `project_future` registration** — pre-existing upstream debt at exact base parity; owner decision, deliberately not widened.
6. **Staging Leg 2 (AC5)** unblocked by this gate: final tree `475884dd4`, net-new 0, packs 177/181/58, FE evidence green.

**Overall: GATE GREEN.** Net-new = 0 (7 found → 7+2 cured → base-parity proven). All in-scope acceptance criteria AC1–AC4, AC6–AC8 PASS; AC5 is leader-owned Leg 2.
