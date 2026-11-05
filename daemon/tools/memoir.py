"""The memoir tools — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2, D0.2).

PHASE 4 OWNS THE CONTENT of this module: the five tools of the v1
surface — ``memoir_write``, ``memoir_pin``, ``memoir_unpin``,
``memoir_recall``, ``memoir_timeline`` — plus the privileged category
registration (category ``memoir``, ari EXACTLY, the two-layer gate).

This skeleton is intentionally EMPTY of behavior: no tool is registered,
no database is touched, nothing is imported from the retrieval service.
Phase 4 lands the bodies.

Design contract refs: ``design.md`` §5 (the tool surface, the
three-step registration, the two-layer gate, ``ENSEMBLE_MEMOIR_ENABLED``)
and §5.4 (the five-tool signature set); ``phase4-plan.md`` (the whole
phase); ``decisions.md`` DD-14 (the category + the in-body factory-captured
agent id — the only layer prompt content cannot bend); amendments AM-12
(``daemon/tools/memoir.py`` contains ZERO references to the embedder — the
write path performs exactly ONE insert and never awaits the worker), AM-16
(the gate ORDER is auth-first, then the kill-switch, then argument
validation, then any DB touch), AM-21 (the frozen ``memoir_*`` refusal
vocabulary), AM-23 (the fixed result shapes), AM-25/AM-26/AM-27 (the
kill-switch consumption semantics), AM-42 (the ``since``/``until``
parameter rename — ``from``/``to`` are Python keywords).

DISCOVERABILITY (D0.7): the resolved knob state must be retrievable
WITHOUT reading logs. Phase 4 embeds
``daemon.tools.memoir_knob.memoir_tool_help()`` — the exact text this
module's ``tool_help`` carries — in the tools' help. Phase 0 owns that
wording in the knob module so it has exactly one home.
"""

from __future__ import annotations
