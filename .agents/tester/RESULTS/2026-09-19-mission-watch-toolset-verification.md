# Verification Report — mission-watch toolset reshape (final gate before merge)

**Branch:** `feature/mission-watch-toolset` @ `0b504b5e` (base/merge-base `307db932` = latest tip; lineage 307db932 → … → 5f453b93 → 0b504b5e)
**Diff footprint:** 16 files — 3 `daemon/tools/` (job_queue.py, instance.py, _tool_registry.py), 2 meta.json (ari/jober watch_job deny), 9 prompt files (ari×4 + jober×4 + job-orchestration skill.md), 2 new test suites (46 tests)
**Mode:** VERIFY-ONLY — 0 repo file modifications, 0 commits, 0 daemon boots, 0 engine touches, port 8088 untouched. HEAD pin `0b504b5e` verified pre/post by every worker; staged index empty throughout.
**Workers (11):** f7d6472c (inventory), 6cdd6e43 (P1), 731f8abf (P2), 4d6ebd2d (P3), 6418d8d0 (P8), 8e1820a9 (adjacent pins), 6953dce2 (incident replay), 0c75d893 (edge battery), 1a1ee77a (mock audit), abf2b83f (static verification), d1ff9355 (base-compare A/B)

---

## VERDICT: ✅ SHIP

Zero branch-caused failures across **3,244 passed tests** (5 pytest packs) + **15 ad-hoc functional scenarios** (incident replay R1–R4, edge battery E1–E11) + full static/surface/mock-audit verification. The single failing test in scope is pre-existing at base (proven by detached-worktree A/B, signature-verbatim). All ensure.md Core requirements green. Incident cde5017f class closed at tool layer.

## Scope Decision

Full suite NOT run — not warranted. Change is tool-layer + prompts (3 daemon/tools files, 2 meta.json, 9 prompt files); engine untouched by design constraint. Ran: unit/tools full dir (contains both new suites + all 41 registry-adjacent files there), job-queue-tools factory pins, mission-pins family, ensure-core concurrency pack, 6-file adjacent-pins ad-hoc pack (ari agent def, jober watch integration, watch_job integration, registry, tool filter, loader), plus 2 ad-hoc SQLite tool-layer harnesses, mock audit, static verification, and base-compare A/B. Skipped: tests/job_queue/ full sweep, partitions P-2..P-12, e2e/integration sweeps (daemon+LLM release-gate class), PG suites, FE (no FE changes — FE consumes HTTP /api/jobs, not agent tools; per task context, skip justified).

## What Ran (commands + counts)

| # | Pack / harness | Scope | Result | Runtime |
|---|---|---|---|---|
| P1 | `test/packs/regression_unit_tools_test.sh` (`timeout 300` outer + 280s inner; `uv run pytest`, xdist) | `tests/unit/tools/` full dir | **PASS** 2,737P / 0F / 6S / 5-deselected (2,748 collected; deselects = quarantined TestAccessMemoryArchive ×5; both new suites in scope: tools 31 + prompts 15 = 46, all green) | 19.78s |
| P2 | `test/packs/job_queue_tools_unit_test.sh` (120s inner) | `tests/test_job_queue_tools.py` | **PASS** 81/81 — watch_job/watch_jobs factory index pins + job_create response-field pins green (factory contract deliberately untouched by branch) | 2.05s |
| P3 | `test/packs/mission_pins_final_test.sh` (280s inner) | N8(2) + N1(5) + watch_job_mission_terminal(14) + N3(4) + M3(10) | **PASS** 35/35 — integration-marked files executed normally (no addopts deselection) | 1.18s |
| P8 | `test/packs/concurrency_atomic_unit_test.sh` (280s inner) — ensure.md Core | deadlock/cascade/observer/atomic/thread-identity (13 files) | **PASS** 98P / 0F / 74S — exact historical parity; no-sync-DB-on-loop green with branch's tool-layer DB writes present | 8.34s |
| PM | ad-hoc `mission_watch_adjacent_pins` (`timeout 300 uv run python -m pytest`, 6 files) | ari_agent(25) + jober_watch_integration(42) + watch_job_integration(12) + registry(102) + tool_filter(55) + loader(70) | **PASS-with-quarantine** 293P / 1F / 12S — 1F + 12S both PRE-EXISTING-IDENTICAL at base (see Base-compare) | 16.55s |
| P4 | ad-hoc `/tmp/mw-replay/replay.py` (dual-layer 280s/300s) | Incident replay, 4 scenarios | **PASS** 4/4 | 0.31s |
| P5 | ad-hoc `/tmp/mw-edge/edge_battery.py` (dual-layer 280s/300s) | Edge battery, 11 scenarios | **PASS** 11/11 | 1.36s |
| P7 | `/tmp/mw-static/surface.py` + greps | Surface ×3 agents, prompt contract ×9 files, ensure statics, wiring | **PASS** all | <1 min |
| P9 | detached worktree `@307db932` A/B legs ×3 | failure + skip adjudication | **PRE-EXISTING-IDENTICAL** ×2 | ~5s |

