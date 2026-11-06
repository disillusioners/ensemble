"""The ``prompt_reload`` tool — the ``prompt-reload`` category (1 tool,
ARI-ONLY).

Refreshes THE CALLING INSTANCE'S OWN system prompt from the active
agent definition without a restart or session loss: validate the
agent dir (self-env aware), verify the loader's read set against the
release manifest, recompose through the SAME loader the spawn/restore
path uses, diff old vs new, and evict the cached graph so the NEXT
dispatch cold-restores onto the new persona (the bare-pop seam — the
effect lands on the NEXT turn, never mid-turn).

Access posture — THREE layers, ALL required (notify-user precedent):

1. The ``prompt-reload`` category is PRIVILEGED (entry #20 of
   ``PRIVILEGED_TOOL_CATEGORIES``) — opt-in-only, never in the
   default-grant universe.
2. ``agents/ari/meta.json`` carries the explicit ``"prompt-reload"``
   allow entry — and ONLY ari.
3. The tool body rejects any caller whose factory-captured
   ``agent_id != "ari"`` with the notify-exact generic envelope (a
   LITERAL tool name in an allow list bypasses the category gate, so
   the category alone cannot spell the audience; this authorization
   class deliberately carries NO REFUSED code — AM-6).

Kill-switch (D11/AM-11): ``ENSEMBLE_PROMPT_RELOAD_ENABLED``, default
ON, read PER CALL here — a killed tool REFUSES typed
``prompt_reload_disabled`` and STAYS REGISTERED (the surface never
shrinks with the switch).

House rules honored: tools NEVER raise; every failure is a typed
envelope string (REFUSED for codes 1-9, FAILED for the internal-error
code 10); the ONE frozen INFO line fires on EVERY terminal path
(D24/AM-15) with counts/flags only.

Next-turn semantics + provider cache invalidation are DISCLOSED in
every success report (R9) and in the runbook
``docs/runbooks/prompt-reload.md`` — never in ari's prompt artifacts
(D7: this lane adds no agent prompt files).
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

#: The category key — KEBAB-case, byte-identical in every place it
#: appears (the module, the registry, ari's meta.json allow entry, the
#: runbook). House pattern (``notify-user`` → ``notify_user``).
PROMPT_RELOAD_CATEGORY_KEY = "prompt-reload"

#: The ONE module constant holding the exact kill-switch knob string
#: (metrics_read.py:139 pattern).
KILL_SWITCH_ENV = "ENSEMBLE_PROMPT_RELOAD_ENABLED"

#: The single authorized agent (R5: ari EXACTLY).
PROMPT_RELOAD_AUTHORIZED_AGENT = "ari"

#: Off-spellings for the per-call kill-switch (instance_ops.py weight).
_OFF_SPELLINGS = {"0", "false", "no", "off", "disabled"}


def _resolve_enabled() -> bool:
    """Kill-switch resolver — default ON, read PER CALL (D11/AM-11)."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    value = str(raw).strip().lower()
    if value in _OFF_SPELLINGS:
        return False
    if value not in {"1", "true", "yes", "on", "enabled"}:
        logger.warning(
            "prompt_reload: invalid %s=%r (expected a boolean spelling); "
            "defaulting to ENABLED",
            KILL_SWITCH_ENV,
            raw,
        )
    return True


def _refusal(code: str, message: str) -> str:
    """The house REFUSED envelope (notify_user.py:314-316 shape)."""
    return f"Error: prompt_reload REFUSED — reason={code}: {message}"


def _failure(code: str, message: str) -> str:
    """The house FAILED envelope (notify_user.py:319-321 shape)."""
    return f"Error: prompt_reload FAILED — reason={code}: {message}"


def _not_ari(agent_id: str) -> str:
    """The notify-exact generic ERROR envelope (notify_user.py:703-711)."""
    return (
        f"ERROR: prompt_reload is available to the "
        f"{PROMPT_RELOAD_AUTHORIZED_AGENT} agent only "
        f"(caller: {agent_id or 'unknown'})."
    )


def _emit_info_line(result: Any, instance_id: str, agent_id: str) -> None:
    """The ONE frozen INFO line (D24/AM-15) — counts/flags only, never
    persona or diff content. Fired on EVERY terminal path."""
    from ..services.prompt_refresh import format_info_line

    provenance = getattr(result, "provenance", {}) or {}
    logger.info(
        format_info_line(
            instance_id=instance_id,
            agent_id=provenance.get("agent_id", agent_id),
            outcome=getattr(result, "outcome", "refused"),
            code=getattr(result, "code", None),
            provenance=provenance,
            fingerprint_changed=bool(provenance.get("fingerprint_changed", False)),
            hash_divergence=bool(provenance.get("hash_divergence", False)),
            cache_busted=bool(provenance.get("cache_busted", False)),
            persona_diff_kind=(getattr(result, "diff", {}) or {}).get("persona_diff", "-"),
            was_cached=bool(provenance.get("was_cached", False)),
            manager_instances=provenance.get("manager_instances", "?->?"),
        )
    )


