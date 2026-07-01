"""Work-status canonicalization helpers.

Phase 1 of the Virtual Job Management Surface feature
(``feature/virtual-job-management-surface``): a single source of truth
for translating between the per-table status strings used by the Task
table and the JobItem table and the unified vocabulary the virtual job
resolver speaks.

Two tables, two vocabularies
----------------------------

The worker pool's ``task`` table (``daemon.repositories.task.models``)
and the dependency bus's ``job_queue_items`` table
(``daemon.repositories.job_queue.models``) each track their own
lifecycle, and the strings they store are not identical:

* ``task.status`` is a :class:`daemon.repositories.task.models.TaskStatus`
  enum value: ``pending``, ``running``, ``paused``, ``completed``,
  ``failed``, ``cancelled``.
* ``job_queue_items.status`` (legacy, dropped in Phase 5) used to be a
  JobStatus enum value: ``pending``, ``processing``, ``paused``,
  ``completed``, ``failed``, ``cancelled``, ``dead_letter``. The
  legacy enum was removed in Phase 7b — the JobItem table now
  carries only ``admission_state``, and execution lifecycle is read
  from the joined ``Instance``.

Notably ``task`` uses ``running`` while ``job_queue_items`` uses
``processing`` for the in-flight state, and only ``job_queue_items``
models the dead-letter terminal state. The virtual job surface is a
single read API — callers should not have to know which table backs a
particular work_id — so it speaks a canonical vocabulary that maps
both source vocabularies onto a single set of strings.

Canonical vocabulary
--------------------

Defined by :data:`_STATUS_CANONICAL_MAP`:

* ``pending`` — work accepted but not yet started
* ``processing`` — work currently in flight (Task ``running`` → ``processing``)
* ``paused`` — work explicitly suspended and resumable (non-terminal)
* ``completed`` — terminal: finished successfully
* ``failed`` — terminal: finished with an error (may be retried by the
  job system, but from the resolver's POV a failure has occurred)
* ``cancelled`` — terminal: explicitly cancelled before completion
* ``dead_letter`` — terminal: failed after exhausting retries; only
  ever produced by ``JobItem``

``paused`` is intentionally **not** terminal — see :func:`is_terminal`.
"""

from __future__ import annotations

from typing import Final

from daemon.repositories.job_queue.models import _ADMISSION_TO_LEGACY_STATUS


# ── Canonical status mapping ──────────────────────────────────────────────
# Single dict merging Task-side and JobItem-side source values onto the
# canonical vocabulary. Order is not significant; the dict is the
# authoritative table. Adding a new source status is a one-line change
# here — every caller (resolvers, UI surfaces, tests) picks it up
# automatically.

_STATUS_CANONICAL_MAP: Final[dict[str, str]] = {
    # Task-side source values
    "pending": "pending",
    "running": "processing",
    "paused": "paused",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    # Instance-side source values (Phase 1, Job as Queue Proxy).
    # ``Instance.status`` is the execution authority for JobItem rows
    # once ``job.instance_id`` is set. The mappings below are the
    # canonical-vocabulary translation of the 10 ``InstanceStatus``
    # enum values. The "active" cluster (``waiting`` /
    # ``waiting_children`` / ``idle`` / ``queued`` / ``running``) all
    # collapse onto ``processing`` because from the resolver's POV
    # these are non-terminal "work is happening" states; finer-grained
    # detail is available to consumers via the Instance detail view.
    # Terminal-cluster mappings (completed → completed, error/failed →
    # failed, terminated → cancelled) preserve the Plan §2.1 terminal
    # classification invariant.
    "idle": "processing",
    "waiting": "processing",
    "waiting_children": "processing",
    "queued": "processing",
    "error": "failed",
    "terminated": "cancelled",
    # Phase 4 (Job as Queue Proxy) admission-state source value — the
    # JobItem ``admission_state`` spelling of the dead admission state.
    # This is the canonical mapping used by ``_job_to_record`` (when a
    # JobItem row has ``admission_state='dead'``) and by the
    # JobItem-side reverse map ``_JOB_CANONICAL_TO_ADMISSION`` in
    # ``work_resolver``. The legacy JobItem ``status='dead_letter'``
    # source key was removed in Phase 4 cleanup — the ``status``
    # column is no longer written, so a reverse lookup on the legacy
    # spelling is no longer needed.
    "dead": "dead_letter",
    # Phase 7c: ``terminal_reason`` discriminator source values. The
    # discriminator records HOW a job terminated when
    # ``admission_state='done'``. ``"aborted"`` collapses onto
    # ``"cancelled"`` because the work-surface vocabulary has no
    # distinct "aborted" state — an aborted job (killed by its
    # parent's instance-terminate cascade) is semantically a
    # cancellation from the work-record consumer's POV. ``completed``
    # / ``failed`` / ``cancelled`` map to themselves (they're already
    # in the canonical vocabulary). See
    # ``work_resolver._job_to_record`` — these are the canonical
    # targets for ``canonicalize_status(terminal_reason)``.
    "aborted": "cancelled",
}


# ── Terminal status set ──────────────────────────────────────────────────
# Terminal = the work unit will not transition again under normal
# resolver operation. ``paused`` is intentionally excluded — it is a
# non-terminal suspended state that may transition back to ``processing``
# on resume. ``dead_letter`` is terminal even though it originated on
# the JobItem side; from the resolver's POV nothing else happens.

_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled", "dead_letter"}
)


