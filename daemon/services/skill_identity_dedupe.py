"""Identity dedupe at the injection boundary (skill-system-revival, workstream F).

**The rule (Ruling F).** At most ONE variant per ``(name, project_id)``
identity may reach a given agent in one injection.  The 27 stranded A/B
pairs (Ruling E) mean two divergent variants of ``code-fix`` /
``dev-strategy`` / ``git-commit`` / ``plan-creation`` can sit in the same
candidate pool; shipping that pool to an agent without determinism would
make the reported "flaky / misreading instructions" symptom WORSE.

**The deterministic pick rule — stated explicitly, reproducible, not
arbitrary:**

    primary key   = LINEAGE GENERATION — the HIGHEST ``generation``
                    wins (the newest lineage child).
    tie-break     = the skill ``id``, lexicographically ASCENDING.

The rule is deliberately **not** timestamp-based: two families carry a
``updated_at`` that is STALE relative to their challenger's
``created_at`` (plan §10, "Timestamp caveat"), so a "newest
``updated_at`` wins" rule would pick the WRONG variant.  The tie-break on
``id`` makes the choice independent of row order and of wall-clock, so a
retry of the same message always resolves the same way.

**Where it is applied.** The injection path is the ONLY consumer of
:class:`~daemon.services.skill_injection_service.SkillInjectionService`'s
private ``_search_service`` reference — the REST search route and the
``skill_search`` tool hold their OWN reference to the search service and
are therefore untouched by this module.  Installing the dedupe on the
injection service's search reference is therefore exactly "the injection
boundary" and nothing else.

**Why a proxy rather than a rewrite of the injector.** The injector
renders the ``[System Inject]`` block internally, so a dedupe applied
*after* the render could not remove a body that was already written.  The
dedupe is therefore applied to the CANDIDATE POOL (the search results),
one layer below the renderer, and the renderer then sees at most one
variant per identity.  The rule itself lives here, ONCE, and both
resolution sites (:mod:`daemon.services.instance_messaging` and
:mod:`daemon.services.context_messages`) call :func:`inject_skills_deduped`
— the logic is never duplicated at a call site.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "dedupe_injected_entries",
    "dedupe_search_results",
    "install_identity_dedupe",
    "inject_skills_deduped",
    "select_canonical_variant",
    "skill_identity_key",
    "variant_generation",
]


# ─────────────────────────────────────────────────────────────
# The identity key + the pick rule
# ─────────────────────────────────────────────────────────────


def skill_identity_key(skill: Any) -> tuple[str, str | None]:
    """Return the identity key of a skill row: ``(name, project_id)``.

    The key is the PAIR, not the name alone: ``skills.name`` is
    INTENTIONALLY non-unique — every project gets its own ``bank_clone``
    generation-0 row — so two rows sharing a name but living in
    DIFFERENT projects are DIFFERENT identities and must both survive
    the dedupe.

    Args:
        skill: A skill row (or any object exposing ``name`` /
            ``project_id``).

    Returns:
        ``(name, project_id)``; ``project_id`` is ``None`` for the
        global scope.
    """
    name = str(getattr(skill, "name", "") or "")
    project_id = getattr(skill, "project_id", None)
    return (name, str(project_id) if project_id is not None else None)


def variant_generation(skill: Any) -> int:
    """Return the LINEAGE GENERATION of a skill row.

    ``generation`` is ``0`` for imported / ``bank_clone`` roots and is
    bumped by the evolution pipeline for each mutated descendant.  A
    missing or non-numeric value degrades to ``0`` (fail-closed to the
    OLDEST generation, never to an arbitrary pick).
    """
    raw = getattr(skill, "generation", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def select_canonical_variant(variants: Sequence[Any]) -> Any:
    """Pick the canonical variant of one identity.

    **The rule:** highest lineage generation wins; ties are broken by the
    skill id, lexicographically ascending.  Never by timestamp (see the
    module docstring).

    Args:
        variants: The candidate rows sharing one identity key.

    Returns:
        The canonical row, or ``None`` when ``variants`` is empty.
    """
    if not variants:
        return None
    return min(
        variants,
        key=lambda s: (
            -variant_generation(s),
            str(getattr(s, "id", "") or ""),
        ),
    )


# ─────────────────────────────────────────────────────────────
# The pool filter
# ─────────────────────────────────────────────────────────────


def dedupe_injected_entries(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep at most ONE entry per identity key.

    The surviving entry is the canonical variant (see
    :func:`select_canonical_variant`).  The result is ORDER-STABLE: the
    canonical entry takes the slot of the FIRST member of its identity
    group, so the ranking the search produced is preserved.

    Entries whose ``skill`` is missing are passed through untouched —
    the injector drops them downstream, and the dedupe must not change
    their handling.

    Args:
        entries: The ``injected`` list of the search result — a list of
            ``{"skill": <row>, "score": float}`` dicts.

    Returns:
        The deduped list (same order, fewer or equal entries).
    """
    if not entries:
        return []

    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for entry in entries:
        skill = entry.get("skill") if isinstance(entry, dict) else None
        if skill is None:
            continue
        groups.setdefault(skill_identity_key(skill), []).append(entry)

    if not groups:
        return list(entries)

    deduped: list[dict[str, Any]] = []
    emitted: set[tuple[str, str | None]] = set()
    for entry in entries:
        skill = entry.get("skill") if isinstance(entry, dict) else None
        if skill is None:
            # No identity to collapse on — pass through (the injector
            # skips these further down).
            deduped.append(entry)
            continue
        key = skill_identity_key(skill)
        if key in emitted:
            # A sibling of this identity already took the slot.
            continue
        emitted.add(key)
        group = groups[key]
        canonical = select_canonical_variant(
            [g["skill"] for g in group if g.get("skill") is not None]
        )
        chosen = next(
            (g for g in group if g.get("skill") is canonical),
            group[0],
        )
        deduped.append(chosen)
    return deduped


