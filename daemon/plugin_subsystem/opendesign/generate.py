"""The native generate adapter (Port: od.generate).

The long pole — and the load-bearing fix for the 2026-10-06 2/2 live
failure. Composes the system prompt from the vendored OD prompt
library, calls the tier-1 LLM proxy (Mode P), and enforces
completeness gates INSIDE the adapter so the caller never sees a
silent partial / empty / thinking-only success.

**Mode-P rationale.** Per REC §4.2 + the slice-⑤ probe artifact
(``2026-10-06-slice-5-probes.md`` §A1.2), the composer chain is
string concatenation of pre-rendered template strings with conditional
inclusion + section ordering — re-expressible faithfully in Python
without a Node runtime or subprocess. The vendored contracts-mirror
strings are imported as module constants; the daemon copies are the
fallback when the mirror is absent (OQ7 disposition).

**Tier-1 LLM client.** The adapter constructs an
``openai.OpenAI(api_key=..., base_url=...)`` per call from the
``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL_VISION``
env vars (the project's existing LLM config; same lane the daemon
already uses, NOT a new client stack; the model is the purpose-bound
design-generation knob, not the default-pool chat model). Streaming is OFF by default; the
non-streaming call surfaces ``finish_reason`` + ``usage`` to the
caller — the live failure mode that the MCP lane cannot distinguish
(``finish_reason=length`` indistinguishable from ``finish_reason=stop``
per ``od-generation-engine.md`` §4).

**Completeness gates INSIDE the adapter.** The adapter refuses to
return a success dict on:

1. ``finish_reason != 'stop'`` — the upstream closed before the model
   finished (the live 2/2 failure's root cause per
   ``od-generation-engine.md`` §10 H1/H2).
2. ``html`` structurally incomplete — no closing ``</html>`` marker
   (catches the partial-mid-CSS failure mode, Attempt A1).
3. ``html`` empty — catches the thinking-only 0B failure mode (Attempt A2).

A truncated / empty / structurally-incomplete result is returned as an
error dict (CON §3 typed error envelope shape — ``ok: false``, ``code``,
``message``, ``details``) so the calling agent can fall back per the
live designer workflow ``fallback_reason: timeout | other:<detail>``
token set. The agent never sees a silent "partial HTML" success.

**No runtime loading (CON §7 sentinel).** The adapter imports the
vendored prompt strings as module constants (read once at module
import); no ``importlib`` or entry-point scan. The plugin tree is a
resource, never a role.

**Schema surface.** Mirrors the Port's ``inputs_schema`` and
``outputs_schema``. The ``error`` field on the outputs is ``null`` on
success and a CON §3 error envelope on failure.

**Compose chain (faithful to upstream ``apps/daemon/src/prompts/system.ts``).**
The :func:`_compose_system_prompt` function mirrors the 9-step
ordering documented at ``2026-10-06-slice-5-probes.md`` §A1.2. Strings
are read from the contracts mirror via the vendored snapshot
(``plugins/opendesign/snapshot_with_drift_alarm/prompts/contracts/``)
when present; daemon copies are the fallback when absent (OQ7).
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from daemon.plugin_subsystem.opendesign.ts_prompt_eval import TsPromptEvalError
from daemon.services.llm_failover import (
    current_failover_url,
    invoke_raw_with_failover,
)

__all__ = ["OdGenerate", "GenerateInput", "GenerateOutput", "_do_chat_call",
           "_invoke_chat_via_facade", "_PROXY_IDENTITY_HEADERS",
           "_resolve_llm_config", "_OD_FAILOVER_INACTIVE_NOTE"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compose inputs / outputs (mirror Port dataclass; JSON-friendly)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerateInput:
    """Inputs to :meth:`OdGenerate.execute`.

    Mirrors the Port's ``inputs_schema`` (od.generate). All fields are
    JSON-serializable; defaults match the upstream MCP defaults.
    """

    prompt: str
    kind: str = "prototype"  # prototype|deck|template|other|image|video|audio
    user_instructions: Optional[str] = None
    project_instructions: Optional[str] = None
    max_tokens: int = 64000
    skip_discovery_brief: bool = False
    design_system: Optional[str] = None
    skill_id: Optional[str] = None
    memory_body: Optional[str] = None
    audio_voice_options: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class GenerateOutput:
    """Outputs from :meth:`OdGenerate.execute`.

    Mirrors the Port's ``outputs_schema`` (od.generate). The ``error``
    field is ``None`` on success; a CON §3 error envelope on failure.
    """

    html: str
    finish_reason: str  # stop | length | content_filter | tool_calls | other
    usage: Dict[str, int]
    model: str
    truncated: bool
    error: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "html": self.html,
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
            "model": self.model,
            "truncated": self.truncated,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Vendored prompt strings (read from the contracts-mirror primary, daemon-copy fallback)
# ---------------------------------------------------------------------------
#
# The contracts mirror is the published API surface of the prompt stack
# (per the OQ7 disposition). We read the strings lazily (once at module
# import) from the vendored snapshot. If a contracts-mirror file is
# absent, we fall back to the daemon copy (the "core-slim asymmetry"
# exception per divergence-register entry id=3).
#
# ``PROMPTS_ROOT`` is computed lazily at the first compose call so the
# adapter is decoupled from the worktree's specific path. The tests
# override ``PROMPTS_ROOT`` to point at a fixture directory.

_PROMPTS_ROOT: Optional[Path] = None


def _default_prompts_root() -> Path:
    """The vendored snapshot's contracts-mirror primary.

    Resolution order:
    1. ``OD_PROMPTS_ROOT`` env var (operator override; CI / test fixture).
    2. ``{repo-root}/plugins/opendesign/snapshot_with_drift_alarm/prompts``
       (the canonical vendored snapshot from slice ②/③).

    Returns the contracts-mirror subtree as the primary source per the
    OQ7 disposition; daemon copies are a sibling directory under the
    same root and used as the fallback when the mirror is missing.
    """
    global _PROMPTS_ROOT
    if _PROMPTS_ROOT is not None:
        return _PROMPTS_ROOT
    override = os.environ.get("OD_PROMPTS_ROOT")
    if override:
        _PROMPTS_ROOT = Path(override)
    else:
        # vendored snapshot root; the callers know the relative subtree
        _PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "plugins" / "opendesign" / "snapshot_with_drift_alarm" / "prompts"
    return _PROMPTS_ROOT


def _read_prompt_file(relpath: str) -> str:
    """Read a vendored prompt string from the contracts-mirror or daemon-copy tree.

    The :func:`_read_prompt_file` helper reads the file relative to the
    contracts mirror; when the contracts mirror is absent, falls back
    to the daemon copy (per OQ7 — the daemon copy is the full module,
    the contracts mirror is the API subset). The fallback is a
    best-effort read; if both are absent, returns ``""`` (the compose
    chain degrades gracefully).
    """
    root = _default_prompts_root()
    # contracts-mirror primary (the published API surface per OQ7)
    contracts_candidate = root / "contracts" / relpath
    if contracts_candidate.exists():
        try:
            return contracts_candidate.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive
            logger.debug("od.generate: failed to read %s (%s); falling back", contracts_candidate, exc)
    # daemon-copy fallback (the full module; same prompt content for the
    # 5 mirrored modules, identical semantics for the compose chain)
    daemon_candidate = root / "daemon" / relpath
    if daemon_candidate.exists():
        try:
            return daemon_candidate.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive
            logger.debug("od.generate: failed to read %s (%s)", daemon_candidate, exc)
    return ""


# Compose chain — 5 mirrored modules from the contracts-mirror primary,
# 1 daemon-only fallback (core-slim is daemon-only per divergence
# register entry id=3 — the contracts mirror has no slim charter).
#
# Extraction (slice-⑤ F1 fix): the vendored snapshot is the TypeScript
# source and the compose chain must embed the RUNTIME string TypeScript
# would evaluate it to. The former ``_TS_STRING_RE`` regex stopped at
# the first closing backtick (including escaped ``\\``` INSIDE the
# literals), truncating every prompt and degrading the deck kind to the
# raw TS blob. :mod:`daemon.plugin_subsystem.opendesign.ts_prompt_eval`
# replaces it with a real scanner + bounded evaluator (escapes decoded,
# ``${…}`` substitutions resolved, mirrored builders for the upstream
# compose functions); the byte-equality corpus
# (``test_opendesign_prompt_extraction.py``) proves it against
# node-evaluated fixtures. Failures are LOUD
# (:class:`TsPromptEvalError` → ``prompt_composition_failed``
# envelope) — never a raw-source fallback.


def _read_prompt_module(relpath: str) -> str:
    """Read a vendored TS module source by module key.

    Same resolution as :func:`_read_prompt_file` for the prompts-tree
    keys (``contracts/<key>`` primary, ``daemon/<key>`` fallback); the
    ``runtime/`` subtree (vendored at slice ⑤'s F1 fix from the pinned
    contracts package) lives BESIDE ``prompts/`` —
    ``snapshot_with_drift_alarm/runtime/<key>`` — per the manifest's
    ``snapshot_with_drift_alarm.paths`` entry.
    """
    if relpath.startswith("runtime/"):
        root = _default_prompts_root().parent / "runtime"
        candidate = root / relpath[len("runtime/") :]
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError as exc:  # pragma: no cover - defensive
                logger.debug("od.generate: failed to read %s (%s)", candidate, exc)
        return ""
    return _read_prompt_file(relpath)


_PROMPT_SOURCE: Optional["TsPromptSource"] = None


def _prompt_source() -> "TsPromptSource":
    """Process-wide :class:`TsPromptSource` (module cache per process)."""
    global _PROMPT_SOURCE
    if _PROMPT_SOURCE is None:
        from daemon.plugin_subsystem.opendesign.ts_prompt_eval import TsPromptSource

        _PROMPT_SOURCE = TsPromptSource(reader=_read_prompt_module)
    return _PROMPT_SOURCE


def _extract_ts_string(source: str, symbol_name: str) -> str:
    """Extract a symbol's runtime string from a single TS source.

    Thin delegate to
    :func:`daemon.plugin_subsystem.opendesign.ts_prompt_eval.extract_symbol_from_source`
    (kept as a module-level name for callers/tests that target the
    extraction surface directly). Raises :class:`TsPromptEvalError` on
    absent symbols or unsupported constructs — loud, never a raw-source
    fallback.
    """
    from daemon.plugin_subsystem.opendesign.ts_prompt_eval import (
        extract_symbol_from_source,
    )

    return extract_symbol_from_source(source, symbol_name)


def _read_symbol(relpath: str, symbol_name: str) -> str:
    """Read a vendored prompt and return the symbol's runtime string.

    The module-level evaluator resolves template escapes, ``${…}``
    substitutions and the mirrored upstream builders. Raises
    :class:`TsPromptEvalError` (loud) when a present source cannot be
    evaluated faithfully; absent sources degrade to ``""`` per the
    documented missing-snapshot behavior.
    """
    from daemon.plugin_subsystem.opendesign.ts_prompt_eval import (
        TsPromptEvalError,
        _AbsentModule,
    )

    try:
        return _prompt_source().symbol_string(relpath, symbol_name)
    except _AbsentModule:
        # Missing vendored file — documented graceful degradation (the
        # compose chain proceeds without that part; the gates still run).
        logger.warning(
            "od.generate: vendored prompt module %s is absent; "
            "composing without it",
            relpath,
        )
        return ""
    except TsPromptEvalError as exc:
        logger.error(
            "od.generate: prompt extraction failed for %s::%s: %s",
            relpath,
            symbol_name,
            exc,
        )
        raise


# ---------------------------------------------------------------------------
# System-prompt composer (Mode-P; mirrors upstream 9-step ordering)
# ---------------------------------------------------------------------------


_API_MODE_OVERRIDE = """# API mode — no tools available (read first — overrides every rule below)

