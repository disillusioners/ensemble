"""Agent-facing read tools for the shipped guideline corpus — the
``guidelines`` category (2 tools).

The guideline-read-tool lane (2026-11-13). The release payload IS the
source of truth: ``releases/<v>/guidelines/`` staged from
``docs/guidelines/``. This module is a READ-ONLY window onto that
payload — no DB copy, no cache, no write path.
**Fixed files are the authority; this tool only reads them.**
**Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md).**

Release-payload disclosure (INTENDED): the reader resolves the payload
dir at CALL time and can never serve a guideline newer than the RUNNING
release — a freshly merged doc reaches the tool only after the next
stage/promote. That lag is a design outcome, not a defect.

House rules honored here:

* Tools NEVER raise — every failure returns a typed envelope string.
* An UNEXPECTED exception is logged SERVER-SIDE and answered with a
  generic envelope (the PB-F1 leak class: ``str(exc)`` / ``traceback``
  / ``repr(exc)`` never reach the payload).
* The parser is TRIVIAL and FAIL-SOFT: a malformed or empty guideline
  file never hard-fails a listing — it degrades to the documented
  fallback values and never invents Feature/Audience fields.

Access posture — TWO layers, BOTH required
-------------------------------------------
1. The ``guidelines`` category is PRIVILEGED (entry #22 of
   ``PRIVILEGED_TOOL_CATEGORIES``), so it is opt-in-only and never
   joins the default-grant universe. Explicit ``tools.allow:
   ["guidelines"]`` entries — ari, leader, devops, sonic-leader ONLY —
   are the visibility path (the first privileged grant to a sonic
   agent: sonic-leader, ruling 2026-11-13).
2. Every tool body carries its own ``agent_id`` gate against
   ``GUIDELINES_AUTHORIZED_AGENTS`` (the metrics-read precedent) — a
   LITERAL tool name in an allow list bypasses the category gate, so
   the category alone cannot spell the audience. The gate is the FIRST
   logic in each body: a refused caller never touches the filesystem.

Payload resolution (the architect's seam, adjudication Q1 — BINDING
2026-11-13)::

    1. explicit ``base_dir`` argument (factory param, default None)
       — tests & ops injection; used verbatim when given
    2. elif sys.frozen:  Path(sys.executable).parent / "guidelines"
       → releases/<v>/guidelines/
    3. else (dev):       Path(__file__).resolve().parents[2] / "docs"
       / "guidelines" → <repo>/docs/guidelines/
    4. absent dir at CALL time → the SAME typed refusal from BOTH
       tools, naming the resolved path + reason ("release predates
       the guidelines payload"); NEVER an exception, NEVER an
       empty-list lie, NEVER a boot/registration failure

The repo path is computable ONLY inside the non-frozen branch (a
frozen binary cannot reach it) — LIVE runtime never reads repo paths,
enforced BY THE FORK SHAPE itself. NO env var, NO caching layer: the
reader hits the filesystem per call.

Parse contract (D4 — TRIVIAL + FAIL-SOFT)
------------------------------------------
Canonical ``guideline-*`` (H1 == filename stem): slug = H1; status =
the ``**Status:**`` value (line 3) cut at the FIRST `` · `` (space +
U+00B7 middle dot + space — never a pipe); title = H1 verbatim
(== slug; harmless duplication, D5); purpose = first paragraph after
``## 1. Purpose & doctrine``. Auxiliary (H1 != stem — README,
TEMPLATE): slug = filename stem; status = rest-of-line after the
FIRST ``**Status:**`` occurrence (the README status wraps L3-5 —
rest-of-line ONLY); title = H1 verbatim; purpose = first paragraph
under the first ``## 1.`` heading, else ``(no purpose section)``.
A file with no ``**Status:**`` line degrades to ``(no status)``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Guidelines"

CATEGORY_DOC = """\
Read-only window onto the shipped guideline corpus (the release payload).

Tools in this category:
- ``guidelines_list`` — parsed summary (slug / title / status / purpose) per shipped guideline
- ``guideline_read`` — full VERBATIM content of one guideline by slug

The doctrine: Fixed files are the authority; this tool only reads them.
Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md).

