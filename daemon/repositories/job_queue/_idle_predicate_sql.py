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

A sixth, READ-ONLY consumer joined (defer-blocked transparency surface,
2026-09-04, docs §8.5): the witness-selecting body
(:func:`defer_busy_witness_statement`) is DERIVED from the defer gate
body constants by unwrapping ``EXISTS`` — the enumeration cannot drift
from the gate it mirrors because there is only one copy of the
predicate text in this module.

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

**PG parameter-type hardening (hotfix 2026-09-04):** the previous
incarnations of the defer busy body collapsed the two scopes
(project-scoped / system-wide) into one body that bound ``:project_id``
under a ``:project_id IS NULL OR j.project_id = :project_id`` scope
switch. The bare ``:project_id IS NULL`` predicate passed to PostgreSQL
without a type context, producing ``psycopg.errors.AmbiguousParameter:
could not determine data type of parameter $1`` on the project-scoped
PG path; SQLite tolerates the typed-vs-untyped NULL and the PG-parity
leg had been SKIPPED (unprovisioned PG test DB) so the breakage
shipped. The fix UN-COLLAPSES the body into two SQL bodies — a
project-scoped form with a plain ``j.project_id = :project_id``
equality (a STRING-bound parameter, no NULL trick) and a system-wide
form with NO project parameter at all — and the helper layer selects
between them on ``project_id``. With no NULL-typed parameter binding on
either dialect, the ambiguity class is impossible by construction.

**WS1 — requester-instance carve-out (defer self-witness gate carve-out,
2026-09-06):** a deferred message sent to a COMPLETED instance REVIVES
it (F7 revive). The revived instance's non-terminal status + its OWN
settled message mirrors would hold the defer busy-gate against ITSELF
(the ``instance_id IS NOT NULL`` mirror clause matches the candidate's
own mirrors) — the held defer task becomes the instance's only work,
no turn ever runs, no exit; every reaper is shielded. The carve-out
adds ``AND j.instance_id != :requester_instance_id`` to the MIRROR
clause ONLY (legacy clause stays UNTOUCHED — a live foreground turn
yields an ACTIVE job which still witnesses correctly). The
``:requester_instance_id`` bind is a STRING (never NULL); the bind is
only added when the caller passes a requester instance (system-scope
and legacy callers see the no-carve-out body, semantics identical to
the pre-WS1 shape). Two-body split preserved: four defer bodies total
(no-carve-out project + system; carve-out project + system) sharing the
disjunction structure and the ``_unwrap_exists_body`` derivation. The
defer-blocked witness surface is system-wide and continues to use the
no-carve-out variant — the carve-out is per-candidate.