You are running through a plain Messages API. **No tools are wired through to you.** `TodoWrite`, `Read`, `Write`, `Edit`, `Bash`, and `WebFetch` are unavailable — calls to them will not execute and will not render in the UI.

Every later instruction in this prompt that tells you to "call TodoWrite", "run Bash", "read via Read", or otherwise invoke a tool is describing the daemon-mode workflow. In this API run those instructions are **overridden** — do not attempt them and do not pretend you did.

**Forbidden output:**
- Pseudo-tool markup such as `<todo-list>...</todo-list>`, `<tool-call>`, or invented XML wrappers around a plan.
- Fake-protocol prose such as `[读取 template.html ...]`, `[读取 layouts.md ...]`, `[正在调用 TodoWrite ...]`, or any `[doing X]` placeholder narrating a tool you cannot run.
- Statements like "I'll call TodoWrite to track this" or "let me read the skill file first" — there is no TodoWrite and no Read in this run.

**Allowed output:**
- Plain chat prose to the user (in their language). State your plan as prose — a short numbered list in markdown is fine; it just must not be wrapped in `<todo-list>` or claim to be a tool call.
- A final `<artifact type="text/html">...</artifact>` block containing a complete `<!doctype html>` document when the brief is ready to deliver.
- `<question-form>` blocks for discovery on turn 1, exactly as the rules below describe — question-form is markup the UI parses, not a tool call.

