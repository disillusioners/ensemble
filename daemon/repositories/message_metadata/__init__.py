"""``message_metadata`` side-table repository (Phase 1 C2).

Persists per-(``thread_id``, ``message_id``) timestamps fired from the
four approved tap sites (decisions.md D1):

* ``user_message_entry`` — at ``_build_graph_input`` (F1 fix).
* ``agent_node_return`` — single-return refactor of the F2 dual-return
  block at ``daemon/graph.py:3386-3397``.
* ``compaction_aupdate_reactive`` — after the reactive-compaction
  ``aupdate_state`` at ``daemon/graph.py:3248-3250``.
* ``compaction_aupdate_messaging`` — after the messaging-side
  ``aupdate_state`` at ``daemon/services/instance_messaging.py:810-822``.

The repository is intentionally SYNC (D14) — it matches the shared
engine factory contract at ``daemon/repositories/factory.py:10`` and
the call sites bridge via ``asyncio.to_thread``. The dual-driver
migration is in
``daemon/migrations/versions/20260825_000001_create_message_metadata.sql``
(SQLite-only) + a matching ``_ensure_postgres_columns()`` block in
``daemon/manager.py`` (existing PG databases) — see decisions.md D2.

Tool messages are NEVER tapped; the ``serialize_message`` ``type=='tool'``
skip at ``daemon/persistence.py:406`` keeps tool messages out of the
GET /messages response output regardless of timestamp correctness (LD-D2
+ decisions.md D10 / D18 / D20).
"""

from .models import MessageMetadata
from .repository import MessageMetadataRepository

__all__ = [
    "MessageMetadata",
    "MessageMetadataRepository",
]
