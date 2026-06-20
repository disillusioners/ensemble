# Completion Authority

> **Status:** Authoritative for the post-Phase-A architecture (2026-06-20).
> **Owns:** The decision "is this parent's correlation complete?".
> **Companion doc:** [`docs/configuration/completion-flags.md`](../configuration/completion-flags.md) — feature-flag semantics, interaction matrix, recommended settings, and triage decision tree.
> **Companion code:** [`daemon/services/correlation_manager.py`](../../daemon/services/correlation_manager.py) — CorrelationManager (CM).
> **Related ADRs:** ADR-011 (`waiting_for` deprecated as control-flow, retained as rebuild cache).

---

## 1. Overview

A parent instance is **complete** when every child it sent a message to has reported back (with or without error). Three mechanisms have, at various times, been used to answer this question. The architecture has consolidated to one — the **CorrelationManager (CM)** — with the other two retained as a kill switch and a graceful-degradation fallback, both behind `USE_LEGACY_WAITING_FOR_CASCADE`. This document is the single source of truth for which mechanism owns completion in which setting, where each lives in code, and the invariant that makes the premature-completion bug class structurally impossible.

---

## 2. The Three Authorities

| # | Authority | Location | Active when | Strengths | Weaknesses |
|---|-----------|----------|-------------|-----------|------------|
| 1 | **CorrelationManager (CM)** | `daemon/services/correlation_manager.py` | `USE_LEGACY_WAITING_FOR_CASCADE=OFF` (default). CM is initialized (`get_correlation_manager() is not None`). | Pure in-memory set ops; no DB query in the completion hot path; per-parent `asyncio.Lock` serializes register/resolve; no TOCTOU window. | In-memory only — needs `rebuild_from_db()` to recover after crash. |
| 2 | **`waiting_for` counter (legacy cascade)** | `daemon/services/{child_reports,error_reporting}.py`, `daemon/tools/instance.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/job_feedback_observer.py` | `USE_LEGACY_WAITING_FOR_CASCADE=ON` (kill switch). | Backward-compatible with the pre-Phase-0-5 implementation; tested by A14. | Vulnerable to Race #3 TOCTOU between `SELECT COUNT(*)` and the cascade UPDATE; reachable **only** under the kill switch. |
| 3 | **`SELECT COUNT(*)` fallback (graceful degradation)** | `daemon/services/child_reports.py:657` (deferred-cascade path), `daemon/services/job_feedback_observer.py:544` | `USE_LEGACY_WAITING_FOR_CASCADE=ON` **AND** CM is `None`. | Keeps the daemon running if CM fails to initialize under the legacy flag. | Contains the Race #3 TOCTOU bug class. **Hard error** (A8) when `OFF` + CM `None` — fallback is not reachable under the new path. |

---

## 3. The Invariant

> **When `USE_LEGACY_WAITING_FOR_CASCADE=OFF`, the CorrelationManager is the sole completion authority. The premature-completion bug class is structurally impossible.**

The invariant is enforced by:
- A4–A7: every control-flow `waiting_for` SQL read/write site is gated behind the flag.
- A8: the `SELECT COUNT(*)` fallback throws (hard error) if CM is `None` while the flag is OFF.
- A11: a CI invariant test pack fails the build if a new control-flow read of `waiting_for` is added without a corresponding flag gate or a documented cache-only rationale.
- A3: runtime divergence logs (`CM_WAITING_FOR_DIVERGENCE`) when CM's pending count disagrees with the DB `waiting_for` snapshot, gated by `DEBUG_COMPLETION_INVARIANT`.

### Honest characterization of the A8 "hard error"

The A8 RuntimeError is the **invariant enforcer** — the act of throwing
is what makes the premature-completion bug class structurally
impossible (because the only code path that would have produced it is
short-circuited). The RuntimeError does NOT need to crash the process
to enforce the invariant; it only needs to **not** fall through to the
`SELECT COUNT(*)` TOCTOU fallback.

In production the RuntimeError is caught by the W2/W3 fail-safe at
`_finalize_job` (`daemon/services/job_feedback_observer.py:904-947`)
or by broader `except Exception` handlers one frame up. The user-
visible result under `OFF` + CM uninitialized is **per-job FAILED**
(or, on the CM-callback path, **logged + restored for retry**), not a
process crash. This is intentional fail-safe behavior — see
[`docs/configuration/completion-flags.md`](../configuration/completion-flags.md)
§"Honest characterization of the A8 hard error" for the full
propagation table.