The tools read THE RELEASE PAYLOAD only — they cannot serve any
guideline newer than the running release (INTENDED). Pure read surface:
no writes, no cache, no database.
"""

#: The category key — EXACTLY ``guidelines`` (D2: single-word exact
#: family word; never a variant like ``guideline`` or ``docs-guidelines``).
GUIDELINES_CATEGORY_KEY = "guidelines"

#: The two tool names this module constructs — the canonical list the
#: registry's ``KNOWN_TOOL_NAMES`` / ``DYNAMIC_TOOL_NAMES`` and the
#: registration pin pack agree with.
GUIDELINES_TOOLS = ("guidelines_list", "guideline_read")

#: The agents allowed to read the shipped guideline corpus.  The privileged
#: category gates VISIBILITY; this set gates AUTHORITY.  Both are
#: required.
GUIDELINES_AUTHORIZED_AGENTS: frozenset[str] = frozenset(
    {"ari", "leader", "devops", "sonic-leader"}
)

# ── the refusal tokens (the in-process mirror of the door's status
#    semantics: 401 / 403 / 503 / 404 / 500) ──────────────────────────
TOKEN_UNKNOWN_AGENT = "unknown-agent"
TOKEN_NOT_AUTHORIZED = "not-authorized"
TOKEN_PAYLOAD_UNAVAILABLE = "guidelines-payload-unavailable"
TOKEN_UNKNOWN_SLUG = "unknown-slug"
TOKEN_INTERNAL_ERROR = "internal-error"

#: The fail-soft fallback when a file carries no ``## 1.`` section.
NO_PURPOSE_FALLBACK = "(no purpose section)"

#: The fail-soft fallback when a file carries no ``**Status:**`` line.
NO_STATUS_FALLBACK = "(no status)"


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope.

    ``Error: {label} REFUSED — reason={token}: {message}`` — the same
    shape the other privileged categories use, so one parser reads them
    all.
    """
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _is_guidelines_authorized(agent_id: str) -> bool:
    """Is this agent allowed to read the shipped guideline corpus?

    ONE shared helper, called by BOTH tool bodies — the set lives in
    exactly one place and the bodies share it (the ``metrics_read``
    precedent).

    Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER authorized.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in GUIDELINES_AUTHORIZED_AGENTS


def _resolve_guidelines_dir(base_dir: Path | None) -> Path:
    """Resolve the guidelines payload directory (adjudication Q1).

    1. explicit ``base_dir`` — used verbatim (tests & ops injection)
    2. frozen — ``Path(sys.executable).parent / "guidelines"`` (the
       release root; ``releases/<v>/guidelines/``)
    3. dev — the repo's ``docs/guidelines`` via the SAME fork's
       non-frozen branch (the repo path is computable ONLY here — a
       frozen binary can never reach it)
    """
    if base_dir is not None:
        return Path(base_dir)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "guidelines"
    # Dev branch ONLY (Q3 — never an unconditional fallback): this
    # module sits at <repo>/daemon/tools/guidelines.py, so parents[2]
    # is the repo root (the frozen-guarded house shape,
    # agent_mother.py:141-143 at the same depth).
    return Path(__file__).resolve().parents[2] / "docs" / "guidelines"


def _payload_refusal(label: str, directory: Path) -> str:
    """The typed payload-unavailable envelope (adjudication Q1(d)).

    The SAME refusal from BOTH tools — an empty list would assert "no
    guidelines exist" when the truth is "payload not present in this
    release"; dishonest and undiagnosable, so it is never returned.
    """
    return _refusal(
        label,
        TOKEN_PAYLOAD_UNAVAILABLE,
        f"no readable guidelines payload at {directory} — the running "
        "release predates the guidelines payload (or the directory is "
        "absent/empty); this tool reads the release payload only and "
        "cannot serve anything newer than the running release",
    )


def _h1_of(lines: list[str]) -> str | None:
    """The verbatim H1 text of line 1, or None when line 1 is no H1."""
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip()
    return None


def _first_status_line(lines: list[str]) -> int | None:
    """Index of the FIRST line containing ``**Status:**``."""
    for idx, line in enumerate(lines):
        if "**Status:**" in line:
            return idx
    return None


def _status_auxiliary(lines: list[str]) -> str:
    """Auxiliary status: rest-of-line after the FIRST ``**Status:**``."""
    idx = _first_status_line(lines)
    if idx is None:
        return NO_STATUS_FALLBACK
    return lines[idx].split("**Status:**", 1)[1].strip()


def _status_canonical(lines: list[str]) -> str:
    """Canonical status: the line-3 value cut at the FIRST `` · ``.

    Fail-soft: when line 3 (index 2) carries no ``**Status:**`` marker,
    fall back to the first line that does (D4's L3 contract with a
    never-raise guard).
    """
    idx = 2 if len(lines) > 2 and "**Status:**" in lines[2] else None
    if idx is None:
        idx = _first_status_line(lines)
    if idx is None:
        return NO_STATUS_FALLBACK
    value = lines[idx].split("**Status:**", 1)[1]
    return value.split(" · ", 1)[0].strip()


def _first_paragraph_after(lines: list[str], start: int) -> str | None:
    """The first paragraph after line ``start`` — the block of
    consecutive non-blank lines beginning at the first non-blank,
    non-heading line; joined with single spaces."""
    idx = start + 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines) or lines[idx].lstrip().startswith("#"):
        return None
    block: list[str] = []
    while idx < len(lines) and lines[idx].strip():
        block.append(lines[idx].strip())
        idx += 1
    return " ".join(block)


def _purpose_canonical(lines: list[str]) -> str:
    """First paragraph after ``## 1. Purpose & doctrine`` (D4)."""
    for idx, line in enumerate(lines):
        if line.strip() == "## 1. Purpose & doctrine":
            paragraph = _first_paragraph_after(lines, idx)
            return paragraph if paragraph is not None else NO_PURPOSE_FALLBACK
    return NO_PURPOSE_FALLBACK