def _render_report(result: Any) -> str:
    """The D14 frozen report shape — abbreviated diff + provenance."""
    from ..services.prompt_refresh import (
        PROMPT_RELOADED_MARKER,
        S10_C_DISPLAY_DIVERGENCE,
        S10_D_NEXT_TURN,
        S10_E_PROVIDER_CACHE,
        S10_J_NO_CHANGE_WORDING,
        S10_N_COLD_START_DELIBERATE,
        OUTCOME_NO_CHANGE,
        OUTCOME_UNPROVEN,
    )

    p = result.provenance
    d = result.diff or {}
    lines: list[str] = []
    lines.append(f"prompt_reload {result.outcome} — {result.message}")
    lines.append(
        f"release: {p.get('running_release') or '?'} "
        f"(current_matches_running={p.get('current_matches_running', 'unknown')}, "
        f"daemon_version={p.get('daemon_version', '?')})"
    )
    if p.get("divergence_line"):
        lines.append(p["divergence_line"])
    lines.append(f"manifest: {p.get('manifest', '-')}")
    lines.append(
        f"db_fingerprint: {p.get('db_fingerprint', '-')} "
        f"(mcp_tool_names_source={p.get('mcp_tool_names_source', '-')}, "
        f"was_cached={str(bool(p.get('was_cached'))).lower()})"
    )
    lines.append(f"manager_instances: {p.get('manager_instances', '-')}")

    if d.get("persona_diff") == "exact":
        lines.append(
            f"persona diff: +{d.get('lines_added', 0)}/-{d.get('lines_removed', 0)} lines, "
            f"{d.get('bytes_before', 0)}→{d.get('bytes_after', 0)} bytes"
        )
        if d.get("headings_added"):
            lines.append("headings added: " + "; ".join(d["headings_added"]))
        if d.get("headings_removed"):
            lines.append("headings removed: " + "; ".join(d["headings_removed"]))
    else:
        lines.append("persona_diff=unavailable")

    files = d.get("files", [])
    changed = [f for f in files if f.get("changed")]
    lines.append(f"files: {len(changed)} changed of {len(files)} in the loader read set")
    for row in changed:
        lines.append(f"  {row['path']}: sha256 {row['sha_before']} → {row['sha_after']}")

    for row in p.get("db_rows", []) or []:
        line = (
            f"  db row: {row['artifact']} ({row.get('class', 'CORE')}, "
            f"selected={row.get('selected', '-')})"
        )
        if row.get("honesty"):
            line += f" — {row['honesty']}"
        lines.append(line)

    lines.append(S10_C_DISPLAY_DIVERGENCE)
    lines.append(S10_D_NEXT_TURN)
    lines.append(S10_E_PROVIDER_CACHE)
    if result.outcome == OUTCOME_NO_CHANGE:
        lines.append(S10_J_NO_CHANGE_WORDING)
    if result.outcome == OUTCOME_UNPROVEN:
        lines.append(S10_N_COLD_START_DELIBERATE)
    lines.append(PROMPT_RELOADED_MARKER.format(outcome=result.outcome))
    return "\n".join(lines)


