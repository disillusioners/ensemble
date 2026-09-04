# Defer-Gate Post-Settle Window — Recommendation & Implementation Outline

**Date:** 2026-09-03 · **Status:** SHIPPED @ 81e8d247 (Phase-1 REDs flipped GREEN + Phase-2 widening + shared-SQL-body constants + drift guard + self-deadlock pin + folding proof + PG/SQLite parity); see commits `853abb1b` … `81e8d247` on branch `fix/defer-gate-post-settle-window` · **Ranked alternatives:** see `solution-options.md`

---

## 1. THE RECOMMENDATION

> **Widen the job-side idle predicates (defer + background) to treat a settled mirror of a non-terminal instance as BUSY, implement the two predicate bodies as shared SQL-text constants in one new module consumed by all five gate/maintenance sites, and leave the claim-folding SQL untouched — the folding "hole" is fixed at the gate (admission-time), per the 2026-07-23 two-leg architecture, with a RED proof-test demonstrating the gate catches what the claim cannot.**

This is option #1 in `solution-options.md`: **C's sharing mechanism carrying B's tested busy-set semantics under A's instance-liveness truthmaker.** It answers the user's standing question — "do queues respect mission status" — with **yes, by construction**: the gate now consults exactly the Mission projection's liveness truthmaker (`Instance.status` non-terminal = mission live).

## 2. The busy-set (exact semantics)

A project is **busy** (defer gate, project-scoped) iff ANY non-deleted JobItem in the project on a non-defer queue satisfies:

1. **Legacy clause (unchanged):** `admission_state='active'` AND (no linked instance OR instance non-terminal), OR
2. **Post-Fix-B clause (the fix):** `job_type='message' AND admission_state='done' AND instance_id IS NOT NULL AND instance.status NOT IN ('completed','error','terminated','failed')`.

Background gate: identical body modulo the **defer vs background legacy-clause asymmetry** — the defer body counts `admission_state = 'active'` only, while the background body counts `admission_state IN ('queued','active')` (a `queued` non-background job with no instance yet would otherwise leak past the system-wide gate). The background body excludes ONLY `queue_type = 'background'` (`('background',)` per commit `6e8a597a`) — NOT `('defer','background')` — for the 2026-07-23 defer-leak fix; see the per-axis asymmetry bullet below.

