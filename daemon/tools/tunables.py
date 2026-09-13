"""Per-instance runtime tunables (long-tool-call-nudge, phase 3).

Sole canonical home for the ``set_instance_tunable`` parent-facing tool
that writes the per-child long-tool-call-nudge threshold into
``instance_metadata``. Tool acquisition is factory-style to mirror the
rest of ``daemon/tools/``: :func:`create_set_instance_tunable_tool`
returns the fully decorated, ``InstanceManager``-bound tool for
``create_instance_tools`` to insert into its surface.

Moved OUT of ``daemon/tools/instance.py`` so the per-instance routing
surface (instance lifecycle / workdir-aware wrappers / spawn-family)
stays under the 1000-3000 line band the original co-location comment
aimed at. After phase-3 phase shipped, ``instance.py`` had grown to
``~4749`` lines — well past the band — and the tunable write surface is
self-contained: one tool, one metadata key, two validation gates. The
move also lets the tool route through the
``InstanceManager.set_metadata_many`` facade instead of reaching into
``manager._instance_repository`` directly (D14 violation; see the M6
note inside the factory body).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import Field

from daemon.services.long_tool_nudge import (
    HARD_MAX_THRESHOLD_SECONDS,
    MIN_THRESHOLD_SECONDS,
)
from daemon.tools._tool_registry import register_tool_category

# Exact-spelling write contract with the scanner's read
# (``daemon/services/long_tool_nudge.py`` ``resolve_threshold``) — a
# misspelling silently disables the override. Floor/ceiling are
# IDENTITY-imported from the canonical home (AD-30): never duplicated,
# never aliased. Pinned by
# ``tests/unit/tools/test_set_instance_tunable.TestHConstants``.
LONG_TOOL_CALL_THRESHOLD_KEY = "long_tool_call_threshold_seconds"
LONG_TOOL_CALL_ALLOWED_TUNABLES: frozenset = frozenset(
    {"long_tool_call_threshold_seconds"}
)


_TOOL_NAME = "set_instance_tunable"


def create_set_instance_tunable_tool(manager: Any) -> Any:
    """Return the ``set_instance_tunable`` tool, factory-style.

    The tool writes EXACTLY ``instance_metadata[
    "long_tool_call_threshold_seconds"]`` and never ``update_instance``
    (the latter rejects ``instance_metadata`` with ``ValueError``). The
    scanner picks up the new threshold on its next tick — no daemon
    restart needed for per-child overrides.

    Writes route through the ``InstanceManager.set_metadata_many``
    facade (D14 compliance — see the M6 design note in the WORKLOG).
    """
    full_doc = (
        "Set a per-instance runtime tunable (currently: the long-tool-call "
        "nudge threshold).\n"
        "\n"
        "Args:\n"
        "    instance_id: The ID of the instance (usually a child) to tune.\n"
        "    key: Tunable name - only 'long_tool_call_threshold_seconds' is "
        "accepted in v1.\n"
        "    value: New threshold in seconds; must be in [60, 1800].\n"
        "\n"
        "Returns:\n"
        "    dict: {instance_id, key, prior_value, effective_value, "
        "applied_at} on success (effective_value mirrors value — the "
        "range check above already rejects out-of-band values, so no "
        "silent clamp); {'error': <msg>, 'error_code': 'UNKNOWN_KEY'} "
        "for an unknown tunable; {'error': <msg>, 'error_code': "
        "'NOT_FOUND'} when the instance does not exist; "
        "{'error': <msg>, 'error_code': 'FEATURE_DISABLED'} when "
        "long-tool-nudge is disabled by config (no metadata written). "
        "Raises ValueError for out-of-range or non-int values - correct "
        "and retry.\n"
        "\n"
        "Effect timing:\n"
        "    The long-tool-nudge scanner picks up the new threshold on "
        "its next tick (default 60s) - no daemon restart needed.\n"
        "\n"
        "Example:\n"
        "    set_instance_tunable(instance_id=\"abc-123\", "
        "key=\"long_tool_call_threshold_seconds\", value=1200)"
    )

    @register_tool_category("instance")
    @tool
    async def set_instance_tunable(
        instance_id: str,
        key: Annotated[
            str,
            Field(
                description=(
                    "Tunable name; only 'long_tool_call_threshold_seconds' "
                    "is currently accepted."
                )
            ),
        ],
        value: Annotated[
            int,
            Field(description="New threshold in seconds. Must be in [60, 1800]."),
        ],
    ) -> dict:
        """Set a per-instance runtime tunable. Use tool_help("set_instance_tunable") for details."""
        # (a) Kill-switch gate (AD-39) — FIRST; when disabled, NO
        # metadata is written. Stamp/log presence in the daemon
        # continues by design (stamp/log presence != delivery).
        config = getattr(manager, "config", None)
        nudge_config = getattr(config, "long_tool_nudge", None)
        enabled = (
            getattr(nudge_config, "enabled", True)
            if nudge_config is not None
            else True
        )
        if not enabled:
            return {
                "error": (
                    "long-tool-nudge is disabled by config "
                    "(LONG_TOOL_NUDGE_ENABLED=0); no metadata written"
                ),
                "error_code": "FEATURE_DISABLED",
            }
        # (c) Allowlist (recoverable error-dict — mirrors
        # project_set_metadata; the LLM self-corrects from the message).
        if key not in LONG_TOOL_CALL_ALLOWED_TUNABLES:
            return {
                "error": (
                    f"Unknown tunable {key!r}. Allowed tunables: "
                    f"{sorted(LONG_TOOL_CALL_ALLOWED_TUNABLES)}"
                ),
                "error_code": "UNKNOWN_KEY",
            }
        # (b) Type check — reject bool explicitly (bool is an int
        # subclass in Python) and any non-int.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{key} must be an integer number of seconds; got "
                f"{value!r} ({type(value).__name__}). Correct the value "
                f"and retry."
            )
        # (d) Range check — BOTH floor and ceiling, loud raise (the
        # spawn_councilor strict-validation house style: a parent
        # typing 18000 WANTED 18000 and must re-read, not receive a
        # silent 1800). The upstream rejection guarantees ``value``
        # is in ``[MIN_THRESHOLD_SECONDS, HARD_MAX_THRESHOLD_SECONDS]``
        # — the ``min(value, HARD_MAX)`` echo in the return dict is
        # therefore an identity; we drop it (M7).
        if value < MIN_THRESHOLD_SECONDS or value > HARD_MAX_THRESHOLD_SECONDS:
            raise ValueError(
                f"{key} must be in [{MIN_THRESHOLD_SECONDS}, "
                f"{HARD_MAX_THRESHOLD_SECONDS}] (got {value}). The hard "
                f"maximum is the system ceiling; the floor prevents "
                f"accidental micro-thresholds that defeat the feature. "
                f"Correct the value and retry."
            )
        # (e) Prior value (single-key read).
        # M6 — facade routing: use ``manager.get_instance_info`` rather
        # than reaching into ``manager._instance_repository`` directly
        # (D14 — the tool layer never touches repositories). The dict
        # shape comes from ``daemon/services/instance_lifecycle.py::
        # get_instance_info`` and carries ``instance_metadata`` as a
        # plain dict. ``KeyError`` is the documented not-found signal;
        # we collapse it to the prior_value=None + NOT_FOUND echo.
        prior_value: Any = None
        try:
            info = manager.get_instance_info(instance_id)
            instance_metadata = info.get("instance_metadata") or {}
            prior_value = instance_metadata.get(LONG_TOOL_CALL_THRESHOLD_KEY)
        except KeyError:
            return {
                "error": f"Instance not found: {instance_id}",
                "error_code": "NOT_FOUND",
            }
        # (f) Write via the ``InstanceManager.set_metadata_many``
        # facade — the SOLE write path the tool layer uses for
        # ``instance_metadata``. Single-key dict → single-statement
        # atomic UPDATE; multi-key shape available if a future
        # tunable needs a paired write.
        updated = manager.set_metadata_many(
            instance_id, {LONG_TOOL_CALL_THRESHOLD_KEY: value}
        )
        if updated is None:
            return {
                "error": f"Instance not found: {instance_id}",
                "error_code": "NOT_FOUND",
            }
        return {
            "instance_id": instance_id,
            "key": LONG_TOOL_CALL_THRESHOLD_KEY,
            "prior_value": prior_value,
            # Range check already enforces ``<= HARD_MAX_THRESHOLD_SECONDS``
            # (M7) — the value is its own effective value.
            "effective_value": value,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }

    set_instance_tunable._full_doc_ = full_doc
    return set_instance_tunable


__all__ = [
    "LONG_TOOL_CALL_THRESHOLD_KEY",
    "LONG_TOOL_CALL_ALLOWED_TUNABLES",
    "create_set_instance_tunable_tool",
]
