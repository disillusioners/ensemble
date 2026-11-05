"""Memoir retrieval — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2/D0.2).

PHASE 2 OWNS THE CONTENT of this module: the hybrid recall (lexical +
semantic + recency prior fused by RRF), the timeline filter-then-rank, the
recall budget enforcement, and the embedding-identity disclosure.

Placement rationale (D0.2): the retrieval service sits BESIDE its
consumer seam (``daemon/services/critical_notes_selector.py`` is the
precedent) because it is consumed by BOTH the tools and the briefing
builder — putting it inside the tool module would keep the tool bodies
thin and would leave the briefing with a second implementation.

Design contract refs: ``design.md`` §7 (the retrieval algorithm) and §8
(the wake briefing); ``phase2-plan.md`` (the whole phase); amendments
AM-1 (the PG lexical-ranker argument order — ``ts_rank_cd(tsv,
plainto_tsquery('english', :q))``), AM-2..AM-5 (the RRF arithmetic),
AM-14 (the marked-id set equals the shipped projection), AM-33 (the
model-identity drift disclosure), AM-36 (the worker must stay
driver-agnostic — no ``tsv`` reference in the portable SQL paths).

The service is a CONSUMER of the kill-switch: it reads
``daemon.tools.memoir_knob.memoir_enabled()`` at CALL TIME (never cached
at import time).
"""

from __future__ import annotations
