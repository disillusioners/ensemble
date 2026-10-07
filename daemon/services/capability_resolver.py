"""Capability resolver for skill ``requires:`` front-matter + bootstrap pre-flight.

Owns the day-1 surface for the designer-agent bootstrap flow (Phase 3 of
the implementation plan, worktree-locked branch ``feature/designer-agent-design``).
The module is the single home for:

* **WP1** — :class:`CapabilityRequirement` dataclass + :func:`parse_requires_block`
  parser. Reads ``requires:`` off a ``skill-set.yaml`` skill entry; unknown
  keys under ``requires:`` log a warning (D6 philosophy — only
  ``pinned_spec_sha`` is hard; everything else advisory). Malformed YAML
  raises with a line number. The dataclass round-trips through JSON.

* **WP2** — :func:`capability_check` synchronous tri-state pre-flight
  (``present`` / ``missing`` / ``unconfigured``), <50ms p95, no LLM /
  subprocess calls. Plus :func:`enforce_mandatory_first_instruction` which
  the skill-seeding path invokes: a skill body declaring ``requires:``
  whose body does NOT begin with ``capability_check(...)`` is rejected
  with a typed error naming the skill + front-matter section.

* **WP3** — :class:`EscalationEnvelope` Pydantic model + :func:`format_envelope_message`
  / :func:`parse_envelope_from_message` (``Result:``-prefixed JSON
  convention). Day-1 firing kinds = ``capability_missing``,
  ``installed_but_unconfigured``; ``policy_denied`` is schema-accepted but
  :func:`assert_no_policy_denied_in_day1_paths` lint-enforces "never
  emitted from day-1 code".

* **WP4** — :func:`load_capabilities_registry` + :class:`CapabilityRegistryEntry`
  for the per-MCP registry co-located with the ``dynamic-skill`` innate
  skill (``agents/_prompt_system/innate-skills/dynamic-skill/capabilities.yaml``).

The module DOES NOT touch KMS primitives, secret material, or the
``mcp_servers.config`` row directly. ``capability_check`` is a thin
synchronous read; the actual install / mint work lives elsewhere
(WP5–WP9 of the plan — separate commissions).

Design notes
------------

* **Inject everything.** ``capability_check`` accepts ``mcp_lookup``,
  ``tools_allow``, ``env_lookup`` callables so the test surface can
  supply in-memory fakes. Production callers wire these to live
  repositories once, at instance-spawn time.
* **Lazy imports.** Cross-referencing ``builtin_servers`` registry and
  the MCP server repository happens lazily inside the relevant
  function; the module itself imports cleanly without DB / MCP infra
  available.
* **No async.** Every public helper in this module is synchronous and
  pure — fast, deterministic, trivially testable. No thread-pooling,
  no LLM round-trips.
* **Robust to absent data.** A missing ``mcp_servers`` row, a missing
  env var, a not-yet-installed builtin — all map to ``missing`` /
  ``unconfigured`` with detection_evidence to drive a future envelope
  without re-running the whole check.

Mandatory-first-instruction rule
--------------------------------

A skill body whose entry declares ``requires:`` MUST begin with a
``capability_check(...)`` call as its first non-blank line. The check
is enforced at seed-load time and at injection-time. The
instruction-reference form is::

    capability_check("opendesign")  # or any matching capability_id

(case-sensitive, snake_case name, optional trailing comment / call
arguments). Any other first line (heading, prose, code fence) raises
:class:`MandatoryFirstInstructionError` naming the skill and the
``requires`` front-matter section.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# WP1 — requires: front-matter parser + CapabilityRequirement dataclass
# ─────────────────────────────────────────────────────────────────────

# Recognized keys under ``requires:``; unknown keys lint-warn.
_REQUIRED_KNOWN_KEYS: frozenset[str] = frozenset({"mcp", "tools", "env"})


@dataclass
class CapabilityRequirement:
    """Parsed ``requires:`` block on a ``skill-set.yaml`` skill entry.

    The dataclass carries only the recognized categories from the plan:
    ``mcp``, ``tools``, ``env``. Unknown keys (e.g. ``docker:``) are
    accepted as advisory and preserved on :attr:`unknown_keys` so
    round-trip serialization is lossless.

    Attributes:
        mcp: Capability IDs of MCP servers the skill needs (``opendesign``).
        tools: Tool names the skill needs in ``tools.allow``
            (``bash``, ``instance``, etc.).
        env: Environment variable names the skill requires set
            (``OPEN_DESIGN_LICENSE``).
        unknown_keys: Mapping of unrecognized keys to their original raw
            values — captured for lint transparency, NOT validated.
        line_number: 1-indexed source line where the ``requires:`` block
            starts in the parsed YAML. ``None`` when synthesized (e.g.
            from a programmatic dict, not a parsed file).
    """

    mcp: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    unknown_keys: dict[str, Any] = field(default_factory=dict)
    line_number: int | None = None

    def is_empty(self) -> bool:
        """True iff every recognized category is empty AND there are no unknown keys."""
        return not (self.mcp or self.tools or self.env or self.unknown_keys)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict. Round-trips through :func:`to_requirement`."""
        out: dict[str, Any] = {}
        if self.mcp:
            out["mcp"] = list(self.mcp)
        if self.tools:
            out["tools"] = list(self.tools)
        if self.env:
            out["env"] = list(self.env)
        for k, v in self.unknown_keys.items():
            out.setdefault(k, v)
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityRequirement":
        """Inverse of :meth:`to_dict` for round-trip testing."""
        if not isinstance(data, dict):
            raise TypeError(
                f"CapabilityRequirement.from_dict expects dict, got "
                f"{type(data).__name__}"
            )
        known = {k: data[k] for k in data if k in _REQUIRED_KNOWN_KEYS}
        unknown = {k: v for k, v in data.items() if k not in _REQUIRED_KNOWN_KEYS}
        return cls(
            mcp=list(known.get("mcp", []) or []),
            tools=list(known.get("tools", []) or []),
            env=list(known.get("env", []) or []),
            unknown_keys=unknown,
        )


_REQUIREMENT_VALUE_NOT_A_LIST_MSG = (
    "requires.{key} must be a list of strings, got {actual}"
)


def _coerce_str_list(
    raw: Any, *, key_name: str, source: str
) -> tuple[list[str], list[str]]:
    """Normalize a requires-value to ``list[str]``. Returns (clean_values, warnings)."""
    warnings: list[str] = []
    if raw is None:
        return [], warnings
    if not isinstance(raw, list):
        warnings.append(
            _REQUIREMENT_VALUE_NOT_A_LIST_MSG.format(
                key=key_name, actual=type(raw).__name__
            )
        )
        return [], warnings
    cleaned: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            warnings.append(
                f"requires.{key_name}[{i}] must be a string, got "
                f"{type(item).__name__}; skipping entry"
            )
            continue
        if not item.strip():
            warnings.append(
                f"requires.{key_name}[{i}] is empty; skipping entry"
            )
            continue
        cleaned.append(item)
    return cleaned, warnings


