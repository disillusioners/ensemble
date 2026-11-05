"""The embedding worker — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2/D0.2).

PHASE 3 OWNS THE CONTENT of this module: the dedicated lifespan task,
the ``embedding IS NULL`` queue, the boot catch-up, the bounded retries,
the greppable per-tick counters and the atomic per-row write-back of the
four embedding columns.

Design contract refs: ``design.md`` §3 (the lexical-immediate /
semantic-soon asymmetry — a write NEVER blocks on the embedding) and §4.1;
``phase3-plan.md`` (the whole phase); ``decisions.md`` DD-6 (embeddings
via the EXISTING lane, per-row model identity, graceful degradation) and
DD-20 (the hot append never blocks the turn); amendments AM-28 (the
request timeout — the house client sets none, so the SDK's 600 s default
applies), AM-29 (a DEDICATED executor — never the shared default
executor), AM-30 (a write-back failure counts as an attempt), AM-31 (a
dimension mismatch is TERMINAL, never a silent zip), AM-32 (the explicit
transient status set), AM-34 (the tick-line semantics), AM-35 (a failed
tick never kills the task), AM-36 (the scan/update SQL stays
driver-agnostic — no ``tsv`` reference).

The worker is a CONSUMER of the kill-switch: it re-reads the knob EVERY
tick (park with a log line, keep the task alive so a re-enable needs no
restart).
"""

from __future__ import annotations