If the rules below tell you to plan with TodoWrite, write the plan as prose instead. If they tell you to read skill side files before writing, describe in one sentence which patterns/conventions you're going to apply and proceed. If they tell you to run brand-spec extraction via Bash + Read + WebFetch, ask the user the missing brand questions in the discovery form instead.
"""


_SKIP_DISCOVERY_BRIEF_OVERRIDE = """# Automated project mode — skip discovery form

This project was created through the daemon API with `skipDiscoveryBrief: true`. Override the discovery rules below: do NOT emit `<question-form id="discovery">`, do NOT show "Quick brief — 30 seconds", and do NOT ask a first-turn clarification form. Treat the user's first message and project metadata as the brief, then proceed directly to planning/building under the normal artifact workflow. Ask at most one concise follow-up only if a required detail is impossible to infer safely.
"""


_ACTIVE_DESIGN_SYSTEM_VISUAL_DIRECTION_OVERRIDE = """

---

## Active design system visual direction

Active design system exception: the active design system is the visual direction for this project. Use its DESIGN.md palette, typography, spacing, component rules, and theme tokens as the source of truth for color and mood.

- Do not ask the user to pick a separate theme color, visual direction, palette, typography mood, or direction card.
- Do not emit a direction question-form, a `direction-cards` picker, or any visual-direction card while an active design system is present.
- If an earlier discovery answer asks to "Pick a direction for me", treat that as already satisfied by the active design system and continue with the plan.
- When a downstream framework mentions "active direction" or "theme tokens", bind those fields from the active design system instead of the built-in direction library.
"""


def _render_metadata_block(
    kind: str,
    design_system: Optional[str],
    skill_id: Optional[str],
    audio_voice_options: Optional[Tuple[str, ...]],
) -> str:
    """Render the project-metadata block (per upstream ``renderMetadataBlock``).

    The block is the trailing semantic context the model uses to bind
    the right framework / contract — the deck framework for decks, the
    media-generation contract for media surfaces, the responsive-web
    contract for responsive-web targets, etc. The render is a minimal
    faithful reading of the upstream function (covers the cases the
    MCP BYOK lane exercises — a faithful full port is owed at ⑤b if
    the designer lane ever needs the more exotic blocks).

    NOTE: the upstream renderer is ~6 KB of conditional logic; this is
    the minimal version for the BYOK lane's typical inputs. The full
    render can be ported as needed; the slice-⑤ scope is the mockup /
    deck lane per the probe artifact.
    """
    lines: List[str] = ["\n\n## Project metadata"]
    lines.append("These are the structured choices the user made (or skipped) when creating this project. Treat known fields as authoritative; for any field marked \"(unknown — ask)\" you MUST include a matching question in your turn-1 discovery form.")
    lines.append("")
    lines.append(f"- **kind**: {kind}")
    if design_system:
        lines.append(f"- **design_system**: {design_system}")
    if skill_id:
        lines.append(f"- **skill**: {skill_id}")
    if audio_voice_options:
        lines.append(f"- **audio_voice_options**: {', '.join(audio_voice_options)}")
    return "\n".join(lines)


def _compose_system_prompt(args: GenerateInput) -> str:
    """Compose the system prompt (Mode-P; faithful to upstream 9-step ordering).

    See ``2026-10-06-slice-5-probes.md`` §A1.2 for the 9-step algorithm.
    """
    parts: List[str] = []

    # Step 1: API_MODE_OVERRIDE (top anchor for plain streamFormat — the
    # BYOK lane is always streamFormat='plain').
    parts.append(_API_MODE_OVERRIDE)
    parts.append("\n\n---\n\n")

    # Step 2: skip_discovery_brief override.
    if args.skip_discovery_brief:
        parts.append(_SKIP_DISCOVERY_BRIEF_OVERRIDE)
        parts.append("\n\n---\n\n")

    # Step 3: DISCOVERY_AND_PHILOSOPHY + BASE_SYSTEM_PROMPT.
    discovery = _read_symbol("discovery.ts", "DISCOVERY_AND_PHILOSOPHY")
    official = _read_symbol("official-system.ts", "OFFICIAL_DESIGNER_PROMPT")
    parts.append(discovery)
    parts.append("\n\n---\n\n# Identity and workflow charter (background)\n\n")
    parts.append(official)

    # Step 4: memory / user / project instructions (each is conditional).
    if args.memory_body and args.memory_body.strip():
        parts.append(
            "\n\n## Personal memory (auto-extracted from past chats)\n\n"
            "The following facts have been sedimented from this user's previous conversations and edited in the settings panel. Treat them as preferences and context, NOT hard rules: when they collide with the active design system tokens, the brand wins; when they collide with the active skill's workflow, the skill wins. They are still authoritative for tone, voice, terminology, and what the user already told you about themselves and their goals — never re-ask the user about something already captured here.\n\n"
            + args.memory_body.strip()
        )
    if args.user_instructions and args.user_instructions.strip():
        parts.append(
            "\n\n## Custom instructions (user-level)\n\n"
            "The user has set the following persistent instructions. Apply them as defaults to every project. When a project-level instruction below contradicts a point here, the project-level version wins.\n\n"
            + args.user_instructions.strip()
        )
    if args.project_instructions and args.project_instructions.strip():
        parts.append(
            "\n\n## Custom instructions (project-level)\n\n"
            "The user has set the following instructions for this specific project. They take precedence over user-level custom instructions whenever both address the same topic (e.g. if user-level says \"use spaces\" but project-level says \"use tabs\", use tabs).\n\n"
            + args.project_instructions.strip()
        )

    # Step 5: design-system block (resolved by design_system param; the
    # design_system string is a path under copy_freely/design-systems/).
    if args.design_system:
        # The actual DESIGN.md body is loaded by the caller into
        # ``memory_body`` when the design system is resolved. For the
        # in-process adapter, we surface the design system name as a
        # structured header; the caller can prepend the DESIGN.md body
        # via memory_body if needed. The schema surface accepts the
        # design_system string either way.
        parts.append(
            f"\n\n## Active design system — {args.design_system}\n\n"
            f"Active design system: `{args.design_system}`. Bind its tokens "
            f"into the artifact's `:root` block before generating any layout. "
            f"DESIGN.md is loaded by the caller via memory_body when the "
            f"full system body is needed."
        )

    # Step 6: skill block (resolved by skill_id param). Same pattern as
    # design_system — the body is the caller's responsibility.
    if args.skill_id:
        parts.append(
            f"\n\n## Active skill — {args.skill_id}\n\n"
            f"Active skill: `{args.skill_id}`. Follow this skill's workflow "
            f"exactly. The full skill body is loaded by the caller when needed."
        )

    # Step 7: renderMetadataBlock
    meta = _render_metadata_block(
        kind=args.kind,
        design_system=args.design_system,
        skill_id=args.skill_id,
        audio_voice_options=args.audio_voice_options,
    )
    parts.append(meta)

    # Step 8: deck framework (deck kind OR freeform kind).
    is_deck = args.kind == "deck"
    is_freeform = args.kind in ("other",)
    has_skill_seed = bool(args.skill_id)
    if is_deck and not has_skill_seed:
        deck = _read_symbol("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE")
        if deck:
            parts.append(f"\n\n---\n\n{deck}")
    elif is_freeform and not has_skill_seed:
        deck = _read_symbol("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE")
        if deck:
            parts.append(
                "\n\n---\n\n## If this brief is a slide deck / keynote / presentation\n\n"
                "The user did not pre-select a \"Slide deck\" surface, but their request may still call for one. **If — and only if — the brief reads as slides, keynote, presentation, deck, PPT, or 讲解, follow the framework below.** Otherwise ignore everything in this section and continue with the freeform output you would have written anyway.\n\n"
                + deck
            )

    # Step 9: media contract (image/video/audio kind).
    if args.kind in ("image", "video", "audio"):
        media = _read_symbol("media-contract.ts", "MEDIA_GENERATION_CONTRACT")
        if media:
            parts.append(media)

    # Trailing effect: ACTIVE_DESIGN_SYSTEM_VISUAL_DIRECTION_OVERRIDE
    if args.design_system:
        parts.append(_ACTIVE_DESIGN_SYSTEM_VISUAL_DIRECTION_OVERRIDE)

    return "".join(parts)


# ---------------------------------------------------------------------------
# Completeness gates INSIDE the adapter
# ---------------------------------------------------------------------------


_HTML_EOF_MARKERS: Tuple[str, ...] = ("</html>", "</body>")


def _gate_html(html: str, finish_reason: str) -> Tuple[bool, Optional[str]]:
    """Run the inline completeness gates.

    Returns ``(truncated, error_code_or_none)``. The adapter refuses to
    return a success dict on truncated=True; the error_code is one of
    the Port's typed envelope codes (``truncation_detected``,
    ``missing_artifact_marker``, ``empty_response``).
    """
    # Gate 1: empty response.
    if not html or not html.strip():
        return True, "empty_response"
    # Gate 2: finish_reason != 'stop' ⇒ the upstream closed before the
    # model finished. This is the load-bearing gate for the live 2/2
    # failure (od-generation-engine.md §10 H1).
    if finish_reason and finish_reason != "stop":
        return True, "truncation_detected"
    # Gate 3: structural completeness — the closing tags must be present.
    # A partial-mid-CSS document (Attempt A1 of the 2026-10-06 smoke)
    # has neither ``</html>`` nor ``</body>``; we surface a
    # ``missing_artifact_marker`` error.
    has_close = any(marker.lower() in html.lower() for marker in _HTML_EOF_MARKERS)
    if not has_close:
        return True, "missing_artifact_marker"
    return False, None


# ---------------------------------------------------------------------------
# LLM client (tier-1 proxy; same lane the daemon already uses)
# ---------------------------------------------------------------------------
#
# Stage 1 of the od-generate-agent-lane plan: wire od.generate's raw SDK
# call onto the ensemble LLM lane via the shared HA facade. Replaces the
# pre-v2 single-shot ``openai.OpenAI(...).chat.completions.create(...)``
# call with a module-level factory + ``invoke_raw_with_failover`` so the
# call gets bounded retry + (when configured) primary→backup swap.
#
# Three things this section preserves from pre-v2:
#
#   1. **OPENAI_MODEL_VISION-first model resolution** — the purpose-bound
#      design knob wins; the daemon's default-pool chat model
#      (``OPENAI_MODEL``) is never read. See the comment at the
#      resolution site for the 2026-10-07 designer-model-vision-fix arc.
#   2. **Adaptive inner timeout formula** — ``max(120.0, max_tokens /
#      370.0)``. Pinned by tests/unit/plugin_subsystem/test_opendesign_b_element.py
#      at ~173 s (64K) and ~540 s (200K). 370 tok/s is the conservative
#      divisor derived from the live 130-170 s observation at 64K tokens.
#   3. **The 3 completeness gates** (empty / non-stop finish_reason /
#      structural ``</html>``/``</body>`` marker) — those are applied
#      AFTER the facade call returns; the facade owns retry, the adapter
#      owns the gate verdict (the failure mode that caused the
#      2026-10-06 2/2 live failure).
#
# Things this section ADDS in Stage 1:
#
#   4. **Per-attempt URL reread via ``current_failover_url()``** — the
#      factory re-enters on every retry, and each attempt re-reads the
#      target URL via the facade's thread-local. Capturing the primary
#      URL in a closure would break failover (see module docstring of
#      ``daemon.services.llm_failover``).
#   5. **Proxy identity headers** (``x-proxy-app`` / ``x-proxy-interleaved-thinking``)
#      — closes the raw-SDK parity gap that the agent-chat hot path
#      already carries (6 inline ``default_headers`` sites; see
#      ``daemon/compaction.py:2277-2278`` for the canonical stamp).
#   6. **SDK max_retries=0** — the openai SDK's built-in retry is
#      disabled; the facade owns retry discipline (otherwise the SDK's
#      default ``max_retries=2`` would silently double-budget the
#      transient-retry ladder and inflate the failure window).
#   7. **wall_clock_cap_s=420.0** — the 130-170 s live observation +
#      the 60 s cushion for transient retry backoff ≈ 230 s nominal,
#      rounded up to 420 s for headroom under the HA-on path.
#   8. **Typed 400-class envelopes** — ``upstream_bad_request`` for
#      generic openai.BadRequestError; ``context_length_exceeded`` for
#      the contextual-overflow sniff (matches the hot-path classifier's
#      existing ``ContextLengthExceededError`` taxonomy).
#
# **Backup endpoint unconfigured today (failover inert, decision D3
# open).** When the operator sets ``OPENAI_BASE_URL_BACKUP``, the
# facade's HA controller activates and the retry ladder splits between
# primary and backup. Until then, retry runs against the primary only
# — same pre-v2 behavior modulo bounded retry. Cite
# ``daemon/services/skill_embedding_service.py:468-474`` for the
# endpoint-mismatch guard precedent: when a backup is configured it MUST
# serve the vision model (the design-generation knob), not the chat
# model — mismatched-endpoint failover is wrong by design.
#
# **Single-shot invocation seam: ``_LLM_INVOKER``.** A class-level
# staticmethod that runs the entire chat invocation — config resolution
# → facade-wrapped API call → response. Default implementation
# (``_invoke_chat_via_facade``) routes through ``invoke_raw_with_failover``
# with the per-attempt URL reread. Tests override ``_LLM_INVOKER`` to
# inject canned responses without touching the openai SDK or the facade.
# The seam replaces the pre-v2 ``_CLIENT_FACTORY`` (which returned an
# openai.OpenAI client); the existing 30+ tests are migrated
# mechanically (see ``test_opendesign_b_element.py``).


# Proxy identity headers — same stamp the agent-chat hot path carries
# (compaction.py:2277 canonical hot-path stamp + keyword_extraction.py:370
# + 4 other inline sites). The proxy uses
# these to identify ensemble traffic and to enable interleaved thinking
# mode on the vision BYOK lane. Missing them from a raw-SDK site is a
# known parity gap that the Stage 1 wiring closes.
_PROXY_IDENTITY_HEADERS: Dict[str, str] = {
    "x-proxy-app": "ensemble",
    "x-proxy-interleaved-thinking": "True",
}

# Per-call wall-clock cap for the facade (prescription: 420 s).
# Default 45 s would kill 130-170 s calls; 420 s leaves room for the
# HA retry ladder (3 transient + 2 timeout attempts + exponential-jitter
# backoff).
_OD_GENERATE_WALL_CLOCK_CAP_S: float = 420.0

# Operators haven't configured ``OPENAI_BASE_URL_BACKUP`` in this
# deployment — failover is INERT until that env var appears. The
# docstring tag keeps the operational truth visible at the call site.
_OD_FAILOVER_INACTIVE_NOTE = (
    "OPENAI_BASE_URL_BACKUP unset on this deployment → "
    "FailoverController.is_configured=False → every retry is against "
    "primary only (bounded, not blind-failover)."
)


def _resolve_llm_config(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Resolve the raw ``llm_config`` dict the facade expects.

    The facade's :func:`invoke_raw_with_failover` requires a RAW config
    dict (``base_url`` / ``base_url_backup`` / ``api_key``). The graph
    path's :func:`clean_llm_config` strips ``base_url_backup`` and
    would silently kill failover — the facade reads the BACKUP directly
    from the dict it receives, so we pass it through untransformed.

    Returns:
        Dict with keys ``base_url``, ``base_url_backup`` (None if unset),
        ``api_key``, ``model``. The ``model`` field carries the
        PURPOSE-BOUND design knob (``OPENAI_MODEL_VISION``); the daemon's
        default-pool chat model ``OPENAI_MODEL`` is NEVER read here.

    Raises:
        RuntimeError: ``byok_not_configured`` envelope when
            ``OPENAI_BASE_URL`` or ``OPENAI_API_KEY`` is missing.
    """
    src = env if env is not None else os.environ
    base_url = src.get("OPENAI_BASE_URL")
    api_key = src.get("OPENAI_API_KEY")
    # Mirrors the 2026-10-07 designer-model-vision fix: the generation
    # model is the PURPOSE-BOUND design knob (OPENAI_MODEL_VISION; .env
    # sets it to ``vision`` — the BYOK design-generation model on the
    # user's llm-supervisor-proxy), NEVER the daemon's default-pool chat
    # model (OPENAI_MODEL). The previous resolution read OPENAI_MODEL,
    # so the live v0.18.0 designer debut generated on ``agentic``
    # (settings-page-redesign/design/OD-LANE-FAILURE.md — the "upstream
    # timeout" attribution was wrong-model, not upstream capacity).
    model = src.get("OPENAI_MODEL_VISION", "vision")
    # Backup URL is opt-in: when absent the facade's _RawFailoverShim
    # builds with ``failover_controller=None`` and every retry hits the
    # primary (bounded retry only; HA swap inert — see D3 open).
    base_url_backup = src.get("OPENAI_BASE_URL_BACKUP") or None
    if not base_url or not api_key:
        raise RuntimeError(
            "byok_not_configured: OPENAI_BASE_URL and OPENAI_API_KEY must be set "
            "(per the project's LLM config; same lane the daemon already uses)"
        )
    return {
        "base_url": base_url,
        "base_url_backup": base_url_backup,
        "api_key": api_key,
        "model": model,
    }


