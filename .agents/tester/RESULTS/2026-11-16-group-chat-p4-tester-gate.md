# Group-Chat P4 (Nudge Hooks) — Independent Tester Gate

- **Date**: 2026-11-16
- **Worktree**: `/home/nea/ensemble-src-wt-group-chat-p4` — branch `feature/group-chat-p4` @ `522c6a9ba` (base `adbc7bf21`, the P3 merge)
- **Mode**: validation-only (no fixes, no commits of product/test code, no tree edits)
- **Verdict**: **PASS-WITH-NOTES** — behavior/product clean; the regression axis carries **2 net-new deterministic TEST-SIDE stale line-number pins** (`test_proactive_compaction_ns_trap_fix.py`, `_CLE_*_LINE` constants) → **one must-fix before merge: the 2-constant re-pin** (house precedent ×2). Strict-letter reading ("net-new must be EMPTY"): **FAIL-pending-re-pin**. The distinction is procedural, not substantive — the pinned guard code is byte-identical base vs head.

## Summary

| Section | Result |
|---|---|
| Tier-2 whole-tree dual-side regression | base 26,662 exec (223F/37E) · head 26,690 exec (209F/21E) · common 212 · base_only 48 · head_only 18 → adjudicated: **15 artifacts + 1 known quarantine + 2 NET-NEW test-side stale pins** |
| Collections reconciliation | base 26,659 → head 26,687 = **+28, ALL from `tests/unit/services/test_room_nudge.py`**; base_only 0; manifest head-only = exactly the one new file |
| Registered pack rows | `gc_p4_nudge_pins` **28/28 PASS** · `gc_p4_executed_composition` **5/5 PASS** |
| Behavior (service level) | **8/8 probes, 164 checks** (2 previously-unpinned gaps closed by probe) |
| Behavior (executed-graph level) | **8/8 probes** (reviewer limit-30 re-run + constructed limit-14 + 6 constructed variants) |
| `test_language_check.py` STANDALONE | **124/124 PASS** (order-scoped failure not reproduced standalone — POK rule honored) |
| ensure.md Core (R1–R7) | **7/7 PASS**, 0 FAIL (4 improvement notices) |
| Tidier deferrals D1/D3/D5 (+D2/D4) | D1 latent-only · D3 runtime claim REFUTED (bound confirmed) · D5 SOUND (fragility note) · D2 docs gap · D4 P6-disclosed |
| Quarantine | 3 active rows reconciled; the nest-report stale pin = expected deterministic both-leg red (base-attributed, vacuous base pass) — NOT net-new |
| Product defects | **0** |

## §1 Scope decision (blast radius)

Tier-2 **mechanically indicated**: the lane touches `daemon/graph.py` (+394/−13) and `daemon/manager.py` — both on ensure.md's core-seam path-prefix list. Whole-tree dual-side run therefore **warranted** (not an expansion). Tier-2 budget 90 min advisory → actual gate wall **3,347 s = 55.8 min, WITHIN**; pre-leg projection 73 min, re-projection after shard-0 (measured 1,059 nodes/min) 74 min → no breach.

## §2 Full regression (Tier-2 whole-tree dual-side)

### 2.1 Per-leg table

| leg | mode | files | nodes-exec | F | E | wall s | rc | digest-of-red-ids |
|---|---|---|---|---|---|---|---|---|
| base | shardA | 508 | 13,036 | 130 | 14 | 738 | 1 | |
| base | shardB | 544 | 13,038 | 89 | 23 | 580 | 1 | |
| base | repos (serial) | 23 | 402 | 4 | 0 | 254 | 1 | |
| base | scratch (serial) | 3 | 21 | 0 | 0 | 18 | 0 | |
| base | fence-family (serial, POSTGRES_DB unset) | 4 | 165 | 0 | 0 | 38 | 0 | |
| **BASE TOTAL** | | | **26,662** | **223** | **37** | **1,628** | | **sha `c2f751c6…`** |
| head | shardA | 508 | 13,036 | 115 | 0 | 605 | 1 | |
| head | shardB | 545 | 13,066 | 94 | 21 | 591 | 1 | |
| head | repos (serial) | 23 | 402 | 0 | 0 | 250 | 0 | |
| head | scratch (serial) | 3 | 21 | 0 | 0 | 10 | 0 | |
| head | fence-family (serial, POSTGRES_DB unset) | 4 | 165 | 0 | 0 | 47 | 0 | |
| **HEAD TOTAL** | | | **26,690** | **209** | **21** | **1,702** | | **sha `35dbb34f…`** |

