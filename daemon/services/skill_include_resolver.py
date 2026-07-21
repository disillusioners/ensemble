r"""Skill include resolver — inline shared skill bodies at seed time.

The Skill Evolution System's design contract is "one skill per
``send_message``" — a worker dispatched with ``load_skill="unit-test"``
gets exactly that one skill in its ``[System Inject]`` block, with no
concurrent skill loaded. Under that contract, a skill MUST be
self-contained: every rule the consuming agent needs has to be inside
the rendered body, because there is no second skill to fall back on.

Without an include mechanism, the only way to share invariant rules
across skills is copy-paste. The tester skill corpus needs the
``test-pack`` invariants (5-min cap, dual-layer timeout, output
format) inside every execution skill — ``test-pack-execution``,
``unit-test``, ``integration-test``, ``mock-test``, ``e2e-test``.
Copy-pasting across five skills creates drift risk: when the invariant
changes, five files need updating in lockstep, and any miss produces
silent divergence in the worker's guidance.

The include resolver solves this with a programming-language-style
``include:`` directive in skill frontmatter. A skill declares which
shared bodies to inline; the resolver walks the directive at seed
time, fetches each named source, recursively resolves the source's
own includes (with cycle + depth guards), and returns a single
rendered string. The seeded ``skill_bank.content`` row holds the
rendered body — runtime injection paths, A/B routing, and the trigger
engine see one canonical string with no runtime include lookup.

Resolution order
----------------

For each name in ``include:`` the resolver tries, first match wins:

1. ``agents/_prompt_system/innate-skills/{name}/skill.md`` — the
   global invariant layer. Innate skills are not owned by any agent
   and represent framework-wide rules (``test-pack`` lives here).
   Highest priority because invariant rules should never be silently
   shadowed by an evolvable variant.
2. ``skill_bank.get_by_name_and_agent(name, agent_id)`` with the
   cross-agent fallback ``get_by_name_any_agent(name)`` — for cases
   where one evolvable skill pulls another shared evolvable skill's
   body. Creates a coupling that the includer's ``version`` bump
   must track when the included skill evolves.

If neither source matches, the resolver logs a warning and stores the
skill body WITHOUT the include (graceful degradation — the skill is
still seeded, just without its shared content).

Render format
-------------

The included body is appended after the includer's body, separated
by a horizontal rule and a level-2 header so the consuming agent can
see "this content came from {name}" without it becoming a separate
skill:

    <includer body>

    ---

    ## Included: test-pack

    <innate test-pack body>

The includer's own frontmatter is stripped from the rendered body —
``include:`` has no value at runtime, and the manifest in
``skill-set.yaml`` is the source of truth for the metadata fields
anyway.

Cycle + depth guards
--------------------

The resolver walks the include graph with a visited-set; a name
that's already being resolved up the chain is skipped (cycle
broken). A depth cap (default 3) bounds the chain length so a
misconfigured graph cannot blow the stack or produce a megabyte of
inlined content. Both guards log a warning and continue rather than
raising — seeding must never crash on a single misconfigured skill.

Metrics model
-------------

The included content has no lineage, no UUID, no usage record. Only
the main skill (the one in ``last_injected_skill_ids``) gets feedback
/ completion / A/B attribution. This is the explicit design contract:
``include`` modifies the rendered text, NOT the metrics graph.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import yaml

if TYPE_CHECKING:
    from ..repositories.skill.skill_bank_repository import SkillBankRepository

logger = logging.getLogger(__name__)


# Default cap on include chain length. A → B → C → D is 3 levels
# deep (A is depth 0, A's includes resolve at depth 1, etc.). The
# cap exists so a misconfigured graph cannot blow the stack or
# produce unbounded inlined content. 3 is generous — the corpus
# today has at most 2 levels (execution skill → innate invariant)
# and there's no realistic case for going deeper.
_DEFAULT_MAX_DEPTH: int = 3


# Frontmatter delimiter pattern. Mirrors the legacy ``.md`` parser
# in :mod:`daemon.services.skill_seed_service` — YAML block between
# two lines of exactly ``---``. Non-greedy so it stops at the first
# closing delimiter. DOTALL so the YAML block can span multiple
# lines.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<yaml>.*?)\n---\s*\n(?P<body>.*)",
    re.DOTALL,
)


# Separator injected between the includer's body and each included
# block. A horizontal rule plus a level-2 header makes the boundary
# obvious to the consuming agent without it looking like a second
# skill header (which would invite a stray ``skill_feedback`` call
# against the included name).
_INCLUDE_SEPARATOR_TEMPLATE: str = "\n\n---\n\n## Included: {name}\n\n"


def resolve_includes(
    content: str,
    agent_id: str,
    agents_dir: Path,
    bank_repo: Optional["SkillBankRepository"],
    *,
    max_depth: int = _DEFAULT_MAX_DEPTH,
) -> str:
    """Resolve ``include:`` directives in a skill body, returning the rendered text.

    Top-level entry point. Parses the ``include:`` key from the
    content's YAML frontmatter (if present), resolves each named
    source from innate-skills then ``skill_bank``, recursively
    resolves nested includes with cycle + depth guards, and returns
    the rendered string with the includer's frontmatter stripped.

    When the content has no frontmatter, has frontmatter without an
    ``include:`` key, or the include list is empty, the body is
    returned as-is (the frontmatter is preserved in this case —
    backwards compatibility with skills that don't use the directive
    and may rely on their frontmatter being part of the stored body).

    Args:
        content: The raw skill body file contents (may include YAML
            frontmatter delimited by ``---`` lines).
        agent_id: Owning agent ID — used for the ``skill_bank``
            include-source lookup when an innate-skill match misses.
        agents_dir: The ``agents/`` directory (NOT the per-agent
            dir). Innate skills live under
            ``agents_dir/_prompt_system/innate-skills/``.
        bank_repo: Optional :class:`SkillBankRepository` for the
            evolvable-skill include source. ``None`` skips the
            bank lookup (used by tests that only exercise the
            innate-skill path).
        max_depth: Cap on include chain length. A value of 3 means
            an includer's includes are resolved (depth 1), their
            includes are resolved (depth 2), and their includes are
            resolved (depth 3); includes at depth 4+ are skipped
            with a warning. Defaults to ``_DEFAULT_MAX_DEPTH``.

    Returns:
        The rendered skill body. Includes are appended after the
        includer's body with a ``---`` separator and a level-2
        ``## Included: {name}`` header. The includer's frontmatter
        is stripped when an ``include:`` directive was present (the
        directive has no runtime value); otherwise the body is
        returned untouched.
    """
    return _resolve_includes_recursive(
        content=content,
        agent_id=agent_id,
        agents_dir=agents_dir,
        bank_repo=bank_repo,
        visited=set(),
        depth=0,
        max_depth=max_depth,
    )


def _resolve_includes_recursive(
    *,
    content: str,
    agent_id: str,
    agents_dir: Path,
    bank_repo: Optional["SkillBankRepository"],
    visited: set[str],
    depth: int,
    max_depth: int,
) -> str:
    """Recursive worker for :func:`resolve_includes`.

    Tracks ``visited`` (the set of include names currently being
    resolved up the chain) and ``depth`` (distance from the top-level
    includer) so cycles and depth-overruns are caught before they
    blow the stack.
    """
    frontmatter, body, had_include = _split_frontmatter_and_includes(content)
    includes = frontmatter.get("include") if frontmatter else None
    include_names = _coerce_include_list(includes)

    # No include directive → return content verbatim (preserve any
    # frontmatter that was there for backwards compatibility). This
    # branch is the common case for skills that don't use the
    # feature.
    if not include_names:
        return content

    # If the includer has an include directive, strip the
    # frontmatter from the rendered body — the directive has no
    # runtime value, and the manifest in ``skill-set.yaml`` is the
    # source of truth for the metadata fields. Returning just
    # ``body`` (without the leading ``---\n...\n---\n`` block)
    # gives the runtime a clean markdown starting point.
    rendered = body.rstrip()

    # Depth cap — bound the include chain so a misconfigured graph
    # cannot recurse unboundedly. We still strip the includer's
    # frontmatter above (so the directive doesn't leak into the
    # stored body) but skip the resolution itself.
    if depth >= max_depth:
        logger.warning(
            f"skill_include_resolver: depth cap ({max_depth}) reached; "
            f"skipping {len(include_names)} include(s) at depth={depth}: "
            f"{include_names}"
        )
        return rendered

    for name in include_names:
        if name in visited:
            # Cycle — this name is already being resolved up the
            # chain. Skipping breaks the cycle without raising;
            # seeding must never crash on a single misconfigured
            # skill.
            logger.warning(
                f"skill_include_resolver: cycle detected skipping "
                f"'{name}' (chain: {sorted(visited)})"
            )
            continue

        source_content = _resolve_source(
            name=name,
            agent_id=agent_id,
            agents_dir=agents_dir,
            bank_repo=bank_repo,
        )
        if source_content is None:
            logger.warning(
                f"skill_include_resolver: '{name}' not found in "
                f"innate-skills or skill_bank (agent={agent_id}); "
                f"skipping include"
            )
            continue

        # Mark this name as visited BEFORE recursing so a child
        # that references back here is skipped at its own include
        # loop. The visited set is shared across the whole
        # resolution chain (top-level call) — it represents the
        # path from the root, not just the current level.
        visited.add(name)
        try:
            resolved_sub = _resolve_includes_recursive(
                content=source_content,
                agent_id=agent_id,
                agents_dir=agents_dir,
                bank_repo=bank_repo,
                visited=visited,
                depth=depth + 1,
                max_depth=max_depth,
            )
        finally:
            # Pop after recursion so sibling includes at the same
            # depth can re-include the same name without false
            # cycle warnings. A→B and A→C where both B and C
            # include D should both resolve D, not skip the second
            # one as a "cycle".
            visited.discard(name)

        rendered += _INCLUDE_SEPARATOR_TEMPLATE.format(name=name)
        rendered += resolved_sub.rstrip()
        rendered += "\n"

    return rendered


def _split_frontmatter_and_includes(
    content: str,
) -> tuple[Optional[dict[str, Any]], str, bool]:
    """Split ``content`` into (frontmatter, body, had_frontmatter).

    Args:
        content: Raw file contents.

    Returns:
        Tuple ``(frontmatter, body, had_frontmatter)``:

        * ``frontmatter`` — parsed YAML dict, or ``None`` when no
          frontmatter block is present. ``None`` (not ``{}``) so
          callers can distinguish "no frontmatter at all" from
          "frontmatter present but parsed to an empty dict".
        * ``body`` — the markdown body after the frontmatter. When
          no frontmatter is present, this is the full ``content``.
        * ``had_frontmatter`` — True iff the content started with
          a ``---`` delimited YAML block.
    """
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return (None, content, False)

    yaml_text = match.group("yaml")
    body = match.group("body")
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        logger.warning(
            f"skill_include_resolver: malformed frontmatter YAML "
            f"({e}); treating as no-frontmatter"
        )
        return (None, content, False)

    if not isinstance(parsed, dict):
        # Frontmatter that parses to a non-dict (e.g., a bare
        # string or list) is malformed for our purposes — there's
        # no ``include`` key to look up. Treat as no-frontmatter
        # so the body passes through unchanged.
        return (None, content, False)

    return (parsed, body, True)


def _coerce_include_list(raw: Any) -> list[str]:
    """Normalize the ``include:`` frontmatter value to a list of names.

    Accepts the common YAML shapes a user might write:

    * ``include: test-pack`` (bare string) → ``["test-pack"]``
    * ``include: [test-pack]`` (inline list) → ``["test-pack"]``
    * ``include:\n  - test-pack\n  - other`` (block list) →
      ``["test-pack", "other"]``
    * ``include: null`` / absent → ``[]``
    * Any other type → ``[]`` with a warning.

    Non-string entries are dropped (with a warning) so a malformed
    ``include: [123, true]`` doesn't crash the resolver.

    Args:
        raw: The parsed YAML value of the ``include:`` key.

    Returns:
        List of include name strings. Empty when ``raw`` is falsy,
        not a string/list, or contains only non-string entries.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        name = raw.strip()
        return [name] if name else []
    if isinstance(raw, list):
        names: list[str] = []
        for entry in raw:
            if isinstance(entry, str):
                name = entry.strip()
                if name:
                    names.append(name)
            else:
                logger.warning(
                    f"skill_include_resolver: non-string include "
                    f"entry {entry!r} skipped"
                )
        return names
    logger.warning(
        f"skill_include_resolver: 'include:' frontmatter value has "
        f"unsupported type {type(raw).__name__}; ignoring"
    )
    return []