def parse_requires_block(
    raw: Any, *, source: str = "<inline>"
) -> CapabilityRequirement:
    """Parse a ``requires:`` block (raw Python value) into a :class:`CapabilityRequirement`.

    Args:
        raw: The raw value as parsed by ``yaml.safe_load``. Typically a
            ``dict``; ``None`` when the key is absent.
        source: Human-readable origin (file path or ``"<inline>"``) for
            warning / error messages.

    Returns:
        A populated :class:`CapabilityRequirement`. Missing/empty → all
        three lists empty (back-compat with all existing skill entries).

    Raises:
        ValueError: When ``raw`` is a non-dict truthy value (e.g. a list
            or scalar at the ``requires:`` position). The error message
            includes the source identifier.

    Lint policy (per plan §3 P3-WP1):
        * Unknown keys (e.g. ``docker:``) → ``logger.warning``, captured
          in :attr:`unknown_keys`; DO NOT hard-fail (D6 philosophy).
        * Wrong-type values inside a known key (e.g. ``mcp: 5`` instead
          of ``mcp: [...]``) → ``logger.warning``; lists default to
          empty for that key.
        * Non-dict at the ``requires:`` slot itself → ``ValueError``
          (malformed; line number surfaced via ``source`` string).
    """
    if raw is None:
        return CapabilityRequirement()
    if not isinstance(raw, dict):
        raise ValueError(
            f"requires: in {source} must be a mapping, got "
            f"{type(raw).__name__}"
        )

    mcp, w = _coerce_str_list(raw.get("mcp"), key_name="mcp", source=source)
    for msg in w:
        logger.warning(f"{source}: {msg}")
    tools, w = _coerce_str_list(raw.get("tools"), key_name="tools", source=source)
    for msg in w:
        logger.warning(f"{source}: {msg}")
    env, w = _coerce_str_list(raw.get("env"), key_name="env", source=source)
    for msg in w:
        logger.warning(f"{source}: {msg}")

    unknown = {
        k: v
        for k, v in raw.items()
        if k not in _REQUIRED_KNOWN_KEYS
    }
    if unknown:
        logger.warning(
            f"{source}: requires block has unknown keys {sorted(unknown)} "
            f"— accepted as advisory, will NOT enforce"
        )

    line_number = getattr(raw, "start_mark", None)
    line = line_number.line + 1 if line_number is not None and hasattr(line_number, "line") else None

    return CapabilityRequirement(
        mcp=mcp,
        tools=tools,
        env=env,
        unknown_keys=unknown,
        line_number=line if isinstance(line, int) else None,
    )


def extend_skill_entry_with_requires(
    raw_entry: dict[str, Any], *, source: str
) -> CapabilityRequirement | None:
    """Convenience wrapper to call from the skill-seed parser.

    Returns ``None`` when ``requires:`` is absent; returns a
    populated :class:`CapabilityRequirement` (possibly empty /
    unknown-only) when the key exists with ``None`` value. Mirrors
    the back-compat "missing key → default no requires" contract.
    """
    if "requires" not in raw_entry:
        return None
    return parse_requires_block(raw_entry.get("requires"), source=source)


# ─────────────────────────────────────────────────────────────────────
# WP2 — capability_check pre-flight + mandatory-first-instruction rule
# ─────────────────────────────────────────────────────────────────────

CapabilityTriState = Literal["present", "missing", "unconfigured"]


@dataclass
class CapabilityCheckResult:
    """Outcome of a single :func:`capability_check` invocation.

    Attributes:
        state: ``present`` / ``missing`` / ``unconfigured``.
        capability_id: The capability being checked (e.g. ``opendesign``).
        capability_kind: Which requirement category produced this check
            (``mcp`` / ``tools`` / ``env``). ``None`` when the check
            ran with no specific category target (e.g. an aggregate).
        detection_evidence: Human-readable diagnostic suitable for the
            ``detection_evidence`` field on the escalation envelope.
            Examples: ``"pre_flight: mcp_servers.query(name=opendesign)
            -> None"`` or ``"env: OPEN_DESIGN_LICENSE not set"``.
        missing_subkeys: When ``state == unconfigured`` and the
            underlying data carries a structured config, the inner
            keys whose absence caused the unconfigured verdict (e.g.
            ``["OPEN_DESIGN_API_KEY"]``). Empty otherwise.
    """

    state: CapabilityTriState
    capability_id: str
    capability_kind: Literal["mcp", "tools", "env"] | None = None
    detection_evidence: str = ""
    missing_subkeys: list[str] = field(default_factory=list)


# Type aliases for the injectable lookup callables.
McpLookupFn = Callable[[str], "McpLookupResult | None"]
# F1-fix (F1a wire completion, 2026-10-02): the production closure
# resolves the receiving instance's agent_id via ``_instance_repository``
# (so the manager-side ``_tools_allow`` can read the real ``meta.tools.allow``).
# The callable signature is ``Callable[[str | None], list[str]]`` —
# the parameter is the instance_id (None when no instance context is
# available, e.g. test fixtures). Tests that don't care about the
# instance context should accept the kwarg and ignore it.
ToolsAllowFn = Callable[[str | None], list[str]]
EnvLookupFn = Callable[[], dict[str, str]]


@dataclass
class McpLookupResult:
    """Minimal projection of an ``mcp_servers`` row consumed by :func:`capability_check`.

    The resolver never touches ``McpServer`` SQLModel directly — callers
    project the row into this dataclass so the test surface can stay
    in-memory.
    """

    name: str
    is_active: bool
    config_env: dict[str, str] = field(default_factory=dict)
    requires_secret: bool = False
    bound_handle: str | None = None

    @property
    def is_live(self) -> bool:
        return self.is_active


def check_mcp_capability(
    capability_id: str, *, mcp_lookup: McpLookupFn | None = None
) -> CapabilityCheckResult:
    """Check a single MCP capability.

    ``present`` iff:
      * ``mcp_lookup`` returns a row with ``is_active=True``
      * the row's ``config_env`` exposes the keys the capability declares it needs.
    ``unconfigured`` if the row exists but key set is empty OR
      ``requires_secret=True`` AND ``bound_handle is None``.
    ``missing`` if no row exists.

    The resolver's known-keys check is lazy — production callers pass
    their own ``expected_env_keys`` via the row's ``config_env`` keys.
    Day-1 contract: presence of any config_env key is "configured";
    the WP4 registry carries the canonical required-keys list (deferred
    to the wire in WP5+ — day-1 tri-state OK).
    """
    evidence_base = f"pre_flight: mcp_servers.query(name={capability_id})"
    if mcp_lookup is None:
        return CapabilityCheckResult(
            state="missing",
            capability_id=capability_id,
            capability_kind="mcp",
            detection_evidence=f"{evidence_base} -> <no mcp_lookup injected>",
        )
    row = mcp_lookup(capability_id)
    if row is None:
        return CapabilityCheckResult(
            state="missing",
            capability_id=capability_id,
            capability_kind="mcp",
            detection_evidence=f"{evidence_base} -> None",
        )
    if not row.is_live:
        return CapabilityCheckResult(
            state="unconfigured",
            capability_id=capability_id,
            capability_kind="mcp",
            detection_evidence=(
                f"{evidence_base} -> row present, is_active=False"
            ),
            missing_subkeys=["is_active"],
        )
    if row.requires_secret and not row.bound_handle:
        return CapabilityCheckResult(
            state="unconfigured",
            capability_id=capability_id,
            capability_kind="mcp",
            detection_evidence=(
                f"{evidence_base} -> row present, requires_secret=True, "
                f"no bound handle"
            ),
            missing_subkeys=["__KMS_REF__<bound_handle>__"],
        )
    if not row.config_env:
        return CapabilityCheckResult(
            state="unconfigured",
            capability_id=capability_id,
            capability_kind="mcp",
            detection_evidence=(
                f"{evidence_base} -> row present, config.env is empty"
            ),
            missing_subkeys=["config.env"],
        )
    return CapabilityCheckResult(
        state="present",
        capability_id=capability_id,
        capability_kind="mcp",
        detection_evidence=f"{evidence_base} -> present",
    )