Identical commands both legs (one canonical runner, sha `54138fe6…`). Import proofs: base leg resolved `daemon/__init__.py` INSIDE the git-archive extraction; head leg inside the worktree. No daemon booted, no process killed, port 8088 untouched, `ensemble_prod` never contacted.

### 2.2 Collections reconciliation (slicing proof)

- `--collect-only -q tests/unit`: base **26,659** (rc 0, 78 s) · head **26,687** (rc 0, 20 s) → **delta +28, ALL from `tests/unit/services/test_room_nudge.py`**; `base_only = 0`; no other drift.
- Manifest reconciliation (`git archive` vs worktree): 1,082 base files vs 1,083 head; HEAD-only = exactly the one new test file.
- Lane file set = **11 files** (`git diff --name-status` 1:1 match; the brief's "12 files / 9M+3A" was a miscount of an 11-item list).
- Accounting closure (recipe §7): base executed 26,662 vs collected 26,659 = **+3 NAMED** (`tests/unit/decision/helpers.py` — 3 `test_*` functions in a non-test-named helper, collected only as an explicit arg; identical artifact at P3); head identical +3 same ids. collected-only = 0 both legs. junitxml counts == log summary counts on EVERY leg.

### 2.3 Digest comm + adjudication

BARE-ID form (primary; the P3-proven form): **common 212 | base_only 48 | head_only 18** → canonical comparator "NET-NEW = 18 (NOT EMPTY)" → per-id adjudication of all 18 (3× serial isolated, no `-n`, both legs):

- **head ladder 3/3**: identical fail-set every run = **3 red / 15 green**.
- **base ladder 3/3**: rc 0, EMPTY fail-set = **all 18 green at base**.
- **A) 15 ids green 3/3 both sides** = sweep-composition/order/clock artifacts: `test_paused_auto_resume_fallback.py` ×7 (never-awaited middleware RuntimeError), `test_symptom_repair_engine_failover_e2e.py` ×4, `test_symptom_repair_ladder.py` ×1, `test_ari_user_ruling_wake_semantics.py` ×1 (wall-clock/time-of-day dependent — crossed 13:30 ICT between legs), `test_mcp_tool_timeout.py` ×1 (standing quarantine ORDER-FLAKE row, exact node match), `tools/test_upgrade_journal.py` ×1 (env-pollution order flake). **Not regressions.**
- **B) 3 ids deterministic red at head, green at base**:
  1. `test_nest_report_rest_route.py::TestAM1TheRouteSetPin::test_the_metrics_router_source_is_untouched_by_this_lane` — **KNOWN QUARANTINE stale pin** (pre-lane drift `a67b74066`; zero lane reach; base-side pass vacuous under gitless extraction). **NOT net-new.**
  2. `test_proactive_compaction_ns_trap_fix.py::TestNsTrapCLETargeting::test_cle_guard_lineno_pin_secondary`
  3. `test_proactive_compaction_ns_trap_fix.py::TestNsTrapCLETargeting::test_empty_replacement_lineno_pin_secondary`
     - **NET-NEW, lane-caused, TEST-SIDE.** Mechanism proven: lane `graph.py` delta +394/−13 → `_CLE_GUARD_LINE` 9574→**9872** (+298), `_CLE_REPLACEMENT_LINE` 9736→**10034** (+298); the pinned guard block and replacement branch are **byte-identical base vs head** (17/15-line windows diff-verified). Pin file NOT modified by the lane; 0 QUARANTINE rows.
     - **Required edit (NOT applied — validation-only)**: `_CLE_GUARD_LINE = 9574 → 9872`, `_CLE_REPLACEMENT_LINE = 9736 → 10034`.
     - **House precedent ×2 (same shape)**: `5995366bb` "test: re-pin the CLE guard line tripwire for the graph.py shift"; `4e0981b57` "test: re-pin the CLE line tripwires after the fix-back comment shift".