Both bodies are plain ``text()``-compatible strings, fully
parameter-bound: the status / queue-type sets ride ``expanding``
bindparams declared by the statement helpers below (never f-string
value interpolation — no injection surface, and one bind plan for both
SQLite and PostgreSQL). The two bodies share the busy-set clause text
via composition (legacy + mirror clause fragments, composed into the
project-scoped and system-wide bodies at module load time).
"""

from __future__ import annotations

import logging
import os
from typing import Final, Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.sql.elements import TextClause

from daemon.constants import TERMINAL_INSTANCE_STATUSES

logger = logging.getLogger(__name__)

#: Instance statuses that mean "the mission behind this job is over".
#: A job whose instance holds any OTHER status (``running``,
#: ``waiting_children``, ``idle``, ``paused``, ...) keeps the
#: defer/background gates shut. ``paused`` is deliberately NOT terminal
#: (W2 invariant, ``7ecf09e2``): pause is suspended-but-occupying, not
#: idle. Feeds the expanding ``:terminal_statuses`` bind; order is
#: irrelevant.
#:
#: Single-sourced from ``daemon.constants.TERMINAL_INSTANCE_STATUSES`` —
#: importing ``daemon.constants`` is the sanctioned exception here
#: because (a) that module is dependency-free by design (it imports
#: nothing) and (b) the two-set desync is a silent-divergence trap
#: (drift-guard test cross-asserts equality with the canonical constant
#: in ``tests/job_queue/test_defer_gate_post_settle_window.py``). The
#: tuple form (vs the canonical ``frozenset``) is what the SQLAlchemy
#: ``expanding`` bindparam expects — ordering is irrelevant for
#: ``NOT IN`` semantics.
JOB_TERMINAL_STATUSES: tuple[str, ...] = tuple(sorted(TERMINAL_INSTANCE_STATUSES))

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

# ── Shared busy-set clause fragments (composition) ─────────────────────────
# These two clauses are the SEMANTIC core of every busy body. They are
# written as plain strings (no f-string interpolation) so they can be
# spliced into the two project-scoped/system-wide bodies below via
# ``str.replace`` without re-escaping braces or risking any drift
# between the three bodies' busy-set definitions.

#: Legacy clause (DEFER) — a non-deleted JobItem is busy iff its
#: ``admission_state`` is ``active`` AND its instance is either absent
#: (``j.instance_id IS NULL``) or non-terminal (``i.status NOT IN
#: :terminal_statuses``).
_LEGACY_CLAUSE: Final[str] = (
    "(j.admission_state = 'active'"
    " AND (j.instance_id IS NULL"
    " OR i.status NOT IN :terminal_statuses))"
)

#: Legacy clause (BACKGROUND) — same as the defer legacy clause BUT
#: counts BOTH ``queued`` AND ``active`` admission states per the §4.1
#: asymmetry: the background queue must starve on both rows because a
#: ``queued`` non-background job with no instance yet would otherwise
#: leak past the gate — see the Fix-2B deadlock carve-out in the
#: background body below. Keeping the two legacy clauses as separate
#: constants prevents a future refactor from collapsing them into a
#: shared ``OR`` and silently breaking the asymmetry pinned by
#: ``tests/job_queue/test_idle_gate_deadlock_fix.py``
#: (``TestJobSideBackgroundPredicateExclusion``).
_BACKGROUND_LEGACY_CLAUSE: Final[str] = (
    "(j.admission_state IN ('queued', 'active')"
    " AND (j.instance_id IS NULL"
    " OR i.status NOT IN :terminal_statuses))"
)

#: Post-Fix-B mirror clause — a settled message mirror of a non-terminal
#: instance is busy iff ``job_type='message'``, ``admission_state='done'``,
#: ``instance_id IS NOT NULL``, and the linked instance is non-terminal.
#: Identical on both gates.
_MIRROR_CLAUSE: Final[str] = (
    "(j.job_type = 'message'"
    " AND j.admission_state = 'done'"
    " AND j.instance_id IS NOT NULL"
    " AND i.status NOT IN :terminal_statuses)"
)

#: Post-Fix-B mirror clause WITH the WS1 requester-instance carve-out.
#: Used when the caller passes a non-None ``requester_instance_id`` —
#: the candidate's own settled mirrors do NOT witness against the
#: candidate (the defer self-witness incident: a deferred message sent
#: to a COMPLETED instance REVIVES it, the revived instance's non-
#: terminal status + its own settled mirrors would otherwise hold the
#: gate against itself forever — no turn ever runs, no exit; every
#: reaper is shielded).
#:
#: The legacy clause is UNTOUCHED — a live foreground turn yields an
#: ACTIVE job which still witnesses correctly via the legacy clause
#: (``admission_state='active'`` + non-terminal instance). The carve-out
#: only suppresses the SELF-witness via the post-Fix-B settled-mirror
#: clause; other-instance mirrors continue to witness.
#:
#: The ``requester_instance_id`` bindparam is a STRING when used
#: (never NULL — the bind is added by the statement helper ONLY when
#: the caller passes a requester instance; when it is None the
#: caller falls back to the no-carve-out body, semantics identical to
#: pre-WS1). This avoids the NULL-typed ``!=`` bind trap that would
#: otherwise drop every mirror row (NULL-comparison evaluates to NULL).
_MIRROR_CLAUSE_WITH_CARVEOUT: Final[str] = (
    "(j.job_type = 'message'"
    " AND j.admission_state = 'done'"
    " AND j.instance_id IS NOT NULL"
    " AND j.instance_id != :requester_instance_id"
    " AND i.status NOT IN :terminal_statuses)"
)

#: The defer busy-set disjunction (defer legacy OR mirror). Shared by
#: both no-carve-out defer bodies (project-scoped + system-wide) —
#: splicing this (rather than re-stating the two clauses inline) makes
#: it impossible for the no-carve-out bodies' semantics to drift apart.
_DEFER_BUSY_DISJUNCTION: Final[str] = (
    "(\n"
    "      " + _LEGACY_CLAUSE + "\n"
    "      OR\n"
    "      " + _MIRROR_CLAUSE + "\n"
    " )"
)

#: The defer busy-set disjunction WITH the WS1 requester-instance
#: carve-out (defer legacy OR carve-out mirror). Shared by both
#: carve-out defer bodies (project-scoped + system-wide). The legacy
#: clause is identical to the no-carve-out disjunction; only the
#: mirror clause adds ``j.instance_id != :requester_instance_id``.
_DEFER_BUSY_DISJUNCTION_WITH_CARVEOUT: Final[str] = (
    "(\n"
    "      " + _LEGACY_CLAUSE + "\n"
    "      OR\n"
    "      " + _MIRROR_CLAUSE_WITH_CARVEOUT + "\n"
    " )"
)

#: The background busy-set disjunction (background legacy OR mirror).
#: Spliced into the background body only — the two disjunctions are
#: intentionally separated to make the §4.1 asymmetry a construction
#: property.
_BACKGROUND_BUSY_DISJUNCTION: Final[str] = (
    "(\n"
    "      " + _BACKGROUND_LEGACY_CLAUSE + "\n"
    "      OR\n"
    "      " + _MIRROR_CLAUSE + "\n"
    " )"
)

# ── Defer busy-set bodies (project-scoped AND system-wide) ─────────────────

#: Defer busy-set body (project-scoped — plain :project_id equality, NO
#: NULL trick).
#:
#: Busy iff ANY non-deleted JobItem on a non-defer queue satisfies the
#: legacy clause (``active`` + instance absent-or-non-terminal) OR the
#: post-Fix-B clause (``message`` mirror ``done`` whose instance is
#: non-terminal). Used by Gate A defer (``_defer_idle_check``), Gate B
#: defer (``_select_next_eligible_job`` defer branch), and any other
#: project-scoped defer consumer. A STRING-typed ``:project_id`` bind —
#: PG infers the type from the comparison column; SQLite is type-tolerant
#: regardless. The bare ``IS NULL`` shape that produced the
#: ``AmbiguousParameter`` PG incident is gone by construction.
JOB_DEFER_BUSY_BODY_PROJECT: Final[str] = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " WHERE j.project_id = :project_id"
    " AND j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND " + _DEFER_BUSY_DISJUNCTION
    + ")"
).replace("\n", " ")

#: Defer busy-set body (system-wide — NO project parameter at all).
#:
#: Same busy-set semantics as the project-scoped body; no project filter
#: (a ``:project_id`` parameter is not bound at all — no PG type-context
#: ambiguity possible). Used by the maintenance ``_is_idle`` probe and
#: any other system-wide defer consumer. NULL ``i.status`` (dangling
#: ``instance_id``) evaluates to NULL under ``NOT IN`` and the row is
#: dropped — identical to the previous ``!=`` chain's three-valued
#: behavior.
JOB_DEFER_BUSY_BODY_SYSTEM: Final[str] = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " WHERE j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND " + _DEFER_BUSY_DISJUNCTION
    + ")"
).replace("\n", " ")

#: Defer busy-set body (project-scoped, WS1 requester-instance carve-out).
#:
#: Same structure as :data:`JOB_DEFER_BUSY_BODY_PROJECT` but uses the
#: WS1 carve-out disjunction (mirror clause adds
#: ``j.instance_id != :requester_instance_id``). The ``:requester_instance_id``
#: bindparam is a STRING when bound (never NULL — the caller only adds
#: the bind when a requester instance is in scope, otherwise the
#: no-carve-out body is selected). Used by Gate A defer
#: (``_defer_idle_check``) and Gate B defer
#: (``_select_next_eligible_job``) when the candidate's instance_id is
#: in scope.
JOB_DEFER_BUSY_BODY_PROJECT_WITH_CARVEOUT: Final[str] = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " WHERE j.project_id = :project_id"
    " AND j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND " + _DEFER_BUSY_DISJUNCTION_WITH_CARVEOUT
    + ")"
).replace("\n", " ")

#: Defer busy-set body (system-wide, WS1 requester-instance carve-out).
#:
#: Same structure as :data:`JOB_DEFER_BUSY_BODY_SYSTEM` but uses the
#: WS1 carve-out disjunction (mirror clause adds
#: ``j.instance_id != :requester_instance_id``). Used by system-wide
#: defer consumers that pass a requester instance.
JOB_DEFER_BUSY_BODY_SYSTEM_WITH_CARVEOUT: Final[str] = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " WHERE j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND " + _DEFER_BUSY_DISJUNCTION_WITH_CARVEOUT
    + ")"
).replace("\n", " ")

#: Background busy-set body (system-wide — NO project clause).
#:
#: Same two busy clauses as the defer body with two differences per the
#: §4.1 asymmetry: no project filter (the background queue waits for
#: the whole system), and the legacy clause counts ``queued`` rows too,
#: subject to the Fix-2B deadlock carve-out (2026-08-10): a ``queued``
#: JobItem whose linked Task is still ``pending`` is UNCLAIMABLE
#: (queue-awareness guard) and must not hold the gate, or the
#: background queue wedges forever.
JOB_BACKGROUND_BUSY_BODY: Final[str] = (
    "SELECT EXISTS ("
    " SELECT 1 FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " LEFT JOIN task t ON t.work_id = j.job_id"
    " WHERE j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND " + _BACKGROUND_BUSY_DISJUNCTION
    + " AND (j.admission_state != 'queued'"
    "      OR t.status IS NULL"
    "      OR t.status != 'pending'))"
).replace("\n", " ")


def _declare_expanding_binds(body: str) -> TextClause:
    """Attach the expanding bindparams shared by both busy bodies.

    Declared here (not at the call sites) so the parameter contract
    cannot drift from the body text.
    """
    return text(body).bindparams(
        bindparam("terminal_statuses", expanding=True),
        bindparam("excluded_queue_types", expanding=True),
    )


def _declare_expanding_binds_project(body: str) -> TextClause:
    """Like :func:`_declare_expanding_binds` but for the project-scoped
    defer body (whose bindparams include ``:project_id``).

    Kept as a separate helper so the bindparam declarations cannot drift
    between the two defer bodies — the project-scoped body needs the
    plain ``:project_id`` declaration (no expanding IN-list), the
    system-wide body does not.
    """
    return text(body).bindparams(
        bindparam("project_id"),
        bindparam("terminal_statuses", expanding=True),
        bindparam("excluded_queue_types", expanding=True),
    )


def _declare_expanding_binds_with_carveout(body: str) -> TextClause:
    """Like :func:`_declare_expanding_binds` but for the WS1
    requester-instance carve-out system-wide body (whose bindparams
    include ``:requester_instance_id``).
    """
    return text(body).bindparams(
        bindparam("requester_instance_id"),
        bindparam("terminal_statuses", expanding=True),
        bindparam("excluded_queue_types", expanding=True),
    )


def _declare_expanding_binds_project_with_carveout(body: str) -> TextClause:
    """Like :func:`_declare_expanding_binds_project` but for the WS1
    requester-instance carve-out project-scoped body (whose bindparams
    include ``:project_id`` AND ``:requester_instance_id``).
    """
    return text(body).bindparams(
        bindparam("project_id"),
        bindparam("requester_instance_id"),
        bindparam("terminal_statuses", expanding=True),
        bindparam("excluded_queue_types", expanding=True),
    )


def defer_busy_statement(
    project_id: str | None,
    requester_instance_id: str | None = None,
) -> TextClause:
    """Return the defer busy-set body as an executable TextClause.

    Selects the project-scoped body when ``project_id`` is not None
    and the system-wide body when it is ``None``. The two-body split
    keeps a bare ``IS NULL`` parameter comparison off every code path
    on both dialects — the PG ``AmbiguousParameter`` incident is
    impossible by construction.

    Selects the WS1 requester-instance carve-out body variant when
    ``requester_instance_id`` is not None (the candidate's own settled
    mirrors are excluded from the busy-set), and the no-carve-out body
    when it is ``None``. System-scope and legacy callers see the
    no-carve-out shape (semantics identical to pre-WS1). The carve-out
    variant adds ``:requester_instance_id`` to the bindparam contract;
    the bind is a STRING (never NULL) — the NULL-typed ``!=`` bind
    trap is impossible by construction because the bind is only
    declared when the caller passes a requester instance.
    """
    has_carveout = requester_instance_id is not None
    if has_carveout:
        body = (
            JOB_DEFER_BUSY_BODY_PROJECT_WITH_CARVEOUT
            if project_id is not None
            else JOB_DEFER_BUSY_BODY_SYSTEM_WITH_CARVEOUT
        )
        if project_id is not None:
            return _declare_expanding_binds_project_with_carveout(body)
        return _declare_expanding_binds_with_carveout(body)
    body = (
        JOB_DEFER_BUSY_BODY_PROJECT
        if project_id is not None
        else JOB_DEFER_BUSY_BODY_SYSTEM
    )
    if project_id is not None:
        return _declare_expanding_binds_project(body)
    return _declare_expanding_binds(body)


def defer_busy_binds(
    project_id: str | None,
    requester_instance_id: str | None = None,
) -> dict[str, object]:
    """Bind values for :func:`defer_busy_statement`.

    ``project_id=None`` selects the system-wide scope (maintenance
    ``_is_idle``); a set project selects the project scope (Gate A/B
    defer lanes).

    ``requester_instance_id`` adds the WS1 requester-instance carve-out
    bind when set (selects the carve-out body variant in
    :func:`defer_busy_statement`). The value is bound as a STRING (never
    NULL) — when omitted, no carve-out bind is added and the no-carve-
    out body is used. System-scope and legacy callers see the pre-WS1
    bind contract.
    """
    binds: dict[str, object] = {
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "excluded_queue_types": list(DEFER_EXCLUDED_QUEUE_TYPES),
    }
    if project_id is not None:
        binds["project_id"] = project_id
    if requester_instance_id is not None:
        binds["requester_instance_id"] = requester_instance_id
    return binds


def background_busy_statement() -> TextClause:
    """Return the background busy-set body as an executable TextClause."""
    return _declare_expanding_binds(JOB_BACKGROUND_BUSY_BODY)


def background_busy_binds() -> dict[str, object]:
    """Bind values for :func:`background_busy_statement` (system-wide)."""
    return {
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "excluded_queue_types": list(BACKGROUND_EXCLUDED_QUEUE_TYPES),
    }


# ── Defer busy-set WITNESS body (enumeration, read-only surfaces) ──────────
# The defer-blocked transparency surface (``GET /api/queues/defer-blocked``,
# ``daemon/routers/queues.py`` + ``daemon/services/defer_block_resolver.py``,
# docs/job-task-system.md §8.5) must ENUMERATE the busy-set witnesses —
# not merely learn that the set is non-empty — and it must enumerate them
# with the SAME predicate composition the gate evaluates, or the warning
# can drift from the gate it mirrors (the exact defect this module exists
# to make impossible).
#
# The witness body is therefore DERIVED from the gate body constants by
# unwrapping the ``SELECT EXISTS ( SELECT 1 … )`` wrapper at module load
# time: the FROM/JOIN/WHERE busy-set text is byte-shared with the gate —
# there is no second copy of the predicate anywhere in this module. Any
# future edit to a gate body automatically flows into the witness body;
# a refactor that reshapes the wrapper makes :func:`_unwrap_exists_body`
# raise at IMPORT time (loud failure, not silent drift). Behavioral
# equivalence (gate decision == witnesses non-empty across fixtures) is
# pinned by ``tests/unit/routers/test_defer_blocked_api.py``.

#: Fixed select list for the witness body. A module-private literal —
#: no external input ever reaches this string (no injection surface).
_WITNESS_SELECT_LIST: Final[str] = (
    " j.job_id AS job_id,"
    " j.instance_id AS instance_id,"
    " j.agent_id AS job_agent_id,"
    " j.created_at AS job_created_at,"
    " i.status AS instance_status,"
    " i.agent_id AS instance_agent_id,"
    " i.paused_at AS instance_paused_at,"
    " i.last_activity_at AS instance_last_activity_at,"
    " i.updated_at AS instance_updated_at,"
    " i.created_at AS instance_created_at"
)

#: The exact prefix the EXISTS-wrapper unwrap expects. Kept as a named
#: constant so a wrapper reshape fails with a readable message.
_GATE_BODY_WITNESS_PREFIX: Final[str] = "SELECT EXISTS ( SELECT 1"


def _unwrap_exists_body(gate_body: str) -> str:
    """Derive a witness body from a gate body by unwrapping ``EXISTS``.

    Replaces the ``SELECT EXISTS ( SELECT 1`` prologue with the witness
    select list and strips the wrapper's closing ``)`` — the FROM/JOIN/
    WHERE tail is byte-identical to the gate's. Raises ``ValueError``
    at IMPORT time if a gate body no longer carries the expected
    wrapper shape (the loud-failure contract: a refactor that reshapes
    the wrapper must update this derivation consciously, never drift
    silently).

    BLAST-RADIUS (P0-8-BE(d), 2026-09-04): the two module-level calls
    below this function — :data:`JOB_DEFER_BUSY_WITNESS_BODY_PROJECT`
    and :data:`JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM` — invoke
    :func:`_unwrap_exists_body` at module-load time, NOT lazily. A
    wrapper reshape that fires this ``ValueError`` therefore bricks
    the import of ``daemon.repositories.job_queue._idle_predicate_sql``
    itself. That module is imported transitively by every consumer of
    the shared busy-set SQL: the gate path
    (``JobRepository.has_active_non_deferred_work`` /
    ``has_active_non_background_work``),
    ``JobProcessor._defer_idle_check`` /
    ``_background_idle_check`` /
    ``_select_next_eligible_job`` defer + background branches, the
    maintenance ``_is_idle`` probe, and the defer-blocked transparency
    surface (``daemon.services.defer_block_resolver``). In practice
    the blast radius is the entire daemon entry point + every test
    that imports anything transitively from ``daemon.repositories.job_queue``
    — ``daemon.api`` /
    ``daemon.manager`` /
    ``daemon.api`` lifespan startup /
    ``daemon.routers.queues`` /
    ``daemon.services.defer_block_resolver`` + ~250 unit tests in
    ``tests/job_queue/`` + ``tests/unit/routers/test_defer_blocked_api.py``
    + ``tests/unit/job_state/test_constitution_drift.py``. The ``"loud
    failure"`` design intent is real — a wrapper reshape that breaks
    the derivation MUST be caught immediately, not at the first witness
    query — but a maintainer editing this function should know the
    failure surface spans module imports and reach for a worktree
    before testing locally. The companion sentinel that catches a
    *silent* helper bug (not a wrapper reshape) is the byte-equality
    pin in
    ``tests/unit/routers/test_defer_blocked_api.py::TestConsistencyPin::test_witness_body_byte_matches_literal_system_body``
    (and its project-scoped sibling) — that pin anchors the witness
    body to a hardcoded literal so a bug in this helper that produces
    *some* body still flips the byte-equality test rather than passing
    silently.
    """
    if not gate_body.startswith(_GATE_BODY_WITNESS_PREFIX):
        raise ValueError(
            "defer busy body no longer starts with "
            f"{_GATE_BODY_WITNESS_PREFIX!r}; the witness-body derivation "
            "in this module must be updated to match the new wrapper "
            "shape (shared-composition contract, docs §8.5)"
        )
    if not gate_body.endswith(")"):
        raise ValueError(
            "defer busy body no longer ends with the EXISTS wrapper's "
            "closing paren; the witness-body derivation in this module "
            "must be updated to match the new wrapper shape "
            "(shared-composition contract, docs §8.5)"
        )
    return (
        "SELECT" + _WITNESS_SELECT_LIST + gate_body[len(_GATE_BODY_WITNESS_PREFIX):-1]
    )


#: Defer busy-set WITNESS body (project-scoped) — :data:`JOB_DEFER_BUSY_BODY_PROJECT`
#: with ``EXISTS`` unwrapped. Same binds as the gate body.
JOB_DEFER_BUSY_WITNESS_BODY_PROJECT: Final[str] = _unwrap_exists_body(
    JOB_DEFER_BUSY_BODY_PROJECT
)

#: Defer busy-set WITNESS body (system-wide) — :data:`JOB_DEFER_BUSY_BODY_SYSTEM`
#: with ``EXISTS`` unwrapped. Same binds as the gate body.
JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM: Final[str] = _unwrap_exists_body(
    JOB_DEFER_BUSY_BODY_SYSTEM
)

#: Defer busy-set WITNESS body (project-scoped, WS1 requester-instance
#: carve-out) — :data:`JOB_DEFER_BUSY_BODY_PROJECT_WITH_CARVEOUT` with
#: ``EXISTS`` unwrapped. Same binds as the gate body. Enumerates
#: witnesses EXCLUDING the candidate's own settled mirrors.
#:
#: RESERVED for future per-candidate witness enumeration (WS4
#: mission-aware cleanup); currently reached only via
#: :func:`defer_busy_witness_statement` when called with a non-None
#: ``requester_instance_id`` (no production caller does today —
#: ``defer_block_resolver`` always passes ``None``). MUST keep
#: deriving via :func:`_unwrap_exists_body` so the bind contract
#: cannot drift from the gate body it mirrors.
JOB_DEFER_BUSY_WITNESS_BODY_PROJECT_WITH_CARVEOUT: Final[str] = _unwrap_exists_body(
    JOB_DEFER_BUSY_BODY_PROJECT_WITH_CARVEOUT
)

#: Defer busy-set WITNESS body (system-wide, WS1 requester-instance
#: carve-out) — :data:`JOB_DEFER_BUSY_BODY_SYSTEM_WITH_CARVEOUT` with
#: ``EXISTS`` unwrapped. Same binds as the gate body.
#:
#: RESERVED for future per-candidate witness enumeration (WS4
#: mission-aware cleanup); currently reached only via
#: :func:`defer_busy_witness_statement` when called with a non-None
#: ``requester_instance_id`` (no production caller does today —
#: ``defer_block_resolver`` always passes ``None``). MUST keep
#: deriving via :func:`_unwrap_exists_body` so the bind contract
#: cannot drift from the gate body it mirrors.
JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM_WITH_CARVEOUT: Final[str] = _unwrap_exists_body(
    JOB_DEFER_BUSY_BODY_SYSTEM_WITH_CARVEOUT
)


def defer_busy_witness_statement(
    project_id: str | None,
    requester_instance_id: str | None = None,
) -> TextClause:
    """Return the defer busy-set WITNESS body as an executable TextClause.

    The enumeration counterpart of :func:`defer_busy_statement`: instead
    of ``EXISTS(...)`` it returns one row per busy-set witness JobItem
    (joined instance columns included). Body selection and bindparam
    declaration follow the exact same two-body discipline as the gate
    (project-scoped vs system-wide on ``project_id``; no bare-NULL
    parameter comparison on either dialect).

    Selects the WS1 requester-instance carve-out witness body variant
    when ``requester_instance_id`` is not None (the candidate's own
    settled mirrors are excluded from the enumeration), and the
    no-carve-out witness body when it is ``None``. The system-wide
    defer-blocked transparency surface (``GET /api/queues/defer-blocked``)
    continues to pass ``None`` — system-wide enumeration, no candidate
    scope, the witness surface shows ALL busy witnesses including any
    self-witnessed mirror (semantics identical to pre-WS1). The
    carve-out witness body is exposed for future per-candidate witness
    enumeration (e.g. WS4 mission-aware cleanup) — those two RESERVED
    constants (:data:`JOB_DEFER_BUSY_WITNESS_BODY_PROJECT_WITH_CARVEOUT`
    and :data:`JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM_WITH_CARVEOUT`) MUST
    keep deriving from their gate-body counterparts via
    :func:`_unwrap_exists_body` so their bind contract cannot drift
    from the gate they mirror.

    READ-ONLY consumers only — the statement is a bare SELECT; the
    transparency surface must never gain a write on this path (census
    contract, docs §8.5).
    """
    has_carveout = requester_instance_id is not None
    if has_carveout:
        body = (
            JOB_DEFER_BUSY_WITNESS_BODY_PROJECT_WITH_CARVEOUT
            if project_id is not None
            else JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM_WITH_CARVEOUT
        )
        if project_id is not None:
            return _declare_expanding_binds_project_with_carveout(body)
        return _declare_expanding_binds_with_carveout(body)
    body = (
        JOB_DEFER_BUSY_WITNESS_BODY_PROJECT
        if project_id is not None
        else JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM
    )
    if project_id is not None:
        return _declare_expanding_binds_project(body)
    return _declare_expanding_binds(body)


def defer_busy_witness_binds(
    project_id: str | None,
    requester_instance_id: str | None = None,
) -> dict[str, object]:
    """Bind values for :func:`defer_busy_witness_statement`.

    Delegates to :func:`defer_busy_binds` — the witness body is derived
    from the gate body, so the parameter contract is shared BY
    CONSTRUCTION and cannot drift.
    """
    return defer_busy_binds(project_id, requester_instance_id)

# ── Idle-gate ACTIVITY RECENCY (fix/idle-gate-recency, 2026-11-24) ─────────
#
# The two idle gates (the BACKGROUND-queue gate in
# ``JobProcessor._background_idle_check`` and the MAINTENANCE-registry gate
# in ``MaintenanceService._is_idle``) used to answer "is the nest busy?"
# with *"does a non-terminal row exist?"* — which is UNSATISFIABLE on a
# nest whose normal shape is a set of always-on sessions (every settled
# message mirror of a non-terminal instance witnesses FOREVER). Measured on
# live ``ensemble_prod``: 67 background-gate witnesses, 52 of them on two
# long-lived ari sessions; the background queue had not drained since
# 2026-11-14T17:44:35Z.
#
# The fix re-scopes the witness SET (it does not remove the gate): a witness
# only counts when the WORK ITEM ITSELF is RECENT — i.e. its creation
# timestamp is within ``W`` minutes of now. The holder instance's
# ``last_activity_at`` is deliberately NOT part of the anchor: the measured
# holders are recently active (0–70 min idle) while their witness ROWS are
# 7 h – 5.7 d old, so a holder anchor cannot discriminate and would keep
# the gate closed forever.
#
# Anchor columns (the only creation clocks the schema offers that an
# always-on session's mere existence does NOT refresh):
#   * ``job_queue_items.created_at`` — ISO-8601 string (UTC)
#   * ``task.created_at``             — timestamp
#
# The bodies below are the ENUMERATED witness shapes (one row per witness)
# for the two recency-bounded probes. The recency filter itself is applied
# in Python (see ``JobRepository.has_recent_*_work``) so that an
# unparseable timestamp can fail CLOSED (BUSY) — a string comparison in SQL
# could not distinguish "old" from "unparseable".
#
# The existing gate bodies (defer / background) are UNTOUCHED: the defer
# gate is a THIRD consumer of the shared bodies with its own witness
# surface (``GET /api/queues/defer-blocked``) derived from those constants,
# and re-scoping it is a separate change.

#: Default recency window (minutes) for "recent meaningful activity".
#:
#: 30 minutes is the default because it (a) spans a normal agent turn's
#: dispatch→settle gap — a settled mirror receipt is fresh for 30 min, so a
#: LIVE mission keeps both gates closed; (b) is 2x the maintenance check
#: interval (15 min), giving one check of hysteresis; and (c) yields real
#: open windows on a 24/7 nest — measured over the last 24 h: 8 quiet
#: stretches longer than 30 min (the longest 113.6 min) against a p50
#: inter-arrival gap of 0.4 min.
DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES: Final[int] = 30

#: Env switch for the recency window (read PER EVALUATION, env-direct).
#:
#: * a positive integer ⇒ the recency window in minutes;
#: * ``0`` ⇒ the recency bound is DISABLED and the gates fall back to the
#:   pre-2026-11-24 UNBOUNDED witness set (the kill-switch);
#: * an unparseable or negative value ⇒ the DEFAULT window (never
#:   "disabled" — an ambiguous knob must never silently open a gate).
ENV_IDLE_ACTIVITY_WINDOW_MINUTES: Final[str] = "ENSEMBLE_IDLE_GATE_ACTIVITY_WINDOW_MINUTES"

#: Sentinel window value meaning "no recency bound" (legacy semantics).
IDLE_ACTIVITY_WINDOW_LEGACY: Final[int] = 0

#: Maximum number of witness rows a recency probe will materialise.
#:
#: The probes bind ``IDLE_WITNESS_LIMIT + 1`` so that an OVERFLOW is
#: detectable (the extra row is the overflow marker). Overflow ⇒ the probe
#: treats the system as BUSY (fail-closed): a witness set too large to
#: enumerate cannot be proven idle.
IDLE_WITNESS_LIMIT: Final[int] = 5000


def resolve_idle_activity_window_minutes(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the idle-gate recency window (minutes).

    Read PER EVALUATION from the process environment so an operator can
    retune (or disable, with ``0``) the bound without a daemon restart.

    Fail-safe posture: an absent, unparseable or negative value resolves to
    :data:`DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES`. A broken knob must never
    silently DISABLE the recency bound (that would re-open the starvation);
    only the explicit ``0`` disables it.
    """
    source = environ if environ is not None else os.environ
    raw = source.get(ENV_IDLE_ACTIVITY_WINDOW_MINUTES)
    if raw is None or not str(raw).strip():
        return DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "idle-gate activity window %r is not an integer — using the "
            "default of %d minutes (the recency bound stays ON)",
            raw,
            DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES,
        )
        return DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES
    if value < 0:
        logger.warning(
            "idle-gate activity window %r is negative — using the default "
            "of %d minutes (the recency bound stays ON)",
            raw,
            DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES,
        )
        return DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES
    return value