def check_tool_capability(
    capability_id: str,
    *,
    tools_allow: ToolsAllowFn | None = None,
    instance_id: str | None = None,
) -> CapabilityCheckResult:
    """Check that ``capability_id`` (a tool name) is in ``tools.allow``.

    Empty-allow semantics (F1 fix, 2026-10-02): an empty ``tools.allow``
    list is treated as the **inherit/default universe** (no restriction),
    matching :func:`daemon.tools.instance.resolve_tool_filter` which
    returns ``None`` (= "all tools allowed") when allow AND deny are both
    empty (instance.py:322-327). Day-1 defect F1 had this gate treating
    empty allow as deny-all, which blocked ``load_skill`` from running
    against worker instances whose pre-flight snapshot of ``tools.allow``
    was ``[]`` (the actual allow list lives on ``meta.tools.allow`` and
    the resolver's accessor was reading a non-existent attribute — see
    the manager-side wiring fix that ships with this change).

    Args:
        capability_id: The tool name to check.
        tools_allow: Closure resolving the active instance's
            ``tools.allow`` list. Receives ``instance_id`` so it can
            look up the right agent meta via ``get_registry`` — see
            manager.py:_wire_skill_injection_capability_gate.
        instance_id: Optional receiving instance id forwarded to
            ``tools_allow``. ``None`` when no instance is in scope
            (test fixtures).
    """
    evidence_base = f"pre_flight: tools.allow.contains({capability_id!r})"
    if tools_allow is None:
        return CapabilityCheckResult(
            state="missing",
            capability_id=capability_id,
            capability_kind="tools",
            detection_evidence=f"{evidence_base} -> <no tools_allow injected>",
        )
    allowed = tools_allow(instance_id)
    # Empty allow = inherit/default universe (matches resolve_tool_filter
    # empty-allow + empty-deny branch → "all tools allowed"). Cannot
    # legitimately return "missing" — there is no restriction declared
    # to be missing from.
    if not allowed:
        return CapabilityCheckResult(
            state="present",
            capability_id=capability_id,
            capability_kind="tools",
            detection_evidence=(
                f"{evidence_base} -> empty allowlist "
                f"(inherit/default universe, matches resolve_tool_filter)"
            ),
        )
    if capability_id in allowed:
        return CapabilityCheckResult(
            state="present",
            capability_id=capability_id,
            capability_kind="tools",
            detection_evidence=f"{evidence_base} -> present",
        )
    return CapabilityCheckResult(
        state="missing",
        capability_id=capability_id,
        capability_kind="tools",
        detection_evidence=f"{evidence_base} -> not in {sorted(allowed)!r}",
    )


def check_env_capability(
    capability_id: str, *, env_lookup: EnvLookupFn | None = None
) -> CapabilityCheckResult:
    """Check that ``capability_id`` (an env var name) is set."""
    evidence_base = f"pre_flight: environ.get({capability_id!r})"
    if env_lookup is None:
        return CapabilityCheckResult(
            state="missing",
            capability_id=capability_id,
            capability_kind="env",
            detection_evidence=f"{evidence_base} -> <no env_lookup injected>",
        )
    env = env_lookup()
    val = env.get(capability_id)
    if val and val.strip():
        return CapabilityCheckResult(
            state="present",
            capability_id=capability_id,
            capability_kind="env",
            detection_evidence=f"{evidence_base} -> present",
        )
    return CapabilityCheckResult(
        state="missing",
        capability_id=capability_id,
        capability_kind="env",
        detection_evidence=f"{evidence_base} -> unset",
    )


def capability_check(
    capability_id: str,
    *,
    mcp_lookup: McpLookupFn | None = None,
    tools_allow: ToolsAllowFn | None = None,
    env_lookup: EnvLookupFn | None = None,
    capability_kind: Literal["mcp", "tools", "env"] | None = None,
    instance_id: str | None = None,
) -> CapabilityCheckResult:
    """Synchronous tri-state capability check.

    Args:
        capability_id: The capability identifier (e.g. ``opendesign``
            for an MCP server; ``bash`` for a tool; ``OPEN_DESIGN_LICENSE``
            for an env var).
        mcp_lookup: Injectable lookup for an MCP server row. Returns
            ``None`` if absent; production wires this to a thin
            SQLModel projection of ``mcp_servers`` filtered by
            ``is_active=True``.
        tools_allow: Injectable accessor for the agent's effective
            ``tools.allow`` list. Receives ``instance_id`` so it can
            resolve the active agent's meta via ``get_version`` /
            ``get_resolved`` (F1a wire completion, 2026-10-02).
        env_lookup: Injectable accessor for the merged environment
            mapping (system + project-scoped).
        capability_kind: When set, dispatch directly to the matching
            single-kind checker. When ``None`` (default for skill
            load-time aggregate checks), the function attempts
            MCP-first, then tool, then env, returning on the first
            ``present`` — the dispatch order matches the plan's
            priority for surfacing the most informative evidence.
        instance_id: Optional receiving instance id forwarded to
            ``tools_allow``. ``None`` when no instance is in scope.

    Returns:
        :class:`CapabilityCheckResult`. ``state`` is one of
        ``present`` / ``missing`` / ``unconfigured``.

    Performance contract:
        P95 < 50ms with all three lookups injectable and synchronous
        (DB read is the only variable). NO LLM calls, NO subprocess.

    Examples:
        Aggregate (auto-dispatch)::

            >>> capability_check("opendesign", mcp_lookup=lookup)
            CapabilityCheckResult(state='present', capability_id='opendesign', ...)

        Targeted (single-kind)::

            >>> capability_check("bash", tools_allow=lambda _: ["bash","read"], capability_kind="tools")
            CapabilityCheckResult(state='present', ...)
    """
    if capability_kind == "mcp":
        return check_mcp_capability(capability_id, mcp_lookup=mcp_lookup)
    if capability_kind == "tools":
        return check_tool_capability(
            capability_id,
            tools_allow=tools_allow,
            instance_id=instance_id,
        )
    if capability_kind == "env":
        return check_env_capability(capability_id, env_lookup=env_lookup)

    # Aggregate auto-dispatch: return the first ``present`` OR fall back
    # to the most-informative miss. Order: MCP → tools → env.
    mcp_result = check_mcp_capability(capability_id, mcp_lookup=mcp_lookup)
    if mcp_result.state == "present":
        return mcp_result
    if mcp_result.state == "unconfigured":
        return mcp_result
    tool_result = check_tool_capability(
        capability_id,
        tools_allow=tools_allow,
        instance_id=instance_id,
    )
    if tool_result.state == "present":
        return tool_result
    env_result = check_env_capability(capability_id, env_lookup=env_lookup)
    if env_result.state == "present":
        return env_result

    # All three missed — return the most-informative miss (mcp first).
    return mcp_result


# ── Mandatory first-instruction rule ────────────────────────────────

# Recognized first-instruction shapes; conservative match. Doc-stated form:
#   capability_check("opendesign")
#   capability_check("opendesign", mcp_lookup=...)
#   capability_check  # bare reference (rare; tolerated)
# Strict semantic: the first NON-BLANK line of the rendered body must
# match this prefix after front-matter stripping. A heading before the
# check is a violation even if a later code fence contains the call.
_MANDATORY_FIRST_INSTRUCTION_RE = re.compile(
    r"^\s*capability_check\s*\(",
)


