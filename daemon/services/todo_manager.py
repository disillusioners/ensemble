"""Per-instance in-memory todo DAG state manager.

Replaces the previous flat-list TodoManager with a directed acyclic graph
(DAG) implementation. Each instance owns a graph stored as
``dict[node_id, TodoNode]``; edges are stored as adjacency lists
(``next_ids``) on each node. The manager enforces DAG validity (no cycles)
on all structural mutations and exposes both graph primitives
(``add_node``, ``add_edge``, ``remove_edge``, ``remove_node``) and the
legacy flat-list API (``create(instance_id, list[str])``) which auto-
converts to a linear chain for backward compatibility.

Todos are not persisted; they exist for the lifetime of the daemon
process and are used by the todo tool surface during a single instance
run.

Thread Safety:
    All state mutations and snapshot reads are guarded by a single
    :class:`threading.Lock` (NOT :class:`asyncio.Lock`). Helpers that
    require the lock (``_has_cycle``, ``_compute_reminder``) document
    that the caller must already hold it.

Frozen SSE Payload Schema:
    :meth:`TodoGraphManager._to_dict` defines the JSON-serializable
    shape that downstream tools (Phase 2), API routes (Phase 3), and the
    Angular frontend (Phase 4) build against. Once published, the dict
    shape — exactly seven keys: ``id``, ``index``, ``text``, ``status``,
    ``comment``, ``next_ids``, ``subtasks`` — is a contract and must not
    change without cross-phase coordination. Schema evolved from v1
    (six keys) to v2 (seven keys) when ``subtasks`` was added in
    Phase 1 of the todo-subtasks feature.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_VALID_STATUSES = ("pending", "in_progress", "done")

# Hard cap on a single todo comment, in characters. Enforced at the HTTP
# boundary (``daemon.routers.instances.set_todo_comment``) which maps an
# over-length comment to a 400. This module re-enforces the same cap
# inside :meth:`TodoGraphManager.set_comment` as defense in depth so any
# non-HTTP caller (tools, scripts, future internal jobs) cannot bypass
# the limit.
MAX_COMMENT_LENGTH = 1000

# Aliases mapping common variants (including case variants) to canonical
# statuses. Lookup is performed against the lower-cased input.
_STATUS_ALIASES: dict[str, str] = {
    "completed": "done",
    "complete": "done",
    "closed": "done",
    "resolved": "done",
    "finished": "done",
    "started": "in_progress",
    "wip": "in_progress",
    "doing": "in_progress",
    "active": "in_progress",
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "in_progress": "in_progress",
    "cancelled": "pending",
    "canceled": "pending",
    "pending": "pending",
    "done": "done",
}


def _normalize_status(status: str) -> str | None:
    """Normalize a status string to its canonical value, or ``None`` if invalid.

    Case-insensitive: input is lowercased before lookup. Empty strings and
    unrecognized values return ``None`` so callers can raise or reject.
    """
    if not isinstance(status, str):
        return None
    key = status.strip().lower()
    if not key:
        return None
    return _STATUS_ALIASES.get(key)


# Hard cap on subtasks per node and per subtask text length. Enforced at all
# three entry points (add_subtask, create_graph per-node, add_node) so any
# caller — HTTP, tool, internal job — is bounded consistently.
MAX_SUBTASKS_PER_NODE = 20
MAX_SUBTASK_TEXT_LENGTH = 500

# Aliases for sub-task statuses. STRICTLY BINARY: subtasks are either
# "pending" or "done". "in_progress" and its aliases are intentionally
# REJECTED — sub-tasks are a simple checklist, not a multi-state workflow.
# Lookup is case-insensitive (input is lowercased before lookup).
_SUBTASK_STATUS_ALIASES: dict[str, str] = {
    "done": "done",
    "completed": "done",
    "complete": "done",
    "closed": "done",
    "resolved": "done",
    "finished": "done",
    "pending": "pending",
    "todo": "pending",
    "cancelled": "pending",
    "canceled": "pending",
}


def _normalize_subtask_status(status: str) -> str | None:
    """Normalize a sub-task status string to its canonical value, or ``None`` if invalid.

    Case-insensitive: input is lowercased before lookup. Empty strings and
    unrecognized values return ``None`` so callers can raise or reject.

    NOTE: Unlike :func:`_normalize_status` for node statuses, sub-task statuses
    are STRICTLY BINARY ("pending" or "done"). ``in_progress`` and any of its
    aliases (e.g., "started", "wip", "doing") are NOT accepted — sub-tasks are
    a simple checklist, not a multi-state workflow.
    """
    if not isinstance(status, str):
        return None
    key = status.strip().lower()
    if not key:
        return None
    return _SUBTASK_STATUS_ALIASES.get(key)


@dataclass
class TodoNode:
    """A single node in the todo DAG.

    Identity is the string ``id`` (prefixed ``n-`` to guarantee non-
    numeric). Edges are stored as ``next_ids`` — a list of node IDs that
    follow this node. A node with empty ``next_ids`` is a terminal/sink
    node.

    The ``index`` field is preserved for backward compatibility: it is
    derived from insertion order and included in serialized output so
    existing consumers that reference ``item["index"]`` continue to work.

    The ``subtasks`` field is a STRICTLY BINARY checklist (each item is
    either ``"pending"`` or ``"done"``) for tracking fine-grained
    acceptance criteria under a parent node. See :class:`SubTask`.
    """

    id: str
    text: str
    status: str
    comment: str = ""
    next_ids: list[str] = field(default_factory=list)
    index: int = 0
    subtasks: list[SubTask] = field(default_factory=list)


@dataclass
class SubTask:
    """A checklist item nested within a :class:`TodoNode`.

    Sub-tasks are a STRICTLY BINARY checklist — each item is either
    ``"pending"`` or ``"done"``. The richer ``"in_progress"`` state used
    on parent nodes is intentionally NOT supported here: sub-tasks model
    fine-grained acceptance criteria, and partial completion is the
    parent's job to track, not the sub-task's.

    Identity is the ``id`` field, formatted ``"s-" + uuid.uuid4().hex[:8]``
    — the ``s-`` prefix guarantees the ID is never all-numeric, preventing
    collision with the API's numeric-index backward-compat path.
    """

    id: str
    text: str
    status: str

    @staticmethod
    def _to_dict(st: "SubTask") -> dict:
        """Serialize a :class:`SubTask` to a plain dict (exactly three keys)."""
        return {"id": st.id, "text": st.text, "status": st.status}


class TodoGraphManager:
    """In-memory, per-instance todo DAG manager.

    Each ``instance_id`` owns a graph stored as
    ``dict[node_id, TodoNode]``. The manager exposes create/update/get
    primitives that return plain dicts for JSON-serializable transport
    to tool callers, plus graph-mutation primitives
    (``add_node``, ``add_edge``, ``remove_edge``, ``remove_node``) and
    structural validation (cycle detection via Kahn's algorithm).

    Thread Safety:
        All state mutations and snapshot reads are serialized through a
        single :class:`threading.Lock`. Helpers that assume the lock is
        held (``_has_cycle``, ``_compute_reminder``) document this
        requirement; they must NOT be called from outside a ``with
        self._lock:`` scope.
    """

    # Hard cap on nodes per instance. Enforced in ``create`` (flat-list
    # path), ``create_graph`` (explicit graph path), and ``add_node``
    # (incremental path). Mirrors the documented 16-alias status
    # normalization budget — small enough to fit comfortably in SSE
    # payloads, large enough for realistic plans.
    MAX_NODES = 200

    def __init__(self) -> None:
        """Initialize the manager with empty per-instance graphs."""
        self._instance_graphs: dict[str, dict[str, TodoNode]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public creation API
    # ------------------------------------------------------------------

    def create(self, instance_id: str, items: list[str]) -> list[dict]:
        """Backward-compatible create: flat list → linear chain DAG.

        ``["A", "B", "C"]`` becomes three nodes
        ``n-xxxxxx (A) → n-yyyyyy (B) → n-zzzzzz (C)`` with
        ``next_ids = ["n-yyyyyy"], ["n-zzzzzz"], []`` respectively.

        Each node also gets an ``index`` field (0, 1, 2, ...) for
        backward compatibility with the flat-list API.

        Args:
            instance_id: Owning instance identifier.
            items: Ordered list of todo text entries.

        Returns:
            The newly stored node list as plain dicts (seven keys each:
            ``id``, ``index``, ``text``, ``status``, ``comment``,
            ``next_ids``, ``subtasks``).

        Raises:
            ValueError: If ``len(items)`` exceeds :data:`MAX_NODES`.
        """
        if len(items) > self.MAX_NODES:
            raise ValueError(
                f"Cannot create {len(items)} todo nodes: exceeds maximum of "
                f"{self.MAX_NODES}. Use todo_clear() to reset first."
            )
        node_ids = [self._generate_id() for _ in items]
        nodes: dict[str, TodoNode] = {}
        for i, text in enumerate(items):
            next_ids = [node_ids[i + 1]] if i + 1 < len(items) else []
            nodes[node_ids[i]] = TodoNode(
                id=node_ids[i],
                text=text,
                status="pending",
                comment="",
                next_ids=next_ids,
                index=i,
            )
        with self._lock:
            self._instance_graphs[instance_id] = nodes
            return [self._to_dict(node) for node in nodes.values()]

    def create_graph(
        self,
        instance_id: str,
        nodes: list[dict],
        edges: list[dict],
    ) -> list[dict]:
        """Create a graph from explicit nodes + edges.

        Args:
            instance_id: Owning instance identifier.
            nodes: List of node dicts ``{"id": str, "text": str,
                "next_ids"?: list[str]}``. User-supplied IDs must be
                non-empty and NOT all-numeric (reserving the numeric
                form for the backward-compat index path).
            edges: List of edge dicts ``{"from": str, "to": str}``.
                Node IDs referenced in ``edges`` must appear in
                ``nodes`` (or be auto-discovered via ``next_ids``).

        Returns:
            The newly stored node list as plain dicts.

        Raises:
            ValueError: If any user-supplied ID is all-numeric, any
                ``next_ids``/edge reference is dangling, the graph
                contains a cycle, the node count exceeds
                :data:`MAX_NODES`, or duplicate node IDs are supplied.
        """
        # Pre-flight: count check before validation so size-violations
        # are reported cleanly.
        if len(nodes) > self.MAX_NODES:
            raise ValueError(
                f"Cannot create {len(nodes)} todo nodes: exceeds maximum of "
                f"{self.MAX_NODES}. Use todo_clear() to reset first."
            )

        # Validate user-supplied IDs (C3 mitigation): an all-numeric ID
        # collides with the API's ``node_id.isdigit()`` backward-compat
        # path which routes to ``set_comment_by_index``. Reject fast
        # with a clear error rather than silently auto-prefixing.
        for node_spec in nodes:
            nid = node_spec.get("id", "")
            if not isinstance(nid, str) or not nid:
                raise ValueError(
                    f"Node spec missing required 'id' field: {node_spec!r}"
                )
            if nid.isdigit():
                raise ValueError(
                    f"Node id {nid!r} is all-numeric and would collide "
                    f"with the index-based backward-compat path. Use a "
                    f"non-numeric id (e.g. 'n-{nid}' or 'step-{nid}')."
                )

        # Validate duplicate IDs among user-supplied nodes.
        seen_ids: set[str] = set()
        for node_spec in nodes:
            nid = node_spec["id"]
            if nid in seen_ids:
                raise ValueError(
                    f"Duplicate node id {nid!r} in nodes list."
                )
            seen_ids.add(nid)

        # Validate per-node ``subtasks`` lists. Each list is parsed and
        # turned into a list of :class:`SubTask` objects before the
        # ``TodoNode`` constructor runs, so the constructor receives
        # already-validated data.
        parsed_subtasks: dict[str, list[SubTask]] = {}
        for node_spec in nodes:
            nid = node_spec["id"]
            raw_subtasks = node_spec.get("subtasks")
            if raw_subtasks is None:
                parsed_subtasks[nid] = []
                continue
            if not isinstance(raw_subtasks, list):
                raise ValueError(
                    f"Node {nid!r} 'subtasks' must be a list, got "
                    f"{type(raw_subtasks).__name__}."
                )
            parsed_subtasks[nid] = self._parse_subtask_specs(raw_subtasks)

        # Build initial node map with auto-assigned ``index`` (insertion
        # order). Default ``next_ids`` to empty list when not provided.
        new_nodes: dict[str, TodoNode] = {}
        for i, node_spec in enumerate(nodes):
            nid = node_spec["id"]
            text = node_spec.get("text", "")
            next_ids = node_spec.get("next_ids") or []
            new_nodes[nid] = TodoNode(
                id=nid,
                text=text,
                status="pending",
                comment="",
                next_ids=list(next_ids),
                index=i,
                subtasks=list(parsed_subtasks[nid]),
            )

        # Apply edges list: ``edges`` is the canonical source when
        # provided, layered on top of any per-node ``next_ids`` already
        # declared. Each ``{"from": ..., "to": ...}`` appends ``to`` to
        # ``from``'s ``next_ids`` (de-duplicated).
        for edge in edges:
            from_id = edge.get("from")
            to_id = edge.get("to")
            if from_id not in new_nodes:
                raise ValueError(
                    f"Edge references unknown from-node {from_id!r}."
                )
            if to_id not in new_nodes:
                raise ValueError(
                    f"Edge references unknown to-node {to_id!r}."
                )
            if to_id not in new_nodes[from_id].next_ids:
                new_nodes[from_id].next_ids.append(to_id)

        # Validate no dangling ``next_ids`` references — only after
        # edges have been merged in (so an edge to a previously-missing
        # node still resolves).
        for node in new_nodes.values():
            for next_id in node.next_ids:
                if next_id not in new_nodes:
                    raise ValueError(
                        f"Node {node.id!r} references dangling next_id "
                        f"{next_id!r}."
                    )

        # Validate DAG (no cycles) before storing.
        with self._lock:
            if self._has_cycle(new_nodes):
                raise ValueError(
                    "create_graph() rejects cyclic graphs: the supplied "
                    "nodes + edges contain a directed cycle."
                )
            self._instance_graphs[instance_id] = new_nodes
            return [self._to_dict(node) for node in new_nodes.values()]

    # ------------------------------------------------------------------
    # Public mutation API
    # ------------------------------------------------------------------

    def update(
        self,
        instance_id: str,
        node_id: str,
        status: str,
    ) -> dict | None:
        """Update the status of a single node and return the reminder payload.

        Identical contract to the previous ``TodoManager.update()``
        except keyed by ``node_id`` instead of positional ``index``.
        Returns ``{"todos": [...], "reminder": str}`` so callers (notably
        the ``todo_update`` tool) get the updated list AND a human-
        readable reminder string in one round-trip.

        Reminder shape:
            * Graph-aware: lists all "ready" pending nodes (those whose
              predecessors are all ``done``) when branching is present,
              falling back to "Waiting: {N} blocked items" when there
              are pending nodes but none are ready, and "All items
              completed!" when nothing remains.
            * When ``status == "done"`` AND the completed node carries a
              non-empty ``comment``: the reminder is prefixed with
              ``"User added new high priority request:\\n---\\n{comment}\\n---\\n"`` (the
              ``---`` fences guard against prompt injection — see
              :meth:`_compute_reminder` for the full policy).

        Args:
            instance_id: Owning instance identifier.
            node_id: Node identifier (must be prefixed ``n-`` for
                internal callers; user-supplied numeric IDs are
                reserved for ``update_by_index``).
            status: New status; must be one of ``pending``,
                ``in_progress``, ``done`` (after alias normalization).

        Returns:
            Dict with keys ``todos`` (full node snapshot) and
            ``reminder`` (formatted string), or ``None`` if the status
            is invalid or the ``node_id`` does not exist for the
            instance.
        """
        normalized = _normalize_status(status)
        if normalized is None:
            logger.warning(
                "TodoGraphManager.update rejected invalid status %r for "
                "instance %s, node %s",
                status,
                instance_id,
                node_id,
            )
            return None

        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                return None
            nodes[node_id].status = normalized
            snapshot = [self._to_dict(n) for n in nodes.values()]
            reminder = self._compute_reminder(nodes, node_id, normalized)
            return {"todos": snapshot, "reminder": reminder}

    def update_by_index(
        self,
        instance_id: str,
        index: int,
        status: str,
    ) -> dict | None:
        """Backward-compat shim: resolve ``index`` → ``node_id``, then call :meth:`update`.

        Resolution is by insertion order — the node returned by
        iterating ``nodes.values()`` at position ``index``. This
        matches the legacy contract where ``index`` was the row
        position.

        Args:
            instance_id: Owning instance identifier.
            index: Position of the node to update (0-based, by
                insertion order).
            status: New status.

        Returns:
            Same as :meth:`update`, or ``None`` if the index does not
            exist for the instance.
        """
        node_id = self._resolve_index_to_node_id(instance_id, index)
        if node_id is None:
            return None
        return self.update(instance_id, node_id, status)

    def set_comment(
        self,
        instance_id: str,
        node_id: str,
        comment: str,
    ) -> dict:
        """Set the comment on the node identified by ``node_id``.

        Args:
            instance_id: Owning instance identifier.
            node_id: Node identifier.
            comment: The annotation text. Empty string clears the
                comment. Must not exceed :data:`MAX_COMMENT_LENGTH`
                characters; the limit is enforced here as defense in
                depth (the HTTP layer returns 400 for the same
                violation).

        Returns:
            The updated node as a plain dict.

        Raises:
            ValueError: If the instance has no graph, the ``node_id``
                does not resolve, or the supplied ``comment`` exceeds
                :data:`MAX_COMMENT_LENGTH` characters.
        """
        # Defense-in-depth length guard — same rationale as the legacy
        # :meth:`TodoManager.set_comment`. The HTTP boundary enforces
        # this and returns 400 before reaching here, but non-HTTP
        # callers (tool layer, scripts) go straight through and must
        # not bypass the limit.
        if len(comment) > MAX_COMMENT_LENGTH:
            raise ValueError(
                f"comment exceeds maximum length of {MAX_COMMENT_LENGTH} "
                f"characters (got {len(comment)})"
            )

        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                raise ValueError(
                    f"node {node_id!r} not found for instance {instance_id!r}"
                )
            nodes[node_id].comment = comment
            return self._to_dict(nodes[node_id])

    def set_comment_by_index(
        self,
        instance_id: str,
        index: int,
        comment: str,
    ) -> dict:
        """Backward-compat shim: resolve ``index`` → ``node_id``, then call :meth:`set_comment`.

        Args:
            instance_id: Owning instance identifier.
            index: Position of the node (0-based, by insertion order).
            comment: The annotation text.

        Returns:
            The updated node as a plain dict.

        Raises:
            ValueError: If the index does not exist, or the comment
                exceeds :data:`MAX_COMMENT_LENGTH`.
        """
        node_id = self._resolve_index_to_node_id(instance_id, index)
        if node_id is None:
            raise ValueError(
                f"index {index} out of range for instance {instance_id!r}"
            )
        return self.set_comment(instance_id, node_id, comment)

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def get_all(self, instance_id: str) -> list[dict]:
        """Return the instance's current nodes.

        Each dict conforms to the **frozen SSE payload schema** (seven
        keys: ``id``, ``index``, ``text``, ``status``, ``comment``,
        ``next_ids``, ``subtasks``). Ordered by insertion order —
        matches the iteration order of ``dict`` since Python 3.7.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            Plain-dict copies of the stored nodes, or ``[]`` if none.
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id, {})
            return [self._to_dict(node) for node in nodes.values()]

    def get_graph(self, instance_id: str) -> dict:
        """Return the graph as ``{nodes: [...], edges: [...]}`` structure.

        Edges are derived from per-node ``next_ids`` adjacency lists
        and emitted as ``{"from": str, "to": str}`` dicts (matching the
        input shape expected by :meth:`create_graph`).

        Args:
            instance_id: Owning instance identifier.

        Returns:
            Structured graph snapshot. Both ``nodes`` and ``edges`` are
            plain lists; ``nodes`` is ``[]`` if the instance has no
            graph. Each node dict conforms to the frozen seven-key
            schema (see :meth:`_to_dict`).
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id, {})
            node_dicts = [self._to_dict(n) for n in nodes.values()]
            edges: list[dict] = []
            for node in nodes.values():
                for next_id in node.next_ids:
                    edges.append({"from": node.id, "to": next_id})
            return {"nodes": node_dicts, "edges": edges}

    def clear(self, instance_id: str) -> None:
        """Drop the instance's graph entirely.

        Args:
            instance_id: Owning instance identifier.
        """
        with self._lock:
            self._instance_graphs.pop(instance_id, None)

    # ------------------------------------------------------------------
    # Public graph-mutation API
    # ------------------------------------------------------------------

    def add_node(
        self,
        instance_id: str,
        text: str,
        next_ids: list[str] | None = None,
        subtasks: list[dict] | None = None,
    ) -> dict:
        """Add a single node to an existing graph.

        Args:
            instance_id: Owning instance identifier.
            text: Human-readable description of the new node.
            next_ids: Optional list of successor node IDs that must
                already exist in the graph. ``None`` is normalized to
                ``[]`` (no successors). Defaults to ``None``.
            subtasks: Optional list of sub-task spec dicts to attach
                to the new node. Each spec is parsed via
                :meth:`_parse_subtask_specs`. ``None`` is normalized to
                ``[]`` (no sub-tasks). Defaults to ``None``.

        Returns:
            The newly created node as a plain dict (frozen schema).

        Raises:
            ValueError: If the instance has no graph (call
                ``create_graph`` first), the count exceeds
                :data:`MAX_NODES`, or any ``next_ids`` reference is
                dangling. Adding the node must not introduce a cycle
                (validated via :meth:`_has_cycle`). Sub-task spec
                validation failures (empty/too-long ``text``,
                all-numeric ``id``, invalid ``status``, count over
                :data:`MAX_SUBTASKS_PER_NODE`) also raise
                ``ValueError``.
        """
        resolved_next_ids = next_ids or []
        new_node_id = self._generate_id()
        # Validate sub-task specs up-front (cheap, no lock needed) so
        # any spec violation surfaces before we mutate state.
        parsed_subtasks = self._parse_subtask_specs(subtasks)

        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None:
                raise ValueError(
                    f"Instance {instance_id!r} has no graph. Call "
                    f"create_graph() first."
                )
            if len(nodes) >= self.MAX_NODES:
                raise ValueError(
                    f"Cannot add node: instance already has "
                    f"{len(nodes)} nodes (max {self.MAX_NODES})."
                )
            for next_id in resolved_next_ids:
                if next_id not in nodes:
                    raise ValueError(
                        f"add_node references dangling next_id {next_id!r}."
                    )

            # Insert at the end (insertion order = ``len(nodes)``).
            new_index = len(nodes)
            nodes[new_node_id] = TodoNode(
                id=new_node_id,
                text=text,
                status="pending",
                comment="",
                next_ids=list(resolved_next_ids),
                index=new_index,
                subtasks=parsed_subtasks,
            )

            # Cycle check — only meaningful if the new node has
            # outbound edges (a node with no successors can never be
            # part of a cycle). The existing graph was already a DAG
            # by construction, so introducing a cycle requires one of
            # the new successors to eventually point back to the new
            # node. We check the post-mutation state.
            if resolved_next_ids and self._has_cycle(nodes):
                # Roll back the insertion — leaving a temporarily
                # cyclic graph in storage would surprise
                # ``get_all``/``get_graph`` callers.
                del nodes[new_node_id]
                raise ValueError(
                    f"add_node would introduce a cycle via next_ids="
                    f"{resolved_next_ids!r}."
                )

            return self._to_dict(nodes[new_node_id])

    def remove_node(
        self,
        instance_id: str,
        node_id: str,
    ) -> dict | None:
        """Remove a node and clean up all inbound + outbound edges.

        Outbound edges: the node's own ``next_ids`` is dropped with the
        node. Inbound edges: any other node whose ``next_ids`` contains
        ``node_id`` has it removed.

        Args:
            instance_id: Owning instance identifier.
            node_id: Node identifier to remove.

        Returns:
            The removed node as a plain dict, or ``None`` if the
            instance has no graph or the ``node_id`` does not resolve.
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                return None

            removed = nodes.pop(node_id)

            # Sweep inbound edges from every other node. We mutate the
            # node-objects directly under the same lock — safe because
            # no other thread sees the dict mid-iteration.
            for other in nodes.values():
                if node_id in other.next_ids:
                    other.next_ids = [
                        nid for nid in other.next_ids if nid != node_id
                    ]

            # Note: we do NOT re-pack ``index`` after removal — the
            # ``index`` field is a one-way insertion-order record, not
            # a "current position" rank. Consumers that need live row
            # positions should re-sort by ``index`` after observing a
            # removal (or rely on insertion-order iteration as the
            # canonical order).

            return self._to_dict(removed)

    def add_edge(
        self,
        instance_id: str,
        from_id: str,
        to_id: str,
    ) -> dict | None:
        """Add a directed edge ``from_id → to_id``.

        If the edge already exists (i.e., ``to_id`` is already in
        ``from_id``'s ``next_ids``), the operation is a no-op and
        returns the current graph.

        Args:
            instance_id: Owning instance identifier.
            from_id: Source node identifier.
            to_id: Target node identifier.

        Returns:
            The updated graph (per :meth:`get_graph`) on success, or
            ``None`` if either node does not exist or the edge would
            introduce a cycle.

        Raises:
            ValueError: If the instance has no graph, either node is
                unknown, or the edge would create a cycle. (The HTTP
                layer may translate these to appropriate status codes
                — the method's ``None`` return path is reserved for
                "instance missing or node missing", and ``ValueError``
                for structural rejections.)
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None:
                return None
            if from_id not in nodes:
                return None
            if to_id not in nodes:
                return None
            if from_id == to_id:
                # Self-loops are unconditionally cycles — reject fast.
                return None

            source = nodes[from_id]
            if to_id not in source.next_ids:
                source.next_ids.append(to_id)

            # Cycle check: only meaningful if the edge was newly added.
            # For idempotent re-adds we skip the check (no state
            # change → no new cycle).
            if self._has_cycle(nodes):
                # Roll back the insertion to leave the graph in a
                # consistent (DAG) state.
                source.next_ids = [
                    nid for nid in source.next_ids if nid != to_id
                ]
                return None

            node_dicts = [self._to_dict(n) for n in nodes.values()]
            edges = [
                {"from": node.id, "to": next_id}
                for node in nodes.values()
                for next_id in node.next_ids
            ]
            return {"nodes": node_dicts, "edges": edges}

    def remove_edge(
        self,
        instance_id: str,
        from_id: str,
        to_id: str,
    ) -> dict | None:
        """Remove a directed edge ``from_id → to_id``.

        Args:
            instance_id: Owning instance identifier.
            from_id: Source node identifier.
            to_id: Target node identifier.

        Returns:
            The updated graph (per :meth:`get_graph`) on success, or
            ``None`` if the instance has no graph, either node does not
            exist, or the edge does not exist.
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None:
                return None
            if from_id not in nodes or to_id not in nodes:
                return None

            source = nodes[from_id]
            if to_id not in source.next_ids:
                # Edge doesn't exist — treat as a no-op miss. Returning
                # ``None`` keeps the contract "absent/missing → None"
                # uniform across graph mutations.
                return None

            source.next_ids = [
                nid for nid in source.next_ids if nid != to_id
            ]

            node_dicts = [self._to_dict(n) for n in nodes.values()]
            edges = [
                {"from": node.id, "to": next_id}
                for node in nodes.values()
                for next_id in node.next_ids
            ]
            return {"nodes": node_dicts, "edges": edges}

    # ------------------------------------------------------------------
    # Public sub-task API
    # ------------------------------------------------------------------

    def add_subtask(
        self,
        instance_id: str,
        node_id: str,
        text: str,
    ) -> dict | None:
        """Append a sub-task to an existing node's checklist.

        Sub-task IDs are auto-generated (``s-`` + 8 hex chars) — callers do
        NOT supply them. Status defaults to ``"pending"``.

        Args:
            instance_id: Owning instance identifier.
            node_id: Parent node identifier.
            text: Sub-task description. Must be non-empty and ≤
                :data:`MAX_SUBTASK_TEXT_LENGTH` characters.

        Returns:
            Dict with keys ``todos`` (full node snapshot as plain dicts) and
            ``reminder`` (formatted string), or ``None`` if the instance or
            node does not exist.

        Raises:
            ValueError: If ``text`` is empty/too long, or the node already
                has :data:`MAX_SUBTASKS_PER_NODE` sub-tasks.
        """
        # Text validation up-front (cheap, no lock needed).
        if not isinstance(text, str) or not text:
            raise ValueError("sub-task text must be a non-empty string")
        if len(text) > MAX_SUBTASK_TEXT_LENGTH:
            raise ValueError(
                f"sub-task text exceeds maximum length of "
                f"{MAX_SUBTASK_TEXT_LENGTH} characters (got {len(text)})"
            )

        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                return None
            node = nodes[node_id]
            if len(node.subtasks) >= MAX_SUBTASKS_PER_NODE:
                raise ValueError(
                    f"Cannot add sub-task: node {node_id!r} already has "
                    f"{len(node.subtasks)} sub-tasks (max "
                    f"{MAX_SUBTASKS_PER_NODE})."
                )
            sub = SubTask(
                id=self._generate_subtask_id(),
                text=text,
                status="pending",
            )
            node.subtasks.append(sub)
            snapshot = [self._to_dict(n) for n in nodes.values()]
            # Reminder is computed against the PARENT node's last update;
            # sub-task changes do not affect the graph-level reminder logic,
            # so we pass the parent's id + status verbatim — _compute_reminder
            # is unchanged.
            reminder = self._compute_reminder(nodes, node_id, node.status)
            return {"todos": snapshot, "reminder": reminder}

    def add_subtasks(
        self,
        instance_id: str,
        node_id: str,
        texts: list[str],
    ) -> dict | None:
        """Append multiple sub-tasks atomically to a node's checklist.

        All ``texts`` are validated up-front (before acquiring the lock);
        the appends then happen within a single ``self._lock`` hold so the
        mutation is atomic — either every sub-task is added or none are.
        This avoids the partial-success window that looping
        :meth:`add_subtask` would create, where each iteration re-acquires
        the lock and could fail mid-way against the per-node cap.

        The per-node cap is checked against the COMBINED count
        (``existing + len(texts)``), not per-item, so a batch that would
        push the node over :data:`MAX_SUBTASKS_PER_NODE` is rejected in
        full rather than silently truncating.

        Args:
            instance_id: Owning instance identifier.
            node_id: Parent node identifier.
            texts: Sub-task descriptions. Each must be a non-empty string
                ≤ :data:`MAX_SUBTASK_TEXT_LENGTH` characters. The combined
                count (existing + new) must not exceed
                :data:`MAX_SUBTASKS_PER_NODE`.

        Returns:
            Dict with keys ``todos`` (full node snapshot as plain dicts),
            ``reminder`` (formatted string), and ``added_ids`` (the list of
            newly created sub-task ids, in insertion order). Returns
            ``None`` if the instance or node does not exist.

        Raises:
            ValueError: If ``texts`` is not a non-empty list, any entry is
                not a non-empty string or exceeds the length cap, or the
                combined count would exceed
                :data:`MAX_SUBTASKS_PER_NODE`.
        """
        # Pre-lock validation: cheap checks that don't need graph state.
        # Mirrors add_subtask's per-text rules but applied to every entry
        # BEFORE any mutation, guaranteeing the atomic all-or-nothing
        # contract.
        if not isinstance(texts, list):
            raise ValueError(
                f"texts must be a list of strings, got "
                f"{type(texts).__name__}."
            )
        if len(texts) == 0:
            raise ValueError("texts must contain at least one sub-task.")
        for i, t in enumerate(texts):
            if not isinstance(t, str) or not t:
                raise ValueError(f"texts[{i}] must be a non-empty string.")
            if len(t) > MAX_SUBTASK_TEXT_LENGTH:
                raise ValueError(
                    f"texts[{i}] exceeds maximum length of "
                    f"{MAX_SUBTASK_TEXT_LENGTH} characters (got {len(t)})."
                )

        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                return None
            node = nodes[node_id]
            existing = len(node.subtasks)
            if existing + len(texts) > MAX_SUBTASKS_PER_NODE:
                raise ValueError(
                    f"Cannot add {len(texts)} sub-task(s): node {node_id!r} "
                    f"already has {existing} sub-task(s); {existing}+"
                    f"{len(texts)} would exceed the maximum of "
                    f"{MAX_SUBTASKS_PER_NODE}."
                )
            added_ids: list[str] = []
            for t in texts:
                sub = SubTask(
                    id=self._generate_subtask_id(),
                    text=t,
                    status="pending",
                )
                node.subtasks.append(sub)
                added_ids.append(sub.id)
            snapshot = [self._to_dict(n) for n in nodes.values()]
            reminder = self._compute_reminder(nodes, node_id, node.status)
            return {
                "todos": snapshot,
                "reminder": reminder,
                "added_ids": added_ids,
            }

    def update_subtask(
        self,
        instance_id: str,
        node_id: str,
        subtask_id: str,
        status: str,
        auto_complete: bool = False,
    ) -> dict | None:
        """Update a sub-task's status, with optional parent auto-completion.

        Sub-task statuses are STRICTLY BINARY (``"pending"`` or ``"done"``).
        The :func:`_normalize_subtask_status` helper rejects ``in_progress``
        and its aliases, returning ``None`` for invalid input — which the
        method translates into a top-level ``None`` return (callers can treat
        "invalid status" and "not found" uniformly).

        Auto-completion policy:
            When ``auto_complete=True`` AND every sub-task on the parent
            node is ``"done"`` AND the parent's own status is NOT already
            ``"done"``, the parent's status is set to ``"done"`` and
            ``auto_completed=True`` is returned. The vacuous-truth guard
            ``if auto_complete and node.subtasks and all(...)`` ensures a
            node with zero sub-tasks never auto-completes from a sub-task
            update — ``all([])`` is ``True`` in Python, which would surprise
            callers. If ``auto_complete=True`` but NOT all sub-tasks are
            done, ``auto_completed=False`` and the parent status is left
            alone. If ``auto_complete=False``, no parent propagation occurs.

        Args:
            instance_id: Owning instance identifier.
            node_id: Parent node identifier.
            subtask_id: Sub-task identifier (``s-`` prefixed).
            status: New status. Must normalize to ``"pending"`` or ``"done"``.
            auto_complete: If ``True``, propagate completion to the parent
                node when all sub-tasks are done. Defaults to ``False``.

        Returns:
            Dict with keys ``todos`` (full node snapshot), ``reminder``
            (formatted string), and ``auto_completed`` (``True`` only when
            this call flipped the parent from non-done to ``"done"`` via
            auto-completion). Returns ``None`` if the instance, parent
            node, or sub-task is not found, OR if ``status`` is invalid
            (sub-task statuses are binary).
        """
        normalized = _normalize_subtask_status(status)
        if normalized is None:
            logger.warning(
                "TodoGraphManager.update_subtask rejected invalid status %r "
                "for instance %s, node %s, sub-task %s",
                status,
                instance_id,
                node_id,
                subtask_id,
            )
            return None

        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                return None
            node = nodes[node_id]
            sub = next((s for s in node.subtasks if s.id == subtask_id), None)
            if sub is None:
                return None
            sub.status = normalized

            auto_completed = False
            # VACUOUS TRUTH GUARD: ``all([]) == True`` in Python, so the
            # explicit ``node.subtasks`` length check prevents a node with
            # zero sub-tasks from auto-completing on this path. Only nodes
            # that actually have a populated checklist participate.
            if (
                auto_complete
                and node.subtasks
                and all(st.status == "done" for st in node.subtasks)
                and node.status != "done"
            ):
                node.status = "done"
                auto_completed = True

            snapshot = [self._to_dict(n) for n in nodes.values()]
            # Reminder uses the LATEST status that meaningfully changed:
            # either the sub-task's parent (if auto-completed) or the
            # parent's current status otherwise. The comment-fence prefix
            # still applies if the parent became done with a comment.
            updated_id = node_id
            updated_status = node.status
            reminder = self._compute_reminder(nodes, updated_id, updated_status)
            return {
                "todos": snapshot,
                "reminder": reminder,
                "auto_completed": auto_completed,
            }

    def remove_subtask(
        self,
        instance_id: str,
        node_id: str,
        subtask_id: str,
    ) -> dict | None:
        """Remove a sub-task from a node's checklist.

        Args:
            instance_id: Owning instance identifier.
            node_id: Parent node identifier.
            subtask_id: Sub-task identifier to remove.

        Returns:
            Dict with keys ``todos`` (full node snapshot) and ``reminder``
            (formatted string), or ``None`` if the instance, parent node,
            or sub-task does not exist.
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or node_id not in nodes:
                return None
            node = nodes[node_id]
            original_len = len(node.subtasks)
            node.subtasks = [s for s in node.subtasks if s.id != subtask_id]
            if len(node.subtasks) == original_len:
                # Sub-task not found — keep the "missing → None" contract
                # uniform with add_subtask / update_subtask.
                return None
            snapshot = [self._to_dict(n) for n in nodes.values()]
            reminder = self._compute_reminder(nodes, node_id, node.status)
            return {"todos": snapshot, "reminder": reminder}

    # ------------------------------------------------------------------
    # Private helpers (all assume ``self._lock`` is held by the caller,
    # unless explicitly documented otherwise).
    # ------------------------------------------------------------------

    def _has_cycle(self, nodes: dict[str, TodoNode]) -> bool:
        """Detect cycles in a node map using Kahn's algorithm.

        O(V+E) topological-sort approach. ``True`` indicates the graph
        contains a cycle (NOT a valid DAG).

        CRITICAL IMPLEMENTATION NOTES:
          * Uses :class:`collections.deque` with ``popleft()`` — O(1)
            per pop. The legacy list-based form ``queue.pop(0)`` was
            O(n) and would silently degrade on dense graphs.
          * When a node's in-degree drops to zero, the **child**
            (the node whose in-degree just became 0) is appended to
            the queue — NOT the parent that triggered the decrement.
            The original plan had ``queue.append(nid)`` (the parent)
            which would either detect nothing or loop forever.

        MUST be called with ``self._lock`` held; reads the node map
        without re-locking.

        Handles gracefully:
          * Empty node dict → returns ``False`` (vacuously a DAG).
          * Disconnected components — each is processed via whatever
            nodes reach zero in-degree.
          * Self-loops (an edge from a node to itself is an immediate
            cycle; Kahn's algorithm will not reduce the source's
            in-degree past 1, so it stays unvisited).
        """
        in_degree: dict[str, int] = {nid: 0 for nid in nodes}
        for node in nodes.values():
            for next_id in node.next_ids:
                if next_id in in_degree:
                    in_degree[next_id] += 1

        # Start with all zero-in-degree nodes (potential roots).
        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        visited = 0
        while queue:
            nid = queue.popleft()  # O(1)
            visited += 1
            for next_id in nodes[nid].next_ids:
                if next_id in in_degree:
                    in_degree[next_id] -= 1
                    if in_degree[next_id] == 0:
                        queue.append(next_id)  # FIXED: append child, not parent
        return visited != len(nodes)

    def _compute_reminder(
        self,
        nodes: dict[str, TodoNode],
        updated_node_id: str,
        updated_status: str,
    ) -> str:
        """Compute a graph-aware reminder string.

        Preserves the comment-fence prompt-injection protection from the
        legacy :meth:`TodoManager.update`: when the updated node is
        marked ``"done"`` AND has a non-empty ``comment``, the base
        reminder is prefixed with
        ``"User added new high priority request:\\n---\\n{comment}\\n---\\n"``. The ``---``
        fences visually separate the untrusted user-supplied comment
        from the rest of the system-formatted reminder, making prompt
        injection attempts obvious to the agent. **This is a security-
        critical pattern** — it must not be lost in the graph refactor.

        Logic:
          1. **Base reminder** (graph-state branch):
             a. Find all "ready" pending nodes — pending nodes whose
                ALL predecessors are ``done``.
             b. If ready nodes exist:
                ``"\\n\\n⏭️ Next: {text1}, {text2}, ..."`` (comma-join
                all ready texts in insertion order).
             c. Else if pending nodes exist (but none ready):
                ``"\\n\\n⏳ Waiting: {count} blocked items"``.
             d. Else (no pending at all):
                ``"\\n\\nAll items completed! ✅"``.
          2. **Comment-fence prefix**: if ``updated_status == "done"``
             AND ``updated_node.comment`` is non-empty, prepend
             ``"User added new high priority request:\\n---\\n{comment}\\n---\\n"``.
          3. Return the combined string.

        MUST be called with ``self._lock`` held; reads the node map
        without re-locking.
        """
        updated_node = nodes[updated_node_id]

        # Build reverse adjacency (predecessor map). A node is "ready"
        # when ALL its predecessors are ``done`` and it itself is
        # ``pending``. Insertion order is preserved by iterating the
        # node dict, which is ordered by Python ≥3.7.
        predecessors: dict[str, list[str]] = {nid: [] for nid in nodes}
        for node in nodes.values():
            for next_id in node.next_ids:
                if next_id in predecessors:
                    predecessors[next_id].append(node.id)

        ready_nodes = [
            node
            for node in nodes.values()
            if node.status == "pending"
            and all(
                nodes[pred_id].status == "done"
                for pred_id in predecessors[node.id]
            )
        ]

        if ready_nodes:
            texts = ", ".join(node.text for node in ready_nodes)
            base_reminder = f"\n\n⏭️ Next: {texts}"
        elif any(node.status == "pending" for node in nodes.values()):
            blocked_count = sum(
                1 for node in nodes.values() if node.status == "pending"
            )
            base_reminder = f"\n\n⏳ Waiting: {blocked_count} blocked items"
        else:
            base_reminder = "\n\nAll items completed! ✅"

        # Comment-fence prefix — exact wording & punctuation preserved
        # from the legacy implementation so existing agent prompts/
        # tests that match on this string keep working.
        if updated_status == "done" and updated_node.comment:
            return (
                f"User added new high priority request:\n---\n{updated_node.comment}\n---\n"
                + base_reminder
            )
        return base_reminder

    @staticmethod
    def _generate_id() -> str:
        """Generate a short, unique, non-numeric node ID.

        Format: ``"n-" + uuid.uuid4().hex[:8]``.

        The ``n-`` prefix guarantees the ID is never all-numeric,
        preventing collision with the API's numeric-index backward-
        compat path (``node_id.isdigit() → set_comment_by_index``). The
        8-hex-char suffix gives ~4 billion possible IDs — collision
        risk for <200 nodes per instance is negligible.
        """
        return f"n-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _generate_subtask_id() -> str:
        """Generate a short, unique, non-numeric sub-task ID.

        Format: ``"s-" + uuid.uuid4().hex[:8]``.

        Mirrors :meth:`_generate_id` for node IDs. The ``s-`` prefix
        guarantees the ID is never all-numeric, preventing collision with
        the API's numeric-index backward-compat path. The 8-hex-char
        suffix gives ~4 billion possible IDs — collision risk for <20
        sub-tasks per node is negligible.
        """
        return f"s-{uuid.uuid4().hex[:8]}"

    def _resolve_index_to_node_id(
        self,
        instance_id: str,
        index: int,
    ) -> str | None:
        """Resolve an insertion-order ``index`` to its ``node_id``.

        Used by ``update_by_index`` and ``set_comment_by_index``. Takes
        the lock to read a consistent snapshot — the helper itself is
        not called from inside any locked context.

        Args:
            instance_id: Owning instance identifier.
            index: Position (0-based).

        Returns:
            The corresponding ``node_id``, or ``None`` if the
            instance has no graph or ``index`` is out of range.
        """
        with self._lock:
            nodes = self._instance_graphs.get(instance_id)
            if nodes is None or index < 0:
                return None
            try:
                # ``dict.values()`` is insertion-ordered in Python 3.7+
                # so indexing into it yields the Nth node. This is the
                # explicit analog of the legacy ``items[index]`` look-up.
                return list(nodes.values())[index].id
            except IndexError:
                return None

    @staticmethod
    def _parse_subtask_specs(specs: list[dict] | None) -> list[SubTask]:
        """Parse + validate a list of sub-task spec dicts into SubTask objects.

        Each spec must contain ``text`` (non-empty, ≤ MAX_SUBTASK_TEXT_LENGTH).
        Optional ``id`` (must be s-prefixed, or auto-generated; all-numeric rejected).
        Optional ``status`` (normalized via _normalize_subtask_status; default "pending").
        Unknown fields silently ignored.
        Total count must not exceed MAX_SUBTASKS_PER_NODE.

        Raises:
            ValueError: On any validation failure.
        """
        if specs is None:
            return []
        if not isinstance(specs, list):
            raise ValueError(
                f"subtasks must be a list, got {type(specs).__name__}."
            )
        if len(specs) > MAX_SUBTASKS_PER_NODE:
            raise ValueError(
                f"subtasks count {len(specs)} exceeds maximum of "
                f"{MAX_SUBTASKS_PER_NODE}."
            )
        result: list[SubTask] = []
        for i, spec in enumerate(specs):
            if not isinstance(spec, dict):
                raise ValueError(
                    f"subtasks[{i}] must be a dict, got {type(spec).__name__}."
                )
            text = spec.get("text", "")
            if not isinstance(text, str) or not text:
                raise ValueError(
                    f"subtasks[{i}].text must be a non-empty string."
                )
            if len(text) > MAX_SUBTASK_TEXT_LENGTH:
                raise ValueError(
                    f"subtasks[{i}].text exceeds maximum length of "
                    f"{MAX_SUBTASK_TEXT_LENGTH} characters (got {len(text)})."
                )
            # ID handling: explicit non-empty ``id`` MUST be ``s-`` prefixed.
            # All-numeric IDs are rejected (collision with index-based path).
            # Non-s-prefixed, non-numeric IDs are silently replaced with an
            # auto-generated one (no hard error).
            raw_id = spec.get("id")
            if raw_id is not None and raw_id != "":
                if not isinstance(raw_id, str):
                    raise ValueError(
                        f"subtasks[{i}].id must be a string, got "
                        f"{type(raw_id).__name__}."
                    )
                if raw_id.isdigit():
                    raise ValueError(
                        f"subtasks[{i}].id {raw_id!r} is all-numeric and "
                        f"would collide with the index-based backward-compat "
                        f"path. Use an 's-' prefixed id instead."
                    )
                if raw_id.startswith("s-"):
                    sub_id = raw_id
                else:
                    sub_id = TodoGraphManager._generate_subtask_id()
            else:
                sub_id = TodoGraphManager._generate_subtask_id()
            # Status normalization: defaults to "pending" if absent;
            # explicit invalid statuses raise ValueError.
            raw_status = spec.get("status", "pending")
            normalized_status = _normalize_subtask_status(raw_status)
            if normalized_status is None:
                raise ValueError(
                    f"subtasks[{i}].status {raw_status!r} is invalid. "
                    f"Sub-task statuses are strictly binary ('pending' or "
                    f"'done')."
                )
            result.append(
                SubTask(id=sub_id, text=text, status=normalized_status)
            )
        # Post-pass: enforce unique sub-task ids within a single node's list.
        # Done AFTER the per-spec validation so each spec's text/length/id
        # checks fire first; collision is a list-level invariant, not a
        # per-spec one. Auto-generated ids cannot collide by construction,
        # so this only ever trips when callers supply duplicate explicit ids.
        seen_sub_ids: set[str] = set()
        for sub in result:
            if sub.id in seen_sub_ids:
                raise ValueError(
                    f"duplicate sub-task id {sub.id!r} within the same node's "
                    f"subtask list. Sub-task ids must be unique within a node."
                )
            seen_sub_ids.add(sub.id)
        return result

    @staticmethod
    def _to_dict(node: TodoNode) -> dict[str, Any]:
        """Serialize a :class:`TodoNode` to a plain dict.

        **FROZEN SCHEMA (v2)** — this is the SSE payload shape that
        downstream consumers build against. Schema evolved from v1 (six
        keys) to v2 (seven keys) when ``subtasks`` was added in Phase 1
        of the todo-subtasks feature. Do NOT change without coordinating
        across all consumers (tools, API, frontend).

        Output shape (seven keys, all required):
            {
                "id": "n-a1b2c3d4",         # Stable node identity (n-prefixed)
                "index": 0,                 # Insertion-order position
                "text": "Setup DB",         # Human-readable description
                "status": "pending",        # pending | in_progress | done
                "comment": "",              # User annotation side-channel
                "next_ids": ["n-e5f6g7h8"], # Adjacency list (successors)
                "subtasks": []              # Checklist of sub-tasks
            }

        Invariants:
          * Exactly seven keys — no extras, no omissions.
          * ``index`` PRESERVED (backward compat — old index-keyed
            callers keep working).
          * ``id`` present, always ``n-`` prefixed.
          * ``next_ids`` present (may be empty list), copied to
            prevent external mutation of internal state.
          * ``subtasks`` present (may be empty list), each sub-task dict
            conforms to the :meth:`SubTask._to_dict` shape — exactly
            three keys (``id``, ``text``, ``status``).
        """
        return {
            "id": node.id,
            "index": node.index,
            "text": node.text,
            "status": node.status,
            "comment": node.comment,
            "next_ids": list(node.next_ids),
            "subtasks": [SubTask._to_dict(st) for st in node.subtasks],
        }


# Backward-compatibility alias: existing import sites
# (``from daemon.services.todo_manager import TodoManager``) keep
# working unchanged. New code should prefer the explicit
# ``TodoGraphManager`` name for clarity.
TodoManager = TodoGraphManager