# ── Recency-bounded witness bodies ───────────────────────────────────────

#: Select list for the recency-bounded JOB witnesses. A module-private
#: literal — no external input ever reaches this string (no injection
#: surface).
_IDLE_JOB_WITNESS_SELECT: Final[str] = (
    " j.job_id AS job_id,"
    " j.created_at AS created_at,"
    " j.admission_state AS admission_state,"
    " j.job_type AS job_type,"
    " j.instance_id AS instance_id,"
    " i.status AS instance_status"
)

#: Recency-bounded JOB witness body — BACKGROUND scope (the background
#: queue's own lane is excluded so it cannot block itself; the Fix-2B
#: deadlock carve-out is preserved VERBATIM: a ``queued`` job row whose
#: linked Task is still ``pending`` is UNCLAIMABLE and must not hold the
#: gate).
#:
#: The busy-set disjunction is the SAME shared clause composition the
#: unbounded background body uses (:data:`_BACKGROUND_BUSY_DISJUNCTION`) —
#: the legacy clause (queued/active job row whose instance is absent or
#: non-terminal) OR the post-Fix-B settled-mirror clause (``message`` /
#: ``done`` whose instance is non-terminal).
IDLE_JOB_WITNESS_BODY_BACKGROUND: Final[str] = (
    "SELECT" + _IDLE_JOB_WITNESS_SELECT +
    " FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " LEFT JOIN task t ON t.work_id = j.job_id"
    " WHERE j.deleted_at IS NULL"
    " AND (q.queue_type IS NULL"
    "      OR q.queue_type NOT IN :excluded_queue_types)"
    " AND " + _BACKGROUND_BUSY_DISJUNCTION
    + " AND (j.admission_state != 'queued'"
    "      OR t.status IS NULL"
    "      OR t.status != 'pending')"
    " LIMIT :witness_limit"
)