class MandatoryFirstInstructionError(ValueError):
    """Raised when a skill body declares ``requires:`` but its body does not begin
    with a ``capability_check(...)`` instruction.

    The error message names the skill and the front-matter section; the
    loader / seeder surfaces it verbosely. ``section`` is
    ``"requires"`` today (the only hard front-matter key driving this
    rule); kept as a field so future keys can attach the same rule.
    """

    def __init__(
        self,
        message: str,
        *,
        skill_name: str,
        section: str,
        skill_set_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.skill_name = skill_name
        self.section = section
        self.skill_set_path = skill_set_path


def enforce_mandatory_first_instruction(
    *,
    skill_name: str,
    skill_body: str,
    requirement: CapabilityRequirement | None,
    skill_set_path: str | None = None,
) -> None:
    """Enforce: skill body declaring ``requires:`` must begin with ``capability_check(...)``.

    Args:
        skill_name: The skill's name (used in the error message).
        skill_body: The full skill body text — front-matter + body.
        requirement: The parsed :class:`CapabilityRequirement`. When
            ``None`` or empty (no ``requires:`` declared), the function
            returns silently (no rule applied to advisory skills).
        skill_set_path: Source path for diagnostic logging.

    Raises:
        MandatoryFirstInstructionError: when ``requirement`` is
            non-empty and the body's first non-blank line does NOT
            start with ``capability_check(`` (after front-matter
            stripping and leading-whitespace trim).

    Implementation notes:
        * The body's front-matter (if present) is stripped before the
          check — we only inspect the rendered body that the agent
          will actually read.
        * Blank lines and leading whitespace are skipped until the
          first substantive line is found.
        * A code fence that runs the call as its first line IS
          accepted (fences are markdown structure, not body prose).
          A heading line BEFORE such a fence is a violation.
    """
    if requirement is None or requirement.is_empty():
        return

    body = _strip_frontmatter(skill_body)
    first = _first_substantive_line(body)
    if first is None or not _MANDATORY_FIRST_INSTRUCTION_RE.match(first):
        first_display = first if first is not None else "<empty body>"
        logger.warning(
            f"skill {skill_name!r} (skill-set={skill_set_path!r}) declares "
            f"requires: but body does not begin with capability_check(...); "
            f"first substantive line: {first_display!r}"
        )
        raise MandatoryFirstInstructionError(
            (
                f"skill {skill_name!r} declares a non-empty `requires:` "
                f"block in {skill_set_path or '<skill-set>'} but its body "
                f"does not begin with a `capability_check(...)` "
                f"instruction (first substantive line: "
                f"{first_display!r}). Per the day-1 contract, a "
                f"`requires:`-bearing skill's FIRST instruction must be "
                f"`capability_check(...)`."
            ),
            skill_name=skill_name,
            section="requires",
            skill_set_path=skill_set_path,
        )


def _strip_frontmatter(body: str) -> str:
    """Strip YAML front-matter (between leading ``---`` fences) from a skill body.

    No-op when no leading front-matter exists. Mirrors the legacy
    ``skill-set.md`` style — see ``daemon.services.skill_seed_service._FRONTMATTER_RE``.
    """
    text = body.lstrip()
    if not text.startswith("---"):
        return body
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("---"):
        return body
    # Find the closing ---
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "".join(lines[i + 1 :])
    return body


def _first_non_blank_line(body: str) -> str:
    for line in body.splitlines():
        if line.strip():
            return line.strip()[:120]
    return "<empty body>"


def _first_substantive_line(body: str) -> str | None:
    """First line that is not blank and not a code-fence delimiter.

    Markdown code fences (triple backticks) are structural delimiters —
    skipping them lets a body that begins with a fenced block satisfy
    the rule. Returns the line stripped of trailing whitespace.
    """
    in_frontmatter_close_zone = False
    saw_frontmatter_open = False
    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        # Skip a leading single-line code-fence marker; a skill body
        # that opens a fence on its first line is intentionally
        # code-first (e.g. a quick-reference). Multi-line fences
        # raise the question of whether prose between fences is the
        # "first instruction" — today we treat the open-fence as the
        # boundary and require the first content inside to start the
        # rule.
        if stripped.startswith("```") or stripped.startswith("~~~"):
            continue
        if line == "---":
            if not saw_frontmatter_open:
                saw_frontmatter_open = True
                continue
            in_frontmatter_close_zone = True
            continue
        return stripped
    return None


# ─────────────────────────────────────────────────────────────────────
# WP3 — Escalation envelope (Pydantic) + Result: prefix conventions
# ─────────────────────────────────────────────────────────────────────

# Note on the day-1 lint: the static assertion
# :func:`assert_no_policy_denied_in_day1_paths` walks the AST and
# flags any *producer* call — :func:`EscalationEnvelope.now_envelope`,
# :meth:`EscalationEnvelope.from_check`, or the bare
# :class:`EscalationEnvelope` constructor — that passes
# ``kind="policy_denied"``. Schema + validator + parser code below
# legitimately references the ``policy_denied`` enum (the Literal type
# + the cross-field validation); the AST walk ignores those.

EnvelopeKind = Literal[
    "capability_missing", "installed_but_unconfigured", "policy_denied"
]
BlockerScope = Literal["this_turn", "this_task"]


class EscalationEnvelope(BaseModel):
    """§7.2 escalation envelope — exact schema, all seven fields required.

    The envelope rides the existing ``internal_report:{iid}:{mid}``
    child-report lane (see ``daemon.services.child_reports``). No new
    transport. The wire format is a ``Result:``-prefixed JSON line
    produced by :func:`format_envelope_message` and parsed by
    :func:`parse_envelope_from_message`.

    Day-1 firing kinds:

    * ``capability_missing`` — the pre-flight (``capability_check``)
      returned ``state=missing``. The worker → designer escalate to
      spawn the installer skill.
    * ``installed_but_unconfigured`` — pre-flight returned ``unconfigured``.
      The installer ran but a required env / secret / handle binding
      is still absent. Mint + resume.

    Forward-compat (NOT day-1):

    * ``policy_denied`` — schema-accepted so future WP7.5a (the policy
      layer) can emit it without a schema break. Per the lint test
      :func:`assert_no_policy_denied_in_day1_paths`, NO day-1 code path
      may construct an envelope with ``kind='policy_denied'``.

    ``policy_denied_reason`` is ``None`` for any non-policy_denied kind.
    """

    model_config = ConfigDict(extra="forbid")

    kind: EnvelopeKind
    capability: str
    installer_skill: str
    detection_evidence: str
    blocker_scope: BlockerScope
    resume_hint: str
    policy_denied_reason: str | None
    ts: str = Field(..., description="ISO-8601 timestamp string")

    @field_validator("ts")
    @classmethod
    def _validate_iso8601(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("ts must be a non-empty ISO-8601 string")
        # Accept whatever ``datetime.fromisoformat`` accepts, which is
        # the same parsing envelope producers/parsers MUST use. Python
        # 3.13 supports the trailing ``Z`` suffix via ``fromisoformat``.
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"ts is not ISO-8601-parseable: {value!r} ({e})")
        return value

    @field_validator("policy_denied_reason")
    @classmethod
    def _validate_policy_denied_reason(cls, value: str | None) -> str | None:
        # ``policy_denied_reason`` may be a non-null string only when
        # ``kind == 'policy_denied'``; null otherwise. The check uses
        # cross-field validation below.
        return value

    def model_post_init(self, __context: Any) -> None:
        # Cross-field invariant: policy_denied_reason null for non-policy_denied.
        if self.kind != "policy_denied" and self.policy_denied_reason is not None:
            raise ValueError(
                f"policy_denied_reason must be null when kind={self.kind!r}; "
                f"got {self.policy_denied_reason!r}"
            )
        # When kind=policy_denied, surface a default reason if not provided.
        # We do NOT auto-generate one — the missing default is also a fault.
        if self.kind == "policy_denied" and not self.policy_denied_reason:
            raise ValueError(
                "policy_denied envelopes MUST carry a non-empty "
                "policy_denied_reason"
            )
        # Field hygiene.
        for f in (
            "capability",
            "installer_skill",
            "detection_evidence",
            "resume_hint",
        ):
            v = getattr(self, f)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"{f} must be a non-empty string")
        if self.kind not in (
            "capability_missing",
            "installed_but_unconfigured",
            "policy_denied",
        ):
            raise ValueError(f"unknown envelope kind: {self.kind!r}")
        if self.blocker_scope not in ("this_turn", "this_task"):
            raise ValueError(f"unknown blocker_scope: {self.blocker_scope!r}")

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def now_envelope(
        cls,
        *,
        kind: EnvelopeKind,
        capability: str,
        installer_skill: str,
        detection_evidence: str,
        blocker_scope: BlockerScope = "this_turn",
        resume_hint: str = "step_after_preflight",
        policy_denied_reason: str | None = None,
    ) -> "EscalationEnvelope":
        """Construct an envelope stamped with the current UTC ISO-8601 timestamp."""
        ts = datetime.now(timezone.utc).isoformat()
        return cls(
            kind=kind,
            capability=capability,
            installer_skill=installer_skill,
            detection_evidence=detection_evidence,
            blocker_scope=blocker_scope,
            resume_hint=resume_hint,
            policy_denied_reason=policy_denied_reason,
            ts=ts,
        )

    @classmethod
    def from_check(
        cls,
        check_result: CapabilityCheckResult,
        *,
        installer_skill: str,
        blocker_scope: BlockerScope = "this_turn",
        resume_hint: str = "step_after_preflight",
    ) -> "EscalationEnvelope":
        """Lift a :class:`CapabilityCheckResult` to the canonical envelope.

        Day-1 mapping:
          ``missing``        → ``capability_missing``
          ``unconfigured``   → ``installed_but_unconfigured``
          ``present``        → raises ValueError (no envelope needed).
        """
        if check_result.state == "present":
            raise ValueError(
                "CapabilityCheckResult.state=='present'; no envelope to emit"
            )
        kind: EnvelopeKind = (
            "capability_missing"
            if check_result.state == "missing"
            else "installed_but_unconfigured"
        )
        return cls.now_envelope(
            kind=kind,
            capability=check_result.capability_id,
            installer_skill=installer_skill,
            detection_evidence=check_result.detection_evidence,
            blocker_scope=blocker_scope,
            resume_hint=resume_hint,
            policy_denied_reason=None,
        )


