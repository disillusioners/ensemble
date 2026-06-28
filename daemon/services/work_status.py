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
* ``job_queue_items.status`` is a
  :class:`daemon.repositories.job_queue.models.JobStatus`` enum value:
  ``pending``, ``processing``, ``paused``, ``completed``, ``failed``,
  ``cancelled``, ``dead_letter``.

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
    # Phase 4 (Job as Queue Proxy): ``processing`` is the legacy
    # JobItem-side in-flight spelling (kept here as a SOURCE KEY so
    # the resolver's reverse map ``_CANONICAL_TO_SOURCES`` can find
    # it via SQL — ``_query_jobs`` splits terminal vs non-terminal
    # status filters and routes non-terminal to ``admission_state``
    # while still honoring the legacy ``status='processing'``
    # mirror). Task uses ``running`` so this is a JobItem-only
    # source. The dual-write contract in Phase 2 keeps
    # ``status='processing'`` and ``admission_state='active'``
    # co-moved, so a single source key covers both reads.
    "processing": "processing",
    # ``dead_letter`` stays as a canonical terminal value for
    # backward compatibility with callers that still read the
    # legacy vocabulary. The JobItem ``admission_state='dead'``
    # spelling maps to ``dead_letter`` below — both source keys
    # collapse onto the same canonical terminal.
    "dead_letter": "dead_letter",
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
    # Phase 4 admission-state source value — the JobItem column
    # spelling of the dead admission state. The legacy ``dead_letter``
    # source key above preserves backward compatibility with callers
    # that still write ``status='dead_letter'`` in transition code
    # (it co-moves with ``admission_state='dead'`` via the dual-write
    # contract introduced in Phase 2).
    "dead": "dead_letter",
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