def create_prompt_reload_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the prompt_reload tool for one instance/agent.

    The factory ALWAYS constructs (kill-switch OFF refuses per call,
    never hides the tool — AM-11). Per-agent visibility is scoped by
    ``_apply_tool_filter`` via the ``prompt-reload`` PRIVILEGED
    category + ari's meta.json allow entry; the body carries the
    third, independent agent_id gate.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class PromptReloadInput(BaseModel):
        """Input schema for prompt_reload."""

        force: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Pop the cached graph even when nothing changed "
                    "(the escape hatch for the shared-cache edge). "
                    "Optional; default false."
                ),
            ),
        ]

    @register_tool_category(PROMPT_RELOAD_CATEGORY_KEY)
    @tool(args_schema=PromptReloadInput)
    async def prompt_reload(force: bool = False) -> str:
        """Refresh MY system prompt from the active agent definition; the new persona takes effect on my NEXT turn. Use tool_help("prompt_reload") for details."""
        try:
            # Layer 3 — the in-body agent gate (auth-first: an
            # unauthorized caller touches nothing else). The denial is
            # still a terminal path → the D24 INFO line fires (code=-:
            # the authorization class deliberately carries no code).
            if caller_agent_id != PROMPT_RELOAD_AUTHORIZED_AGENT:
                from ..services.prompt_refresh import (
                    OUTCOME_REFUSED,
                    PromptRefreshResult,
                )

                denial = PromptRefreshResult(
                    outcome=OUTCOME_REFUSED,
                    code=None,
                    message="non-ari caller",
                    provenance={"agent_id": caller_agent_id},
                )
                _emit_info_line(denial, caller_instance_id, caller_agent_id)
                return _not_ari(caller_agent_id)

            # Kill-switch — per call, in-body (AM-11).
            if not _resolve_enabled():
                from ..services.prompt_refresh import (
                    CODE_DISABLED,
                    PromptRefreshResult,
                    OUTCOME_REFUSED,
                )

                result = PromptRefreshResult(
                    outcome=OUTCOME_REFUSED,
                    code=CODE_DISABLED,
                    message=f"kill-switch {KILL_SWITCH_ENV} is OFF",
                    provenance={"agent_id": caller_agent_id},
                )
                _emit_info_line(result, caller_instance_id, caller_agent_id)
                return _refusal(CODE_DISABLED, f"kill-switch {KILL_SWITCH_ENV} is OFF")

            result = await manager.refresh_instance_prompt(
                caller_instance_id, force=force
            )
            _emit_info_line(result, caller_instance_id, caller_agent_id)
            if result.code is not None:
                if result.code in {
                    "prompt_reload_internal_error",
                }:
                    return _failure(result.code, result.message)
                return _refusal(result.code, result.message)
            return _render_report(result)
        except Exception:  # noqa: BLE001 — house pattern: typed envelope, never raise
            logger.exception("prompt_reload failed")
            from ..services.prompt_refresh import (
                CODE_INTERNAL_ERROR,
                PromptRefreshResult,
                OUTCOME_REFUSED,
            )

            result = PromptRefreshResult(
                outcome=OUTCOME_REFUSED,
                code=CODE_INTERNAL_ERROR,
                message="an unexpected error occurred (logged server-side)",
                provenance={"agent_id": caller_agent_id},
            )
            _emit_info_line(result, caller_instance_id, caller_agent_id)
            return _failure(
                CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); nothing "
                "was popped",
            )

    prompt_reload._full_doc_ = _build_full_doc()

    return [prompt_reload]


def _build_full_doc() -> str:
    from ..services.prompt_refresh import REFUSAL_CODES, FAILURE_CODES

    codes = "\n".join(f"- {code} (REFUSED envelope)" for code in sorted(REFUSAL_CODES))
    failed = "\n".join(f"- {code} (FAILED envelope)" for code in sorted(FAILURE_CODES))
    return f"""\
Refresh this instance's OWN system prompt from the active agent
definition — no restart, no session loss. The recomposed persona takes
effect on the NEXT dispatch (the current turn completes on the old
persona); this tool's return is the only in-turn signal.

What it does
------------
- Resolves this agent's directory the same way the spawn path does
  (registry), validates it against this daemon's environment (dev vs
  live/sandbox release), and verifies every file the prompt loader
  reads against the release manifest (tamper detection).
- Recomposes the prompt through the SAME loader the spawn/restore path
  uses — DB-approved prompt versions are honored automatically.
- Compares old vs new (per-file sha256 + the prompt-versions DB
  fingerprint) and evicts the cached graph ONLY when something changed
  (or force=true). Idempotent: unchanged inputs leave the cache in
  place and say so.
- Returns an abbreviated diff + provenance. When the old persona
  cannot be proven, the report says persona_diff=unavailable rather
  than guessing.

Accepted consequences (disclosed, by design)
--------------------------------------------
- The effect lands on the NEXT turn (never mid-turn); an in-flight
  turn completes on the old persona unharmed.
- Reloading the persona invalidates provider prompt caches — accepted.
- The FE-visible system message may lag until the next display
  rebuild; no display-parity promise.
- A first call after any daemon restart reports outcome=unproven and
  recomposes+evicts as a precaution — deliberate, not a defect.

Refusal codes (typed envelopes; tools never raise)
--------------------------------------------------
{codes}
{failed}
Kill-switch: {KILL_SWITCH_ENV}=false disables the tool per call (it
stays registered and refuses typed prompt_reload_disabled).
"""
