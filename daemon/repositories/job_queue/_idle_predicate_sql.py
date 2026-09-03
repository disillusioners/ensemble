"""Shared SQL-text bodies for the job-side idle predicates (defer + background).

Single source for the busy-set SQL consumed by
``JobRepository.has_active_non_deferred_work`` and
``JobRepository.has_active_non_background_work``. Those two predicates
together serve five gate/maintenance sites — Gate A
(``JobProcessor._defer_idle_check`` / ``_background_idle_check``), Gate B
(the defer + background branches of
``JobQueueService._select_next_eligible_job``), and the
``MaintenanceService._is_idle`` probe — so centralizing the bodies makes
five-site agreement a construction property instead of a discipline
requirement. ``tests/job_queue/test_defer_gate_post_settle_window.py::
test_sql_body_shared_constant`` guards the wiring (drift paranoia).

**I3 clarifying line (defer-gate post-settle window fix, 2026-09-03):**
a settled mirror of a non-terminal instance counts as live for the
defer/background gate, terminal for everything else.

The busy-set's truthmaker is ``Instance.status`` — a deliberate
mission-model coupling: instance liveness IS mission liveness (the
Mission projection's read-model semantics), so the gate blocks exactly
while a mission is live, even though Fix B (``e53d0519``) settles the
message mirror to ``admission_state='done'`` at T0 while the parent
instance is still non-terminal. The pre-Fix-B legacy clause (active rows
whose instance is absent or non-terminal) is retained unchanged for
pre-Fix-B rows and queue-stage rows.

Both bodies are plain ``text()``-compatible strings, fully
parameter-bound: the status / queue-type sets ride ``expanding``
bindparams declared by the statement helpers below (never f-string
value interpolation — no injection surface, and one bind plan for both
SQLite and PostgreSQL).
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.sql.elements import TextClause

#: Instance statuses that mean "the mission behind this job is over".
#: A job whose instance holds any OTHER status (``running``,
#: ``waiting_children``, ``idle``, ``paused``, ...) keeps the
#: defer/background gates shut. ``paused`` is deliberately NOT terminal
#: (W2 invariant, ``7ecf09e2``): pause is suspended-but-occupying, not
#: idle. Feeds the expanding ``:terminal_statuses`` bind; order is
#: irrelevant.
JOB_TERMINAL_STATUSES = ("completed", "error", "terminated", "failed")

#: The defer gate's own lane. Excluding it is what makes defer
#: self-deadlock structurally impossible: the defer candidate's own row
#: sits on the defer queue and therefore can never witness against
#: itself.
QUEUE_TYPE_DEFER = "defer"

#: The background gate's own lane (same self-deadlock rationale).
QUEUE_TYPE_BACKGROUND = "background"

#: ``excluded_queue_types`` bind for the defer busy-set.
DEFER_EXCLUDED_QUEUE_TYPES = (QUEUE_TYPE_DEFER,)

#: ``excluded_queue_types`` bind for the background busy-set.
#:
#: Deliberately excludes ONLY ``background`` — NOT ``defer``. Defer work
#: IS non-background work (the 2026-07-23 defer-leak fix): a background
#: queue must starve while defer lanes hold queued/active work, and the
#: mirror clause below cannot see a queued defer job that has no
#: instance yet. Excluding ``defer`` here would reintroduce the leak
#: pinned by ``tests/job_queue/test_defer_idle_gate_phase2.py::
#: test_defer_queue_job_counts_as_non_background``.
BACKGROUND_EXCLUDED_QUEUE_TYPES = (QUEUE_TYPE_BACKGROUND,)

#: Defer busy-set body (project-scoped OR system-wide).
#:
#: Busy iff ANY non-deleted JobItem on a non-defer queue satisfies the
#: legacy clause (``active`` + instance absent-or-non-terminal) OR the
#: post-Fix-B clause (``message`` mirror ``done`` whose instance is
#: non-terminal). The ``:project_id IS NULL OR`` scope switch lets one
#: body serve both the project-scoped gate lanes (Gate A/B defer) and
#: the system-wide probe (maintenance ``_is_idle``), replacing the two
#: former hand-copied branches. NULL ``i.status`` (dangling
#: ``instance_id``) evaluates to NULL under ``NOT IN`` and the row is
#: dropped — identical to the previous ``!=`` chain's three-valued
#: behavior.
JOB_DEFER_BUSY_BODY = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " WHERE (:project_id IS NULL OR j.project_id = :project_id)"
    " AND j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND ("
    "      (j.admission_state = 'active'"
    "       AND (j.instance_id IS NULL"
    "            OR i.status NOT IN :terminal_statuses))"
    "      OR"
    "      (j.job_type = 'message'"
    "       AND j.admission_state = 'done'"
    "       AND j.instance_id IS NOT NULL"
    "       AND i.status NOT IN :terminal_statuses)"
    " )"
    ")"
)

#: Background busy-set body (system-wide — NO project clause).
#:
#: Same two busy clauses as the defer body with two differences per the
#: §4.1 asymmetry: no project filter (the background queue waits for
#: the whole system), and the legacy clause counts ``queued`` rows too,
#: subject to the Fix-2B deadlock carve-out (2026-08-10): a ``queued``
#: JobItem whose linked Task is still ``pending`` is UNCLAIMABLE
#: (queue-awareness guard) and must not hold the gate, or the
#: background queue wedges forever.
JOB_BACKGROUND_BUSY_BODY = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " LEFT JOIN task t ON t.work_id = j.job_id"
    " WHERE j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND ("
    "      (j.admission_state IN ('queued', 'active')"
    "       AND (j.instance_id IS NULL"
    "            OR i.status NOT IN :terminal_statuses))"
    "      OR"
    "      (j.job_type = 'message'"
    "       AND j.admission_state = 'done'"
    "       AND j.instance_id IS NOT NULL"
    "       AND i.status NOT IN :terminal_statuses)"
    " )"
    " AND (j.admission_state != 'queued'"
    "      OR t.status IS NULL"
    "      OR t.status != 'pending')"
    ")"
)


def _declare_expanding_binds(body: str) -> TextClause:
    """Attach the expanding bindparams shared by both busy bodies.

    Declared here (not at the call sites) so the parameter contract
    cannot drift from the body text.
    """
    return text(body).bindparams(
        bindparam("terminal_statuses", expanding=True),
        bindparam("excluded_queue_types", expanding=True),
    )


def defer_busy_statement() -> TextClause:
    """Return the defer busy-set body as an executable TextClause."""
    return _declare_expanding_binds(JOB_DEFER_BUSY_BODY)


def defer_busy_binds(project_id: str | None) -> dict[str, object]:
    """Bind values for :func:`defer_busy_statement`.

    ``project_id=None`` selects the system-wide scope (maintenance
    ``_is_idle``); a set project selects the project scope (Gate A/B
    defer lanes).
    """
    return {
        "project_id": project_id,
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "excluded_queue_types": list(DEFER_EXCLUDED_QUEUE_TYPES),
    }


def background_busy_statement() -> TextClause:
    """Return the background busy-set body as an executable TextClause."""
    return _declare_expanding_binds(JOB_BACKGROUND_BUSY_BODY)


def background_busy_binds() -> dict[str, object]:
    """Bind values for :func:`background_busy_statement` (system-wide)."""
    return {
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "excluded_queue_types": list(BACKGROUND_EXCLUDED_QUEUE_TYPES),
    }
