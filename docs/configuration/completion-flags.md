# Completion-Architecture Feature Flags (Phase A)

Two feature flags gate the migration from the legacy `waiting_for` SQL cascade to the CorrelationManager (CM) authoritative completion path. Both flags live in `daemon.config.JobSystemConfig` (env prefix `ENSEMBLE_JOB_SYSTEM_`) and are read by Phase A call-site gates (tasks A3–A8).

| Field name | YAML key | Env var | Default |
|------------|----------|---------|---------|
| `use_legacy_waiting_for_cascade` | `job_system.use_legacy_waiting_for_cascade` | `ENSEMBLE_JOB_SYSTEM_USE_LEGACY_WAITING_FOR_CASCADE` | `false` |
| `debug_completion_invariant` | `job_system.debug_completion_invariant` | `ENSEMBLE_JOB_SYSTEM_DEBUG_COMPLETION_INVARIANT` | `false` |

---

## Flag Semantics

### `use_legacy_waiting_for_cascade` (kill switch)

- **`false` (default, production):** CorrelationManager is the SOLE completion authority. The 18 `waiting_for` control-flow call sites skip the legacy SQL decrement + `if waiting_for == 0` cascade branch. The premature-completion bug class is structurally impossible under this setting.
- **`true` (rollback path):** The legacy `waiting_for` cascade runs in the gated call sites. The premature-completion bug class is reachable again.

> ⚠️ **[W6] This is a "lesser-evil" rollback, not a safe revert.** Flipping ON reverts to the **M0 band-aid path** — the same path that has the premature-completion bug class. It is the fallback when the new path is *worse* than the old path, not when the new path is *broken*. If the new path is broken in a way that does not involve premature completion (e.g. a deadlock), the kill switch will not help — revert the PR.

### `debug_completion_invariant` (observability)

- **`false` (default, production):** No divergence logging. Recommended for steady-state production.
- **`true` (recommended in dev/CI and for 2 weeks post-release):** The runtime emits a structured warning with `event=CM_WAITING_FOR_DIVERGENCE` (and fields `parent_id, child_id, message_id, cm_pending, waiting_for`) whenever CM's in-memory pending count disagrees with the DB `waiting_for` counter. Absent log lines mean the invariant holds.

> Divergence logs are rate-limited and never block completion. They are observability, not enforcement.

---

## Interaction Matrix

All four combinations of the two flags are defined and supported. There is no undefined combination.

| `USE_LEGACY_WAITING_FOR_CASCADE` | `DEBUG_COMPLETION_INVARIANT` | Valid? | Use case |
|----------------------------------|-------------------------------|--------|----------|
| `ON`  | `ON`  | ✅ VALID | **Operational triage.** Monitor divergence while on the legacy path. Used in the incident response: "kill switch is on, but we still want to see if CM matches." |
| `ON`  | `OFF` | ✅ VALID | **Pure rollback.** Normal legacy-path operation with no observability overhead. |
| `OFF` | `ON`  | ✅ VALID | **Default dev/CI / first 2 weeks post-release.** CM is authoritative, but every divergence is logged so we know if the invariant holds. |
| `OFF` | `OFF` | ✅ VALID | **Steady-state production.** CM is authoritative, no divergence logging. Premature-completion bug class is structurally impossible. |

### Required external precondition (not a flag combo, but an operational hard error)

| `USE_LEGACY_WAITING_FOR_CASCADE` | CM state | Outcome |
|----------------------------------|----------|---------|
| `OFF` | CM **not initialized** (`get_correlation_manager() is None`) | **HARD ERROR** at the gated call site. Implemented in task A8. Graceful degradation with the kill switch OFF is *unsupported* — CM must be initialized. This is the only invariant failure that aborts the operation; all other combinations above keep the daemon running. |

> The legacy `SELECT COUNT(*)` TOCTOU fallback path (Race #3 in the decouple plan) is the bug being fixed; under `OFF` it must not be reachable. A8 throws instead of falling back.

---

## Recommended Settings

| Environment | `use_legacy_waiting_for_cascade` | `debug_completion_invariant` | Notes |
|-------------|----------------------------------|-------------------------------|-------|
| Local dev | `false` | `true` | Catch any divergence early; keep logs visible. |
| CI | `false` | `true` | Test packs should never trigger divergence logs; flag surfaces any regression. |
| Staging (pre-release) | `false` | `true` | 48h staging run; any divergence log is a release blocker. |
| Production (steady-state) | `false` | `false` | Default. |
| Production (first 2 weeks post-release) | `false` | `true` | Safety net that replaces the M2 dwell period. Turn OFF after 2 weeks if no divergence logs appeared. |
| Production (incident rollback) | `true` | `true` | Kill switch ON; keep observability ON to monitor. |

---

## Triage Decision Tree (when `CM_WAITING_FOR_DIVERGENCE` fires)

1. **Read the structured fields.** `parent_id`, `child_id`, `message_id`, `cm_pending`, `waiting_for`.
2. **Determine which side is wrong.**
   - CM pending > DB waiting_for → CM has a correlation that DB missed (registration race). Usually self-heals on next register/decrement.
   - CM pending < DB waiting_for → DB has a `waiting_for++` without a CM entry. Indicates a bypass (legacy path still running) or a CM registration failure that rolled back the increment.
3. **Check kill-switch state.**
   - `use_legacy_waiting_for_cascade=OFF` and divergence persists for >10/hour → **flip kill switch ON**, follow `[W6]` caveats, file an incident.
   - `use_legacy_waiting_for_cascade=ON` → divergence is expected behaviour; ignore unless it correlates with a user-visible regression.

There is no safe "middle state" between "ignore the warning" and "full rollback to legacy." If neither side is acceptable, revert the PR.

---

## Related Documents

- `docs/plans/decouple-execution-plan.md` — Phase A scope (tasks A2–A14).
- `docs/plans/decouple-job-task-message-correlation.md` — Architectural context for the 18 gated call sites and the CM authoritative path.
- `docs/plans/decouple-review.md` — Reviewer findings W6, W7, W9, W11 that motivated this matrix.
- `daemon/config.py` — `JobSystemConfig` (env prefix `ENSEMBLE_JOB_SYSTEM_`).
- `tests/postgres/test_premature_completion_regression.py` — Verifies `OFF` invariant.
- `tests/test_kill_switch_legacy_path.py` (A14) — Verifies the full legacy path under `ON`.
