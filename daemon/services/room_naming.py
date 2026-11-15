"""Room member naming service — the D-S3 cascade, tiers 2-3 (P2).

Ruling B's FULL NAMING CASCADE (decisions.md D-S3, KV-verbatim; the
spec of record):

1. Parent-provided purpose name (preferred/expected) — resolved at the
   spawn tool layer (``member_name`` param); this service only
   sanitizes it.
2. Omitted → INLINE quick-model generation on the purpose-bound QUICK
   model (``config.llm.model_quick``, the ``model_title`` field
   pattern — purpose-bound, NEVER consulted against
   ``allowed_models``). Short prompt, purpose name only (e.g.
   'capturer' / 'log-analyzer'). Sync-bounded
   ``ROOM_NAME_MODEL_TIMEOUT_S = 3.0`` — the ONE place a spawn waits
   on naming (the bounded SUCCESS-path wait; the ruling's "never
   block spawn on naming" reads as "never block on naming FAILURE" —
   explicit ratification line, emperor item #6).
3. Quick-model FAILURE (error/timeout/unavailable/empty) →
   deterministic fallback = the agent id as the base name (e.g.
   ``capture``; tier 4's registry turns a collision into
   ``capture-2``), instant + predictable, no retry-loop, never blocks
   spawn.
4. Collision IN-ROOM → per-room numeric suffix — handled by the
   suffix registry at REGISTRATION
   (``daemon/repositories/room/membership.resolve_member_name``),
   NOT here; this service only produces BASE names.

Every tier's FINAL name (post-registry) is returned in the spawn
toolcall result — no silent naming, ever (the tool assembles that
line from the committed membership row).

Shape: TitleGenerationService (``daemon/services/title_generation.py``)
— manager-injected config access for test mockability, raw llm_config
dict → ``clean_llm_config`` at the constructor only (F1 kwarg hygiene),
``wrap_langchain_failover`` facade, ``asyncio.to_thread`` invoke,
belt-and-braces ``asyncio.wait_for`` (the site-level cap; the facade's
wall-clock cap is the primary line of defense). Differences from the
title lane: the 3.0s bound (vs 30s), NO broadcast, NO dedup set (one
call per spawn), and failure returns ``None`` instead of logging-only
(the caller falls back to tier 3).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..graph import ThinkingChatOpenAI, clean_llm_config
from ..repositories.room.membership import (
    TIER_AGENT_FALLBACK,
    TIER_PARENT_PROVIDED,
    TIER_QUICK_MODEL,
    sanitize_member_name,
)
from ..utils import parse_think_tags
from .llm_failover import wrap_langchain_failover

if TYPE_CHECKING:
    from ..config import Config
    from ..manager import InstanceManager

__all__ = [
    "ROOM_NAME_MODEL_TIMEOUT_S",
    "RoomNamingService",
]

# D-S3 tier 2: the bounded SUCCESS-path wait. The ONLY sanctioned
# spawn-blocking naming wait; the failure path (timeout included) is
# instant-to-3.0s and never blocks the spawn itself.
ROOM_NAME_MODEL_TIMEOUT_S = 3.0

# Tier-3 terminal base: when even a NON-EMPTY agent id sanitizes to
# nothing (e.g. all-symbols), the guaranteed-kebab ``member`` stands —
# the cascade never returns an empty base name.
_TERMINAL_MEMBER_BASE = "member"

_default_logger = logging.getLogger(__name__)

_NAMING_SYSTEM_PROMPT = (
    "You generate short team-member purpose names for agent chat "
    "rooms. Reply with ONLY the name: lowercase kebab-case, 1-2 words, "
    "no quotes, no explanation (e.g. capturer, log-analyzer)."
)

_NAMING_PROMPT_TEMPLATE = """Generate a short purpose name for a new team member joining a chat room.

Agent type: {agent_id}
Instance name: {instance_name}

