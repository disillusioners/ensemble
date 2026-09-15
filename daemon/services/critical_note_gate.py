"""Critical-notes R21 entry-gate predicate (cycle-neutral canonical home).

Single source of truth for the ACTIVE-note gate applied at the three
external pass-through surfaces (R21, LOCKED-L 2026-09-15):

* ``daemon/routers/projects.py::_get_critical_notes_safe``
* ``daemon/services/context_injection.py`` (``_mcp_rag_hint`` filter)
* ``daemon/tools/external_opencode.py`` (preload list-and-coerce path)

An entry counts as ACTIVE iff its ``superseded_by_id`` is ``None``.
Defensive against the empty-string class: a stray empty ``""`` pointer
must NOT bypass the filter — ``is not None`` is the authoritative
comparison. Accepts either a SQLModel row, a ``CriticalNotes``
BaseModel, or a plain dict (the surfaces pass different shapes, so the
predicate duck-types).

Why a dedicated module: the three surfaces live in the routers /
services / tools layers respectively. Housing the predicate in
``daemon.tools.critical_notes`` would force ``context_injection`` (a
low-level service imported by the tools layer itself) to import the
tool package at module load — a cycle hazard. This module imports
nothing but the standard library, so every layer can depend on it
safely. ``daemon/tools/critical_notes.py`` re-exports it under its
historical ``_is_active_critical_note`` name for compatibility.
"""

from __future__ import annotations

from typing import Any


def is_active_critical_note(entry: Any) -> bool:
    """R21 entry-gate predicate — an entry counts as ACTIVE iff its
    ``superseded_by_id`` is ``None``.

    Defensive against the empty-string class (a stray empty ``""``
    pointer must NOT bypass the filter — ``is not None`` is the
    authoritative comparison). Accepts either a SQLModel row, a
    ``CriticalNotes`` BaseModel, or a plain dict (the surfaces pass
    different shapes, so the predicate duck-types).
    """
    if isinstance(entry, dict):
        sid = entry.get("superseded_by_id")
    else:
        sid = getattr(entry, "superseded_by_id", None)
    return sid is None