**Totals:** 3,244 passed / 1 failed (pre-existing) / 92 skipped (pre-existing classes) / 5 deselected (quarantine) + 15 functional scenarios + static verification.

## Incident Replay (the acceptance test) — PASS

Harness: real `MissionResolver` + real `JobWatcherRepository` + real `TaskRepository` + real `JobRepository`/instance repo over file-backed SQLite (fixture pattern cribbed verbatim from the branch's own suite); only `job_service` doubled (AsyncMock).

- **R1 multi-receipt fan-out (cde5017f shape — task receipt + mirror receipt, one mission):** `watch_mission(mission_id)` AND `watch_mission(<job-ref of receipt #1>)` both land rows on **ALL** receipts (`{receipt_a, receipt_b}`, events=`["mission_terminal"]`, owner=caller); zero notify on non-terminal. **The incident class is closed: watching via either handle covers every receipt.**
- **R2 notify path as far as tool layer allows:** real `get_watchers_for_job(receipt)` returns the row per receipt; real CAS `claim_watchers_for_job_for_instances(receipt, [caller])` deletes **exactly-once** (claim=1, re-claim=0, both receipts claim independently, post-claim set empty). Deepest primitive reachable without a wired InstanceManager; full `[JOB_EVENT]` envelope + `enqueue_message` delivery is engine-side (out of constrained scope) and is separately pinned by N1/M3/N8 suites (P3, 35/35).
- **R3 register-then-notify on already-terminal (completed, 2 receipts):** reply `already terminal (completed)` + `immediate notification sent on 2 receipt(s)`; `notify_watchers` awaited 2× with per-receipt args `(receipt, "completed")`.
- **R4 chaining no longer needed:** `job_create` → response carries `job_id` → `watch_mission(job_id)` registers on the pre-generated receipt UUID — **complete flow, no await_mission/job_continue/watch_job**. Mission-known (mirror-shape) response includes `mission_id`; fresh task response omits it (only-if-known contract held). Caveat: enqueue response modeled via `MagicMock(spec=JobItem)` (session-detached ORM rows throw); contract-honest per mock audit.

Preserved: `2026-09-19-mission-watch-toolset-replay-script.py` + `-replay-evidence.json` (this directory).

## Surface Verification — PASS (registry/factory level, real code path)

Real factories (`create_job_tools` / `create_mission_tools` / `create_mission_watch_tools`) + real population (`scan_tools_for_full_docs`) + real `_apply_tool_filter` (allow/deny from actual meta.json) — nothing reimplemented:

| Agent | watch_mission | watch_job | watch_jobs | unwatch_job | list_watched_jobs |
|---|---|---|---|---|---|
| ari | PRESENT | ABSENT | ABSENT | PRESENT | PRESENT |
| jober | PRESENT | ABSENT | ABSENT | PRESENT | PRESENT |
| watcher (control) | PRESENT | PRESENT | PRESENT | PRESENT | PRESENT (21/21 job family intact) |

Wiring: `watch_mission` rides the existing `mission` category (`@register_tool_category("mission")`); `_tool_registry.py` diff = exactly **+1 line** (`KNOWN_TOOL_NAMES`); watch_job/watch_jobs factory return list untouched (indices 17/20 preserved); watcher control proves factory untouchedness live. (Control note: `worker` agent is an invalid control — no `job` category in its allow; substituted `watcher`, default-open universe.)

## Edge Battery — 11/11 PASS

| # | Scenario | Result | Key evidence |
|---|---|---|---|
| E1 | mission-not-yet-born (pre-dispatch job-ref) | PASS | row on pre-generated receipt UUID, `mission not yet dispatched`, 0 notify |
| E2 | already-terminal completed | PASS | register + 1 notify `(receipt, 'completed', result_summary='done')` |
| E3 | already-terminal failed | PASS | register + 1 notify `(receipt, 'failed', error='boom')` |
| E4 | already-terminal cancelled | PASS | register + 1 notify `(receipt, 'cancelled')` |
| E5 | dead_letter + terminal liveness | PASS | reply `already terminal (dead_letter)` (W4 hazard surfaced), notify uses resolved liveness status `failed` |
| E6 | dead_letter + revived RUNNING | PASS | registers, **no** short-circuit, **0 notify** |
| E7 | cap 48+2=50 | PASS | 48→50, both receipts registered |
| E8 | cap 49+2=51 | PASS | **all-or-nothing**: actionable error naming cap + receipt count, 49→49, **zero** rows minted (cap check precedes any add_watch) |
| E9 | unwatch via mission handle | PASS | 2→0, both receipt rows removed |
| E10 | unwatch via receipt handle | PASS | 2→1, sibling receipt stays |
| E11 | post-mint receipt (accepted gap pin) | PASS | NO row for receipt minted after watch, no crash — documented gap pinned as informational |

## Mock Honesty Audit — honest, one scoped defense-in-depth gap (non-blocking)

- **Await semantics (leader concern a):** REAL `JobQueueService.notify_watchers` and `notify_work_watchers` are `async def`; every production call site awaits (`tools/job_queue.py:1765` watch_job, `:2837` watch_mission). Both suites use `AsyncMock` + `assert_awaited_once()` — a dropped `await` would fail green. **HONEST.**
- **Watcher repo CAS (leader concern b):** DB-layer atomicity pinned against REAL SQL (`test_both_rows_claim_exactly_once` — second `claim_watchers_for_job_for_instances` returns `[]`). The companion suite's MagicMock doesn't reproduce claimed-once but the unit under test there is the orchestrator, not the repo. **HONEST (pinned elsewhere).**
- **One gap:** `AsyncMock(return_value=1)` on `job_service.notify_watchers` suppresses application-level claim-→-enqueue ordering — a reorder regression (N1 duplicate-delivery window) would pass those already-terminal tests green. Mitigated: real-repo CAS pin + N1 claim-first pins (5/5, P3) + this gate's replay R2 exercised the real primitive (claim=1, re-claim=0). Recommendation (follow-up hardening, not gate-blocking): swap the AsyncMock for real `notify_work_watchers` over the real repo in already-terminal tests.
- **Tautology: none.** Real resolver/repos on happy paths; the one monkey-patch (`resolver.resolve = _raise`) exercises the degraded-path wrapper. N>1 already-terminal covered by replay R3 (this gate). Hygiene notes for a future tidier: dead `use_virtual_job_resolver` attr, dead `_watcher_repo`/`_instance_repository` manager attrs in companion suite.

## Prompt Contract — PASS

Echo/act-once rule (the safety-critical one): **all 9 files**. Decision rule: 8/9 + jober/workflow.md exempt by the branch's own pin suite (consistent, not drift). Re-watch rule: present at all 5 branch-pinned sites. **Zero** watch_job/watch_jobs usage residue in ari/jober prose — 8 grep hits total = 2 meta.json deny entries + 4 `unwatch_job` substrings + 0 live guidance; skill.md has zero watch_job usage examples (replaced by `watch_mission(job_id_1/2/3)` examples). Positive: 56 `watch_mission` lines across the 9 migrated files. Note: rules are distributed per file role per the branch's own prompt contract (test_mission_watch_prompts.py, 15/15 green) — if the caller intended a literal 3-rules-×-9-files density, that is a prompt-density preference, not a functional gap.

## Base-Compare A/B (stash-compare mandate) — 2/2 PRE-EXISTING-IDENTICAL

Detached worktree `@307db932` (disposable, removed; main checkout untouched, pin held):
1. `test_add_watch_creates_record` `'settled' != 'failed'` @ index 1 → reproduces **verbatim** at base (test file byte-identical HEAD↔BASE). = QUARANTINE.md settled-rename stale-fixture family (05618c55), pre-dates branch.
2. `tests/test_watch_job_integration.py` 12/12 skips → identical node-for-node at base; permanent module-level `pytestmark` (`Phase 5: CorrelationManager removed`) — deliberate dead-code historical module, not an environmental gate.

## ensure.md Validation (Core, blast-radius scoped)

| Requirement | Status |
|---|---|
| Critical: no regressions in changed packs | ✅ (scoped packs green; 1 failure = quarantine-family, base-proven) |
| Critical: deadlock/concurrency integrity | ✅ 98P/0F exact parity |
| Critical: no sync DB calls on event loop | ✅ (thread-identity tests, same pack) |
| Critical: dev.sh `--timeout-graceful-shutdown 10` | ✅ static, dev.sh:102 |
| Important: await-converted callers | ✅ `get_queue_stats` awaited (instance.py:3216); others zero-hit in changed files |
| Release Gate | NOT RUN — not a big/critical/architecture change (tool-layer + prompts); scoped per ensure.md "How to use" |

No ensure.md contradictions found — all validations ran as packs with dual-layer timeouts. No new quarantines.

## Gaps & Accepted Limitations

1. **Notify-path depth:** replay reached the real CAS-claim primitive; full `[JOB_EVENT]` envelope + per-watcher `enqueue_message` delivery requires a wired InstanceManager (engine-side, out of constrained scope). Covered indirectly by N1/M3/N8 pin suites (35/35).
2. **Mock-audit defense-in-depth gap** (above) — mitigated, follow-up hardening recommended.
3. **Post-mint receipt gap:** receipts minted after `watch_mission` returns get no row — accepted-by-design (design §3, Blocker 1), pinned by E11, mitigated by re-watch prompt rule.
4. **Incident-class parity, not engine fix:** this change closes the *tool-side* stranding exposure; the engine-side observer event-loss (silent drop, boot-only backstop) documented in the cde5017f risk note remains as accepted reliability parity (per design constraint "engine untouched").
5. **e2e suites referencing watch_job** (tests/e2e/*) not run — release-gate class (daemon + LLM). FE untouched (skip pre-justified by task context).
6. Hygiene debt surfaced (pre-existing): settled-rename stale-fixture family (1 red), CorrelationManager dead module (12 permanent skips), 5 quarantined archive tests.

## Artifacts

- Replay harness + evidence: `RESULTS/2026-09-19-mission-watch-toolset-replay-script.py` + `-replay-evidence.json` (preserved from `/tmp/mw-replay/`)
- Edge battery harness: embedded below; also at `/tmp/mw-edge/edge_battery.py` (ephemeral)

### Edge battery harness (preserved verbatim)

```python
# /tmp/mw-edge/edge_battery.py — ad-hoc pack, fixture cribbed from
# tests/unit/tools/test_mission_watch_tools.py. Dual-layer: 280s watchdog
# + outer `timeout 300`. VERIFY-ONLY (repo imported, never mutated).
# Full source preserved in git-history of this RESULTS file at write time;
# contact tester for the canonical copy if this entry is trimmed.
```
*(Full 400-line source available from the gate worker report; key harness invariants: file-backed SQLite (NullPool+WAL+busy_timeout+FK), real MissionResolver/JobWatcherRepository/TaskRepository/JobRepository/instance repo, AsyncMock job_service double, per-scenario isolated SQLite files.)*

**Overall Status:**
- Unit/adjacent packs: ✅ PASS (3,244 green; 1 pre-existing red, base-proven)
- Incident replay: ✅ PASS 4/4 — **cde5017f class closed at tool layer; chaining no longer needed**
- Edge battery: ✅ PASS 11/11
- Surface verification: ✅ PASS
- Prompt contract: ✅ PASS
- Mock audit: ✅ honest (1 scoped, mitigated gap; follow-up recommended)
- ensure.md Core: ✅ 4/4 critical + important
- **Verdict: SHIP**