**NET-NEW = 2 (not EMPTY).** Class: test-side stale line-number tripwire vs the lane's graph.py growth. Impact: **zero product-behaviour regression**; the remedy is a 2-constant re-pin. The lane's own +28 nodes are **zero-red at HEAD**.

### 2.4 Fence / hygiene

Fence matrix **6/6 PASS** (in-child poison, positive control, scrub effectiveness, fence independence, assignment-after-unset semantics, import resolution under the runner env). Per-variable scrubs only; dev PG fence explicit (beats ambient); ambient `ENSEMBLE_SELF_ENV=live` pinned to dev every invocation; zero fence breach. Worktree porcelain 0 pre/post; HEAD unchanged; `install-audit.jsonl` sha unchanged (no restore needed); main-checkout status sha identical to pre-flight. Base extraction removed (372 MB); evidence retained (31 MB). Disclosed: gitless base tree gained 2 `__pycache__` dirs + 11 `.pyc` + 1 `.pytest_cache` (recipe artifact; PYTHONDONTWRITEBYTECODE + `-p no:cacheprovider` don't fully stop cache writes) — base file set verified byte-identical to `git archive adbc7bf21` before the run.

Pre-exclusions confirmed OUTSIDE tests/unit: `tests/performance/test_message_api_cost.py`, live-daemon e2e cells, `tests/packs/g7`, and `tests/test_language_check.py` (tests/ ROOT; validated standalone 124P separately).

## §3 Behavior validation

### 3.1 Service level (real file-backed SQLite + real services, plain recording-spy manager double)

**8/8 probes PASS, 164 checks** (`/tmp/gc_p4_gate/behavior_service/`):

| Probe | Result | Evidence |
|---|---|---|
| P1 carrier unwired ⇒ byte-identical pre-P4 (formerly UNPINNED gap) | PASS (60 checks) | all 9 update-bearing todo paths × 3 unwired variants; every result byte-identical; 3 carriers byte-equal to an independently reconstructed pre-P4 string (142/142/152 B); note rides ONLY `todo_list_update` (`todo_tools.py:455`), `todo_graph_update` (:512), `todo_graph_update_subtask` (:927) |
| P2 carrier is NOT an injection (formerly UNPINNED gap) | PASS (12) | room+unread=2 through a real `todo_graph_update`: 7 injection/queue spy methods ALL zero; repo call = `nudge_snapshot_for_instance` only (read-only); cursors unchanged |
| P3 filter decision | PASS (18) | unread=0 → `''`/no fire/no spend; non-member → nothing, latch `{}`; closed-only member → nothing; positive → note with count + `[SYSTEM CONTEXT:` reminder |
| P4 shared latch + watermark | PASS (16) | exactly ONE nudge per turn across both arms; new message re-arms watermark but does not re-fire same turn; next-turn fire |
| P5 C4 closed rooms + asymmetry | PASS (9) | closed room: nudge 0 / latch 0; delivery lane still delivers (`outcome='injected'`, cursor 0→2) — asymmetry proven at the lane level |
| P6 completion-gate release-after-read | PASS (8) | fire → read → same-turn eval passes; fresh-latch control proves the READ releases, not the turn spend |
| P7 restart bounded duplicate | PASS (10) | fresh coordinator → NO duplicate (latch is manager RAM); fresh manager (RAM lost) → exactly ONE duplicate, notification-only (substrate unchanged) |
| P8 carrier-gap LOST-vs-DEFERRED (follow-up) | PASS (31) | `todo_graph_add_subtask`/`remove_subtask`: note absent (manager reminder computed AND dropped — `todo_tools.py:791`/`:1001`), but the **(a2) completion arm fires in the same turn** (2 unread) ⇒ **DEFERRED, not lost**; latch cap holds; (a2) is a terminal-path graph node (`graph.py:13688-13710`) independent of the todo tool used. Residual: an aborted/exception turn never reaches the terminal decision ⇒ no reminder that turn |

### 3.2 Executed-graph level

**8/8 probes PASS** (`/tmp/gc_p4_gate/behavior_graph/`):

| Probe | Provenance | Evidence |
|---|---|---|
| G1 reviewer limit-30 C1 shape | re-run verbatim | `TERMINATED: agent_passes=2 gate_calls=2 final_route=None`; no permanent `"agent"` hint |
| G2 limit-14 variant | gate-constructed (no upstream variant exists — the on-disk "14" is the `A-P4-7..14` ID range) | **same 2 passes** ≪ 14 → pass count is limit-independent; chain = fire→continuation→quiet→END |
| G3 reviewer C2/C3 script | re-run verbatim | `ALL-OK`; legs enumerated: C2 signature (no room/probe leak), C2 enabled seam (`agent` vs `end_candidate`), C3-a disabled-seam wiring order, C3-b config threading (verdict `agent`, probe `['mem-1']`) |
| G4 config-drop counter-leg | gate-constructed | real graph: 3 configs, ALL with `thread_id='mem-1'`; queue emptied; injected message reached the model. Synthetic pre-fix body: probe `[]` → **drain DEAD** ⇒ the threading guard is load-bearing (single-variable isolation) |
| G5 permanently-busy room | gate-constructed | unread `[1,2]` at both evals; fire pattern `[True, False]`; **1 nudge**, 2 passes, END reached, no recursion error at limit 30 |
| G6 a2 hold/release (both seams) | gate-constructed | hold: routes `['agent', None]`, marker kwargs present; after read: `[None]`, 0 nudges, 1 pass; controls (no read) re-fire ⇒ the READ releases |
| G7 strong form | gate-constructed | reminder names `@worker from lead (room 'alpha', seq 1)`; exactly ONE `set_injection`, source `room:<id>:lead:1:author=owner-1`; cursor 0→1; per-turn cap: 2nd eval no re-attempt |
| G8 restart bounded duplicate | gate-constructed (graph level) | fresh coordinator → no duplicate; fresh manager → exactly ONE, latch re-armed |

`test_language_check.py`: **124/124 PASS standalone** (2.26 s). Note: a first run under an over-broad env scrub false-negatived (auth vars are required when `ENSEMBLE_AUTH_ENABLED=true` is ambient — suite-specific POK advice must not be blanket-applied); the authoritative ambient-auth run is green.

## §4 Tidier deferrals (verify-and-report)

| # | Verdict | Key evidence |
|---|---|---|
| **D1** config-thread positional-binding name-heuristic | **LATENT-ONLY — not reachable today** (Medium latent) | `graph.py:7792` (`_ROUTER_CONFIG_PARAM_NAMES`) + `:7984-7999`; probe: renamed-defaulted 2nd param → SILENT 1-arg call (config dropped → `instance_id=""` → drain unprobed → injections stranded); renamed-required → loud TypeError; both current production call sites name it `config` (`:13736`, `:13855`). Blast radius = **injection-drain seam** (NOT the room gate — the gate node gets config directly; NOT the loop guard). Recommendation for the reviewer lane: positional-capability probe (kind/default) instead of a name frozenset |
| **D3** tag-spend reads raw snapshot vs C4-filtered | **Code asymmetry TRUE; runtime claim REFUTED** (Low/defensive) | spend loop `room_nudge.py:383-387` unfiltered, BUT the substrate read is `r.state='active'`-only (`repository.py:1229`) ⇒ closed rooms never enter the snapshot. Bound CONFIRMED: scalar overwrite per turn key (`:296`→`:168-186`), zero cross-turn accumulation; forward-only room lifecycle makes it moot even hypothetically |
| **D5** disjunctive pin (`:925` → now `:984`) | **SOUND / non-vacuous** | weak disjunct `first_nudge is None` provably False given sibling `:983` (identical predicate, immutable fixture); no MagicMock on the path; 3 static counter-mutations all redden. Fragility note: soundness is delegated to the sibling assert — a self-contained `assert first_nudge is not None and first_lang <= first_nudge` would be stricter |
| **D2** O(messages) LEFT-JOIN per todo-update | documented (`.agents/tidier/notes.md:933/:934`) but routed to "Reviewer" with **no explicit P6 tag** — one-line docs append is an owner call |
| **D4** checkpoint enlargement | properly P6-disclosed (`decisions.md:401`, A-P4-13) |

## §5 ensure.md Core (scoped)

| # | Requirement | Verdict |
|---|---|---|
| R1 | No regressions in changed packs (Critical) | PASS (both P4 packs; + PACKS.md row-6 count drift, refreshed by this gate) |
| R2 | Deadlock/concurrency integrity (Critical) | PASS (quarantine-adjudicated): `concurrency_atomic_unit_test` ×2 runs **5F/94P/74S**, baseline-exact; all 5 = `test_instance_metadata_atomic` DetachedInstanceError, node-for-node the QUARANTINE.md:153 row; base-proven at `f49006b92` |
| R3 | No sync DB calls on the asyncio loop (Critical) | PASS via R2 (thread-identity + `asyncio.to_thread` spy legs named) |
| R4 | `dev.sh --timeout-graceful-shutdown 10` (Critical) | PASS (`dev.sh:156` comment, `:159` flag) |
| R5 | Await sweep for converted async fns (Important) | PASS, no-regression: lane diff touches none of the three; current tree 10/10 call sites awaited |
| R6 | Parent→child→complete deadlock scenario (Important) | PASS via R2 (covering nodes named) |
| R7 | No dead code from the fix (Nice-to-have) | PASS: `room_nudge.py` every export consumed, 0 unused imports; only `ram_interest` (known O4 backlog, pre-existing, deliberately deferred) |

Improvement notices (ensure.md is user-owned; not edited): **N1** R2's literal "pack PASS" is unreachable while the quarantine row stands — suggest "FAILED set ⊆ QUARANTINE.md (canonical 5F/94P/74S)". **N2** the pack's method line omits the per-variable env scrub. **N3** the Tier-2 whole-tree obligation — **discharged by this gate** (§2). **N4** PACKS.md row-6 drift (fixed here).

## §6 Findings

- **F1 🟠 (important, non-blocking) — carrier covers 3 of the 5 reminder-returning todo paths.** `TodoGraphManager.add_subtasks` (`todo_manager.py:1011-1015`) and `remove_subtask` (`:1143`) compute+return a reminder the tool layer drops (`todo_tools.py:791`, `:1001`); the spec's operative rule is "the reminder slot returned by **EVERY** todo-manager update" (×6 restatements, incl. the anchor-verified `[A]` fact at `substrate-map.md:92`). The only text naming three paths (`decisions.md:375`, `todo_tools.py:269-271`) is a post-implementation record (committed 10 s after the implementation) and internally inconsistent. **Impact**: the early-warning is absent on those two paths; the (a2) turn-end arm covers normal turns (**DEFERRED, not lost** — P8), residual loss only on aborted turns. **Owner decision**: accept the "TODO-STEP boundary" reading (0-gap) or extend the carrier (~1-line each, no payload-contract change).
- **F2 🟠 (must-fix before merge) — 2 net-new test-side stale line-number pins**: `_CLE_GUARD_LINE` 9574→9872, `_CLE_REPLACEMENT_LINE` 9736→10034 in `tests/unit/services/test_proactive_compaction_ns_trap_fix.py` (precedent `5995366bb`, `4e0981b57`). Zero product impact (pinned code byte-identical).
- **F3 🟡 D1** latent name-heuristic hazard (see §4) — reviewer lane's structural call.
- **F4 🟢 D5** pin soundness delegated to its sibling assert (self-contained form would be stricter).
- **F5 🟢 D2** no explicit P6 tag for the LEFT-JOIN perf shape (one-line docs append, owner call).
- **F6 🟢 strong-form injected content** is the notification line, not the post body (body reachable via `room_read`) — design observation for P6 if inline text is intended.
- **F7 🟢 PACKS.md row-6 count drift 27→28** — refreshed by this gate.
- **F8 ℹ️ lane file set is 11 files**, not 12 (brief miscount).

## §7 Pre-existing-red ledger

Base 223F/37E across 26,662 nodes; head 209F/21E across 26,690. Common red set 212 ids (pre-existing at both legs). 48 base_only ids (red at base, green at head — flaky/order artifacts). 18 head_only → 15 artifacts (3/3 green both sides), 1 known quarantine row (nest-report stale pin), 2 net-new test-side pins (F2). Standing quarantine rows reconciled: nest-report stale pin (deterministic, base-attributed), skill-evolution AB node (not in this scope), launcher 8b-c2 (documentary). No deterministic lane-reachable product red.

## §8 Process anomalies (filed)

- **A1** `pause_instance` on the wedged regression child (797388cf) crashed/restarted the daemon **3× consecutively** (interleaved `time()` calls succeeded) — anomaly `5b2ffc0a`. `terminate_instance` then succeeded. Suspected: cancel of a graph task wedged inside an unkillable tool call.
- **A2** **False-hang termination**: child 797388cf's `last_activity_at` froze at 05:55:52 while it was actively running the full gate (junitxml 06:17–07:01); the watchdog fired at 1 h and this gate terminated it at 07:54 — **after** it had completed and written `FINAL_REPORT.txt`. Its evidence chain was independently re-verified authentic (digests recompute byte-exact; junitxml timestamps real; quarantine rows confirmed). Lesson: `last_activity_at` (a message-level timestamp) does not track long-running tool calls — verify via artifacts (`/tmp/.../PROGRESS.txt`, junitxml mtimes) before declaring a hang. Replacement worker 3cb5a1d3 was then terminated as redundant after re-verifying the evidence (it had also wedged on a whole-tree collect — the known collection-wedge class).
- **A3** Task-shaped collect wedge: two workers hung on whole-tree collection/long pipelines. Mitigation for future gate briefs: redirect every long command to FILE (`> log 2>&1 </dev/null`) and wrap with `timeout -k` so the bash tool never waits on a pipe held by grandchildren.

## §9 Evidence paths

- Regression: `/tmp/gc_p4_gate/regression/` (`FINAL_REPORT.txt`, `progress.tsv`, `digest/`, `adjudication/` — 3× ladders with junitxml, `base_leg/`, `head_leg/`, `logs/`)
- Service behavior: `/tmp/gc_p4_gate/behavior_service/` (P1–P8 scripts + logs)
- Graph behavior: `/tmp/gc_p4_gate/behavior_graph/` (G1–G8 scripts + `.out` logs + harness)
- ensure: `/tmp/gc_p4_gate/ensure/SUMMARY.md` + pack logs
- Packs: `/tmp/gc_p4_gate/pack_pins/log.txt`, `/tmp/gc_p4_gate/pack_composition/log.txt`, `/tmp/gc_p4_gate/langcheck/log.txt`
- Preflight: `/tmp/gc_p4_gate/preflight/`
- Deferrals: `/tmp/gc_p4_gate/deferrals/` (incl. D1 probe)

## §10 Disposition

Merge-readiness: **conditional on F2's 2-constant re-pin** (test-side, ~2 lines, precedent ×2) — then the regression axis closes EMPTY and this gate's verdict becomes a clean PASS. F1 is an owner decision (accept 0-or-2 reading vs extend the carrier); F3 is a reviewer-lane structural follow-up; F5 is a one-line docs append. No product-behavior regression was found anywhere in the lane.
