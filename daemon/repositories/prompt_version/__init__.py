"""Prompt-version repository package (agent-prompt-immutability Phase A).

Phase A ships the DB-backed prompt-version layer so a runtime prompt
for an agent is resolved by
``effective = selected_version > latest_approved > template_file``.
The ``.md`` files under ``agents/<agent>/`` become immutable base /
template content (runtime writes fail loud; see decisions.md F8 +
``daemon/tools/prompt_artifacts.py`` from the Phase A worker parallel
to this one).

The package exposes:

* :mod:`daemon.repositories.prompt_version.models` — the
  :class:`PromptVersion` + :class:`PromptAuditEvent` SQLModel
  declarations. Byte-identical index / unique-constraint names to the
  ``.sql`` migration (``daemon/migrations/versions/20261013_214000_create_prompt_versions.sql``)
  per the dual-driver parity contract — the model is the canonical
  source for the PG ``create_all`` path.
* :mod:`daemon.repositories.prompt_version.repository` — the
  :class:`PromptVersionRepository` sync class implementing the 13
  methods in phase-contract.md A2 (Phase B's compile target) plus the
  ``create_and_submit`` composite (A5/C4) used by ``inner_soul`` and
  ``agent_mother.agent_modify`` rewrites.

The Phase B REST endpoints (``prompts:approve`` scope) sit on top of
the service facade at
``daemon.services.prompt_version_service.PromptVersionService``.
Phase C / D are not in this package — see
``.agents/shared/planning/agent-prompt-immutability/``.
"""

from __future__ import annotations

from .models import (
    PROMPT_ARTIFACTS,
    PROMPT_AUDIT_EVENT_TYPES,
    PROMPT_STATES,
    PromptAuditEvent,
    PromptVersion,
)

__all__ = [
    "PromptVersion",
    "PromptAuditEvent",
    "PROMPT_STATES",
    "PROMPT_AUDIT_EVENT_TYPES",
    "PROMPT_ARTIFACTS",
]