# ─────────────────────────────────────────────────────────────────────
# Result: prefix parser / formatter (child-report lane convention)
# ─────────────────────────────────────────────────────────────────────

RESULT_PREFIX = "Result: "


class EnvelopeParseError(ValueError):
    """Raised when the message body is not parseable as an envelope.

    The exception is narrow: a malformed Result: prefix, a malformed
    JSON body, or a payload whose schema differs from the eight §7.2
    fields.
    """


def format_envelope_message(
    envelope: EscalationEnvelope, *, prefix: str = RESULT_PREFIX
) -> str:
    """Render an envelope as a child-report body ``Result: <json>\\n``."""
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("prefix must be a non-empty string")
    body_json = envelope.to_json()
    return f"{prefix}{body_json}\n"


def parse_envelope_from_message(message: str) -> EscalationEnvelope:
    """Parse a child-report body into an :class:`EscalationEnvelope`.

    Accepted shapes (in order):

    1. ``"Result: <json-object>"`` (possibly with leading whitespace;
       whitespace is stripped). The ``Result:`` line is the canonical
       escalation wire format.
    2. A bare JSON object string (defensive — receives malformed
       producers during rollout).

    Anything else raises :class:`EnvelopeParseError`.
    """
    if not isinstance(message, str) or not message.strip():
        raise EnvelopeParseError("message is empty or whitespace")
    text = message.lstrip()
    # Strip the Optional ``Result: `` prefix (with or without trailing whitespace).
    if text.startswith(RESULT_PREFIX):
        text = text[len(RESULT_PREFIX):]
    elif text.startswith("Result:"):
        # Tolerate the exact prefix with no trailing space (older test fixtures).
        text = text[len("Result:"):].lstrip()
    # Strip trailing whitespace / newline.
    text = text.rstrip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise EnvelopeParseError(
            f"envelope body is not valid JSON: {e}"
        ) from e
    if not isinstance(data, dict):
        raise EnvelopeParseError(
            f"envelope body must be a JSON object, got "
            f"{type(data).__name__}"
        )
    try:
        return EscalationEnvelope.model_validate(data)
    except Exception as e:  # pydantic.ValidationError or our cross-field
        raise EnvelopeParseError(
            f"envelope schema validation failed: {e}"
        ) from e


# ─────────────────────────────────────────────────────────────────────
# Day-1 static lint — assert no day-1 code emits policy_denied
# ─────────────────────────────────────────────────────────────────────

# Day-1 source roots to scan. The AST walk below is the canonical
# mechanism — substring search is too coarse (schema / docstrings /
# error messages legitimately carry ``policy_denied``).
_DAY1_SOURCE_ROOTS: tuple[str, ...] = (
    "daemon/services/capability_resolver.py",
    "daemon/services/skill_seed_service.py",
)


def assert_no_policy_denied_in_day1_paths(
    *,
    additional_roots: Iterable[str] = (),
    scan_string: str | None = None,
) -> None:
    """Static lint: assert no day-1 code path can construct an envelope
    with ``kind='policy_denied'``.

    The lint is a focused AST walk, NOT a substring search. It inspects
    every :class:`ast.Call` node in the day-1 source trees and flags:

    * ``EscalationEnvelope.now_envelope(kind="policy_denied", ...)``
    * ``EscalationEnvelope.from_check(kind="policy_denied", ...)``
    * ``EscalationEnvelope(kind="policy_denied", ...)`` (parameterless
      constructor form)

    Substring search misses the producer-vs-schema distinction: a
    ``kind="policy_denied"`` literal is fine inside the schema
    definition (``EnvelopeKind`` Literal, cross-field validators, error
    messages, docstrings) — it's NOT fine in a producer call. The AST
    walk distinguishes those structurally.

    The lint walks the project tree once. Day-1 source roots default
    to the two files in scope for Phase 3 WP1–WP2. Callers from later
    phases (WP5+ install skill authors) WILL add policy_denied —
    they're NOT day-1; until §7.5a lands, add their paths to
    ``additional_roots`` ONLY when the policy layer is in flight.

    ``scan_string`` is a test/dev hook — when supplied, the string is
    parsed as a Python module. Used by tests to assert the lint
    rejects a synthetic offender. A scanner-caller (not this module)
    can also pass an AST tree through ``_ast_assert_no_policy_denied``
    directly to avoid reparsing.
    """
    import ast as _ast

    if scan_string is not None:
        try:
            tree = _ast.parse(scan_string)
        except SyntaxError as e:
            raise AssertionError(
                f"scan_string is not parseable Python: {e}"
            ) from e
        _ast_assert_no_policy_denied(
            tree,
            origin="<scan_string>",
        )
        return

    for rel in (*_DAY1_SOURCE_ROOTS, *additional_roots):
        src = _read_module_source(rel)
        if not src:
            continue
        try:
            tree = _ast.parse(src)
        except SyntaxError as e:
            raise AssertionError(
                f"failed to AST-parse {rel}: {e}"
            ) from e
        _ast_assert_no_policy_denied(tree, origin=rel)


