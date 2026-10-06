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
``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL`` env vars
(the project's existing LLM config; same lane the daemon already
uses, NOT a new client stack). Streaming is OFF by default; the
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

__all__ = ["OdGenerate", "GenerateInput", "GenerateOutput"]

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
# NOTE: the upstream prompt files are compiled JS strings
# (``dist/vendor/od-contracts/src/prompts/<name>.js``) in the installed
# MCP shim. The vendored snapshot in this repo is the **TypeScript
# source** (``.ts``) because the contracts package ships source. The
# Python adapter strips TypeScript ``export const`` wrappers via a
# simple regex to extract the string body. This is a no-op for the
# ``.js`` files when the operator overrides ``OD_PROMPTS_ROOT``.
_TS_STRING_RE = re.compile(
    r"^export\s+const\s+(?P<name>[A-Z_][A-Z0-9_]*)\s*[:=]\s*(?P<quote>['\"`])"
    r"(?P<body>.*?)"
    r"(?P=quote)\s*[;\n]",
    re.MULTILINE | re.DOTALL,
)


def _extract_ts_string(source: str, symbol_name: str) -> str:
    """Extract a ``export const NAME = '…'`` body from a TypeScript source string.

    The vendored snapshot's prompt files are TypeScript
    (``export const OFFICIAL_DESIGNER_PROMPT = `…`;``); the upstream
    MCP shim's compiled JS uses ``module.exports.NAME = '…'`` instead.
    Both forms have the same surface — find the matching symbol and
    return its body.

    Used by :func:`_compose_system_prompt` so the vendored snapshot
    can be the source of truth regardless of the upstream file
    format. Falls back to the raw source when no match is located (a
    degraded but working path — the model receives the TypeScript source
    verbatim and the gates still run).
    """
    if not source:
        return ""
    m = _TS_STRING_RE.search(source)
    if m and m.group("name") == symbol_name:
        return m.group("body")
    # Try alternate forms — the upstream ``module.exports.NAME = "…"``
    # JS shape and the inline ``const NAME = "…"`` shape.
    for pat in (
        rf"module\.exports\.(?P<name>{symbol_name})\s*=\s*['\"`]" r"(?P<body>.*?)" r"['\"`]\s*;",
        rf"const\s+(?P<name>{symbol_name})\s*=\s*['\"`]" r"(?P<body>.*?)" r"['\"`]\s*;",
        rf"export\s+const\s+(?P<name>{symbol_name})\s*[:=]\s*['\"`]" r"(?P<body>.*?)" r"['\"`]\s*[;\n]",
    ):
        for m in re.finditer(pat, source, re.DOTALL):
            if m.group("name") == symbol_name:
                return m.group("body")
    return source  # degraded: return raw source


def _read_symbol(relpath: str, symbol_name: str) -> str:
    """Read a vendored prompt and extract the named exported string constant."""
    raw = _read_prompt_file(relpath)
    return _extract_ts_string(raw, symbol_name)


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
_HTML_OPEN_MARKERS: Tuple[str, ...] = ("<!doctype", "<html", "<body")


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


def _build_openai_client(env: Optional[Mapping[str, str]] = None):
    """Build the tier-1 proxy client from OPENAI_* env vars.

    Returns an ``openai.OpenAI`` (sync) client. The non-streaming call
    returns a ``ChatCompletion`` whose ``choices[0].finish_reason`` and
    ``usage`` are read by the adapter's completeness gates. Tests mock
    this client by passing a stub into :meth:`OdGenerate.execute`.

    The env var resolution is opt-in via ``env`` for testability; the
    default reads ``os.environ``. The function reads:

    - ``OPENAI_BASE_URL`` (the tier-1 proxy URL).
    - ``OPENAI_API_KEY`` (the API key).
    - ``OPENAI_MODEL`` (the model identifier; default ``vision``).

    Failures (missing keys, import errors) surface as
    ``byok_not_configured`` so the calling agent sees a typed error.
    """
    src = env if env is not None else os.environ
    base_url = src.get("OPENAI_BASE_URL")
    api_key = src.get("OPENAI_API_KEY")
    model = src.get("OPENAI_MODEL", "vision")
    if not base_url or not api_key:
        raise RuntimeError(
            "byok_not_configured: OPENAI_BASE_URL and OPENAI_API_KEY must be set "
            "(per the project's LLM config; same lane the daemon already uses)"
        )
    try:
        import openai  # noqa: PLC0415 - imported here for lazy init
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(f"openai SDK not importable: {exc}") from exc
    return openai.OpenAI(api_key=api_key, base_url=base_url), model


# ---------------------------------------------------------------------------
# Adapter surface
# ---------------------------------------------------------------------------


class OdGenerate:
    """The native generate adapter (Port: od.generate).

    Stateless; instantiated once at boot. :meth:`execute` is the public
    entry; :meth:`execute_dict` accepts a dict matching the Port's
    ``inputs_schema`` and returns the ``outputs_schema`` shape (for
    the plugin tool factory wiring).
    """

    # --- injection points for tests ---------------------------------------

    _CLIENT_FACTORY = staticmethod(_build_openai_client)
    _COMPOSER = staticmethod(_compose_system_prompt)

    @classmethod
    def execute(cls, args: GenerateInput, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        """Compose the prompt, call the LLM, gate the output. Return
        the Port's ``outputs_schema`` dict.

        The ``env`` parameter is opt-in for tests; the default reads
        ``os.environ``. ``client_factory`` and ``composer`` are
        class-level injection points for tests (see :meth:`_set_test_hooks`).
        """
        if not isinstance(args.prompt, str) or not args.prompt:
            return cls._error_envelope(
                "prompt_composition_failed",
                "'prompt' is required and must be a non-empty string",
                details=None,
                finish_reason="other",
            )

        try:
            client, model = cls._CLIENT_FACTORY(env)
        except Exception as exc:  # noqa: BLE001 - any factory failure becomes a typed envelope
            logger.warning("od.generate: client factory failed: %s", exc)
            return cls._error_envelope(
                "byok_not_configured",
                f"client factory failed: {exc}",
                details={"max_tokens": args.max_tokens},
                finish_reason="other",
            )

        system_prompt = cls._COMPOSER(args)

        # Make the LLM call (non-streaming; surface finish_reason +
        # usage). The retry discipline is the openai SDK's
        # ``request_timeout`` plus ``max_retries`` (default 2); for
        # this adapter we set ``timeout`` to the max_tokens-derived
        # wall-clock budget (the live lane's 130-170s observation is
        # the budget-shaped latency, not a timeout-shaped one).
        timeout = max(60.0, args.max_tokens / 800.0)  # ~800 tok/s budget
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": args.prompt},
                ],
                max_tokens=args.max_tokens,
                temperature=0.7,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - any upstream failure becomes a typed envelope
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
        client_factory=None,
        composer=None,
    ) -> None:
        """Inject test hooks (class-level; restore in tearDown).

        The hooks let tests stub the OpenAI client and the composer
        without monkey-patching the module globals. Tests should
        restore the defaults in tearDown via the returned restore tuple.
        """
        saved: List[Any] = []
        if client_factory is not None:
            saved.append(("client_factory", cls._CLIENT_FACTORY))
            cls._CLIENT_FACTORY = staticmethod(client_factory)
        if composer is not None:
            saved.append(("composer", cls._COMPOSER))
            cls._COMPOSER = staticmethod(composer)
        return saved