def canonicalize_status(status: str) -> str:
    """Map a Task or JobItem status string onto the canonical virtual-job vocabulary.

    The canonical vocabulary is:

    * ``pending`` — accepted, not yet started
    * ``processing`` — in flight (maps both Task ``running`` and JobItem
      ``processing`` onto this single label)
    * ``paused`` — suspended, resumable
    * ``completed`` — terminal: succeeded
    * ``failed`` — terminal: errored
    * ``cancelled`` — terminal: cancelled
    * ``dead_letter`` — terminal: failed past max retries (JobItem only)

    Unknown values are returned unchanged. This is defensive: the
    resolver is read-only and should not crash on a future status the
    map has not been taught about — logging/metrics on the caller side
    can flag it, but the read path must keep working.

    Args:
        status: A status string from ``task.status`` or
            ``job_queue_items.status``.

    Returns:
        The canonical status string. If ``status`` is not in the map,
        returns it verbatim.
    """
    return _STATUS_CANONICAL_MAP.get(status, status)


def is_terminal(status: str) -> bool:
    """Return True if ``status`` is a terminal virtual-job state.

    Terminal statuses are: ``completed``, ``failed``, ``cancelled``,
    ``dead_letter``. ``paused`` is **not** terminal — a paused work
    unit can be resumed back to ``processing``. ``pending`` and
    ``processing`` are also non-terminal.

    The check operates on the canonical vocabulary. Callers that
    receive raw Task/JobItem status strings should first run them
    through :func:`canonicalize_status`.

    Args:
        status: A status string (typically already canonicalized).

    Returns:
        True if ``status`` is in :data:`_TERMINAL_STATUSES`, False
        otherwise. Unknown statuses are treated as non-terminal so the
        conservative answer ("it might still move") is returned for
        any future status the canonical vocabulary has not been
        taught about.
    """
    return status in _TERMINAL_STATUSES


# ── Legacy-status derivation (F16 fix) ────────────────────────────────────
# The JobItem ``admission_state`` column collapses three terminal
# outcomes (completed / failed / cancelled) plus the instance-cascade
# ``aborted`` onto a single ``done`` value. The legacy API vocabulary
# still distinguishes those outcomes, so read paths must consult
# ``terminal_reason`` to recover the fine-grained status.
#
# The primary read path (``WorkResolverService._job_to_record``) already
# does this via the F3 fix — but four production fallback paths (used
# when the resolver is unwired / unreachable) historically derived
# ``status`` straight from ``_ADMISSION_TO_LEGACY_STATUS`` without
# consulting ``terminal_reason``, so a ``done`` job with
# ``terminal_reason='failed'`` incorrectly surfaced as ``"completed"``.
#
# This helper centralises the derivation so all four sites use the
# same priority chain:

def _derive_legacy_status(
    admission_state: str, terminal_reason: str | None
) -> str:
    """Resolve the legacy API status string for a JobItem row.

    F16 fix: the lossy :data:`_ADMISSION_TO_LEGACY_STATUS` map
    collapses ``done → completed`` regardless of ``terminal_reason``.
    Four legacy fallback paths (used when ``WorkResolverService`` is
    unwired / unreachable — see the F16 deferral note in the defer-seam
    bugfix plan) historically called the map directly and so
    mis-reported ``failed`` / ``cancelled`` jobs as ``"completed"``.

    This helper implements the same priority chain as the F3 fix in
    :meth:`WorkResolverService._job_to_record`, applied directly to
    the JobItem row:

    1. ``admission_state='done'`` AND ``terminal_reason`` set →
       canonicalise the discriminator (``"failed"`` / ``"cancelled"``
       pass through; ``"aborted"`` collapses onto ``"cancelled"``
       because the work-record vocabulary has no distinct aborted
       state).
    2. ``admission_state='done'`` AND ``terminal_reason`` is ``None``
       → the lossy ``done → completed`` map value. This preserves
       backward compatibility for pre-Phase-7c rows where the
       ``terminal_reason`` column did not exist.
    3. Any other ``admission_state`` (``"queued"`` / ``"active"`` /
       ``"dead"``) → the lossy map value. ``terminal_reason`` is not
       consulted for non-terminal admission states because the
       terminal-write boundary (``JobQueueService._finalize_terminal``)
       always pairs an ``active → done`` transition with a
       ``terminal_reason`` write, and ``dead`` rows have no
       ``terminal_reason`` discriminator (they're a separate queue
       endpoint).

    Args:
        admission_state: The JobItem ``admission_state`` column value
            (``"queued"`` / ``"active"`` / ``"done"`` / ``"dead"``).
        terminal_reason: The JobItem ``terminal_reason`` column value,
            or ``None`` for pre-7c rows and non-terminal states.

    Returns:
        A valid legacy status string suitable for ``JobResponse.status``
        — one of ``"pending"``, ``"processing"``, ``"completed"``,
        ``"failed"``, ``"cancelled"``, ``"dead_letter"``. Falls back
        to ``"pending"`` for unknown ``admission_state`` values
        (matches the pre-F16 behaviour).
    """
    if admission_state == "done" and terminal_reason:
        # Phase 7c: terminal_reason is the discriminator for done rows.
        # canonicalize_status handles the ``"aborted"`` → ``"cancelled"``
        # mapping and is a no-op for ``"completed"`` / ``"failed"`` /
        # ``"cancelled"``. Unknown terminal_reason values (a future
        # discriminator the canonical map has not been taught about)
        # pass through unchanged, matching the resolver's defensive
        # behaviour.
        return canonicalize_status(terminal_reason)
    # Backward-compat: pre-7c rows (NULL ``terminal_reason``) and all
    # non-done states fall through the lossy map. ``"pending"`` is the
    # default for unknown ``admission_state`` values.
    return _ADMISSION_TO_LEGACY_STATUS.get(admission_state, "pending")