Purpose name:"""


class RoomNamingService:
    """Inline quick-model purpose-name generation (tiers 2-3)."""

    def __init__(
        self,
        manager: "InstanceManager",
        logger: logging.Logger | None = None,
    ):
        self._manager = manager
        self._logger = logger if logger is not None else _default_logger

    @property
    def _config(self) -> "Config":
        """Access config through the manager (test mockability — the
        TitleGenerationService precedent)."""
        return self._manager.config

    def _model_for_naming(self) -> str | None:
        """The purpose-bound QUICK model with the cleared-field
        fallback (``model_title`` lane precedent): ``model_quick``
        when set, else the global default ``model``."""
        configured = getattr(self._config.llm, "model_quick", None)
        if configured and configured.strip():
            return configured.strip()
        fallback = getattr(self._config.llm, "model", None)
        return fallback.strip() if fallback and fallback.strip() else None

    async def generate_member_name(
        self,
        *,
        agent_id: str,
        instance_name: str | None = None,
    ) -> str | None:
        """Tier 2 — one bounded quick-model call → sanitized name.

        Returns the sanitized candidate, or ``None`` on ANY failure
        INSIDE the model lane (timeout / exception / empty /
        think-only output). NEVER retries (the ruling: no
        retry-loop). Config-construction errors — the LLM client /
        failover-wrapper setup, which sits OUTSIDE the guarded invoke
        below — PROPAGATE to the caller (only the invoke itself and
        its wait are guarded).
        """
        model = self._model_for_naming()
        if not model:
            # Unavailable by configuration — instant tier-3 fallback.
            return None
        llm_config = {
            "base_url": self._config.llm.base_url,
            "base_url_backup": self._config.llm.base_url_backup,
            "api_key": self._config.llm.api_key,
            "model": model,
            "temperature": 0.3,
            "default_headers": {
                "x-proxy-app": "ensemble",
                "x-proxy-interleaved-thinking": "True",
                **(
                    {"X-LLMProxy-Buffer-Response": "true"}
                    if getattr(self._config.llm, "buffer_response_header", False)
                    else {}
                ),
            },
        }
        # F1: clean at constructor only — the facade needs the RAW
        # dict (base_url_backup failover). TitleGenerationService
        # precedent.
        llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
        llm_wrapper = wrap_langchain_failover(llm, llm_config)
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = _NAMING_PROMPT_TEMPLATE.format(
            agent_id=agent_id,
            instance_name=instance_name or "(none)",
        )
        try:
            # Belt-and-braces: the 3.0s wait_for cancels the inner
            # task on timeout (tier-2's bound); the facade's
            # wall-clock cap is the primary line of defense (see the
            # title lane's identical posture).
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    llm_wrapper.invoke,
                    [
                        SystemMessage(content=_NAMING_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                    ],
                ),
                timeout=ROOM_NAME_MODEL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            self._logger.info(
                "room naming: quick model timed out after %.1fs "
                "(agent=%s) — tier-3 fallback",
                ROOM_NAME_MODEL_TIMEOUT_S,
                agent_id,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — any failure ⇒ tier 3
            self._logger.info(
                "room naming: quick model failed (%s) — tier-3 fallback",
                exc,
            )
            return None
        content = getattr(response, "content", None)
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(str(block))
            raw = " ".join(parts)
        else:
            raw = str(content) if content else ""
        # Strip <think> blocks (reasoning models) — the title lane's
        # parse_think_tags posture; a think-only output sanitizes to
        # None ⇒ tier 3.
        raw, _thinking = parse_think_tags(raw)
        return sanitize_member_name(raw)

    def fallback_member_name(self, agent_id: str) -> str:
        """Tier 3 — deterministic instant base: the agent id.

        Sanitized through the same ladder (an agent id like
        ``ui_worker`` becomes ``ui-worker``). A fully unusable agent
        id falls to the guaranteed-kebab ``member`` base
        (``_TERMINAL_MEMBER_BASE``). The tier-4 registry appends
        ``-N`` on collision. Predictable: the same agent id always
        yields the same base.
        """
        return sanitize_member_name(agent_id) or _TERMINAL_MEMBER_BASE

    async def resolve_base_name(
        self,
        *,
        provided: str | None,
        agent_id: str,
        instance_name: str | None = None,
    ) -> tuple[str, str]:
        """The cascade orchestration (tiers 1→2→3) for BASE names.

        Returns ``(base_name, tier_label)`` where ``tier_label`` ∈
        ``{"parent_provided", "quick_model", "agent_fallback"}`` —
        the spawn tool surfaces the tier in its result string (no
        silent naming). Tier 4 (collision suffixing) happens at
        registration under the room lock.
        """
        tier1 = sanitize_member_name(provided)
        if tier1:
            return tier1, TIER_PARENT_PROVIDED
        tier2 = await self.generate_member_name(
            agent_id=agent_id, instance_name=instance_name
        )
        # Defense-in-depth normalize: generate_member_name already
        # sanitizes, but the cascade owns the registry-entry contract
        # (kebab-safe names — the @tag/address surface) — a subclassed
        # or patched lane returning a raw candidate must still land
        # normalized or fall through to tier 3.
        tier2 = sanitize_member_name(tier2)
        if tier2:
            return tier2, TIER_QUICK_MODEL
        return self.fallback_member_name(agent_id), TIER_AGENT_FALLBACK