#: Recency-bounded JOB witness body — MAINTENANCE scope (ANY queue type:
#: the maintenance sweeps contend with the whole nest, so the background
#: lane's own rows count as activity too — exactly as the pre-2026-11-24
#: maintenance probe did via ``list_all_pending`` + ``find_processing_jobs``).
#:
#: No Fix-2B carve-out here: the maintenance probe never had one (a queued
#: job row IS pending work for a sweep to defer behind).
IDLE_JOB_WITNESS_BODY_ALL_QUEUES: Final[str] = (
    "SELECT" + _IDLE_JOB_WITNESS_SELECT +
    " FROM job_queue_items j"
    " LEFT JOIN instances i ON j.instance_id = i.instance_id"
    " WHERE j.deleted_at IS NULL"
    " AND ( (j.admission_state IN ('queued', 'active')"
    "        AND (j.instance_id IS NULL"
    "             OR i.status NOT IN :terminal_statuses))"
    "       OR (j.job_type = 'message'"
    "           AND j.admission_state = 'done'"
    "           AND j.instance_id IS NOT NULL"
    "           AND i.status NOT IN :terminal_statuses) )"
    " LIMIT :witness_limit"
)

#: Select list for the recency-bounded TASK witnesses.
_IDLE_TASK_WITNESS_SELECT: Final[str] = (
    " t.work_id AS work_id,"
    " t.created_at AS created_at,"
    " t.status AS status,"
    " t.instance_id AS instance_id,"
    " i.status AS instance_status"
)

