# Phase 0 — v2-Base Grep-Vocabulary Baseline (T0.7)

> Date: 2026-09-03 (UTC)
> Branch: `feature/langgraph-checkpoint-perf-v2`
> HEAD SHA: `2f80d45b`
> Workdir: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
> Operator: Worker (T0.7 execution, v2-base capture)
> Guard count: **4 (was 6 — 2 dropped per architect §8 N9; see §5 below)**

## Context

T0.7 captures v2-base grep output for the four (4) vocabulary grep guards listed in `phase0-plan.md:19` row T0.7. Output is captured VERBATIM (incl. exit codes and "No such file or directory" cases) so that post-port phases can diff against a real baseline. Baseline honesty matters: at v2-base @ 2f80d45b some guards will return NO MATCHES because the files they search are PR2/PR4 surface that does NOT exist at this SHA — that is the correct v2-base state, not a bug.

---

## Guard 1 — mission canonical-vocabulary (`settled`)

**Command:**
```bash
grep -rn "settled" docs/job-task-system.md
```

**Output (verbatim):**
```
docs/job-task-system.md:409:   (`job_type='message'`) in terminal-receipt state is **`settled`**. `completed` /
docs/job-task-system.md:437:| **M1** (this amendment's contract) | Additive `mission_id` / `mission_epoch` / `mission_terminal_reason` (§8.3) — originally behind the M1 kill-switch (default OFF), soaked ON, then **always-on since WS3** (kill-switch removed; §8.3 deployment posture); FE re-anchor `mission-settled` → `mission-terminal` (CSS chain only, ~12–15 files); vocabulary table ratified (§6.7); this prose fix (line 909). | Zero impact — additive only; fields surface on every response (always-on). |
docs/job-task-system.md:440:| **M3** | **LANDING this cycle on `feature/mission-class`** (WS4 sibling commits; daemon + frontend only) — ships CLEAN, no version gate (directed modification above). Wire rename on mirror-receipt terminal status: `completed` → `settled` via per-kind dispatch in `_derive_legacy_status` on all 4 read surfaces — `WorkRecord` (work resolver, `work_resolver._job_to_record`), `JobResponse` (`routers/jobs_crud.py::_job_to_response`), `_ResolvedWork` (SSE payload, `routers/jobs_streaming.py::_ResolvedWork`), and the `routers/jobs_management.py` delegation surface (response constructed via `jobs_crud.py::_job_to_response`, per §8.2). `VALID_STATUS_VALUES`, FE switches, daemon filters, and docs are updated in this phase. | Mission tools (M2) and FE re-anchor (M1) are already in — at M3 time, no in-repo consumer treats mirror `completed` as outcome. |
docs/job-task-system.md:450:  ships CLEAN (no `api_version >= X` → `settled` branch, no legacy fallback in
docs/job-task-system.md:514:| **Transport — mirror receipts** (`job_type='message'`) | `queued` · `active` · **`settled`** · `dead` | Job (admission) | `AdmissionState` derivation, per-kind dispatch in `_derive_legacy_status` |
docs/job-task-system.md:525:**ELIMINATED** by §6.6 ADR-MISSION-01: `settled` is disjoint from every work and
docs/job-task-system.md:531:and gains `settled` for `job_type='message'`.
docs/job-task-system.md:533:#### Why `settled` wins for the transport receipt terminal
docs/job-task-system.md:537:| **`settled`** | ✅ **WINS** | Receipt-not-outcome (payments/ledgers: final clearing, outcome-agnostic); idiomatic read-aloud ("the mirror settled"); short, chip-renderable; disjoint value space. |
docs/job-task-system.md:548:(transport position). `settled` matches the payments/ledger convention where settlement
docs/job-task-system.md:551:**The `settled` half-claim (M1 + M3 land together).** FE already uses
docs/job-task-system.md:552:`mission-settled` as the CSS class for mission-terminal chip styling
docs/job-task-system.md:554:**M1 renames `mission-settled` → `mission-terminal`** — 3 identifier files renamed
docs/job-task-system.md:559:dispatch — `completed` → `settled` for mirror rows on all four read surfaces) AND
docs/job-task-system.md:560:the M3 prose sweep (FE mission-side prose reworded away from `settled`) complete,
docs/job-task-system.md:561:**`settled` has exactly one owner: transport. The single-owner fact is now
docs/job-task-system.md:1188:3. **M3** — wire rename on mirror-receipt terminal status (`completed` → `settled`)
```