def _ast_assert_no_policy_denied(tree: "Any", *, origin: str) -> None:
    """Walk an AST, raising ``AssertionError`` on day-1 policy_denied vectors."""
    import ast as _ast

    def _is_kw_policy_denied(value: "Any") -> bool:
        if isinstance(value, _ast.Constant) and isinstance(value.value, str):
            return value.value == "policy_denied"
        return False

    def _walk_call(call: _ast.Call) -> None:
        # Find the target function as a dotted attribute or simple name.
        func = call.func
        target = None
        if isinstance(func, _ast.Attribute):
            parts: list[str] = []
            cur = func
            while isinstance(cur, _ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, _ast.Name):
                parts.append(cur.id)
                target = ".".join(reversed(parts))
        elif isinstance(func, _ast.Name):
            target = func.id

        # Vectors flagged: bare EscalationEnvelope construction or any
        # call into its class methods that exposes a ``kind=`` kwarg.
        if target not in (
            "EscalationEnvelope",
            "EscalationEnvelope.now_envelope",
            "EscalationEnvelope.from_check",
        ):
            return

        for kw in call.keywords:
            if kw.arg == "kind" and _is_kw_policy_denied(kw.value):
                raise AssertionError(
                    f"day-1 lint violation: a code path constructs "
                    f"an envelope with kind='policy_denied' in "
                    f"{origin}. Per Phase 3 §3 WP3 acceptance: "
                    f"'policy_denied' is schema-only until §7.5a "
                    f"lands. The call is `{target}(...)`."
                )

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            _walk_call(node)


def _read_module_source(rel_path: str) -> str:
    """Read a module source by relative path under the project root.

    The lint walks the project tree once at the start of the test run;
    modules outside the project root are reported but never scanned
    here (callers may pass them via ``additional_roots`` explicitly).
    """
    p = Path(rel_path)
    if not p.is_absolute() and not p.exists():
        # Try a couple of common project roots before giving up.
        cwd = Path(os.getcwd())
        candidates = [
            cwd / rel_path,
            cwd.parent / rel_path,
            cwd.parent.parent / rel_path,
        ]
        for c in candidates:
            if c.exists():
                p = c
                break
    if not p.exists():
        logger.debug(f"day-1 lint: source not found for {rel_path!r}, skipping")
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug(f"day-1 lint: failed to read {rel_path!r}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────
# WP4 — capabilities.yaml registry
# ─────────────────────────────────────────────────────────────────────

# Default registry path relative to the project root. Phase-lead path
# decision: co-located with the dynamic-skill innate skill (not at
# agents/dynamic-skill/ which doesn't exist).
DEFAULT_CAPABILITIES_REGISTRY_PATH = Path(
    "agents/_prompt_system/innate-skills/dynamic-skill/capabilities.yaml"
)

_CAPABILITY_REGISTRY_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"capability_id", "installer_skill", "builtin_mcp_class", "schema_version", "requires_secret", "kms_service_id"}
)


@dataclass
class CapabilityRegistryEntry:
    """One entry from ``capabilities.yaml``.

    The mapping is the planner's contract (§3 P3-WP4):

      capability_id          — opaque stable id (e.g. ``opendesign``)
      installer_skill        — name of a skill in some agent's skill-set
                                (cross-referenced at load; may be flagged
                                ``_pending=True`` when the skill is
                                declared but not yet present)
      builtin_mcp_class      — class name resolvable against
                                ``daemon.mcp.builtin_servers`` registry
      schema_version         — version string for ``config_schema_version``
                                drift detection
      requires_secret        — bool; when ``True``, ``kms_service_id``
                                MUST be non-empty
      kms_service_id         — service identifier handed to KMS-Lite
                                mint primitive
      pending                — bool flag; set when the entry's
                                ``installer_skill`` is declared but
                                not yet present in any skill-set. The
                                strict cross-reference activates once
                                the skill lands; until then, the entry
                                loads with a ``logger.warning``.
    """

    capability_id: str
    installer_skill: str
    builtin_mcp_class: str
    schema_version: str
    requires_secret: bool
    kms_service_id: str
    pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "installer_skill": self.installer_skill,
            "builtin_mcp_class": self.builtin_mcp_class,
            "schema_version": self.schema_version,
            "requires_secret": self.requires_secret,
            "kms_service_id": self.kms_service_id,
            "_pending": self.pending,
        }


class CapabilityRegistryError(ValueError):
    """Raised for registry-load failures: missing field, dangling
    ``installer_skill``, dangling ``builtin_mcp_class``,
    ``requires_secret=True`` with empty ``kms_service_id``."""


def load_capabilities_registry(
    registry_path: Path | str | None = None,
    *,
    installer_skill_names: Iterable[str] | None = None,
    builtin_mcp_resolver: Callable[[str], Any] | None = None,
    lazy_installer_validation: bool = True,
) -> list[CapabilityRegistryEntry]:
    """Load ``capabilities.yaml`` and validate the per-entry schema.

    Args:
        registry_path: Path to the YAML file. Defaults to
            :data:`DEFAULT_CAPABILITIES_REGISTRY_PATH` (project-relative;
            resolved against ``os.getcwd()``).
        installer_skill_names: Skill names from all ``skill-set.yaml``
            files. When supplied, ``installer_skill`` cross-reference
            is enforced strictly; missing skills raise
            :class:`CapabilityRegistryError`. When ``None`` (default),
            the cross-reference is lazy (logs a warning, sets
            ``pending=True``).
        builtin_mcp_resolver: Callable that maps a class name to a
            ``BuiltinServerDefinition`` instance (production:
            ``daemon.mcp.builtin_servers.get_registry().get_by_name``).
            When ``None``, the cross-reference is skipped (test
            surface).
        lazy_installer_validation: When ``True`` (default), missing
            ``installer_skill`` references set ``pending=True`` and
            log a warning rather than raising. Flip to ``False`` to
            enforce strictly (recommended before phase exit — the
            plan mandates the strict path is ACTIVE by phase end).

    Returns:
        A list of validated :class:`CapabilityRegistryEntry` objects,
        in document order.

    Raises:
        FileNotFoundError: when ``registry_path`` does not exist.
        CapabilityRegistryError: for any validation failure EXCEPT
            pending ``installer_skill`` (which becomes a warning +
            ``pending=True``).
    """
    path = Path(registry_path) if registry_path else DEFAULT_CAPABILITIES_REGISTRY_PATH
    if not path.is_absolute() and not path.exists():
        # Resolve against cwd-up the project tree.
        for ancestor in (Path.cwd(), *Path.cwd().parents):
            candidate = ancestor / path
            if candidate.exists():
                path = candidate
                break
    if not path.exists():
        raise FileNotFoundError(
            f"capabilities registry not found at {path!r}"
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CapabilityRegistryError(
            f"capabilities registry at {path!r} top-level must be a list, "
            f"got {type(raw).__name__}"
        )

    valid_installer_set = set(installer_skill_names or ())
    entries: list[CapabilityRegistryEntry] = []
    for i, raw_entry in enumerate(raw):
        if not isinstance(raw_entry, dict):
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} must be a mapping, got "
                f"{type(raw_entry).__name__}"
            )
        missing = _CAPABILITY_REGISTRY_REQUIRED_FIELDS - set(raw_entry.keys())
        if missing:
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} missing required fields: "
                f"{sorted(missing)}"
            )
        pending = bool(raw_entry.get("_pending", False))
        try:
            entry = CapabilityRegistryEntry(
                capability_id=str(raw_entry["capability_id"]),
                installer_skill=str(raw_entry["installer_skill"]),
                builtin_mcp_class=str(raw_entry["builtin_mcp_class"]),
                schema_version=str(raw_entry["schema_version"]),
                requires_secret=bool(raw_entry["requires_secret"]),
                kms_service_id=str(raw_entry["kms_service_id"]),
                pending=pending,
            )
        except (ValueError, TypeError) as e:
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} invalid: {e}"
            ) from e

        if not entry.capability_id.strip():
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} has empty capability_id"
            )
        if not entry.installer_skill.strip():
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} has empty installer_skill"
            )
        if not entry.builtin_mcp_class.strip():
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} has empty builtin_mcp_class"
            )
        if not entry.schema_version.strip():
            raise CapabilityRegistryError(
                f"registry entry #{i} in {path!r} has empty schema_version"
            )

        # ``requires_secret=True`` ⇒ ``kms_service_id`` non-empty.
        if entry.requires_secret and not entry.kms_service_id.strip():
            raise CapabilityRegistryError(
                f"registry entry #{i} ({entry.capability_id!r}) in {path!r}: "
                f"requires_secret=True but kms_service_id is empty"
            )

        # ``installer_skill`` cross-reference (lazy by default).
        if (
            valid_installer_set
            and entry.installer_skill not in valid_installer_set
        ):
            if lazy_installer_validation:
                logger.warning(
                    f"registry entry #{i} ({entry.capability_id!r}) "
                    f"references installer_skill={entry.installer_skill!r} "
                    f"which is not present in any agent's skill-set — "
                    f"flagging _pending=True and continuing "
                    f"(strict validation will activate once the skill "
                    f"lands)"
                )
                entry.pending = True
            else:
                raise CapabilityRegistryError(
                    f"registry entry #{i} ({entry.capability_id!r}) "
                    f"references installer_skill={entry.installer_skill!r} "
                    f"which is not present in any agent's skill-set"
                )

        # ``builtin_mcp_class`` cross-reference.
        if builtin_mcp_resolver is not None:
            resolved = builtin_mcp_resolver(entry.builtin_mcp_class)
            if resolved is None:
                raise CapabilityRegistryError(
                    f"registry entry #{i} ({entry.capability_id!r}) "
                    f"references builtin_mcp_class={entry.builtin_mcp_class!r} "
                    f"which does not resolve against the builtin_servers "
                    f"registry"
                )

        entries.append(entry)

    return entries


