"""Turn-3 brief composer (Port: od.compose_brief).

Pure formatter — assembles ``[form answers — discovery]`` + ``[brand spec]`` +
``[page brief]`` sections into the prompt shape the model recognizes on turn 3
(causes it to skip re-asking discovery questions).

**Attribution.** Ported from ``open-design-mcp@0.16.1`` (Apache-2.0,
verified 2026-10-06; OQ4 disposition in
``.agents/shared/planning/plugin-subsystem/2026-10-06-slice-5-probes.md``).
The upstream function lives at
``/home/nea/services/opendesign/node_modules/open-design-mcp/dist/src/tools/compose-brief.js``
as ``composeBrief(args)`` (96 lines). This Python module is a faithful
mechanical translation with section ordering preserved verbatim. Per
Apache-2.0 §4(d), attribution + a statement of changes are kept on this
module's docstring (above) and on the source file comment that ships with
the own_outright/ tree at slice-⑤.

**Mode-P invariant.** No network, no env vars, no LLM calls. The
:class:`OdComposeBrief.compose` method is a deterministic pure function —
same inputs ⇒ same output. The plugin_tool_factory.py can therefore cache
per-call results without invalidation concerns.

**Schema surface (Port's ``inputs_schema`` / ``outputs_schema``).**
Mirrored exactly from the upstream Zod schema:

- Input: ``page_prompt`` (required), ``brief_answers`` (optional),
  ``brand_spec`` (optional), ``sibling_artifact_slugs`` (optional;
  reserved for cross-page consistency, currently ignored).
- Output: ``{prompt: string}`` — the assembled Turn-3 prompt.

**Empty-section rule.** Empty sections (undefined / empty) are omitted
entirely (no ``[]`` markers, no blank placeholder lines). The
``:func:`has_non_empty_brief_answers`` helper handles the ``brief_answers``
case where any of seven fields may be present.

**String-array join rule.** ``platform`` and ``tone`` arrays join with
``", "`` (comma-space), preserving the upstream exact spacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

__all__ = ["OdComposeBrief", "BriefAnswers", "ComposeBriefInput"]


# ---- Typed input shapes (mirror the upstream Zod schema; CON §3 JSON-friendly) --


@dataclass(frozen=True)
class BriefAnswers:
    """The seven optional fields of the brief_answers input."""

    output: Optional[str] = None
    platform: Optional[Tuple[str, ...]] = None
    audience: Optional[str] = None
    tone: Optional[Tuple[str, ...]] = None
    brand: Optional[str] = None  # pick_direction | brand_spec | reference_match
    scale: Optional[str] = None
    constraints: Optional[str] = None


@dataclass(frozen=True)
class ComposeBriefInput:
    """Inputs to :meth:`OdComposeBrief.compose`.

    Mirrors the Port's ``inputs_schema`` (od.compose_brief) — every
    field is JSON-serializable.
    """

    page_prompt: str
    brief_answers: Optional[BriefAnswers] = None
    brand_spec: Optional[str] = None
    sibling_artifact_slugs: Optional[Tuple[str, ...]] = None  # reserved


class OdComposeBrief:
    """The Turn-3 brief composer (Port: od.compose_brief).

    Stateless; instantiated once at boot. :meth:`compose` is the public
    entry; :meth:`compose_dict` accepts the raw Port dataclass (the
    typical adapter tool-factory wiring) and dispatches to
    :meth:`compose`.
    """

    @staticmethod
    def has_non_empty_brief_answers(answers: Mapping[str, Any]) -> bool:
        """True iff any of the seven brief_answers fields is non-empty.

        Mirrors ``hasNonEmptyBriefAnswers`` in the upstream JS (lines
        67-83 of compose-brief.js). Used to suppress the
        ``[form answers — discovery]`` section when every field is
        absent / empty.
        """
        if answers.get("output"):
            return True
        platform = answers.get("platform")
        if platform and len(platform) > 0:
            return True
        if answers.get("audience"):
            return True
        tone = answers.get("tone")
        if tone and len(tone) > 0:
            return True
        if answers.get("brand"):
            return True
        if answers.get("scale"):
            return True
        if answers.get("constraints"):
            return True
        return False

    @classmethod
    def compose(cls, args: ComposeBriefInput) -> str:
        """Assemble the Turn-3 prompt.

        Section order: ``[form answers — discovery]`` → ``[brand spec]`` →
        ``[page brief]``. Empty sections are omitted entirely.
        """
        sections: list[str] = []

        # [form answers — discovery] section
        if args.brief_answers is not None and cls.has_non_empty_brief_answers(
            args.brief_answers.__dict__
        ):
            form_lines = ["[form answers — discovery]"]
            ba = args.brief_answers
            if ba.output:
                form_lines.append(f"- output: {ba.output}")
            if ba.platform and len(ba.platform) > 0:
                form_lines.append(f"- platform: {', '.join(ba.platform)}")
            if ba.audience:
                form_lines.append(f"- audience: {ba.audience}")
            if ba.tone and len(ba.tone) > 0:
                form_lines.append(f"- tone: {', '.join(ba.tone)}")
            if ba.brand:
                form_lines.append(f"- brand: {ba.brand}")
            if ba.scale:
                form_lines.append(f"- scale: {ba.scale}")
            if ba.constraints:
                form_lines.append(f"- constraints: {ba.constraints}")
            sections.append("\n".join(form_lines))

        # [brand spec] section
        if args.brand_spec and args.brand_spec.strip():
            sections.append(f"[brand spec]\n{args.brand_spec}")

        # [page brief] section — ALWAYS rendered; page_prompt is required.
        sections.append(f"[page brief]\n{args.page_prompt}")

        # Join sections with exactly one blank line between them (the
        # upstream uses ``"\n\n"`` between sections, producing a single
        # blank line in the rendered output).
        return "\n\n".join(sections)

    @classmethod
    def compose_dict(cls, raw: Mapping[str, Any]) -> Dict[str, str]:
        """Adapter-friendly entrypoint: accept a dict that matches the
        Port's ``inputs_schema`` and return the Port's ``outputs_schema``.

        Used by the plugin tool factory; the dict shape is JSON
        serializable by construction (CON §3).
        """
        page_prompt = raw.get("page_prompt")
        if not isinstance(page_prompt, str) or not page_prompt:
            raise ValueError("compose_brief: 'page_prompt' is required and must be a non-empty string")
        raw_ba = raw.get("brief_answers")
        brief_answers: Optional[BriefAnswers] = None
        if raw_ba is not None:
            if not isinstance(raw_ba, Mapping):
                raise ValueError("compose_brief: 'brief_answers' must be an object when present")
            brief_answers = BriefAnswers(
                output=raw_ba.get("output"),
                platform=tuple(raw_ba["platform"]) if raw_ba.get("platform") else None,
                audience=raw_ba.get("audience"),
                tone=tuple(raw_ba["tone"]) if raw_ba.get("tone") else None,
                brand=raw_ba.get("brand"),
                scale=raw_ba.get("scale"),
                constraints=raw_ba.get("constraints"),
            )
        brand_spec = raw.get("brand_spec")
        sibling_slugs = raw.get("sibling_artifact_slugs")
        result = cls.compose(
            ComposeBriefInput(
                page_prompt=page_prompt,
                brief_answers=brief_answers,
                brand_spec=brand_spec if isinstance(brand_spec, str) else None,
                sibling_artifact_slugs=tuple(sibling_slugs) if sibling_slugs else None,
            )
        )
        return {"prompt": result}