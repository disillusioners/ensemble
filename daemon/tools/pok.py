"""POK tools — project operating knowledge write/read/curate (Phase 1 T4).

Three tools (arch v3.4 §4.2 / §4.3 / §4.6; category ``pok``,
NON-privileged — grants flow through the per-agent ``tools.allow``
entries landed by the B4 sweep; authorization differences are
IN-BODY role gates from the FACTORY-CAPTURED ``agent_id``
(attribution ≠ authorization — the two-layer gate)):

* :func:`pok_upsert` — any agent on a non-default project writes;
  the writer STATES the audience classification at capture
  (default ``operation`` — the channel's genre) and MAY state the
  trust tier (Phase 2, addendum T-1: ``source_tier`` default
  ``agent``; ``curated`` is leader/PM-only). Write-time fence +
  escape-aware budget + exact-dup echo + dispute-laundering guard +
  supersede transaction + admission caps (active-entry
  refuse-never-evict; family >3). Supersede-of-PINNED or
  -of-CURATED is leader/PM-only (the guard predicate widened in
  Phase 2, addendum T-3; an omitted tier on supersede INHERITS the
  target's tier).
* :func:`pok_query` — ONE step, project-scoped, selector-reuse.
  UNIVERSAL READ INVARIANT (ER-1.4, generalized by addendum T-4):
  NO audience filter and NO tier filter — every agent on the
  project reads every entry; audience and source_tier gate ACTIVE
  injection only. Family chains in slug order; pinned bypass;
  typed errors; embedding lag surfaced; ``[CURATED]`` renders in
  the lineage surface only (no in-block marker in v1).
* :func:`pok_curate` — leader/PM only (pinned | disputed | archived
  | audience | audience_agents | source_tier — the tier flip is
  Phase 2, both directions, addendum T-3). Pin cap 2 PER PROJECT;
  disputed suppresses injection but stays readable; archived
  forward-only.

PROJECT SCOPING (server-side): every tool resolves the CALLING
instance's project from the instance row (the
``self_alarm._resolve_project_id`` precedent) — there is no
``project_id`` tool argument, so an agent cannot scope a call at a
project it is not linked to. The default project is refused with
typed errors BOTH directions (A-5, AC10).

Recording surface: typed echo + ``[POK]`` logger lines (the daemon
log is what ``ens_system_log`` reads).

Engine discipline (memoir): the factory reads ONLY
``manager._engine`` and ``manager._instance_repository``, both
defensively — a test double without them yields typed refusals,
never raises. The service/repository never build an engine.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from ..services.pok_service import (
    POKError,
    POK_ACTIVE_CAP_PER_PROJECT,
    POKService,
    is_default_project,
    is_curator,
)
from ._tool_registry import register_tool_category

__all__ = ["CATEGORY_DOC", "CATEGORY_NAME", "create_pok_tools"]

logger = logging.getLogger(__name__)

CATEGORY_NAME = "pok"
CATEGORY_DOC = (
    "Project operating knowledge (POK) — ordered operating procedures "
    "per project. pok_upsert writes a fenced single-paragraph fact "
    "(stating its audience: operation | implementation | both; "
    "optionally source_tier: agent | curated — curated is leader / "
    "project-manager only); pok_query reads ALL entries of the "
    "project (universal read — audience and tier never filter a "
    "read); pok_curate (leader / project-manager only) pins, "
    "disputes, archives, reclassifies audience, or flips the trust "
    "tier."
)


def _resolve_project_id(instance_repository: Any, current_instance_id: str) -> str | None:
    """Resolve the caller's project from the instance row (the
    ``self_alarm._resolve_project_id`` precedent, mirrored)."""
    if instance_repository is None or not current_instance_id:
        return None
    try:
        instance = instance_repository.get(current_instance_id)
    except Exception as exc:  # noqa: BLE001 — degraded read REFUSES, never scopes wrong
        logger.warning(
            "pok: cannot resolve project_id for instance %s: %s",
            current_instance_id,
            exc,
        )
        return None
    if instance is None:
        return None
    project_id = getattr(instance, "project_id", None)
    return str(project_id) if project_id else None


def _project_name(instance_repository: Any, project_id: str | None) -> str | None:
    """The project's name (the is_system_default name fallback needs it)."""
    if instance_repository is None or not project_id:
        return None
    project_repo = getattr(instance_repository, "_project_repository", None)
    if project_repo is None:
        return None
    try:
        project = project_repo.get(project_id)
    except Exception:  # noqa: BLE001 — defensive
        return None
    return getattr(project, "name", None) if project is not None else None