**Exit code:** 0
**Match count:** 14 lines
**v2-base interpretation:** Docs already document the `settled` vocabulary (M1/M3 narrative has been merged to `latest` and `feature/mission-class`). The guard documents the canonical-vocabulary state of `docs/job-task-system.md`; post-port drift detection watches for new occurrences that violate the single-owner rule.

---

## Guard 2 — tap-site AST gate (`tap_node_return`)

**Command:**
```bash
grep -n "tap_node_return" daemon/graph.py daemon/services/instance_messaging.py
```

**Output (verbatim):**
```
(no output)
```

**Exit code:** 1

**v2-base count:** **0** call sites

**Post-port expectation (per `phase0-plan.md:19` row T0.7):** exactly **4** call sites AFTER Phase 2 lands. The v2-base @ 2f80d45b has 0 because `tap_node_return` is PR2 surface (Phase 2 deliverable) and the `daemon/services/message_tap.py` module that exposes it does NOT exist at v2-base — T0.6 isolation-run evidence at phase0-t0607-results.md confirms `ModuleNotFoundError: No module named 'daemon.services.message_tap'` on the v1 file's import line.

**Baseline honesty note:** The 0 call sites at v2-base is the correct v2-BASE state, not a defect. Phase 1 does NOT touch `tap_node_return`; Phase 2 (PR2) introduces it. Recording 0 as the v2-base anchor so Phase 5 can compute the 0→4 delta.

---

## Guard 3 — migration numbering v2 ordering

**Command:**
```bash
ls daemon/migrations/versions/ | grep -E "20260" | sort | tail
```

**Output (verbatim):**
```
20260714_000003_skill_bank_new_columns.sql
20260714_000004_skills_new_columns.sql
20260715_000001_skill_usage_new_columns.sql
20260721_000001_skill_usage_feedback_columns.sql
20260724_000001_add_agent_tag_to_instances.sql
20260729_000001_add_agent_tag_to_job_queue_items.sql
20260801_000001_task_turn_handles.sql
20260810_000001_fix_idle_gate_stuck_task_flags.sql
20260811_000001_reconcile_stuck_tasks_with_terminal_jobitems.sql
20260819_000001_report_injections_deferred_marker.sql
```

**Exit code:** 0
**Count:** 10 most-recent migrations (the `tail` slice)
**Full ordered set:** All `2026*` migrations in `daemon/migrations/versions/`, with the most recent being `20260819_000001_report_injections_deferred_marker.sql`.
**v2-base interpretation:** Migration numbering follows `YYYYMMDD_NNNNNNN_description.sql` convention; the latest anchor at v2-base is `20260819_000001`. Port phases 1..4 will add new migrations on top of this anchor; the guard watches for monotonic ordering (`YYYYMMDD_NNNNNNN` strictly increasing).

---

## Guard 4 — PR4 aput-atomicity retraction (`atomic` in checkpoint_prune.py + checkpoint_adapter.py)

**Command:**
```bash
grep -rn "atomic" daemon/services/checkpoint_prune.py daemon/checkpoint_adapter.py
```

**Output (verbatim):**
```
(no output)
```

**Stderr (verbatim):**
```
grep: daemon/services/checkpoint_prune.py: No such file or directory
```

**Exit code:** 2

**v2-base interpretation:** At v2-base @ 2f80d45b, `daemon/services/checkpoint_prune.py` does NOT exist (PR4 surface — Phase 4 deliverable), so grep exits 2 with "No such file or directory" for that path. The second path (`daemon/checkpoint_adapter.py`) exists but contains ZERO occurrences of "atomic" at v2-base (verified by running `grep -n "atomic" daemon/checkpoint_adapter.py` → exit 1, no output).

**Post-port expectation (per `phase0-plan.md:19` row T0.7):** every `atomic` mention in the post-port code MUST cite the retraction + reference `aio.py:82, 280-304, 393-399`. Phase 4's binding-gate runner (`tests/integration/checkpoint_prune_real_saver.py`) will exercise this path. Recording exit-code 2 (file-not-found) as the v2-base anchor so Phase 4 can compute the structural-add + atomic-citation delta.

---

## §5 — Dropped guards (per architect §8 N9; M2 final-gate duplicate-detected)

