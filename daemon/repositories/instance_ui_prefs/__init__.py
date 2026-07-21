"""Instance UI preferences repository module.

DB-backed, per-instance record of UI-only "pinned" + "color tag"
preferences, surfaced through the ``PUT /api/instances/{id}/ui-prefs``
and ``DELETE /api/instances/{id}/ui-prefs`` endpoints and merged into
the ``GET /api/instances`` list at the API router layer.

Why a dedicated table
---------------------
The preferences are UI-only state — the agent-tool :class:`Instance`
model does not need (and historically has not had) any awareness of
them. Putting the data on a separate ``instance_ui_prefs`` table
keeps the agent-side ``instances`` schema insulated from
frontend-driven concerns, avoids contaminating ``Instance.to_dict()``,
and lets the UI prefs row be created lazily (most instances never get
pinned / tagged, so they have no row at all).

Global scope
------------
The table is keyed by ``instance_id`` with no user/auth scoping —
this daemon is single-tenant by design; the ``UI-only preferences``
are global across the process. If multi-tenant scoping becomes a
requirement, add a ``user_id`` column and a composite PK; the rest of
the code is structured so the swap is local.

Merge at the API router layer
-----------------------------
Reads (the ``list_instances`` / ``get_instance`` responses) merge
``pinned`` / ``pinned_at`` / ``color_tag`` into the Pydantic
``InstanceInfo`` model at the router level, NOT at the repository
layer (see :mod:`daemon.routers.instances`). The repository stays
generic — its methods operate on the prefs table directly without
needing to know about ``Instance`` shapes or Pydantic models.

Hard-delete cascade
-------------------
On a destructive instance hard-delete (``DELETE
/api/instances/{id}?hard_delete=true``), the prefs row is swept as
step 9b inside
:meth:`SQLModelInstanceRepository.hard_delete_tree` alongside the
other dependent tables, so a hard-deleted instance never leaves an
orphan prefs row behind.
"""

from .models import InstanceUiPrefs
from .repository import InstanceUiPrefsRepository

__all__ = [
    "InstanceUiPrefs",
    "InstanceUiPrefsRepository",
]