def _err(code: str, message: str, **details: Any) -> dict:
    payload = {"error": message, "code": code}
    payload.update({k: v for k, v in details.items() if v is not None})
    return payload


def create_pok_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
) -> list[Any]:
    """Build the three POK tools for one instance/agent.

    ``agent_id`` is the FACTORY-CAPTURED attribution + the in-body
    role gate's input (``create_instance_tools(..., agent_id=...)``
    parameter — never a caller-supplied key at call time).
    """
    engine = getattr(manager, "_engine", None)
    instance_repository = getattr(manager, "_instance_repository", None)
    # Captured agent id (factory param first — the proven in-body
    # pattern; the instance row's agent_id is the fallback so a
    # test/spawn path that omitted the param still attributes
    # honestly).
    captured_agent_id = agent_id or ""
    if not captured_agent_id and instance_repository is not None and current_instance_id:
        try:
            row = instance_repository.get(current_instance_id)
            captured_agent_id = getattr(row, "agent_id", "") or ""
        except Exception:  # noqa: BLE001 — defensive
            captured_agent_id = ""

    def _service() -> POKService | None:
        if engine is None:
            return None
        return POKService(engine)

    def _scope() -> tuple[str | None, str | None, dict | None]:
        """(project_id, project_name, refusal-dict-or-None)."""
        project_id = _resolve_project_id(instance_repository, current_instance_id)
        if not project_id:
            return None, None, _err(
                "pok_no_project",
                "this instance is not linked to a project — "
                "project_operating_knowledge is project-scoped",
            )
        name = _project_name(instance_repository, project_id)
        if is_default_project(project_id, name):
            return None, name, _err(
                "pok_default_project",
                "project_operating_knowledge is scoped to non-default "
                "projects — the system-default project accepts no POK "
                "calls (AC10)",
            )
        return project_id, name, None

    def _substitute_active_cap(fn: Any) -> Any:
        """Substitute the LIVE admission cap into the docstring BEFORE
        ``@tool`` bakes it into the tool description (and any
        ``args_schema`` doc).

        This runs on the PLAIN function object — a pure string op on
        our own ``__doc__``, never a post-construction attribute write
        on the tool object (an upstream ``frozen=True`` on ``BaseTool``
        would otherwise break every instance spawn, since
        ``create_pok_tools`` runs bare in the spawn path).

        Load-bearing: the constant is read from the MODULE GLOBAL
        inside the body. It must NOT be captured as a default argument
        — a default binds at ``def`` time and would freeze the value,
        defeating the discriminating test that patches
        ``daemon.tools.pok.POK_ACTIVE_CAP_PER_PROJECT``.
        """
        if fn.__doc__:
            fn.__doc__ = fn.__doc__.replace(
                "<active_cap>", str(POK_ACTIVE_CAP_PER_PROJECT)
            )
        return fn

    @register_tool_category(CATEGORY_NAME)
    @tool
    @_substitute_active_cap
    def pok_upsert(
        slug: str,
        body: str,
        domain: str = "other",
        provenance: str = "observed",
        audience: str = "operation",
        source_tier: str | None = None,
    ) -> dict:
        """Write one operating-knowledge fact for THIS project.

        Use tool_help() for details. Behavior:
        - ``slug``: kebab-case [a-z0-9-], <=44 chars. Family-ordered
          slugs follow ``<family>-<NN>-<tail>`` (e.g.
          ``pipeline-01-merge-master``); members of a family render
          adjacently.
        - ``body``: ONE paragraph, <=240 chars measured AFTER
          escaping. Newlines, leading '#', '[SYSTEM CONTEXT:' and
          triple-backticks are REFUSED at write (the content fence).
        - ``audience`` (stated at capture, default 'operation'):
          operation → {ari, leader, devops, sonic-leader,
          sonic-developer}; implementation → {leader, developer,
          sonic-developer}; both → every agent on the project.
          Curator-narrow override is pok_curate's ``audience_agents``.
        - ``source_tier`` (default 'agent'): omit for agent-captured
          facts. ``'curated'`` marks a dev-curated immutable
          reference (a guideline) and is LEADER / PROJECT-MANAGER
          ONLY — one call captures a curated guideline. Non-curators
          passing 'curated' are refused (pok_tier_requires_curator).
          On a supersede, an omitted tier INHERITS the target's tier
          (content refresh never reclassifies authority); tier flips
          on a live row are pok_curate's ``source_tier``.
        - Same slug + same content = no-op echo. Same slug + changed
          content = SUPERSEDE (the prior row flips superseded with
          its lineage wire; the successor starts unpinned).
          Superseding a PINNED or CURATED entry requires leader /
          project-manager.
        - Admission is refuse-never-evict: at <active_cap> active
          entries the call refuses naming the archive cure; a family's
          4th active member refuses (pok_family_full).
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than writing blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        try:
            from ..services.context_messages import (  # noqa: PLC0415
                escape_for_context_block,
            )

            outcome = svc.upsert(
                project_id=project_id,
                slug=slug,
                body=body,
                domain=domain,
                provenance=provenance,
                audience=audience,
                source_tier=source_tier,
                agent_id=captured_agent_id,
                project_name=project_name,
                escape_fn=escape_for_context_block,
            )
        except POKError as exc:
            return _err(exc.code, exc.message, **exc.details)
        except Exception as exc:  # noqa: BLE001 — M13
            logger.exception("pok_upsert store error")
            return _err("pok_store_error", f"POK store error: {type(exc).__name__}")
        entry = outcome.entry
        logger.info(
            "[POK] upsert action=%s slug=%s project=%s by=%s audience=%s "
            "tier=%s",
            outcome.action,
            entry.slug,
            project_id,
            captured_agent_id or "unknown",
            entry.audience,
            entry.source_tier,
        )
        return {
            "action": outcome.action,
            "id": entry.id,
            "slug": entry.slug,
            "domain": entry.domain,
            "audience": entry.audience,
            "source_tier": entry.source_tier,
            "status": entry.status,
            "created_by_agent": entry.created_by_agent,
            **(
                {"superseded_id": outcome.superseded_id}
                if outcome.superseded_id
                else {}
            ),
            **(
                {
                    "note": "exact duplicate of the active entry — no "
                    "new row written"
                }
                if outcome.action == "no-op"
                else {}
            ),
        }

    @register_tool_category(CATEGORY_NAME)
    @tool
    def pok_query(query: str = "", domain: str | None = None) -> dict:
        """Read THIS project's operating knowledge (one step).

        Use tool_help() for details. UNIVERSAL READ: returns entries
        of ALL audiences to any agent on the project — the audience
        tags gate ACTIVE injection only, never a read (injection is
        curated; reading is free).

        Behavior:
        - ``query``: natural-language ranking (BM25→vector fusion on
          the shared selector; pinned rows bypass the threshold and
          always load). Empty query = the ranked baseline (pinned +
          floor).
        - ``domain``: optional filter (artifact | deploy | verify |
          environment | catalog | other).
        - Superseded lineages ride along per entry (``lineage``);
          ``embedding_lag`` names entries ranked BM25-only this call
          (their vector is still minting — never fatal).
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than reading blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        try:
            outcome = svc.query(
                project_id=project_id,
                query=query,
                domain=domain,
                agent_id=captured_agent_id,
                project_name=project_name,
            )
        except POKError as exc:
            return _err(exc.code, exc.message, **exc.details)
        except Exception as exc:  # noqa: BLE001
            logger.exception("pok_query store error")
            return _err("pok_store_error", f"POK store error: {type(exc).__name__}")
        entries = []
        for e in outcome.entries:
            item = {
                "id": e.id,
                "slug": e.slug,
                "domain": e.domain,
                "audience": e.audience,
                "body": e.body,
                "status": e.status,
                "disputed": e.disputed,
                "pinned": e.pinned,
                "source_tier": e.source_tier,
                "provenance": e.provenance,
                "created_by_agent": e.created_by_agent,
                "verify_count": e.verify_count,
                "access_count": e.access_count,
                "last_verified": e.last_verified,
            }
            chain = outcome.lineage.get(e.slug)
            if chain:
                # The lineage surface is where [CURATED] renders
                # (trust-tier addendum T-4 — query lineage only; the
                # ACTIVE-injection block carries NO in-block marker in
                # v1, the band position signals status). Universal read
                # across tiers: source_tier is visible to every agent,
                # it never filters a read.
                item["lineage"] = [
                    {
                        "id": r.id,
                        "status": r.status,
                        "superseded_by_id": r.superseded_by_id,
                        "body": r.body,
                        "source_tier": r.source_tier,
                        **(
                            {"marker": "[CURATED]"}
                            if r.source_tier == "curated"
                            else {}
                        ),
                    }
                    for r in chain
                ]
            entries.append(item)
        if outcome.bm25_only:
            logger.info(
                "[POK] query bm25_only=true (no query embedding) "
                "project=%s results=%d",
                project_id,
                len(entries),
            )
        result: dict[str, Any] = {
            "entries": entries,
            "count": len(entries),
            "bm25_only": outcome.bm25_only,
        }
        if outcome.embedding_lag_entries:
            result["embedding_lag"] = outcome.embedding_lag_entries
        return result

    @register_tool_category(CATEGORY_NAME)
    @tool
    def pok_curate(
        slug: str,
        pinned: bool | None = None,
        disputed: bool | None = None,
        archived: bool | None = None,
        audience: str | None = None,
        audience_agents: list[str] | None = None,
        source_tier: str | None = None,
    ) -> dict:
        """Curate THIS project's operating knowledge (leader /
        project-manager ONLY).

        Use tool_help() for details. Flips (all optional, combinable):
        - ``pinned``: core tier (ALWAYS injected first). Cap 2 per
          project — the 3rd pin refuses naming the current pins.
        - ``disputed``: TRUE suppresses ACTIVE injection and marks
          the entry; it stays READABLE via pok_query. Re-adding the
          same content under a new slug while disputed is refused
          (laundering guard).
        - ``archived``: TRUE retires the entry. FORWARD-ONLY — an
          archived entry cannot return (write a successor instead).
        - ``audience`` / ``audience_agents``: reclassification /
          curator narrowing. ``audience_agents`` REPLACES the
          audience's default set when non-empty; pass [] to clear
          back to the default set.
        - ``source_tier``: trust-tier reclassification, both
          directions ('agent' ↔ 'curated'). 'curated' marks a
          dev-curated immutable reference — it renders in the
          dedicated curated band (cap 2 per view) and requires
          curator authority to supersede.
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than curating blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        # The in-body role gate is ALSO stated here (the service
        # enforces it from the same captured id; the explicit check
        # gives the tool surface its own typed echo).
        if not is_curator(captured_agent_id):
            return _err(
                "pok_curator_required",
                "pok_curate is leader / project-manager only — "
                f"attribution {captured_agent_id!r} is not a curator "
                "(attribution ≠ authorization)",
            )
        try:
            row = svc.curate(
                project_id=project_id,
                slug=slug,
                agent_id=captured_agent_id,
                pinned=pinned,
                disputed=disputed,
                archived=archived,
                audience=audience,
                audience_agents=audience_agents,
                source_tier=source_tier,
                project_name=project_name,
            )
        except POKError as exc:
            return _err(exc.code, exc.message, **exc.details)
        except Exception as exc:  # noqa: BLE001 — M13
            logger.exception("pok_curate store error")
            return _err("pok_store_error", f"POK store error: {type(exc).__name__}")
        # M12: curate() can return None — guard the dereference.
        if row is None:
            return _err("pok_not_found", f"entry {slug!r} vanished during curate")
        logger.info(
            "[POK] curate slug=%s project=%s by=%s "
            "pinned=%s disputed=%s archived=%s audience=%s audience_agents=%s "
            "tier=%s",
            row.slug,
            project_id,
            captured_agent_id,
            row.pinned,
            row.disputed,
            row.status,
            row.audience,
            row.audience_agents,
            row.source_tier,
        )
        return {
            "action": "curated",
            "id": row.id,
            "slug": row.slug,
            "pinned": row.pinned,
            "disputed": row.disputed,
            "status": row.status,
            "audience": row.audience,
            "audience_agents": row.audience_agents,
            "source_tier": row.source_tier,
        }

    return [pok_upsert, pok_query, pok_curate]