def _build_openai_client(env: Optional[Mapping[str, str]] = None):
    """Legacy v1 seam — preserved for ``test_generate_model_resolution.py``.

    Returns ``(openai.OpenAI(...), model)``. The model resolution is the
    load-bearing 2026-10-07 fix; the client itself is constructed
    directly (the v2 prod path goes through
    :func:`_invoke_chat_via_facade` instead, leaving this seam for the
    4 pinned regression tests only).

    The env var resolution is opt-in via ``env`` for testability; the
    default reads ``os.environ``.
    """
    cfg = _resolve_llm_config(env)
    try:
        import openai  # noqa: PLC0415 - imported here for lazy init
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(f"openai SDK not importable: {exc}") from exc
    return openai.OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"]), cfg["model"]


def _do_chat_call(
    model: str,
    base_url: Optional[str],
    api_key: Optional[str],
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
    default_headers: Optional[Dict[str, str]] = None,
    max_retries: int = 0,
) -> Any:
    """Module-level chat-completion factory.

    Constructed fresh on every retry attempt. URL is re-read via
    :func:`current_failover_url` (a thread-local the facade updates
    per attempt) so a primary→backup swap is observed by the next
    attempt's client construction. Capturing the URL in a closure
    would break failover (the stale-URL failure mode flagged in
    :mod:`daemon.services.llm_failover`).

    Mirror of ``daemon.services.skill_embedding_service._do_chat_call``
    + ``daemon.services.snapshot_embedding_service._do_chat_call``;
    the three factories share the per-attempt URL reread pattern. The
    differences here: the model is the vision knob (not the chat
    default), the request carries ``max_tokens``/``temperature``/``timeout``
    (the generate-specific knobs), and the SDK's built-in retry is
    disabled (``max_retries=0`` — the facade owns retry discipline).
    The proxy identity headers ride on ``default_headers`` to close the
    raw-SDK parity gap.
    """
    import openai  # noqa: PLC0415 - imported here for lazy init

    url = current_failover_url() or base_url
    client_kwargs: Dict[str, Any] = {
        "api_key": api_key or "",
        "base_url": url or None,
        "max_retries": max_retries,
    }
    if default_headers:
        client_kwargs["default_headers"] = dict(default_headers)
    client = openai.OpenAI(**client_kwargs)
    return client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