Pinned semantics: PAUSED instance → busy (holds by-design, `7ecf09e2`); WAITING_CHILDREN → busy (the defect scenario); terminal instance → idle (baseline GREEN #2 preserved); NULL-instance jobs pass through to Leg 2 (task-side, unchanged); self-deadlock excluded structurally (candidate's own row sits on the defer queue, excluded by queue-type filter).

## 3. Implementation outline (single PR on the fix branch)

**New module** `daemon/repositories/job_queue/_idle_predicate_sql.py` (~80 lines):
- `JOB_TERMINAL_STATUSES = ('completed','error','terminated','failed')`
- `JOB_DEFER_BUSY_BODY` and `JOB_BACKGROUND_BUSY_BODY` — the two SQL bodies above, as `text()`-compatible constants (parameter-bound, no f-string interpolation of values).
- Docstring: the I3 clarifying line — *"a settled mirror of a non-terminal instance counts as live for the defer/background gate, terminal for everything else."*

**Widen** (bodies replaced by the constants; signatures unchanged):
- `JobQueueRepository.has_active_non_deferred_work` (`repository.py:700-768`) — project-scoped.
- `JobQueueRepository.has_active_non_background_work` (`repository.py:770-893`) — system-wide sister (`:863` hole closed).

**Repoint (no code change — call sites resolve to the widened predicates):**
- Gate A: `_defer_idle_check` (`job_processor.py:213`) + `_background_idle_check` (`:338`).
- Gate B: `_select_next_eligible_job` defer + background branches (`job_queue_service.py:2696`, ~2786-2901).
- Maintenance: `_is_idle` (`maintenance.py:250-307`, system-wide).

**NOT touched:** `claim_pending_task` folding SQL (`task/repository.py:1357-1371`) — stays the task-granular atomic race-guard per plan §4. Leg 2 task predicates unchanged.

**Docs:** `docs/job-queue.md` defer/background idle semantics section + one-paragraph ADR-style note in the job-task-retrospective `decisions.md` (I3 clarifying line; read-only predicate change; no amendment trigger — no writer, no stored-state meaning change).

**Diff:** ~200 LOC (80 constants + ~50 widening ×2 predicates + tests). No migration. No FE.

## 4. Test plan

| Test | Source | Expectation |
|---|---|---|
| `test_leg1_job_predicate_with_settled_mirror_and_live_instance` | committed RED @853abb1b | → GREEN |
| `test_full_gate_with_settled_mirror_and_no_tasks_blocks` | committed RED | → GREEN |
| `test_background_gate_also_affected_when_settled_mirror_is_global` | committed RED | → GREEN |
| `test_defer_idle_check_probe_path_with_settled_mirror` | committed RED (probe) | → GREEN |
| `test_gate_b_select_next_eligible_with_settled_mirror` | committed RED (probe) | → GREEN |
| `test_leg2_task_predicate_with_no_tasks_present` | committed GREEN baseline | stays GREEN (Leg 2 unchanged — Task IS completed) |
| `test_baseline_settled_mirror_of_terminal_instance_is_idle` | committed GREEN | stays GREEN (terminal filter) |
| `test_baseline_active_mirror_of_live_instance_blocks` | committed GREEN | stays GREEN (legacy clause) |
| **NEW: `test_post_settle_admission_gate_catches_what_claim_cannot`** | **the mandatory folding RED scenario** | defer candidate pending; parent message Task COMPLETED; instance `waiting_children`; mirror `done`. Assert: Gate B `_select_next_eligible_job` returns None (gate blocks). Also assert the claim-guard's t2 correctly finds no active task — documenting that the claim clearing is CORRECT behavior, not a hole. |
| NEW: `test_sql_body_shared_constant` | drift paranoia | assert the constant bodies appear in both repository predicates (guards the 2-SQL-site defer/background parity). |
| NEW: `test_defer_candidate_own_live_instance_does_not_self_deadlock` | A's guard pin | defer candidate whose own instance is `waiting_children` admits (queue-type exclusion holds). |
| NEW: `test_idle_predicate_pg_sqlite_parity` | dual-driver knob | same return on both engines (boolean-bind pattern). |

## 5. Deferred / follow-up tickets (named, out of this branch)

1. **Claim-time belt-and-suspenders (optional hardening):** A's in-SQL instance-liveness EXISTS inside `claim_pending_task` with self-deadlock bind — closes the narrow residual TOCTOU (gate-check straddling a full short message turn). Requires EXPLAIN validation on the claim hot path; A's own risk rating is HIGH. Ship only if the TOCTOU materializes or the user wants defense-in-depth now.
2. **Dead-mirror scenario** (`admission_state='dead'` + live instance): write the scenario test; if reachable, widen the shared constant by one term (1-line change under this design).
3. **Always-on empirical check** (pre-merge, cheap): confirm experiencer/kb-importer/explorer `job_queue_items.instance_id` rows do not cross-join into other projects' defer candidates (starvation guard for the system-wide background gate).
4. **Zombie reaper `_has_live_work`** as 6th consumer of the shared predicate (cleanup).
5. **datetime DeprecationWarnings** (reviewer advisory W2, 2026-09-03): the defer-gate tests emit `DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12` from `sqlalchemy.engine.default` at line 952. Source is `datetime.now(timezone.utc).isoformat()` writes that flow into SQLite text columns — SQLite has no native datetime so SQLAlchemy emits a deprecation when binding back. Not a behavior defect today; replace with explicit adapter calls or `datetime.now(timezone.utc).strftime(...)` if/when Python 3.13/3.14 raises the warning to error. Tracked out-of-branch; cost is 2-3 LOC across the four `_insert_*` helpers if/when adopted.
6. **Historical count addendum** (reviewer advisory W2, 2026-09-03): the docstring preamble at `tests/job_queue/test_defer_gate_post_settle_window.py` line 50 declares "Census stays 23" — accurate at Phase-1, but the Phase-2 commit series (widening + shared-body constants + drift guard) is read-only (no new admission_state writers, no JobItem mint sites), so the count remains 23 post-merge. The Phase-0 constitution drift test (`tests/unit/job_state/test_constitution_drift.py::test_known_admission_state_writers_matches_source_exactly_no_drift`) is the live gate; cross-check it green before claiming "census 23" elsewhere in the docstring/commentary.

## 6. Census & constitution notes

- **Census 23 unchanged** — read-only predicate widening; zero new admission_state writers; Phase-0 census sets untouched.
- **No amendment trigger:** no new writer, no stored-state meaning change, no sweep predicate re-scope (the gates are not sweeps). The I3 clarifying line is documentation, not amendment.
- **Mission-program alignment:** the busy-set's truthmaker is exactly `mission_liveness` (Mission M1 projection). If M4(ii) ever ships (`mission_events` storage), the gate predicate swaps truthmaker mechanically — same shape as the Mission package's upgrade-path promise.

## 7. Why this beats the alternatives (short form)

- **vs A (inline instance-liveness + claim EXISTS):** same truthmaker, but A modifies the most concurrency-sensitive SQL in the daemon (its own HIGH risk self-score) and partially re-defines idle at the claim layer against the 2026-07-23 two-leg intent. This fix leaves the claim untouched and still closes every committed RED test.
- **vs B standalone:** B's own verdict — it cannot reach the claim seam and is "not a standalone fix." Its narrow busy-set survives INSIDE this recommendation (adopted as the tested Leg-1b clause), so B's ~30-LOC economy is preserved within a complete fix.
- **vs C-full (PR2):** PR2 interpolates constants into the atomic claim — zero behavioral gain under the gate-time diagnosis, pure refactor with f-string risk on the hottest path. Demoted to optional follow-up; C itself concedes the degenerate case is "fine."