#: Recency-bounded TASK witness body — BACKGROUND scope.
#:
#: The shape is the mirror of ``TaskRepository.has_active_non_background_work``:
#: a RUNNING/PAUSED non-background task, or a PENDING non-background task
#: whose linked JobItem is NOT still ``queued`` (the Fix-2A deadlock
#: exclusion — a PENDING task whose JobItem is still queued is UNCLAIMABLE
#: and must not hold the gate), with the Phase-2 defensive exclusion of
#: tasks whose linked JobItem is already terminal (``done``/``dead``).
#:
#: The ``instances`` join is an INNER join (a task with no instance row is
#: not a witness) — identical to the task repository's predicate.
IDLE_TASK_WITNESS_BODY_BACKGROUND: Final[str] = (
    "SELECT" + _IDLE_TASK_WITNESS_SELECT +
    " FROM task t"
    " JOIN instances i ON t.instance_id = i.instance_id"
    " WHERE t.is_background = :is_background_false"
    " AND NOT EXISTS ("
    "      SELECT 1 FROM job_queue_items _qi"
    "      WHERE _qi.job_id = t.work_id"
    "        AND _qi.admission_state IN (:qi_done, :qi_dead)"
    "        AND _qi.deleted_at IS NULL)"
    " AND ( t.status IN (:status_running, :status_paused)"
    "       OR (t.status = :status_pending"
    "           AND NOT EXISTS ("
    "              SELECT 1 FROM job_queue_items _qi"
    "              WHERE _qi.job_id = t.work_id"
    "                AND _qi.admission_state = :qi_queued"
    "                AND _qi.deleted_at IS NULL)) )"
    " LIMIT :witness_limit"
)