def _purpose_auxiliary(lines: list[str]) -> str:
    """First paragraph under the FIRST ``## 1.`` heading (D4)."""
    for idx, line in enumerate(lines):
        if line.strip().startswith("## 1."):
            paragraph = _first_paragraph_after(lines, idx)
            return paragraph if paragraph is not None else NO_PURPOSE_FALLBACK
    return NO_PURPOSE_FALLBACK


def parse_guideline(text: str, stem: str) -> dict[str, str]:
    """Parse one guideline file's summary fields — TRIVIAL, FAIL-SOFT.

    Canonical branch (H1 == filename stem): slug = H1, status cut at
    the first `` · ``, purpose from ``## 1. Purpose & doctrine``.
    Auxiliary branch (README / TEMPLATE / any H1 != stem): slug =
    stem, rest-of-line status, purpose from the first ``## 1.``
    heading. NEVER raises; never invents Feature/Audience fields.
    """
    lines = text.splitlines()
    h1 = _h1_of(lines)
    canonical = h1 is not None and h1 == stem
    if canonical:
        return {
            "slug": h1,
            "title": h1,
            "status": _status_canonical(lines),
            "purpose": _purpose_canonical(lines),
        }
    return {
        "slug": stem,
        "title": h1 if h1 is not None else stem,
        "status": _status_auxiliary(lines),
        "purpose": _purpose_auxiliary(lines),
    }


def _list_guideline_files(directory: Path) -> list[Path]:
    """Every ``*.md`` file in the payload dir, sorted by name (may be
    empty — the caller turns that into the typed refusal)."""
    if not directory.is_dir():
        return []
    try:
        return sorted(directory.glob("*.md"))
    except OSError:
        return []


