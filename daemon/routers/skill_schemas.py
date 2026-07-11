"""Pydantic schemas for the skill management REST API.

Phase 6 of the Skill Evolution System — HTTP surface for skill
lifecycle, lineage, metrics, A/B tests, and trigger rules.

The schemas are deliberately decoupled from the underlying SQLModel
definitions in :mod:`daemon.repositories.skill` so the request/
response shape can evolve independently of the on-disk column
layout. Field-level constraints (``min_length``, ``ge``/``le``,
default factories) mirror the conventions used in
:mod:`daemon.routers.schemas` and :mod:`daemon.routers.agents`.

Module map (consumed by :mod:`daemon.routers.skills`):

* :class:`SkillCreateRequest` — POST body for creating a skill.
* :class:`SkillUpdateRequest` — PUT body for partial updates.
* :class:`SkillSearchRequest` — POST body for natural-language
  search (BM25 + embedding re-rank + LLM selection).
* :class:`SkillFeedbackRequest` — POST body for skill feedback.
* :class:`SkillFixRequest` — POST body for user-reported fixes
  (dispatches an evolution job to the skill-keeper agent).
* :class:`TriggerCreateRequest` — POST body for trigger rules.
* :class:`TriggerUpdateRequest` — PUT body for trigger edits.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillCreateRequest(BaseModel):
    """Request body for ``POST /api/skills`` — create a new skill.

    The full skill body is required at creation time; partial
    creates are not supported. Embeddings are refreshed
    asynchronously by the store service after the row is committed
    (best-effort — see ``SkillStoreService.create_skill``).

    Attributes:
        name: Human-readable skill name. Must be unique within
            ``(project_id, name, generation)``.
        description: One-line summary of what the skill does.
        content: Markdown body of the skill instructions.
        project_id: Owning project ID, or ``None`` for a global
            skill.
        category: Free-form category bucket (default
            ``"workflow"``).
    """

    name: str = Field(..., min_length=1, description="Human-readable skill name")
    description: str = Field(..., description="One-line summary")
    content: str = Field(..., description="Markdown body of the skill")
    project_id: str | None = Field(default=None, description="Owning project ID (None for global)")
    category: str = Field(default="workflow", description="Free-form category")


class SkillUpdateRequest(BaseModel):
    """Request body for ``PUT /api/skills/{skill_id}`` — partial update.

    ``None`` values are excluded from the forwarded ``**fields``
    mapping (the router uses ``exclude_none=True``) so a missing
    field never accidentally clears a column.

    Attributes:
        description: New short description.
        content: New markdown body. When provided, the store
            refreshes the embedding cache.
        category: New category bucket.
        is_active: Soft-activate flag (``True`` -> ``status='active'``,
            ``False`` -> ``status='inactive'``).
    """

    description: str | None = Field(default=None, description="New description")
    content: str | None = Field(default=None, description="New markdown content")
    category: str | None = Field(default=None, description="New category")
    is_active: bool | None = Field(default=None, description="Soft-activate flag")


class SkillSearchRequest(BaseModel):
    """Request body for ``POST /api/skills/search``.

    Attributes:
        query: Natural-language query (the user's message, the
            task brief, or a one-skill-request string).
        project_id: Project scope for the search corpus. ``None``
            restricts to the global library.
        max_results: Maximum number of skills to surface in the
            ``injected`` list (bounded ``1..20`` to keep the LLM
            prompt within reason).
    """

    query: str = Field(..., description="Natural-language query")
    project_id: str | None = Field(default=None, description="Project scope (None for global)")
    max_results: int = Field(default=2, ge=1, le=20, description="Maximum skills to inject")


class SkillFeedbackRequest(BaseModel):
    """Request body for ``POST /api/skills/{skill_id}/feedback``.

    The metrics service looks up the latest usage record for the
    pair ``(skill_id, instance_id)`` and stamps ``feedback_applied``
    plus the free-form ``note``. ``instance_id`` and ``agent_id``
    are surfaced as Query parameters on the route (not in the
    body) because they originate from the calling context, not the
    caller.

    Attributes:
        applied: ``True`` if the skill was actually applied,
            ``False`` if recorded-but-not-applied, ``None`` if the
            agent is unsure. ``None`` skips the counter bump.
        note: Free-form note. Default empty string.
    """

    applied: bool | None = Field(default=None, description="Whether the feedback was applied")
    note: str = Field(default="", description="Free-form note")


class SkillFixRequest(BaseModel):
    """Request body for ``POST /api/skills/{skill_id}/fix``.

    The router dispatches a ``FIX`` evolution job to the
    skill-keeper agent via ``SkillJobDispatcher.dispatch_fix``.
    Returns ``202 Accepted`` with the job ID so the caller can
    poll for resolution instead of blocking on the LLM call.

    Attributes:
        issue_description: Plain-language description of the
            issue (required).
        suggested_fix: Optional proposed change. Appended to the
            direction string the skill-keeper consumes.
    """

    issue_description: str = Field(..., description="Plain-language description of the issue")
    suggested_fix: str | None = Field(default=None, description="Optional proposed fix")


class TriggerCreateRequest(BaseModel):
    """Request body for ``POST /api/skills/triggers`` — create a trigger rule.

    The ``condition_json`` shape depends on ``condition_type`` —
    the router does not validate the body so non-standard
    discriminators (e.g. ``"embedding_match"``) can be added by
    downstream services without a router revision.

    Attributes:
        name: Human-readable name (required).
        condition_type: Discriminator (e.g. ``"keyword"``,
            ``"regex"``, ``"embedding_match"``).
        condition_json: Rule body, type-specific.
        action: Action string (e.g.
            ``"select_skill:workflow-debug"``).
        project_id: Owning project, or ``None`` for a global
            trigger.
    """

    name: str = Field(..., min_length=1, description="Trigger name")
    condition_type: str = Field(..., description="keyword | regex | embedding_match | …")
    condition_json: dict[str, Any] = Field(default_factory=dict, description="Rule body")
    action: str = Field(..., description="e.g. select_skill:workflow-debug")
    project_id: str | None = Field(default=None, description="Owning project (None for global)")


class TriggerUpdateRequest(BaseModel):
    """Request body for ``PUT /api/skills/triggers/{trigger_id}`` — partial update.

    ``None`` fields are excluded (the router uses
    ``exclude_none=True``) so a typo cannot wipe a column.

    Attributes:
        name: New name.
        condition_type: New discriminator.
        condition_json: New rule body.
        action: New action string.
        is_enabled: Disable/enable the trigger.
    """

    name: str | None = Field(default=None, description="New name")
    condition_type: str | None = Field(default=None, description="New condition type")
    condition_json: dict[str, Any] | None = Field(default=None, description="New condition body")
    action: str | None = Field(default=None, description="New action string")
    is_enabled: bool | None = Field(default=None, description="Enable/disable trigger")


__all__ = [
    "SkillCreateRequest",
    "SkillUpdateRequest",
    "SkillSearchRequest",
    "SkillFeedbackRequest",
    "SkillFixRequest",
    "TriggerCreateRequest",
    "TriggerUpdateRequest",
]