#: Recency-bounded TASK witness body — MAINTENANCE scope.
#:
#: The mirror of ``TaskRepository.has_active_non_deferred_work`` (system-wide
#: probe): the DEFER flag is what the maintenance probe excludes (a
#: deferred task is not contention for the sweeps), and the same Fix-2A +
#: Phase-2 defensive exclusions apply.
IDLE_TASK_WITNESS_BODY_ALL_QUEUES: Final[str] = (
    "SELECT" + _IDLE_TASK_WITNESS_SELECT +
    " FROM task t"
    " JOIN instances i ON t.instance_id = i.instance_id"
    " WHERE t.is_deferred = :is_deferred_false"
    " AND NOT EXISTS ("
    "      SELECT 1 FROM job_queue_items _qi"
    "      WHERE _qi.job_id = t.work_id"
    "        AND _qi.admission_state IN (:qi_done, :qi_dead)"
    "        AND _qi.deleted_at IS NULL)"
    " AND ( t.status IN (:status_running, :status_paused)"
    "       OR (t.status = :status_pending"
    "           AND NOT EXISTS ("
    "              SELECT 1 FROM job_queue_items _qi"
    "              WHERE _qi.job_id = t.work_id"
    "                AND _qi.admission_state = :qi_queued"
    "                AND _qi.deleted_at IS NULL)) )"
    " LIMIT :witness_limit"
)


