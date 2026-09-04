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

Both bodies are plain ``text()``-compatible strings, fully
parameter-bound: the status / queue-type sets ride ``expanding``
bindparams declared by the statement helpers below (never f-string
value interpolation — no injection surface, and one bind plan for both
SQLite and PostgreSQL). The two bodies share the busy-set clause text
via composition (legacy + mirror clause fragments, composed into the
project-scoped and system-wide bodies at module load time).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import bindparam, text
from sqlalchemy.sql.elements import TextClause

from daemon.constants import TERMINAL_INSTANCE_STATUSES

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

#: The defer busy-set disjunction (defer legacy OR mirror). Shared by
#: both defer bodies (project-scoped + system-wide) — splicing this
#: (rather than re-stating the two clauses inline) makes it impossible
#: for the defer bodies' semantics to drift apart.
_DEFER_BUSY_DISJUNCTION: Final[str] = (
    "(\n"
    "      " + _LEGACY_CLAUSE + "\n"
    "      OR\n"
    "      " + _MIRROR_CLAUSE + "\n"
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


def defer_busy_statement(project_id: str | None) -> TextClause:
    """Return the defer busy-set body as an executable TextClause.

    Selects the project-scoped body when ``project_id`` is not None
    and the system-wide body when it is ``None``. The two-body split
    keeps a bare ``IS NULL`` parameter comparison off every code path
    on both dialects — the PG ``AmbiguousParameter`` incident is
    impossible by construction.
    """
    body = (
        JOB_DEFER_BUSY_BODY_PROJECT
        if project_id is not None
        else JOB_DEFER_BUSY_BODY_SYSTEM
    )
    if project_id is not None:
        return _declare_expanding_binds_project(body)
    return _declare_expanding_binds(body)


def defer_busy_binds(project_id: str | None) -> dict[str, object]:
    """Bind values for :func:`defer_busy_statement`.

    ``project_id=None`` selects the system-wide scope (maintenance
    ``_is_idle``); a set project selects the project scope (Gate A/B
    defer lanes).
    """
    binds: dict[str, object] = {
        "terminal_statuses": list(JOB_TERMINAL_STATUSES),
        "excluded_queue_types": list(DEFER_EXCLUDED_QUEUE_TYPES),
    }
    if project_id is not None:
        binds["project_id"] = project_id
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


def defer_busy_witness_statement(project_id: str | None) -> TextClause:
    """Return the defer busy-set WITNESS body as an executable TextClause.

    The enumeration counterpart of :func:`defer_busy_statement`: instead
    of ``EXISTS(...)`` it returns one row per busy-set witness JobItem
    (joined instance columns included). Body selection and bindparam
    declaration follow the exact same two-body discipline as the gate
    (project-scoped vs system-wide on ``project_id``; no bare-NULL
    parameter comparison on either dialect).

    READ-ONLY consumers only — the statement is a bare SELECT; the
    transparency surface must never gain a write on this path (census
    contract, docs §8.5).
    """
    body = (
        JOB_DEFER_BUSY_WITNESS_BODY_PROJECT
        if project_id is not None
        else JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM
    )
    if project_id is not None:
        return _declare_expanding_binds_project(body)
    return _declare_expanding_binds(body)


def defer_busy_witness_binds(project_id: str | None) -> dict[str, object]:
    """Bind values for :func:`defer_busy_witness_statement`.

    Delegates to :func:`defer_busy_binds` — the witness body is derived
    from the gate body, so the parameter contract is shared BY
    CONSTRUCTION and cannot drift.
    """
    return defer_busy_binds(project_id)