---

## 4. Feature Flag Interaction Matrix

See [`docs/configuration/completion-flags.md` §"Interaction Matrix"`](../configuration/completion-flags.md#interaction-matrix) for the full matrix, valid combinations, and the **`OFF` + CM-uninitialized hard error** table. Summary:

| `USE_LEGACY_WAITING_FOR_CASCADE` | `DEBUG_COMPLETION_INVARIANT` | Mode |
|----------------------------------|-------------------------------|------|
| `OFF` | `OFF` | **Production steady state.** CM authoritative, no observability overhead. |
| `OFF` | `ON`  | **Dev / CI / first 2 weeks post-release.** CM authoritative + divergence logs. |
| `ON`  | `OFF` | **Pure rollback.** Legacy path, no observability overhead. |
| `ON`  | `ON`  | **Operational triage on legacy.** |
| `OFF` | —     | **+ CM not initialized → HARD ERROR** at the gated call site (A8). Graceful degradation with the kill switch OFF is unsupported. |

---

## 5. Call Sites Reading `waiting_for`

Every `waiting_for` reference in the daemon, categorized. The categories are exhaustive: **(G)** gated control-flow (A4–A8), **(C)** cache-only (ADR-011 rebuild, retained), **(D)** display-only (response payloads, logs), **(B)** build/schema (column definition, repository writer).

### 5.1 Gated — control-flow (A4–A8)

| File | Lines | Purpose | Gated by | Task |
|------|-------|---------|----------|------|
| `daemon/services/child_reports.py` | 486–557 | Decrement `waiting_for` on child completion (success path) | `USE_LEGACY_WAITING_FOR_CASCADE` | A4 |
| `daemon/services/child_reports.py` | 634–680 | Cascade decision (`waiting_for == 0` → finalize parent) + `SELECT COUNT(*)` fallback | Flag + A8 throw | A4 + A8 |
| `daemon/services/child_reports.py` | 1307–1390 | Decrement + cascade on the deferred-cascade path (error reports) | Flag | A4 |
| `daemon/services/error_reporting.py` | 190–280 | Error-path decrement + cascade | Flag | A4 |
| `daemon/tools/instance.py` | 566–760 | `send_message` increment + M0 parent-revive `UPDATE` | Flag | A5 |
| `daemon/services/instance_lifecycle.py` | 893–960 | Pause/resume `waiting_for` reset | Flag | A6 |
| `daemon/services/job_feedback_observer.py` | 1230–1395 | `SELECT ... FOR UPDATE` row-lock gate (M0 band-aid) | Flag | A7 |
| `daemon/services/job_feedback_observer.py` | 525–558 | Legacy terminal-check fallback (CM is `None` + flag `ON`) | Flag | A9 |
| `daemon/services/job_processor.py` | 147–244 | Graceful-deg `in_progress` re-emit (CM `None` + flag `ON`) | Flag | A9 |
| `daemon/services/message_job_handler.py` | 440–553 | Graceful-deg watcher notification (CM `None` + flag `ON`) | Flag | A9 |
| `daemon/manager.py` | 2980–2991 | Graceful-deg check (CM `None` + flag `ON`) | Flag | A9 |

### 5.2 Cache-only — rebuild (ADR-011, retained)

These reads drive `rebuild_from_db()` and are **deliberately not gated** — the column is the source of truth for the rebuild query. They are not control-flow.

| File | Lines | Purpose |
|------|-------|---------|
| `daemon/services/correlation_manager.py` | 662–869 | `rebuild_from_db()` — selects parents with `waiting_for > 0` and reconstructs `_pending` |
| `daemon/services/correlation_manager.py` | 871–900 | `_validate_shadow_mode` — shadow-mode divergence logging |
| `daemon/repositories/instance/repository.py` | 536–548 | `get_all_with_waiting_for()` — rebuild query |
| `daemon/repositories/instance/repository.py` | 653–663 | `update_waiting_for()` — low-level writer; **callers are gated sites only** |

### 5.3 Display-only (backward compat)

| File | Lines | Purpose |
|------|-------|---------|
| `daemon/routers/instances.py` | 194 | Echoed in HTTP API response payload |
| `daemon/services/job_queue_service.py` | 197, 205, 251, 252 | Notification text ("Waiting for: N child agent(s)") |
| `daemon/services/job_processor.py` | 90, 244 | `in_progress` event payload (`waiting_for` field) |
| `daemon/services/message_job_handler.py` | 553 | Watcher notification payload |
| `daemon/services/child_reports.py` | 974, 1147 | Log lines (informational snapshot) |
| `daemon/services/instance_lifecycle.py` | 107, 469, 524, 806, 897, 901, 903, 909, 941, 944, 954 | Pause/resume bookkeeping (some gated — see 5.1) |

### 5.4 Build / schema

| File | Lines | Purpose |
|------|-------|---------|
| `daemon/models/instance.py` | 50 | Pydantic field |
| `daemon/repositories/instance/models.py` | 79 | SQLModel column |
| `daemon/repositories/instance/models.py` | 107 | `to_dict()` for serialization |
| `daemon/migrations/versions/20260412_000003_enhance_instance_for_worker_pool.sql` | — | Schema migration |

### 5.5 Documentation / comment-only

`daemon/api.py:361`, `daemon/services/message_processing_pipeline.py:197, 210`, `daemon/services/child_reports.py:49, 86, 88, 466, 469, 785, 798, 822, 1020, 1034, 1042, 1053, 1055, 1104, 1110, 1307, 1453, 1477, 1480, 1504`, `daemon/services/job_feedback_observer.py:35, 86, 145, 146, 416, 461, 582, 594, 689, 694, 1290, 1445`, `daemon/manager.py:2254, 2257, 2277, 2290, 2296, 2307, 2309`, `daemon/services/instance_lifecycle.py:62`, `daemon/services/job_feedback_observer.py:1294`, `daemon/services/child_reports.py:1483`. Docstring/comment references; no runtime impact.

---

## 6. `DEBUG_COMPLETION_INVARIANT`

The observability companion to the invariant. When `ON`:
- `CorrelationManager` runs `_validate_shadow_mode()` after every `register_message_send` and `resolve_response` (lines 374, 389 of `correlation_manager.py`).
- It reads the DB `waiting_for` snapshot for the parent and compares it with CM's `_pending[parent_id]` count.
- On mismatch, it emits a structured warning with `event=CM_WAITING_FOR_DIVERGENCE` and fields `parent_id, child_id, message_id, cm_pending_count, db_waiting_for` (correlation_manager.py:658).
- Logging is rate-limited (default 100/min) and never blocks completion. It is observability, not enforcement.

### Triage decision tree

See [`docs/configuration/completion-flags.md` §"Triage Decision Tree"`](../configuration/completion-flags.md#triage-decision-tree-when-cm_waiting_for_divergence-fires). Summary:
1. `cm_pending > db_waiting_for` → CM has a correlation DB missed (registration race). Usually self-heals.
2. `cm_pending < db_waiting_for` → DB has a `waiting_for++` without a CM entry. Indicates legacy path still running, or a CM registration failure that rolled back the increment.
3. `OFF` + divergence > 10/hour → **flip kill switch ON**, follow `[W6]` caveats, file an incident.
4. `ON` + divergence → expected; ignore unless it correlates with a user-visible regression.

There is no "middle state" between ignoring the warning and a full rollback. If neither is acceptable, revert the PR.

---

## 7. Crash Recovery — `rebuild_from_db()` Contract

The sole mechanism for reconstructing CM state after a daemon restart. Full contract in `correlation_manager.py:662–765`. Architectural summary:

| Step | Action |
|------|--------|
| 1 | `self._pending = {}` (W2 fix: top-level **OVERWRITE** — stale entries are wiped). |
| 2 | Single query for all parents with `waiting_for > 0` (`get_all_with_waiting_for`). |
| 3 | Per parent: read children (`get_children`) + batched pending messages across all children (`get_pending_for_instances`) — 1 + P + 1 queries, not 1 + P + P·C·3. |
| 4 | For each parent, acquire per-parent `asyncio.Lock`; **MERGE** DB-backed `(child, msg)` pairs into existing `_pending[parent_id]` (or create fresh `ParentCorrelation`). The MERGE preserves a concurrent `register_message_send` that landed after the top-level clear. |
| 5 | Per-parent log: `cm_count` vs `db_waiting_for`. Mismatch → `WARNING: CM rebuild mismatch: parent=X, DB waiting_for=Y, CM found=Z`. Match → `DEBUG`. |

**Orphan count** (zero children, `waiting_for > 0`): CM tracks nothing; mismatch warning fires. The CM cannot fabricate entries that aren't there. External recovery code is responsible for reconciling (clear the stale `waiting_for` or terminate the wedged parent).

**Concurrency**: `start()` is the only production caller; it runs once at startup before any EventBus traffic. Do not call `rebuild_from_db()` from inside a register/resolve callback.

---

## 8. ADR-011 Reference

> **The `waiting_for` column is deprecated as control-flow. It is retained as a rebuild-only cache for `rebuild_from_db()`.**

Implementation rules (enforced by the A11 invariant test pack and the code comments in §5.1):
1. **All reads of `waiting_for` for control-flow must be replaced** with the CorrelationManager API (`cm.get_pending_count()`, `cm.is_complete()`, `cm.resolve_response()`).
2. **All writes to `waiting_for` are retained** at three locations (the `daemon/services/{child_reports,error_reporting}.py` decrements and `daemon/tools/instance.py` increment) as the rebuild cache, but the writes themselves are **gated behind `USE_LEGACY_WAITING_FOR_CASCADE`** so they are no-ops in the default configuration.
3. **The column is not dropped** in this release. Drop is deferred to a follow-up release that includes a data-migration story for the existing rebuild-cache values.
4. **`rebuild_from_db()` is the only sanctioned reader** of `waiting_for` as a source of truth.

---

## 9. Kill Switch — How to Roll Back

To roll back to the legacy completion path:

```yaml
# config.yaml
job_system:
  use_legacy_waiting_for_cascade: true   # or env: ENSEMBLE_JOB_SYSTEM_USE_LEGACY_WAITING_FOR_CASCADE=true
```

What the kill switch preserves:
- The full legacy `waiting_for` SQL cascade (A4, A6, A7 paths reactivated).
- The M0 `SELECT ... FOR UPDATE` row-lock gate at `job_feedback_observer.py:1230–1320` is re-engaged.
- The `SELECT COUNT(*)` fallback in `child_reports.py:657` becomes reachable when CM is uninitialized.
- The M0 parent-revive `UPDATE` in `tools/instance.py` is re-engaged.

**Caveat ([W6] in `decouple-review.md`):** This is a "lesser-evil" rollback, not a safe revert. Flipping ON reverts to the M0 band-aid path — the same path that has the premature-completion bug class. Use the kill switch when the new path is *worse* than the old path, not when the new path is *broken* in a non-completion way (e.g. a deadlock — for that, revert the PR).

The legacy path is regression-tested by `tests/test_kill_switch_legacy_path.py` (A14). Keep `DEBUG_COMPLETION_INVARIANT=ON` while on the kill switch to monitor divergence.

---

## 10. Related Documents

- [`docs/configuration/completion-flags.md`](../configuration/completion-flags.md) — feature-flag semantics, interaction matrix, recommended settings, triage tree.
- [`docs/architecture/message-processing-and-correlation.md`](message-processing-and-correlation.md) — current three-layer dispatch architecture; CM, ExecutionGate, MessageProcessingPipeline.
- [`docs/plans/decouple-execution-plan.md`](../plans/decouple-execution-plan.md) — Phase A task breakdown (A1 = this doc, A2 = flags doc, A3 = invariant check, A4–A8 = gating, A9 = audit, A10 = pointer, A11 = invariant tests, A12 = shadow tests, A14 = kill-switch tests, A15 = in-flight flag-flip).
- [`docs/plans/decouple-review.md`](../plans/decouple-review.md) — reviewer findings W6, W7, W9, W10, W11 that motivated the kill switch, the invariant check, and the hard-error decision.
- `daemon/services/correlation_manager.py` — CM API (`register_message_send`, `resolve_response`, `rebuild_from_db`, `is_complete`, `get_pending_count`).
- `daemon/config.py` — `JobSystemConfig` (env prefix `ENSEMBLE_JOB_SYSTEM_`).
- `tests/test_completion_authority_invariant.py` — A11 invariant pack (CI gate).
- `tests/test_kill_switch_legacy_path.py` — A14 kill-switch regression pack.
- `tests/postgres/test_premature_completion_regression.py` — verifies the `OFF` invariant.
- `tests/postgres/test_inflight_flag_flip.py` — A15 in-flight-during-flag-flip.