def list_known_skill_names_from_skill_set_files(
    agents_dir: Path,
) -> set[str]:
    """Static scan: collect every ``skills[].name`` from each agent's ``skill-set.yaml``.

    Used as the ``installer_skill_names`` input to
    :func:`load_capabilities_registry`. Walks legacy ``skill-set.md``
    via the same frontmatter extraction as the seed service. Per-plan
    convention, manifest format is dominant (``.yaml`` over ``.md``).

    Lazy-import of ``parse_skill_set_file`` keeps capability_resolver
    importable WITHOUT pulling in skill_seed_service (which depends on
    skill_include_resolver + repository wiring) — the bootstrap loader
    can stand alone for tests that only need the registry loader.
    """
    from .skill_seed_service import parse_skill_set_file  # lazy: avoid cycle

    names: set[str] = set()
    if not agents_dir.exists():
        return names
    for entry in agents_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        yaml_path = entry / "skill-set.yaml"
        md_path = entry / "skill-set.md"
        path = yaml_path if yaml_path.exists() else md_path
        if not path.exists():
            continue
        try:
            entries = parse_skill_set_file(path)
        except Exception as e:
            logger.warning(
                f"discovery: failed to parse {path}: {e!r}"
            )
            continue
        for e in entries:
            names.add(e.name)
    return names


# ─────────────────────────────────────────────────────────────────────
# WP4 strictness flip — production loader (P3-WP5 fold)
# ─────────────────────────────────────────────────────────────────────

#: Agents root scanned for ``skill-set.yaml`` manifests by the
#: production loader (project-relative; resolved against cwd).
DEFAULT_AGENTS_DIR = Path("agents")


def _default_builtin_mcp_resolver(class_or_name: str) -> Any:
    """Resolve a ``builtin_mcp_class`` entry against the live registry.

    ``capabilities.yaml`` stores the CLASS name (e.g. ``SomeMCPServer``)
    while :meth:`BuiltinServerRegistry.get_by_name` keys on the SERVER
    name. Production resolution therefore tries the server-name lookup
    first, then a class-name scan — either match counts as resolved;
    anything else returns ``None`` (which the loader turns into a
    strict error).

    Lazy import: keeps capability_resolver importable without the MCP
    subsystem (tests inject their own resolvers).
    """
    from daemon.mcp.builtin_servers import get_registry  # lazy: avoid cycle

    registry = get_registry()
    by_name = registry.get_by_name(class_or_name)
    if by_name is not None:
        return by_name
    for candidate in registry.get_all():
        if type(candidate).__name__ == class_or_name:
            return candidate
    return None


def load_production_capabilities_registry(
    registry_path: Path | str | None = None,
    agents_dir: Path | str | None = None,
) -> list["CapabilityRegistryEntry"]:
    """Load the capabilities registry the way PRODUCTION does — strict.

    This is the single production entry point for registry loads
    (P3-WP5 strictness flip: install skills now exist in
    ``agents/worker/skill-set.yaml``, so a dangling
    ``installer_skill`` reference is a real misconfiguration and MUST
    fail loud instead of silently loading as ``_pending``):

    - ``installer_skill_names`` discovered from every agent's
      ``skill-set.yaml`` via
      :func:`list_known_skill_names_from_skill_set_files`.
    - ``builtin_mcp_class`` resolved against the live builtin_servers
      registry (class-name OR server-name match).
    - ``lazy_installer_validation=False`` — STRICT. A dangling
      installer skill raises :class:`CapabilityRegistryError`.

    Tests keep their fixture-based non-strict surface by calling
    :func:`load_capabilities_registry` directly with explicit
    arguments; only production wiring goes through here.

    Raises:
        FileNotFoundError: registry yaml missing.
        CapabilityRegistryError: any strict-validation failure
            (dangling installer skill, unresolvable builtin class,
            schema violation).
    """
    names = list_known_skill_names_from_skill_set_files(
        Path(agents_dir) if agents_dir else DEFAULT_AGENTS_DIR
    )
    return load_capabilities_registry(
        registry_path=registry_path,
        installer_skill_names=names,
        builtin_mcp_resolver=_default_builtin_mcp_resolver,
        lazy_installer_validation=False,
    )


# ─────────────────────────────────────────────────────────────────────
# WP11 — [resume] convention (documented convention + parse helper)
# ─────────────────────────────────────────────────────────────────────

#: Body marker that tags a resume block inside a ``job_continue``
#: message (WP11 ratification — mirrors the ``Result: `` prefix
#: convention of the child-report lane). Canonical shape::
#:
#:     [resume] {"capability_id": "opendesign",
#:               "status": "installed_but_unconfigured",
#:               "tools_now_available": ["kms_request", "kms_attach"],
#:               "resume_from": "step_after_kms_bind"}
RESUME_TAG = "[resume]"

#: The four REQUIRED fields of a resume block (arch §7.3).
_RESUME_REQUIRED_FIELDS = (
    "capability_id",
    "status",
    "tools_now_available",
    "resume_from",
)