def _invoke_chat_via_facade(
    model: str,
    base_url: Optional[str],
    base_url_backup: Optional[str],
    api_key: Optional[str],
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
    default_headers: Optional[Dict[str, str]] = None,
    wall_clock_cap_s: float = _OD_GENERATE_WALL_CLOCK_CAP_S,
) -> Any:
    """Default implementation of :attr:`OdGenerate._LLM_INVOKER`.

    Wraps :func:`_do_chat_call` in a closure factory and routes through
    :func:`invoke_raw_with_failover` with ``wall_clock_cap_s`` passed
    EXPLICITLY (the facade's default 45 s would kill 130-170 s calls).
    The factory closure captures ONLY the per-call arguments (model,
    prompts, knobs); the URL is re-read inside :func:`_do_chat_call`
    on every retry via ``current_failover_url()`` — never captured.

    Args:
        model, base_url, base_url_backup, api_key: Resolved from
            :func:`_resolve_llm_config`.
        system_prompt, user_prompt: Composed upstream
            (``_compose_system_prompt``).
        max_tokens, temperature, timeout: The generate knobs.
            ``timeout = max(120.0, max_tokens / 370.0)`` per the
            pre-v2 formula (preserved; pinned by tests).
        default_headers: Carries the proxy identity headers
            (``x-proxy-app`` / ``x-proxy-interleaved-thinking``).
        wall_clock_cap_s: Total wall-clock cap for the entire
            facade cycle (default 420 s — calibrated above the
            HA-on backoff envelope).

    Raises:
        Whatever :func:`invoke_raw_with_failover` surfaces after the
        retry budget is exhausted. The caller (``OdGenerate.execute``)
        catches ``openai.BadRequestError`` for the typed 400 envelopes
        and ``Exception`` for the catch-all upstream envelope.
    """
    llm_config: Dict[str, Any] = {
        "base_url": base_url,
        "base_url_backup": base_url_backup,
        "api_key": api_key,
    }

    def _factory() -> Any:
        return _do_chat_call(
            model=model,
            base_url=base_url,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            default_headers=default_headers,
        )

    return invoke_raw_with_failover(
        _factory,
        llm_config,
        wall_clock_cap_s=wall_clock_cap_s,
    )


