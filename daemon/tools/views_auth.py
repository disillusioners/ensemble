"""The ONE shared layer-3 in-body privilege gate for the Phase-3 view tools.

ari-tools Phase 3 (the live-views surface). This module owns:

* :func:`_is_views_authorized` — the shared gate helper;
* :data:`VIEW_WRITE_AGENTS` — the ``view_write`` gate set (ARI-ONLY);
* :data:`BOARD_LINK_AGENTS` — the ``board_link`` gate set (ari-only).

Why the helper lives in its OWN module
--------------------------------------
``daemon/tools/view_write.py`` and ``daemon/tools/board_link.py`` both
need a gate, and the two gates are DIFFERENT. If the helper lived
inside either tool module, the other module would have to import the
tool module to reach it — i.e. one tool would own the other's policy.
A separate module means:

* ONE helper, TWO call sites (the phase-1 "one shared helper, not
  four copies" discipline — ``daemon/tools/board_tools.py``);
* both gate sets are visible SIDE BY SIDE, so the audience of each is a
  one-glance fact, not a cross-file lookup;
* Phase 3 imports NO phase-1 file (D13) — this module is phase-3
  owned and touches nothing else.

NOTE (2026-10-20 user ruling, Discord, direct): the two sets now carry
the SAME audience — ``ari`` alone. The separation survives as
STRUCTURE (each tool keeps its own named set), not as an audience
split: the earlier "mint ≠ read" (ari + leader) audience is RETRACTED
— see ``.agents/shared/planning/ari-tools/decisions.md``.

Why layer 3 is LOAD-BEARING (and not belt-and-suspenders)
-----------------------------------------------------------
The privilege posture for every Phase-3 tool is THREE layers, and
all three are required:

1. the CATEGORY is in ``PRIVILEGED_TOOL_CATEGORIES`` (``view-write``
   / ``board-link``) ⇒ the category never joins the empty-allow
   default universe;
2. an explicit CATEGORY entry on the agent's ``tools.allow``;
3. THIS in-body gate.

Layer 3 exists because layers 1 + 2 do NOT enforce "ari-only": a
**literal tool-name** allow entry (an allow entry that is not a
category key) is added VERBATIM and therefore bypasses the category
gate outright — ``daemon/tools/instance.py`` (the category-expansion
loop: ``if item in tool_categories: allowed_tools.update(...) else:
allowed_tools.add(item)``). Only the body gate closes that bypass.
This is the commission's standing doctrine (phase1-plan.md /
decisions.md R-C1): the category gate AND the body gate are both
required — one is not a substitute for the other.

Fail-closed
-----------
A blank / non-``str`` ``agent_id`` is NEVER authorized. ``""``
never means "ari" — the factory default is the deny case, so a
construction site that forgets to thread ``agent_id`` yields a dead
tool rather than an open one.
"""

from __future__ import annotations


#: The agents allowed to PUBLISH a report page (``view_write``).
#:
#: ARI-ONLY — a binding pre-ruling of the commission (the phase3
#: plan header block). Ari is the agent that authors rich HTML
#: reports; no other agent may write to the shared ``reports`` root.
VIEW_WRITE_AGENTS: frozenset[str] = frozenset({"ari"})

#: The agents allowed to MINT the live board's public link
#: (``board_link``).
#:
#: ARI-ONLY — the user ruling of 2026-10-20 (Discord, direct) is
#: BINDING and RETRACTS the earlier ``{ari, leader}`` audience: the
#: board categories belong to ``ari`` alone. The set stays a SEPARATE
#: named constant (one tool owns its own policy) even though it now
#: carries the same audience as :data:`VIEW_WRITE_AGENTS`.
BOARD_LINK_AGENTS: frozenset[str] = frozenset({"ari"})


def _is_views_authorized(agent_id: str, allowed_agents: frozenset[str]) -> bool:
    """Layer-3 in-body privilege gate — the THIRD of three required layers.

    The privileged CATEGORY does not close the literal-tool-name allow
    bypass (``daemon/tools/instance.py``), so the body must gate too.

    Fail-closed: a blank / non-``str`` ``agent_id`` is NOT authorized.

    Args:
        agent_id: The CALLING agent's id, captured at factory time.
        allowed_agents: The gate set for the tool being called
            (:data:`VIEW_WRITE_AGENTS` or :data:`BOARD_LINK_AGENTS`).

    Returns:
        True only when ``agent_id`` is a string member of
        ``allowed_agents``.
    """
    return isinstance(agent_id, str) and agent_id in allowed_agents


__all__ = [
    "BOARD_LINK_AGENTS",
    "VIEW_WRITE_AGENTS",
    "_is_views_authorized",
]