def create_guidelines_tools(
    manager,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
    base_dir: Path | None = None,
) -> list:
    """Create the two read-only guidelines tools for one instance/agent.

    Args:
        manager: The instance manager (signature parity with the
            sibling ``create_*_tools`` factories — the reader needs NO
            manager handle: the payload is a filesystem directory, so
            there is no service-presence wiring check. The factory
            ALWAYS constructs both tools; the payload dir is resolved
            and checked at CALL time (notify-user precedent).
        current_instance_id: Caller's instance id (signature parity).
        agent_id: Caller's agent_id — captured into the layer-3 body
            gate (``GUIDELINES_AUTHORIZED_AGENTS``).
        agent_tag: Caller's version tag (signature parity; unused).
        base_dir: Explicit payload-dir override (adjudication Q1 step
            1 — tests & ops injection). Default None = the frozen/dev
            fork.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""
    resolved_dir = _resolve_guidelines_dir(base_dir)

    class GuidelinesReadInput(BaseModel):
        """Input schema for guideline_read."""

        slug: Annotated[
            str,
            Field(
                description=(
                    "The guideline slug (the filename stem, e.g. "
                    "'guideline-notify-user-bounds'). Use "
                    "guidelines_list first to see every valid slug."
                )
            ),
        ]

    @register_tool_category(GUIDELINES_CATEGORY_KEY)
    @tool
    async def guidelines_list() -> str:
        """List the shipped guidelines with parsed summaries. Use tool_help("guidelines_list") for details."""
        # ── the layer-3 authority gate: FIRST logic in the body — a
        # refused caller never touches the filesystem (metrics ladder).
        if not _is_guidelines_authorized(caller_agent_id):
            if not caller_agent_id:
                return _refusal(
                    "guidelines_list",
                    TOKEN_UNKNOWN_AGENT,
                    "the shipped guideline corpus is readable by ari, "
                    "leader, devops and sonic-leader only",
                )
            return _refusal(
                "guidelines_list",
                TOKEN_NOT_AUTHORIZED,
                "the shipped guideline corpus is readable by ari, "
                "leader, devops and sonic-leader only",
            )
        try:
            files = _list_guideline_files(resolved_dir)
            if not files:
                return _payload_refusal("guidelines_list", resolved_dir)
            entries = []
            for path in files:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    entry = {
                        "slug": path.stem,
                        "title": path.stem,
                        "status": NO_STATUS_FALLBACK,
                        "purpose": NO_PURPOSE_FALLBACK,
                    }
                else:
                    entry = parse_guideline(text, path.stem)
                entries.append(entry)
            entries.sort(key=lambda e: e["slug"])
            return json.dumps({"guidelines": entries}, indent=2)
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("guidelines_list failed")
            return _refusal(
                "guidelines_list",
                TOKEN_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); "
                "retry later",
            )

    @register_tool_category(GUIDELINES_CATEGORY_KEY)
    @tool(args_schema=GuidelinesReadInput)
    async def guideline_read(slug: str) -> str:
        """Read one shipped guideline's full verbatim content. Use tool_help("guideline_read") for details."""
        # ── the layer-3 authority gate: FIRST logic in the body — a
        # refused caller never touches the filesystem (metrics ladder).
        if not _is_guidelines_authorized(caller_agent_id):
            if not caller_agent_id:
                return _refusal(
                    "guideline_read",
                    TOKEN_UNKNOWN_AGENT,
                    "the shipped guideline corpus is readable by ari, "
                    "leader, devops and sonic-leader only",
                )
            return _refusal(
                "guideline_read",
                TOKEN_NOT_AUTHORIZED,
                "the shipped guideline corpus is readable by ari, "
                "leader, devops and sonic-leader only",
            )
        try:
            files = _list_guideline_files(resolved_dir)
            if not files:
                return _payload_refusal("guideline_read", resolved_dir)
            valid_slugs = sorted(path.stem for path in files)
            if slug not in valid_slugs:
                return _refusal(
                    "guideline_read",
                    TOKEN_UNKNOWN_SLUG,
                    f"no guideline named {slug!r} in the release "
                    f"payload; valid slugs: {', '.join(valid_slugs)}",
                )
            target = resolved_dir / f"{slug}.md"
            try:
                return target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return _refusal(
                    "guideline_read",
                    TOKEN_INTERNAL_ERROR,
                    f"the guideline file {target} exists but could not "
                    f"be read ({exc.__class__.__name__}); retry later",
                )
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("guideline_read failed")
            return _refusal(
                "guideline_read",
                TOKEN_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); "
                "retry later",
            )

    guidelines_list._full_doc_ = _build_full_doc("guidelines_list")
    guideline_read._full_doc_ = _build_full_doc("guideline_read")

    return [guidelines_list, guideline_read]


def _build_full_doc(label: str) -> str:
    """The tool's full documentation (tool_help surface).

    Carries BOTH doctrine lines VERBATIM and the release-payload
    disclosure in the SAME block.
    """
    return (
        f"{label} — a read-only window onto the guideline corpus that "
        "shipped with the RUNNING release (staged from docs/guidelines/ "
        "into releases/<v>/guidelines/).\n\n"
        "Doctrine\n"
        "--------\n"
        "Fixed files are the authority; this tool only reads them. "
        "Every feature shipping dormant / config-armed / ceremony-gated "
        "/ with silently-biting rules OWES a guideline (see "
        "docs/guidelines/TEMPLATE.md).\n\n"
        "Release-payload disclosure (INTENDED): the tools read THE "
        "RELEASE PAYLOAD only — they cannot serve any guideline newer "
        "than the running release. A freshly merged doc reaches these "
        "tools only after the next stage/promote; that lag is a design "
        "outcome, not a defect.\n\n"
        "Access\n"
        "------\n"
        "Readable by ari, leader, devops and sonic-leader ONLY "
        "(privileged category #22 + the in-body agent gate — both "
        "required). The gate runs BEFORE any file access.\n\n"
        "Parsing (guidelines_list)\n"
        "-------------------------\n"
        "Per shipped guideline: slug, title, status, one-line purpose. "
        "Canonical guideline-* files parse their Status line (cut at "
        "the first middle-dot separator) and their '## 1. Purpose & "
        "doctrine' section; auxiliary files (README, TEMPLATE) fall "
        "back to rest-of-line status and the first '## 1.' paragraph. "
        "The parser NEVER fails a listing — malformed files degrade to "
        "the documented fallback values.\n\n"
        "Reading (guideline_read)\n"
        "------------------------\n"
        "Returns the guideline file's full VERBATIM content — byte-"
        "faithful text, no truncation, no reformatting. An unknown "
        "slug is refused with the list of all valid slugs.\n\n"
        "No write path exists in this category: the tools never write, "
        "never cache, never touch the database — a window onto stone."
    )