# ---------------------------------------------------------------------------
# Adapter surface
# ---------------------------------------------------------------------------


class OdGenerate:
    """The native generate adapter (Port: od.generate).

    Stateless; instantiated once at boot. :meth:`execute` is the public
    entry; :meth:`execute_dict` accepts a dict matching the Port's
    ``inputs_schema`` and returns the ``outputs_schema`` shape (for
    the plugin tool factory wiring).

    **Stage-1 LLM lane.** :attr:`_LLM_INVOKER` is the single seam where
    the chat-completion call enters. Default impl
    (:func:`_invoke_chat_via_facade`) routes through the shared HA
    facade (``invoke_raw_with_failover``) so every call gets bounded
    retry + (when ``OPENAI_BASE_URL_BACKUP`` is set) primary→backup
    failover. Tests override the seam via
    :meth:`_set_test_hooks` — see
    ``tests/unit/plugin_subsystem/test_opendesign_b_element.py`` for
    the migration from the pre-v2 ``_CLIENT_FACTORY`` (the v1 seam
    returned ``(openai.OpenAI, model)``; v2 returns the chat
    completion directly).
    """

    # --- injection points for tests ---------------------------------------

    _LLM_INVOKER = staticmethod(_invoke_chat_via_facade)
    _COMPOSER = staticmethod(_compose_system_prompt)

    @classmethod
    def execute(cls, args: GenerateInput, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        """Compose the prompt, call the LLM, gate the output. Return
        the Port's ``outputs_schema`` dict.

        The ``env`` parameter is opt-in for tests; the default reads
        ``os.environ``. ``_LLM_INVOKER`` and ``_COMPOSER`` are
        class-level injection points for tests (see
        :meth:`_set_test_hooks`).

        The Stage-1 wiring routes the chat-completion call through
        :func:`invoke_raw_with_failover` with
        ``wall_clock_cap_s=420.0``. The SDK's built-in retry
        (``max_retries=0``) is disabled so the facade owns the retry
        discipline. Typed 400-class envelopes
        (``upstream_bad_request`` / ``context_length_exceeded``) are
        surfaced here when the facade exhausts its retry budget and
        re-raises ``openai.BadRequestError`` unmodified.
        """
        if not isinstance(args.prompt, str) or not args.prompt:
            return cls._error_envelope(
                "prompt_composition_failed",
                "'prompt' is required and must be a non-empty string",
                details=None,
                finish_reason="other",
            )

        # Resolve the raw llm_config dict from env. Failures here
        # surface as the typed ``byok_not_configured`` envelope (same
        # shape as pre-v2).
        try:
            cfg = _resolve_llm_config(env)
        except Exception as exc:  # noqa: BLE001
            logger.warning("od.generate: config resolution failed: %s", exc)
            return cls._error_envelope(
                "byok_not_configured",
                f"config resolution failed: {exc}",
                details={"max_tokens": args.max_tokens},
                finish_reason="other",
            )
        model = cfg["model"]
        base_url = cfg["base_url"]
        base_url_backup = cfg["base_url_backup"]
        api_key = cfg["api_key"]

        # Compose (F1: extraction is loud — a vendored source that cannot
        # be evaluated faithfully surfaces as a typed envelope instead of
        # a truncated / raw-blob prompt riding into the LLM call).
        try:
            system_prompt = cls._COMPOSER(args)
        except TsPromptEvalError as exc:
            logger.error("od.generate: prompt composition failed: %s", exc)
            return cls._error_envelope(
                "prompt_composition_failed",
                f"vendored prompt extraction failed: {exc}",
                details=None,
                finish_reason="other",
            )

        # Adaptive inner per-request timeout (preserved verbatim from
        # pre-v2). Derivation: the live lane observed 130-170s at
        # 64K tokens (tools_note.md:49 + workflow.md:77), giving ~376-492 tok/s.
        # We use a CONSERVATIVE divisor 370 tok/s (64000/370 ~= 173s;
        # 200000/370 ~= 540s) with a 120s floor (a sub-120s budget is never
        # right for generation — the prior 60s floor was below the live
        # observation and would fire upstream_http_error on SUCCESSFUL calls).
        # This per-request ``timeout`` guards against a single hanging
        # request; ``wall_clock_cap_s=420`` on the facade is the
        # retry-storm ceiling.
        timeout = max(120.0, args.max_tokens / 370.0)  # 370 tok/s conservative
        try:
            response = cls._LLM_INVOKER(
                model=model,
                base_url=base_url,
                base_url_backup=base_url_backup,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=args.prompt,
                max_tokens=args.max_tokens,
                temperature=0.7,
                timeout=timeout,
                default_headers=_PROXY_IDENTITY_HEADERS,
            )
        except Exception as exc:  # noqa: BLE001 - any facade-exhausted failure becomes a typed envelope
            # Lazy import — the openai SDK is an optional dep; tests
            # that override ``_LLM_INVOKER`` may never import it.
            try:
                import openai  # noqa: PLC0415
            except ImportError:  # pragma: no cover
                openai = None  # type: ignore[assignment]
            if openai is not None and isinstance(exc, openai.BadRequestError):
                # Stage-1 typed 400-class envelope (commission override
                # over the plan's "envelopes unchanged"). The openai SDK
                # raises BadRequestError for any 400-class HTTP error
                # AFTER the facade exhausts its retry budget (the
                # facade's classifier treats 400 as non-retryable — the
                # raw exception re-raises unmodified).
                err_str = str(exc).lower()
                if any(
                    needle in err_str
                    for needle in (
                        "context_length_exceeded",
                        "maximum context length",
                        "reduce the length",
                        "context length",
                    )
                ):
                    logger.warning(
                        "od.generate: context length exceeded: %s", exc
                    )
                    return cls._error_envelope(
                        "context_length_exceeded",
                        f"context length exceeded: {exc}",
                        details={
                            "max_tokens": args.max_tokens,
                            "model": model,
                        },
                        finish_reason="other",
                    )
                logger.warning("od.generate: upstream BadRequestError: %s", exc)
                return cls._error_envelope(
                    "upstream_bad_request",
                    f"upstream BadRequestError: {exc}",
                    details={
                        "max_tokens": args.max_tokens,
                        "model": model,
                    },
                    finish_reason="other",
                )
            logger.warning("od.generate: upstream call failed: %s", exc)
            return cls._error_envelope(
                "upstream_http_error",
                f"upstream call failed: {exc}",
                details={"max_tokens": args.max_tokens, "model": model},
                finish_reason="other",
            )

        # Extract finish_reason + usage. The OpenAI client returns a
        # ChatCompletion object; we read the attributes defensively.
        try:
            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or "other"
            html = getattr(choice.message, "content", "") or ""
        except (IndexError, AttributeError) as exc:
            logger.warning("od.generate: response shape unexpected: %s", exc)
            return cls._error_envelope(
                "upstream_stream_closed",
                f"response shape unexpected: {exc}",
                details={"model": model},
                finish_reason="other",
            )

        # Usage is on the response (not the choice).
        usage_obj = getattr(response, "usage", None)
        usage: Dict[str, int] = {
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0) if usage_obj else 0,
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0) if usage_obj else 0,
            "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0) if usage_obj else 0,
        }
        # ``reasoning_tokens`` is OpenAI-specific; capture if present.
        details = getattr(usage_obj, "completion_tokens_details", None) if usage_obj else None
        if details is not None:
            reasoning = getattr(details, "reasoning_tokens", None)
            if reasoning is not None:
                usage["reasoning_tokens"] = int(reasoning)

        # Apply the inline completeness gates.
        truncated, error_code = _gate_html(html, finish_reason)
        if truncated:
            logger.info(
                "od.generate: completeness gate refused (code=%s, finish_reason=%s, html_bytes=%d)",
                error_code,
                finish_reason,
                len(html),
            )
            return cls._error_envelope(
                error_code or "truncation_detected",
                (
                    f"completeness gate refused: finish_reason={finish_reason}, "
                    f"html_bytes={len(html)} (refused to return a partial / "
                    f"empty / structurally-incomplete artifact as success)"
                ),
                details={
                    "finish_reason": finish_reason,
                    "html_bytes": len(html),
                    "model": model,
                    "usage": usage,
                },
                finish_reason=finish_reason,
                model=model,
                usage=usage,
            )

        # Success path.
        output = GenerateOutput(
            html=html,
            finish_reason=finish_reason,
            usage=usage,
            model=model,
            truncated=False,
            error=None,
        )
        return output.as_dict()

    @classmethod
    def execute_dict(cls, raw: Dict[str, Any], env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        """Adapter-friendly entrypoint: accept a dict matching the
        Port's ``inputs_schema`` and return the Port's ``outputs_schema``.
        """
        if not isinstance(raw, Mapping):
            return cls._error_envelope(
                "prompt_composition_failed",
                "input must be a JSON object",
                details=None,
            )
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return cls._error_envelope(
                "prompt_composition_failed",
                "'prompt' is required and must be a non-empty string",
                details=None,
            )
        kind = raw.get("kind", "prototype")
        if kind not in ("prototype", "deck", "template", "other", "image", "video", "audio"):
            return cls._error_envelope(
                "prompt_composition_failed",
                f"'kind' must be one of prototype|deck|template|other|image|video|audio; got {kind!r}",
                details={"kind": kind},
            )
        max_tokens = raw.get("max_tokens", 64000)
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 64000
        if max_tokens < 1 or max_tokens > 200000:
            max_tokens = 64000
        skip_discovery_brief = bool(raw.get("skip_discovery_brief", False))
        audio = raw.get("audio_voice_options")
        args = GenerateInput(
            prompt=prompt,
            kind=kind,
            user_instructions=raw.get("user_instructions") if isinstance(raw.get("user_instructions"), str) else None,
            project_instructions=raw.get("project_instructions") if isinstance(raw.get("project_instructions"), str) else None,
            max_tokens=max_tokens,
            skip_discovery_brief=skip_discovery_brief,
            design_system=raw.get("design_system") if isinstance(raw.get("design_system"), str) else None,
            skill_id=raw.get("skill_id") if isinstance(raw.get("skill_id"), str) else None,
            memory_body=raw.get("memory_body") if isinstance(raw.get("memory_body"), str) else None,
            audio_voice_options=tuple(audio) if isinstance(audio, list) and all(isinstance(x, str) for x in audio) else None,
        )
        return cls.execute(args, env=env)

    @staticmethod
    def _error_envelope(
        code: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        finish_reason: str = "other",
        model: str = "",
        usage: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """Format the CON §3 typed error envelope (ok=false).

        The success-shaped output dict has ``error=null``; the
        truncated path has the error envelope at the top level
        (the agent routes via ``error.code``). The truncated-path
        fields (html, finish_reason, usage, model, truncated) are
        still surfaced for the caller to inspect — the gate verdict
        is in ``error.code`` + ``truncated=True``.

        ``finish_reason`` is the actual reason from the upstream call
        (when available), so the caller can distinguish ``length`` from
        ``content_filter`` from ``tool_calls``. ``model`` and ``usage``
        are passed through when the adapter successfully constructed the
        request; default to the safe "other" + "" + zeros when the
        failure happened before the upstream call.
        """
        envelope: Dict[str, Any] = {
            "ok": False,
            "code": code,
            "message": message,
        }
        if details is not None:
            envelope["details"] = details
        return {
            "html": "",
            "finish_reason": finish_reason,
            "usage": dict(usage) if usage is not None else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "model": model,
            "truncated": True,
            "error": envelope,
        }

    @classmethod
    def _set_test_hooks(
        cls,
        *,
        llm_invoker=None,
        composer=None,
    ) -> None:
        """Inject test hooks (class-level; restore in tearDown).

        The hooks let tests stub the LLM invocation and the composer
        without monkey-patching the module globals. Tests should
        restore the defaults in tearDown.

        Stage 1 renaming: the pre-v2 ``client_factory`` kwarg was
        replaced by ``llm_invoker``. The semantics shifted from "build
        an openai.OpenAI client and return it" to "run the full chat
        invocation and return the response" — the facade wrapping is
        now internal to the default ``_LLM_INVOKER``. Tests that
        need a canned response inject a callable matching the
        :func:`_invoke_chat_via_facade` signature; the existing
        ``fake_invoker`` helper in ``test_opendesign_b_element.py``
        provides the canonical idiom.
        """
        saved: List[Any] = []
        if llm_invoker is not None:
            saved.append(("llm_invoker", cls._LLM_INVOKER))
            cls._LLM_INVOKER = staticmethod(llm_invoker)
        if composer is not None:
            saved.append(("composer", cls._COMPOSER))
            cls._COMPOSER = staticmethod(composer)
        return saved