class ResumeParseError(ValueError):
    """Raised when a message's ``[resume]`` block is missing, not
    valid JSON, or violates the four-field shape. Consumers translate
    this into a ``capability_missing`` escalation envelope (see
    :func:`escalation_for_malformed_resume`) — never a crash."""


@dataclass
class ResumeMessage:
    """Parsed ``[resume]`` block (WP11 shape, arch §7.3).

    ``resume_from`` is the step label the consumer MUST continue from —
    honoring it is contractual (re-running completed steps, e.g.
    minting a second KMS handle, is a violation). ``raw`` preserves the
    full JSON object for lossless diagnostics.
    """

    capability_id: str
    status: str
    tools_now_available: list[str]
    resume_from: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The canonical four-field payload (round-trips through
        :func:`format_resume_message` / :func:`parse_resume_message`)."""
        return {
            "capability_id": self.capability_id,
            "status": self.status,
            "tools_now_available": list(self.tools_now_available),
            "resume_from": self.resume_from,
        }


def format_resume_message(resume: ResumeMessage) -> str:
    """Render a resume block as ``[resume] <json>\\n`` (canonical wire form)."""
    return f"{RESUME_TAG} {json.dumps(resume.to_dict())}\n"


def parse_resume_message(message: str) -> ResumeMessage:
    """Parse the ``[resume]``-tagged block out of a ``job_continue`` message.

    Accepted shapes (mirroring :func:`parse_envelope_from_message`'s
    tolerance):

    1. ``"[resume] {...}"`` — JSON on the same line (canonical).
    2. ``"[resume]\\n{...}"`` — JSON starting on a following line.
    3. Prose around the block — the FIRST ``[resume]`` tag in the
       message wins; everything after its opening ``{`` is handed to
       ``json.JSONDecoder.raw_decode`` (so multi-line pretty-printed
       JSON parses and trailing prose is ignored).

    Raises:
        ResumeParseError: no tag in message, no JSON object after the
            tag, invalid JSON, or a schema violation (missing fields /
            wrong types).
    """
    if not isinstance(message, str) or not message.strip():
        raise ResumeParseError("message is empty or whitespace")
    idx = message.find(RESUME_TAG)
    if idx == -1:
        raise ResumeParseError(
            f"no {RESUME_TAG} block found in message"
        )
    after_tag = message[idx + len(RESUME_TAG):]
    brace = after_tag.find("{")
    if brace == -1:
        raise ResumeParseError(
            f"{RESUME_TAG} block carries no JSON object"
        )
    try:
        data, _end = json.JSONDecoder().raw_decode(after_tag[brace:])
    except json.JSONDecodeError as e:
        raise ResumeParseError(
            f"{RESUME_TAG} block is not valid JSON: {e}"
        ) from e
    if not isinstance(data, dict):
        raise ResumeParseError(
            f"{RESUME_TAG} block must be a JSON object, got "
            f"{type(data).__name__}"
        )
    missing = [f for f in _RESUME_REQUIRED_FIELDS if f not in data]
    if missing:
        raise ResumeParseError(
            f"{RESUME_TAG} block missing required fields: {missing}"
        )
    for str_field in ("capability_id", "status", "resume_from"):
        value = data[str_field]
        if not isinstance(value, str) or not value.strip():
            raise ResumeParseError(
                f"{RESUME_TAG} field {str_field!r} must be a non-empty string"
            )
    tools = data["tools_now_available"]
    if not isinstance(tools, list) or not all(
        isinstance(t, str) for t in tools
    ):
        raise ResumeParseError(
            f"{RESUME_TAG} field 'tools_now_available' must be a list "
            f"of strings"
        )
    return ResumeMessage(
        capability_id=data["capability_id"],
        status=data["status"],
        tools_now_available=list(tools),
        resume_from=data["resume_from"],
        raw=data,
    )


#: Mapping from carried resume statuses to the tri-state a FRESH
#: ``capability_check`` should agree with (WP11 consumer step 2).
_RESUME_STATUS_TO_STATE: dict[str, str] = {
    "missing": "missing",
    "capability_missing": "missing",
    "unconfigured": "unconfigured",
    "installed_but_unconfigured": "unconfigured",
}


def resume_capability_check(
    resume: ResumeMessage,
    **check_kwargs: Any,
) -> "CapabilityCheckResult":
    """Re-run the pre-flight for a resumed capability (WP11 step 2).

    The carried ``status`` is NEVER trusted — the fresh tri-state check
    is the single source of truth. All ``check_kwargs`` forward to
    :func:`capability_check` (``mcp_lookup`` / ``tools_allow`` /
    ``env_lookup``).
    """
    return capability_check(resume.capability_id, **check_kwargs)


def resume_status_agrees(
    resume: ResumeMessage,
    check_result: "CapabilityCheckResult",
) -> bool:
    """Whether a fresh check agrees with the resume's carried status.

    ``True`` when the fresh state is ``present`` (resolved during the
    resume window — consumer proceeds) or equals the mapped tri-state
    of the carried status. An UNRECOGNIZED carried status agrees
    vacuously (informational field; the fresh check governs).
    """
    expected = _RESUME_STATUS_TO_STATE.get(resume.status)
    if expected is None:
        return True
    return check_result.state in (expected, "present")


def escalation_for_malformed_resume(
    *,
    capability: str,
    installer_skill: str,
    reason: str,
    blocker_scope: "BlockerScope" = "this_task",
) -> "EscalationEnvelope":
    """Build the WP11-mandated envelope for an unparseable resume block.

    Acceptance: "malformed resume message → escalation
    ``kind=capability_missing``". ``detection_evidence`` carries the
    parse failure reason; ``resume_hint`` points back at the pre-flight
    (the consumer must re-establish ground truth before proceeding).
    """
    return EscalationEnvelope.now_envelope(
        kind="capability_missing",
        capability=capability,
        installer_skill=installer_skill,
        detection_evidence=f"malformed [resume] block: {reason}",
        blocker_scope=blocker_scope,
        resume_hint="step_after_preflight",
        policy_denied_reason=None,
    )


# Re-export for convenience — sibling modules import these from
# capability_resolver.py to keep the bootstrap wiring in a single place.
__all__ = [
    # WP1
    "CapabilityRequirement",
    "parse_requires_block",
    "extend_skill_entry_with_requires",
    # WP2
    "CapabilityTriState",
    "CapabilityCheckResult",
    "McpLookupResult",
    "capability_check",
    "check_mcp_capability",
    "check_tool_capability",
    "check_env_capability",
    "enforce_mandatory_first_instruction",
    "MandatoryFirstInstructionError",
    # WP3
    "EnvelopeKind",
    "BlockerScope",
    "EscalationEnvelope",
    "format_envelope_message",
    "parse_envelope_from_message",
    "EnvelopeParseError",
    "RESULT_PREFIX",
    "assert_no_policy_denied_in_day1_paths",
    # WP4
    "CapabilityRegistryEntry",
    "load_capabilities_registry",
    "load_production_capabilities_registry",
    "list_known_skill_names_from_skill_set_files",
    "CapabilityRegistryError",
    "DEFAULT_CAPABILITIES_REGISTRY_PATH",
    "DEFAULT_AGENTS_DIR",
    # WP11 — [resume] convention
    "RESUME_TAG",
    "ResumeMessage",
    "ResumeParseError",
    "format_resume_message",
    "parse_resume_message",
    "resume_capability_check",
    "resume_status_agrees",
    "escalation_for_malformed_resume",
]
