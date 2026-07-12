"""LangChain tool category for the shared context metadata KV store.

Exposes a single internal tool, ``shared_context_metadata``, that manages a
small key-value store partitioned by ``context_key`` (the tree-root instance
id of the caller). The store is intended for lightweight metadata that an
agent wants to remember across turns or share with sibling agents in the
same context tree (e.g. ``last_seen``, ``topic``, ``user_locale``).

Unlike :mod:`daemon.tools.context_tools`, the ``context_key`` is
auto-resolved from the caller via closure — the agent never passes it
explicitly. This keeps the surface area minimal and prevents accidental
cross-context writes.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Shared Context"
CATEGORY_DOC = """\
Shared context metadata tools for managing a lightweight key-value store
partitioned by context_key (the tree-root instance id of the caller).

Internal agents can call ``shared_context_metadata`` directly to upsert,
delete, or clear metadata rows. External agent systems connected via the
hosted MCP use the equivalent ``ensemble_context_metadata*`` tools.

The ``context_key`` is auto-resolved from the calling instance, so the
agent never supplies it. Calling the tool with no arguments returns the
current state as JSON without mutating anything.
"""


def create_shared_context_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create the Shared Context Metadata tool category tools.

    Args:
        manager: The InstanceManager instance. Used (lazily, inside the
            tool body) to access the shared metadata repository and to
            resolve the caller's tree-root id. Stored only as a closure
            variable — the factory itself touches no ``manager``
            attributes so it can be invoked with a ``None`` manager
            for static checks.
        current_instance_id: The ID of the current instance. The
            ``context_key`` is derived lazily inside the tool body via
            ``manager._instance_repository.get_tree_root_id(...)`` so a
            missing instance never breaks the factory call.

    Returns:
        List containing the single tool function
        ``[shared_context_metadata]``.
    """

    @register_tool_category("shared_context")
    @tool
    async def shared_context_metadata(
        set_kv: dict[str, Any] | None = None,
        delete_keys: list[str] | None = None,
        clear_all: bool = False,
    ) -> str:
        """Manage the shared context metadata KV store for the calling instance.

        The metadata store is partitioned by ``context_key`` (the tree-root
        instance id of the caller). The ``context_key`` is auto-resolved
        from your instance, so you do NOT pass it.

        Args:
            set_kv: Dict of ``meta_key → meta_value`` to upsert. Existing
                keys are updated; new keys are inserted. Values may be any
                JSON-serializable type (str, int, float, bool, list, dict,
                None). Pass an empty dict or ``None`` to skip.
            delete_keys: List of ``meta_key`` strings to delete. Missing
                keys are ignored. Pass an empty list or ``None`` to skip.
            clear_all: When ``True``, deletes EVERY metadata entry for this
                ``context_key``. Wins over ``set_kv`` / ``delete_keys`` if
                both are provided.

        Operations order when multiple are provided:
            1. ``delete_keys`` (apply first so it cannot remove rows just
               inserted by ``set_kv``).
            2. ``set_kv`` (upsert).
            3. ``clear_all`` (final state — wins if ``True``, the other
               two become no-ops).

        Calling with no arguments (all ``None`` / ``False``) returns the
        current state as JSON without mutating anything — useful as a
        read-only ``list``.

        Returns:
            JSON string of ``{meta_key: meta_value}`` representing the
            current state after the requested operations. On error, a
            JSON object ``{"error": "..."}``.
        """
        # Resolve context_key lazily inside the tool body — never at
        # factory time, so an unfilled manager does not break import
        # or factory call (the static check passes ``None`` as manager).
        try:
            context_key = manager._instance_repository.get_tree_root_id(current_instance_id)
            if not context_key:
                context_key = current_instance_id
        except Exception as e:
            logger.warning(
                "shared_context_metadata: get_tree_root_id(%s) failed, falling back to current_instance_id: %s",
                current_instance_id,
                e,
            )
            context_key = current_instance_id

        # Acquire the repo lazily so a partially-initialised manager
        # does not break factory calls — only tool invocations fail.
        try:
            repo = manager.shared_context_metadata_repo
        except Exception as e:
            logger.warning(
                "shared_context_metadata: failed to acquire repo for instance %s: %s",
                current_instance_id,
                e,
            )
            return json.dumps({"error": f"shared_context_metadata_repo unavailable: {e}"})

        try:
            # Apply precedence: clear_all wins and finalises the state.
            # Within the non-clear path, deletes run first so any keys
            # listed in delete_keys are removed before set_kv upserts.
            if clear_all:
                await asyncio.to_thread(repo.delete_all, context_key)
            else:
                if delete_keys:
                    await asyncio.to_thread(repo.delete_many, context_key, delete_keys)
                if set_kv:
                    await asyncio.to_thread(repo.set_many, context_key, set_kv)

            # Always return the post-operation snapshot.
            kvs = await asyncio.to_thread(repo.get_all_as_dict, context_key)
            return json.dumps(kvs, indent=2)
        except Exception as e:
            logger.warning(
                "shared_context_metadata failed for context_key=%s: %s",
                context_key,
                e,
            )
            return json.dumps({"error": str(e)})

    return [shared_context_metadata]