def dedupe_search_results(
    results: dict[str, Any],
) -> dict[str, Any]:
    """Apply the identity dedupe to a search result dict.

    Only the ``injected`` list is collapsed.  The ``low_match`` list
    carries ``name`` / ``score`` / ``description`` only — it carries NO
    skill row, so it carries NO ``project_id`` and therefore NO identity
    key.  Those entries are rendered as "other available skills" (a
    name + one-line description, never a body), so they cannot deliver
    two DIVERGENT variants of one skill to the agent.  Leaving them
    untouched is the honest choice: collapsing them by name alone would
    destroy the legitimate case of two same-named skills in two
    different projects.
    """
    if not isinstance(results, dict):
        return results
    injected = results.get("injected")
    if not injected:
        return results
    deduped = dedupe_injected_entries(list(injected))
    if len(deduped) == len(injected):
        return results
    out = dict(results)
    out["injected"] = deduped
    return out


# ─────────────────────────────────────────────────────────────
# The injection-boundary wiring
# ─────────────────────────────────────────────────────────────


class _IdentityDedupingSearchProxy:
    """A transparent proxy that dedupes the search result pool.

    Every attribute other than ``search`` is delegated to the wrapped
    search service, so the injector (and any other consumer of the
    service it holds) keeps seeing the same object contract.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate the search, then collapse duplicate identities."""
        inner = object.__getattribute__(self, "_inner")
        results = await inner.search(*args, **kwargs)
        return dedupe_search_results(results)

    def __getattr__(self, item: str) -> Any:
        inner = object.__getattribute__(self, "_inner")
        return getattr(inner, item)


def install_identity_dedupe(service: Any) -> bool:
    """Idempotently install the identity dedupe on an injection service.

    The install replaces the service's PRIVATE search reference with a
    deduping proxy.  Only the injection path reads that reference, so the
    blast radius is exactly the injection boundary.

    Args:
        service: A :class:`SkillInjectionService` (or anything exposing
            a ``_search_service`` attribute).

    Returns:
        ``True`` when this call performed the install, ``False`` when it
        was already installed (or the service carries no search
        reference — nothing to dedupe then).
    """
    if service is None:
        return False
    if getattr(service, "_skill_identity_dedupe_installed", False):
        return False
    inner = getattr(service, "_search_service", None)
    if inner is None:
        return False
    if isinstance(inner, _IdentityDedupingSearchProxy):
        # Already wrapped (e.g. two call sites raced) — keep the
        # existing wrapper rather than stacking a second one.
        try:
            service._skill_identity_dedupe_installed = True
        except Exception:  # pragma: no cover - defensive
            pass
        return False
    try:
        service._search_service = _IdentityDedupingSearchProxy(inner)
        service._skill_identity_dedupe_installed = True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "[SkillIdentityDedupe] Could not install the identity dedupe: %s",
            exc,
        )
        return False
    logger.debug(
        "[SkillIdentityDedupe] Identity dedupe installed on the injection "
        "boundary (one variant per (name, project_id))."
    )
    return True


async def inject_skills_deduped(
    service: Any,
    user_message: str,
    *,
    project_id: str | None = None,
    instance_id: str | None = None,
    message_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """Inject skills with at most one variant per identity.

    This is the SINGLE entry point both injection resolution sites use
    (:mod:`daemon.services.instance_messaging` and
    :mod:`daemon.services.context_messages`).  The dedupe logic lives
    here, once; the call sites never re-implement it.

    Args:
        service: The manager's ``_skill_injection_service``.
        user_message: The raw message text handed to the search.
        project_id: The project scope of the search.
        instance_id: The receiving instance (A/B hash key + file scope).
        message_id: The queue message id (A/B hash key).
        agent_id: The receiving agent (owner filter).

    Returns:
        The injector's ``(injection_text, injected_skill_ids)`` tuple.
    """
    install_identity_dedupe(service)
    return await service.inject_skills(
        user_message,
        project_id=project_id,
        instance_id=instance_id,
        message_id=message_id,
        agent_id=agent_id,
    )