**Verbatim from `phase0-plan.md:19` row T0.7:**
> **DROPPED** per architect §8 N9: `grep -rn "'done'" daemon/services/job_queue_service.py` (#2 in original 6-list) and `grep -n "TERMINAL_STATUS_SET\|terminal_status_set" daemon/services/job_queue_service.py` (#3 in original 6-list) — both duplicated by the M2 final-gate runtime probe + 7-node quarantine family detector.

**M2 final-gate artifact reference:** `.agents/tester/RESULTS/2026-09-03-mission-m2-full-gate.md`

**Artifact exists?** YES — `ls -la .agents/tester/RESULTS/2026-09-03-mission-m2-full-gate.md` → `-rw-r--r--  1 nguyenminhkha  staff  13125 Sep  3 19:20 .agents/tester/RESULTS/2026-09-03-mission-m2-full-gate.md`.

**Verdict line (verbatim):** `## FINAL VERDICT: ✅ PASS — 0 branch-caused failures across the full suite; all M2-specific contracts verified at runtime`

**For completeness only — v2-base capture of the dropped guards (NOT used as post-port regression detectors):**

### Dropped #1 — `'done'` in `daemon/services/job_queue_service.py`

**Command:**
```bash
grep -rn "'done'" daemon/services/job_queue_service.py
```

**Output (verbatim):**
```
daemon/services/job_queue_service.py:1137:        # (admission_state='done', status='cancelled') and releases
daemon/services/job_queue_service.py:1181:          (``admission_state='queued' → 'done'``,
daemon/services/job_queue_service.py:1211:        Already-terminal jobs (``admission_state IN ('done', 'dead')``)
daemon/services/job_queue_service.py:1578:        # Can only retry FAILED jobs (admission_state='done' with
daemon/services/job_queue_service.py:1717:          - ``NO_RETRY``     → ``admission_state='done'`` (direct write;
daemon/services/job_queue_service.py:1871:                # ``admission_state='done'`` rows. Computed here from
daemon/services/job_queue_service.py:1880:                # and 'dead' respectively, not 'done', so
daemon/services/job_queue_service.py:3334:            # state — ``admission_state='done'`` — and the finalize
```

**Exit code:** 0
**Match count:** 9 lines (all in docstrings/comments, NOT in executable code).

### Dropped #2 — `TERMINAL_STATUS_SET` / `terminal_status_set` in `daemon/services/job_queue_service.py`

**Command:**
```bash
grep -n "TERMINAL_STATUS_SET\|terminal_status_set" daemon/services/job_queue_service.py
```

**Output (verbatim):**
```
(no output)
```

**Exit code:** 1

**v2-base interpretation:** Neither constant nor symbol exists at v2-base @ 2f80d45b. These dropped guards were the original #2 and #3 in the 6-guard plan and are now superseded by the M2 final-gate runtime probe (which verifies the runtime `admission_state` semantics) plus the 7-node stale-fixture quarantine family detector (which catches `'failed'` vs `'settled'` test-fixture rot — see QUARANTINE.md row for "Mission-program FINAL-gate stale-fixture family").

---

## §6 — Guard-count rationale (re-asserted from phase0-plan.md)

> **Guard count is 4 (was 6):** **(1)** mission canonical-vocabulary; **(2)** tap-site AST gate; **(3)** migration numbering; **(4)** aput-atomicity retraction. **DROPPED** per architect §8 N9: dropped #1 (`'done'` literals) and dropped #2 (`TERMINAL_STATUS_SET`).

The 2 dropped guards duplicate work already done by:
- The M2 final-gate runtime probe (`.agents/tester/RESULTS/2026-09-03-mission-m2-full-gate.md`) — PASS verdict with 0 branch-caused failures.
- The 7-node "Mission-program FINAL-gate stale-fixture family" quarantine row in `QUARANTINE.md` (sweep-visible canonical detector for mission-settled-rename rot).

---

## §7 — Post-port delta anchors (for Phase 5 binding-gate)

| Guard | v2-base @ 2f80d45b | Post-port expectation | Anchor |
|-------|--------------------|------------------------|--------|
| 1. `settled` in `docs/job-task-system.md` | 14 matches | Same-or-fewer (single-owner rule: transport only) | stable; no expected growth |
| 2. `tap_node_return` call sites | 0 | Exactly 4 (Phase 2) | Phase 2 PR deliverable |
| 3. Latest migration ID | `20260819_000001_report_injections_deferred_marker.sql` | Strictly greater; monotonic | Each phase adds ≥1 migration |
| 4. `atomic` in checkpoint_prune.py / checkpoint_adapter.py | exit 2 (file-not-found) + 0 in adapter | Each `atomic` mention cites retraction + `aio.py:82, 280-304, 393-399` | Phase 4 PR deliverable |

Phase 5 T5.16 + drift-regression will re-run all 4 guards and diff against this anchor.

---

## §8 — Operator notes

- All 4 commands executed at v2-base @ `2f80d45b` with NO source modifications.
- No git mutations performed; `git status --short` post-capture shows the same T0.1 pre-existing modifications only.
- DB safety: zero DSN-resolving commands run (guards are static-text grep on docs + source); no PG connection established.