def _declare_idle_witness_binds(body: str) -> TextClause:
    """Attach the bindparams shared by the recency-bounded JOB bodies.

    The ALL-QUEUES (maintenance) body has no queue filter, so it does not
    declare ``:excluded_queue_types`` — declaring an unused bindparam would
    make it REQUIRED at execution time.
    """
    return text(body).bindparams(
        bindparam("terminal_statuses", expanding=True),
        bindparam("witness_limit"),
    )


def _declare_idle_witness_binds_background(body: str) -> TextClause:
    """Like :func:`_declare_idle_witness_binds` plus the queue exclusion."""
    return text(body).bindparams(
        bindparam("terminal_statuses", expanding=True),
        bindparam("excluded_queue_types", expanding=True),
        bindparam("witness_limit"),
    )


def _declare_idle_task_witness_binds(body: str) -> TextClause:
    """Attach the bindparams shared by the recency-bounded TASK bodies."""
    return text(body).bindparams(
        bindparam("witness_limit"),
        bindparam("status_running"),
        bindparam("status_paused"),
        bindparam("status_pending"),
        bindparam("qi_queued"),
        bindparam("qi_done"),
        bindparam("qi_dead"),
    )


def idle_job_witness_statement_background() -> TextClause:
    """Recency-bounded JOB witnesses — BACKGROUND scope."""
    return _declare_idle_witness_binds_background(IDLE_JOB_WITNESS_BODY_BACKGROUND)


