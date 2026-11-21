"""Tool-usage digest tool (Phase 3) — the ``scheduling`` category.

A single read-only tool ``tool_usage_digest`` that runs the deterministic
SQL rollup over ``tool_usage_daily`` and writes a durable markdown digest
to the canonical root (D-6). No prompt-driven rollup: the tool does all
work; the scheduled message is minimal and deterministic.
"""

from __future__ import annotations

import logging

from daemon._redact import redact_exc_str
from typing import Any, Optional

from ._schema_registry import tool
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY = "scheduling"


def create_tool_usage_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: Optional[str] = None,
) -> list:
    @register_tool_category(CATEGORY)
    @tool
    async def tool_usage_digest(run_date: str = "") -> str:
        """Write the deterministic tool-usage digest for the given run date (UTC date of the fire). Defaults to today (UTC). Returns a short status string."""
        try:
            engine = getattr(manager, "_engine", None)
            if engine is None:
                return "ERROR: no database engine available"

            rd = run_date.strip() if run_date else None
            md, path = await _run_write(engine, rd)
            return f"Digest written: {path} ({len(md)} bytes)"
        except Exception as exc:  # noqa: BLE001 - tools never raise
            logger.warning("tool_usage_digest failed: %s", redact_exc_str(exc, limit=200))
            return f"ERROR: {redact_exc_str(exc, limit=200)}"

    return [tool_usage_digest]


async def _run_write(engine, run_date):
    import asyncio
    from daemon.services.tool_usage_digest import write_digest

    return await asyncio.to_thread(write_digest, engine, run_date)


def create_tool_usage_tools_if_available(
    manager: Any,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: Optional[str] = None,
) -> list:
    # Available whenever an engine exists.
    engine = getattr(manager, "_engine", None)
    if engine is None:
        return []
    return create_tool_usage_tools(
        manager,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
        agent_tag=agent_tag,
    )


__all__ = [
    "create_tool_usage_tools",
    "create_tool_usage_tools_if_available",
]
