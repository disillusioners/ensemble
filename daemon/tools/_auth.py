"""Authorization helpers for tool→agent spawn checks.

This module centralizes the authorization layer that determines whether a
caller agent is allowed to spawn another agent via the ``spawn_instance``
tool (and the council/convenience variants that share the same gate).

Single source of truth: ``tools.allow`` in ``meta.json``.

If a caller agent grants itself access to a tool category (via
``tools.allow``), it implicitly has the team members that tools in that
category need to spawn. The :data:`TOOL_REQUIRED_AGENTS` map is the SINGLE
DECLARATION POINT for which categories require which backing agents. Any
new tier-B agent-backed tool category must add an entry here.

Backward compatible: explicit ``team_members`` entries are still honored.
The merged allow-set is ``raw_members + implied_members`` (raw first so
explicit declarations take priority in display ordering, then
canonicalized).
"""

from __future__ import annotations

# Tool category → agents that tools in this category require.
#
# When an agent declares a category in ``tools.allow``, the matching
# ``required_agents`` are implicitly added to its effective
# ``team_members`` allow-set. This removes the previous "double
# authorization" requirement (BOTH ``tools.allow`` AND ``team_members``
# needed to be configured in sync) and makes ``tools.allow`` the single
# source of truth.
#
# Add a new entry when introducing a new tier-B agent-backed tool
# category. The category name MUST match the string passed to
# ``@register_tool_category(...)`` in the tool module.
TOOL_REQUIRED_AGENTS: dict[str, list[str]] = {
    "knowledge": ["explorer", "kb-writer"],
    "chart": ["charter"],
    "image": ["image-reader"],
    "council": ["governor"],
}


def _check_team_membership(caller_agent_id: str, requested_agent_id: str) -> str | None:
    """Verify the caller agent is allowed to spawn the requested agent.

    Reads the caller's ``meta.json`` ``team_members`` list and checks that the
    requested agent_id (resolved to its canonical id) is present. Returns
    ``None`` when the spawn is permitted, or an error message describing the
    rejection when it is not.

    Both the caller's list entries AND the requested ``agent_id`` are
    canonicalized via :func:`registry.resolve_pure_id` so renamed agents
    continue to match their ``team_members`` entries correctly.

    Implicit team members: categories in the caller's ``tools.allow`` map are
    expanded via :data:`TOOL_REQUIRED_AGENTS` and merged into the effective
    allow-set. Explicit ``team_members`` declarations are still honored — the
    union is used.

    Secure default: ``team_members`` missing OR empty AND no matching
    ``tools.allow`` category → deny everything.

    Args:
        caller_agent_id: The agent_id of the instance invoking
            ``spawn_instance`` (the parent instance's agent).
        requested_agent_id: The agent_id the caller wants to spawn.

    Returns:
        ``None`` when the spawn is authorized, otherwise a human-readable
        error string suitable for the tool's existing error path.
    """
    # Import here to avoid circular import (registry imports utils indirectly).
    from ..registry import get_registry

    registry = get_registry()

    # Canonicalize the REQUESTED id first — unknown agent → reject (will be
    # reported as "not allowed" rather than "not found" since this is a
    # permissions check). The downstream lifecycle service still raises a
    # "not found" ValueError for unresolvable ids, which is the right
    # primary signal for callers; the membership check is purely an
    # authorization filter on top.
    requested_canonical = registry.resolve_pure_id(requested_agent_id)
    if requested_canonical is None:
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_agent_id}'. Requested agent does not exist. "
            "Allowed team members: []"
        )

    # Look up the caller's metadata.
    caller_meta = registry.get_resolved(caller_agent_id)
    if caller_meta is None:
        # Caller agent_id is unknown — this is a wiring/misconfiguration
        # bug, but we fail closed (deny). The downstream lifecycle service
        # will raise a "not found" ValueError for the caller as well.
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_canonical}'. Caller agent not found. "
            "Allowed team members: []"
        )

    # Use the caller's canonical id from the registry as the basis for
    # team_members matching.
    caller_canonical = caller_meta.id
    raw_members = caller_meta.team_members or []

    # --- Auto-derive implied team members from tool access ---
    # If the caller has a category in tools.allow that maps to a backing
    # agent(s) (see TOOL_REQUIRED_AGENTS), treat those agents as implicitly
    # allowed. This makes tools.allow the single source of truth — the
    # caller no longer needs to ALSO duplicate the backing agent in
    # team_members. Explicit team_members declarations are still honored
    # below; we just merge in the implied ones before canonicalizing.
    #
    # CATEGORY-ONLY MATCHING (intentional contract): auto-derive matches
    # category names in tools.allow against TOOL_REQUIRED_AGENTS only.
    # Individual tool names (e.g. ``"explore"`` as a bare name) do NOT
    # imply any team members. The tool-layer ``resolve_tool_filter``
    # expands both category and tool names, but this auth gate does not —
    # simpler, and the practical impact is minimal: the only known agent
    # with a bare tool name like ``"explore"`` in its allow list is
    # ``wanderer``, which never spawns the ``explorer`` agent via
    # ``spawn_instance`` (it uses the ``explore()`` tool, which bypasses
    # the gate via ``invoke_agent_and_wait``).
    implied_members: list[str] = []
    if caller_meta.tools and caller_meta.tools.allow:
        for category, required_agents in TOOL_REQUIRED_AGENTS.items():
            if category in caller_meta.tools.allow:
                implied_members.extend(required_agents)
    # ---

    # Honor ``tools.deny`` — "deny wins over allow" (matches
    # ``resolve_tool_filter`` semantics). A category in deny drops its
    # implied backing agents from ``implied_members``; a deny entry that
    # is NOT a ``TOOL_REQUIRED_AGENTS`` key is treated as a literal tool
    # name (no-op at this layer — the tool layer handles literal names).
    # This closes the spawn-gate bypass where a caller could deny a
    # category at the tool layer yet still spawn its backing agent
    # directly via ``spawn_instance``.
    if implied_members and caller_meta.tools and caller_meta.tools.deny:
        denied_implied: set[str] = set()
        for category, required_agents in TOOL_REQUIRED_AGENTS.items():
            if category in caller_meta.tools.deny:
                denied_implied.update(required_agents)
        if denied_implied:
            # Canonicalize denied entries the same way we canonicalize
            # the allowed set below, so renamed agents compare
            # consistently on both sides of the filter.
            denied_canonical = {
                registry.resolve_pure_id(m) for m in denied_implied
                if registry.resolve_pure_id(m) is not None
            }
            implied_members = [
                m for m in implied_members
                if registry.resolve_pure_id(m) not in denied_canonical
            ]
    # ---

    # Canonicalize each member (raw + implied) so a renamed team member
    # still matches the requested agent_id consistently.
    combined_members = list(raw_members) + implied_members
    allowed_canonical: set[str] = set()
    for member in combined_members:
        canonical = registry.resolve_pure_id(member)
        if canonical is not None:
            allowed_canonical.add(canonical)

    if requested_canonical not in allowed_canonical:
        allowed_display = sorted(allowed_canonical) if allowed_canonical else []
        return (
            f"Agent '{caller_canonical}' is not allowed to spawn "
            f"'{requested_canonical}'. Allowed team members: {allowed_display}"
        )

    return None