def _resolve_source(
    *,
    name: str,
    agent_id: str,
    agents_dir: Path,
    bank_repo: Optional["SkillBankRepository"],
) -> Optional[str]:
    """Resolve an include name to its source content.

    Tries, first match wins:

    1. ``agents_dir/_prompt_system/innate-skills/{name}/skill.md`` —
       the global invariant layer. Highest priority because innate
       rules should never be silently shadowed by an evolvable
       variant with the same name.
    2. ``skill_bank`` lookup: ``(name, agent_id)`` first, then the
       cross-agent fallback ``get_by_name_any_agent(name)``. The
       same disambiguation precedence the clone-on-miss path uses.

    Args:
        name: Include name to resolve.
        agent_id: Owning agent for the ``skill_bank`` lookup.
        agents_dir: The ``agents/`` directory.
        bank_repo: Optional bank repository. ``None`` skips the
            bank lookup.

    Returns:
        The source content as a string, or ``None`` when no source
        matches.
    """
    # 1. Innate-skills dir.
    innate_path = (
        agents_dir / "_prompt_system" / "innate-skills" / name / "skill.md"
    )
    if innate_path.is_file():
        try:
            return innate_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(
                f"skill_include_resolver: innate-skill '{name}' "
                f"exists but could not be read: {e}"
            )
            # Fall through to bank lookup as a last resort.

    # 2. skill_bank.
    if bank_repo is not None:
        try:
            item = bank_repo.get_by_name_and_agent(name, agent_id)
            if item is None:
                item = bank_repo.get_by_name_any_agent(name)
            if item is not None:
                return item.content or ""
        except Exception as e:
            logger.warning(
                f"skill_include_resolver: skill_bank lookup for "
                f"'{name}' raised: {e}"
            )

    return None