def idle_job_witness_binds_background() -> dict[str, object]:
    """Bind values for :func:`idle_job_witness_statement_background`."""
    return {
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "excluded_queue_types": list(BACKGROUND_EXCLUDED_QUEUE_TYPES),
        "witness_limit": IDLE_WITNESS_LIMIT + 1,
    }


def idle_job_witness_statement_all_queues() -> TextClause:
    """Recency-bounded JOB witnesses — MAINTENANCE scope (any queue)."""
    return _declare_idle_witness_binds(IDLE_JOB_WITNESS_BODY_ALL_QUEUES)


def idle_job_witness_binds_all_queues() -> dict[str, object]:
    """Bind values for :func:`idle_job_witness_statement_all_queues`."""
    return {
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "witness_limit": IDLE_WITNESS_LIMIT + 1,
    }


def idle_task_witness_statement_background() -> TextClause:
    """Recency-bounded TASK witnesses — BACKGROUND scope."""
    return _declare_idle_task_witness_binds(IDLE_TASK_WITNESS_BODY_BACKGROUND)


def idle_task_witness_binds_background() -> dict[str, object]:
    """Bind values for :func:`idle_task_witness_statement_background`."""
    return {
        "witness_limit": IDLE_WITNESS_LIMIT + 1,
        "status_running": "running",
        "status_paused": "paused",
        "status_pending": "pending",
        "qi_queued": "queued",
        "qi_done": "done",
        "qi_dead": "dead",
        "is_background_false": False,
    }


def idle_task_witness_statement_all_queues() -> TextClause:
    """Recency-bounded TASK witnesses — MAINTENANCE scope."""
    return _declare_idle_task_witness_binds(IDLE_TASK_WITNESS_BODY_ALL_QUEUES)


def idle_task_witness_binds_all_queues() -> dict[str, object]:
    """Bind values for :func:`idle_task_witness_statement_all_queues`."""
    return {
        "witness_limit": IDLE_WITNESS_LIMIT + 1,
        "status_running": "running",
        "status_paused": "paused",
        "status_pending": "pending",
        "qi_queued": "queued",
        "qi_done": "done",
        "qi_dead": "dead",
        "is_deferred_false": False,
    }
