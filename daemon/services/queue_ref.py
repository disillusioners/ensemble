"""Queue reference resolution for agent-facing job tools.

Job tools (``job_create``, ``job_list``, ``dlq_list``, ``queue_update``)
accept a ``queue_id`` parameter. Historically that parameter only ever
carried a literal queue ID (a UUID, or a seeded ``sys-fifo-<project_id>``
style ID). This module adds **system-queue aliases**: agents may pass a
friendly name (``"parallel"``, ``"system_parallel_queue"``, ``"FIFO"``, ...)
and the tool layer resolves it to the project's actual queue row before
hitting the service layer.

Precedence (documented, deterministic — see ``resolve_queue_ref``):

1. Exact in-project queue-ID lookup (works for UUID-shaped IDs AND seeded
   ``sys-fifo-<project_id>`` style IDs). A hit that belongs to another
   project is NOT a match — it falls through (and ultimately errors), so a
   cross-project ID can never be silently used.
2. Alias map — both full system names and short aliases, case-insensitive —
   to the canonical system name, then an in-project ``get_by_name`` lookup.
   Short aliases map DIRECTLY to canonical system names. A user-created
   queue literally named ``parallel`` can never shadow the alias: the alias
   always wins (``get_by_name(project, "parallel")`` is never attempted).
3. No match → ``None``. The CALLER raises/reports the error.

This module is deliberately service-layer-only read helpers: it NEVER
writes, and ``JobQueueService.enqueue`` semantics are untouched (unknown
non-alias IDs keep the service's soft-fail behavior, regression-pinned by
``tests/job_queue/test_task_queue_service.py``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from daemon.repositories.job_queue.models import JobQueue
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository

logger = logging.getLogger(__name__)

# Canonical full names of the five reserved system queues
# (mirrors RESERVED_QUEUE_NAMES in job_queue_mgmt_service — kept literal
# here so this module has no import cycle with the mgmt service).
_CANONICAL_SYSTEM_QUEUES: tuple[str, ...] = (
    "system_fifo_queue",
    "system_parallel_queue",
    "system_background_queue",
    "system_defer_queue",
    "system_kb_fifo_queue",
)

# Short alias per canonical name. Short aliases map DIRECTLY to the
# canonical system name — never to a bare-name lookup (user queues CAN be
# named "parallel" today; the alias must always win deterministically).
_SHORT_ALIASES: dict[str, str] = {
    "system_fifo_queue": "fifo",
    "system_parallel_queue": "parallel",
    "system_background_queue": "background",
    "system_defer_queue": "defer",
    "system_kb_fifo_queue": "kb_fifo",
}

#: Case-insensitive alias map: lowercase key (full system name OR short
#: alias) → canonical full system name.
QUEUE_ALIAS_TO_CANONICAL: dict[str, str] = {
    name: name for name in _CANONICAL_SYSTEM_QUEUES
}
QUEUE_ALIAS_TO_CANONICAL.update(
    {short: canonical for canonical, short in _SHORT_ALIASES.items()}
)


def is_known_alias_name(ref: str | None) -> bool:
    """True when ``ref`` names a system queue (full name or short alias).

    Case-insensitive. Callers use this to decide strict-vs-soft error
    handling: a known name that fails to resolve is a hard error, while an
    unknown non-alias string (e.g. a stale UUID) keeps the service layer's
    existing soft-fail semantics (pass through untouched).
    """
    if not ref:
        return False
    return ref.strip().lower() in QUEUE_ALIAS_TO_CANONICAL


def resolve_queue_ref(
    repo: "JobQueueRepository | None",
    project_id: str | None,
    ref: str | None,
) -> "JobQueue | None":
    """Resolve a queue reference (ID or system-queue alias) to a queue row.

    Precedence:

    a. Exact in-project queue-ID lookup. Works for UUID-shaped IDs and
       seeded ``sys-fifo-<project_id>`` style IDs alike (both are primary
       keys). A row that exists but belongs to ANOTHER project is not a
       match — fall through (the caller then surfaces the error), so a
       cross-project ID is never silently used.
    b. Alias map (full system names + short aliases, case-insensitive) →
       canonical name → in-project ``get_by_name`` (case-insensitive by
       design).
    c. No match → ``None``; the CALLER raises the error.

    Args:
        repo: JobQueueRepository (or None — callers without repo plumbing
            degrade to pass-through; ``None`` in → ``None`` out).
        project_id: Project scope for the lookup.
        ref: Raw ``queue_id`` string as supplied by the caller.

    Returns:
        The matching in-project JobQueue, or None when unresolved.
    """
    if repo is None:
        return None
    if not ref or not ref.strip():
        return None
    ref = ref.strip()

    # (a) Exact ID lookup — UUID-shaped and seeded sys-* IDs alike.
    queue = repo.get(ref)
    if queue is not None and queue.project_id == project_id:
        return queue
    # A row from ANOTHER project is NOT a match: fall through to the alias
    # path (which won't match an ID-shaped ref) so the caller errors loudly
    # instead of silently using (or silently dropping) a cross-project ID.

    # (b) Alias path — canonical name, project-scoped.
    canonical = QUEUE_ALIAS_TO_CANONICAL.get(ref.lower())
    if canonical is not None:
        return repo.get_by_name(project_id, canonical)

    # (c) No match.
    return None


def describe_valid_queues(
    repo: "JobQueueRepository | None",
    project_id: str | None,
) -> str:
    """Human-readable listing of valid queue references for error dicts.

    Lists the five system queues (full name + short alias) always, and the
    project's actual queues (name + id) when repo plumbing is available.
    Never raises — a listing failure degrades to the static alias list.
    """
    lines = [
        "Valid queue references:",
        "System queues (full name or short alias, case-insensitive):",
    ]
    lines.extend(
        f"  {canonical} | {_SHORT_ALIASES[canonical]}"
        for canonical in _CANONICAL_SYSTEM_QUEUES
    )
    if repo is not None and project_id:
        try:
            queues = list(repo.list_by_project(project_id))
        except Exception as e:  # listing is best-effort for an error message
            logger.warning("queue_ref: could not list queues for project %s: %s", project_id, e)
            queues = []
        if queues:
            lines.append("Project queues:")
            lines.extend(f"  {q.queue_name} (id: {q.queue_id})" for q in queues)
        else:
            lines.append("(no project-specific queues)")
    return "\n".join(lines)
