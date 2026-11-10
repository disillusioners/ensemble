"""Dynamic skill tools for searching, listing, viewing, creating, fixing,
and providing feedback on dynamically-evolved skills.

Mirrors the closure-injection pattern of ``daemon.tools.todo_tools`` and
``daemon.tools.chart_tools``: ``create_skill_tools(manager,
current_instance_id)`` is invoked from ``create_instance_tools`` to
assemble the per-instance tool list. The 6 tools delegate to
``manager._skill_search_service`` and ``manager._skill_store_service`` for
DB reads/writes and to ``manager._skill_job_dispatcher`` for the user-facing
fix flow.

Service-availability model
--------------------------

The skill-evolution services are wired to the ``InstanceManager``
incrementally across phases. To keep the tool layer usable in any
intermediate state, every tool follows a "soft-fail" pattern:

* If the underlying service is missing (``getattr`` returns ``None``), the
  tool returns a clear "not yet available" message — never raises.
* If the service raises, the tool catches, logs, and returns an
  ``ERROR: ...`` string so the agent sees a tool response (not a stack trace).

Tools produced
--------------

* ``skill_search`` — semantic search across skills.
* ``skill_list`` — list skills in the active project scope.
* ``skill_view`` — view one skill's full body + lineage.
* ``skill_create`` — create a new skill row.
* ``skill_fix`` — user-facing fix request (dispatched, never invoked inline).
* ``skill_feedback`` — Phase 2 stub; replaced by Phase 4's
  ``SkillMetricsService.record_feedback``.
"""

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from daemon.repositories.project_skill_file.models import (
    ProjectSkillUsageRecord,
)
from daemon.services.project_skill_source.model import (
    ADDRESS_PREFIX,
    PSEUDO_ID_PREFIX,
    SOURCE_PROJECT_FILE,
    is_pf_address,
    mint_address,
)
from daemon.services.project_skill_source.service import (
    ProjectSkillBodyUnavailable,
)
from daemon.services.skill_job_dispatcher import SkillFixBurstCapRefused
from daemon.services.skill_search_service import _bm25_rank
from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Dynamic Skill"
CATEGORY_DOC = """\
Dynamic-skill tools for searching, listing, viewing, creating, fixing,
and providing feedback on dynamically-evolved skills.

- skill_search — semantic search across the active project + global corpus.
- skill_list — list skills (project + global) with optional category filter.
- skill_view — view one skill's full body + lineage graph.
- skill_create — create a new skill row (delegates to skill_store_service).
- skill_fix — user-facing fix request; dispatched to skill_job_dispatcher.
- skill_feedback — Phase 2 stub; Phase 4 SkillMetricsService will replace it.

All tools soft-fail when their underlying service is not yet wired to the
manager — they return a clear "not yet available" message rather than
raising. The dynamic-skill category is auto-granted to agents with
innate_skills:["dynamic-skill"] via INNATE_SKILL_TOOL_CATEGORIES.
"""


def _json_default(obj):
    """JSON encoder fallback for SQLModel / dataclass-like objects.

    Calls ``to_dict()`` when available; falls back to ``__dict__``;
    falls back to ``str(obj)`` as a last resort. Keeps the JSON
    payload readable without hard-coding a SQLModel dependency in
    this module.
    """
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        return d
    return str(obj)


# ============================================================
# File-backed project-skill surface (Phase-3, project-skill-loader)
# ============================================================
#
# Manager attributes consumed (all None-tolerant; Phase-4 construction
# wiring populates them — names documented here are the Phase-4
# contract):
#
# * ``manager._project_skill_source``          — ProjectSkillSourceService
# * ``manager._project_skill_file_repository`` — ProjectSkillFileRepository
#   (invalid-rows mirror for ``skill_list``; ``skill.json`` D-Q2 rows)
# * ``manager._project_skill_usage_repository``— ProjectSkillUsageRecordRepository
#   (D2 companion table — metrics-only ``skill_feedback`` on ``pf:``)
#
# Before Phase-4 wiring every attribute is absent ⇒ all file-skill
# branches below soft-fail exactly like the DB branches (A9
# None-tolerance, approver P4).

#: Uniform refusal wording for every evolution-facing surface handed a
#: ``pf:`` address (P7: file skills evolve via git PRs only; mirrors
#: the ``skill_fix`` tool refusal — architect A14).
PF_REFUSAL_TEMPLATE = (
    "File-backed skills evolve via git PRs against `.agents/<ns>/skills/` — "
    "{action} does not apply to `pf:` addresses (P7)."
)


def _pf_refusal(action: str) -> str:
    return f"⛔ {PF_REFUSAL_TEMPLATE.format(action=action)}"


def _pf_skill_payload(file_skill: Any) -> dict[str, Any]:
    """JSON-safe payload for one file skill (search/view surfaces).

    Mirrors the DB ``skill.to_dict()`` shape (name/description/content
    present) plus the additive file keys (``source`` / ``address`` /
    ``digest`` / namespace / paths). ``content`` is the FRESH disk body
    attached by ``corpus_for``/``lookup_explicit`` — never cached
    (FR-P1-3).
    """
    manifest = getattr(file_skill, "manifest", None) or {}
    return {
        "id": getattr(file_skill, "address", None),
        "pseudo_id": getattr(file_skill, "id", None),
        "name": getattr(file_skill, "name", ""),
        "description": getattr(file_skill, "description", ""),
        "content": getattr(file_skill, "content", ""),
        "category": getattr(file_skill, "category", None),
        "version": str(manifest.get("version", "") or ""),
        "tiers": manifest.get("tiers", []),
        "namespace": getattr(file_skill, "namespace", ""),
        "address": getattr(file_skill, "address", ""),
        "project_id": getattr(file_skill, "project_id", None),
        "root_path": getattr(file_skill, "root_path", ""),
        "body_path": getattr(file_skill, "body_path", ""),
        "digest": getattr(file_skill, "digest", ""),
        "shadows": list(getattr(file_skill, "shadows", []) or []),
        "source": SOURCE_PROJECT_FILE,
    }


async def _file_only_bm25_search(
    file_source: Any,
    query: str,
    instance_id: str,
    limit: int,
    project_id: str | None,
) -> dict[str, Any]:
    """A9 pinned degraded semantics: file-only search when the DB stack
    is absent (mode M10 shape, ``manager._skill_search_service is
    None``).

    Stage-1 BM25 over the FILE corpus ONLY — same tokenizer, same
    scorer (the shared :func:`~daemon.services.skill_search_service._bm25_rank`
    loop, imported so the two can never drift) — ``top_k = limit``, NO
    stage 2, NO stage-3 LLM. Deterministic, zero LLM cost, works with no
    evolution config. Results use the standard ``{"injected", "low_match"}``
    shape with ``source: "project-file"`` on every entry plus a
    ``degraded: "file_only"`` marker on the result dict.
    """
    empty = {"injected": [], "low_match": [], "degraded": "file_only"}
    try:
        resolution = await file_source.resolve_active_project(
            instance_id=instance_id,
            explicit_project_id=project_id,
        )
    except Exception as exc:  # A8: broken file source ⇒ empty, no raise
        logger.warning(
            "[skill_search] file-only resolution failed: %s — empty result",
            exc,
        )
        return empty
    scope_pid = getattr(resolution, "project_id", None)
    roots = [r for r in (getattr(resolution, "roots", None) or []) if r]
    if not scope_pid or getattr(resolution, "refused", False) or not roots:
        return empty

    corpus: list[Any] = []
    for root in roots:
        try:
            corpus.extend(await file_source.corpus_for(scope_pid, root) or [])
        except Exception as exc:  # per-root resilience (A8 layer 1)
            logger.warning(
                "[skill_search] file-only corpus failed for root=%s: %s",
                root,
                exc,
            )
    if not corpus:
        return empty

    # A3(b): the ONE BM25 loop (no local tokenizer/scorer copy). The
    # full ranked list is requested because the low-match tail renders
    # the first three entries PAST the injection cap.
    scored = _bm25_rank(corpus, query)
    top = scored[: max(1, limit)]
    injected = [
        {
            "skill": _pf_skill_payload(skill),
            "score": score,
            "source": SOURCE_PROJECT_FILE,
        }
        for score, skill in top
    ]
    low_match = [
        {
            "name": getattr(skill, "name", ""),
            "score": score,
            "description": getattr(skill, "description", ""),
            "source": SOURCE_PROJECT_FILE,
            "address": getattr(skill, "address", ""),
        }
        for score, skill in scored[max(1, limit):][:3]
    ]
    return {"injected": injected, "low_match": low_match, "degraded": "file_only"}


def create_skill_tools(
    manager: "InstanceManager", current_instance_id: str
) -> list:
    """Create dynamic-skill tools with injected manager reference.

    Args:
        manager: The :class:`InstanceManager` instance. May expose
            ``_skill_search_service``, ``_skill_store_service``, and
            ``_skill_job_dispatcher`` (Phase 2 wiring) — if any of those
            are missing the matching tool(s) return a "not yet available"
            message rather than raising.
        current_instance_id: The ID of the calling instance. Captured in
            the closure for project_id auto-injection.

    Returns:
        List of 6 tool functions in this exact order:
        ``[skill_search, skill_list, skill_view, skill_create, skill_fix,
        skill_feedback]``.
    """

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context. Returns None on any failure.

        A7 (tidier fold + LEADER RULING — the SERVICE's precedence
        WINS): ``instance_metadata["project_id"]`` FIRST, then the
        ``instances.project_id`` row column. That is the exact drift
        order :meth:`ProjectSkillSourceService.resolve_active_project`
        uses (D-Q1). This helper used to read the ROW ONLY, so on a
        drifted instance the DB-skill tools saw one project while the
        file-skill resolver derived another — and the resolver then
        REFUSED the file corpus as an A6 "explicit project conflict"
        on a pill the caller never supplied. One precedence, one
        project, both halves of the call.
        """
        try:
            repo = getattr(manager, "_instance_repository", None)
            if repo is None:
                return None
            instance_meta = repo.get(current_instance_id)
            if instance_meta is None:
                return None
            meta = getattr(instance_meta, "instance_metadata", None) or {}
            meta_pid = meta.get("project_id") if isinstance(meta, dict) else None
            if meta_pid:
                return str(meta_pid)
            row_pid = getattr(instance_meta, "project_id", None)
            if row_pid:
                return str(row_pid)
        except Exception as e:
            logger.debug("project_id lookup failed in dynamic-skill tool: %s", e)
        return None

    def _get_agent_id() -> str | None:
        """Resolve the agent_id for the calling instance.

        Phase 4 helper used by ``skill_feedback`` so the metrics
        service can stamp the agent on the usage record. Reads the
        instance row's ``agent_id`` (set at instance creation time).
        Returns ``None`` on any failure — the metrics service treats
        ``None`` and ``""`` symmetrically.
        """
        try:
            repo = getattr(manager, "_instance_repository", None)
            if repo is None:
                return None
            instance_meta = repo.get(current_instance_id)
            if instance_meta is not None:
                return getattr(instance_meta, "agent_id", None)
        except Exception as e:
            logger.debug("agent_id lookup failed in dynamic-skill tool: %s", e)
        return None

    # --------------------------------------------------------
    # File-backed project-skill helpers (Phase-3). All manager
    # attributes are None-tolerant — pre-Phase-4 wiring they are
    # absent and every branch below soft-fails (A9 / approver P4).
    # --------------------------------------------------------

    def _get_file_source() -> Any:
        """The file-backed project skill source (Phase-4 wires
        ``manager._project_skill_source``; ``None`` ⇒ file surface
        soft-fails)."""
        return getattr(manager, "_project_skill_source", None)

    def _get_pf_file_repo() -> Any:
        """``project_skill_files`` mirror repo (invalid-rows surface for
        ``skill_list``). Phase-4 wires
        ``manager._project_skill_file_repository``."""
        return getattr(manager, "_project_skill_file_repository", None)

    def _get_pf_usage_repo() -> Any:
        """D2 companion usage repo (metrics-only ``skill_feedback`` on
        ``pf:``). Phase-4 wires
        ``manager._project_skill_usage_repository``."""
        return getattr(manager, "_project_skill_usage_repository", None)

    async def _resolve_pf_scope(
        explicit_project_id: str | None = None,
    ) -> tuple[str | None, list[str]]:
        """Resolve the file-skill scope through the SANCTIONED scope
        reader (A13). Returns ``(project_id, roots)`` or ``(None, [])``.

        ``skill_view`` passes NO explicit hint (A13: caller-supplied
        project hints are forbidden there); the search/list surfaces
        pass the instance-derived ``project_id`` which may only
        CONFIRM (A6), never widen.
        """
        file_source = _get_file_source()
        if file_source is None:
            return (None, [])
        try:
            resolution = await file_source.resolve_active_project(
                instance_id=current_instance_id,
                explicit_project_id=explicit_project_id,
            )
        except Exception as e:
            logger.warning(
                "[dynamic-skill] project-scope resolution failed: %s — "
                "file skills invisible for this call",
                e,
            )
            return (None, [])
        if getattr(resolution, "refused", False):
            return (None, [])
        pid = getattr(resolution, "project_id", None)
        roots = [r for r in (getattr(resolution, "roots", None) or []) if r]
        return (pid or None, roots)

    async def _ensure_pf_index(roots: list[str], pid: str) -> None:
        """``lookup_explicit`` searches only already-indexed roots —
        ensure_index FIRST (frozen Phase-3 design note)."""
        file_source = _get_file_source()
        for root in roots:
            try:
                await file_source.ensure_index(root, pid)
            except Exception as e:
                logger.warning(
                    "[dynamic-skill] ensure_index failed for root=%s: %s",
                    root,
                    e,
                )

    async def _render_pf_view(file_skill: Any, file_source: Any, pid: str) -> str:
        """Render the file-skill view document (task 3 + task 7).

        Header metadata (name / version / category / tiers / namespace
        / address / body_path / digest + S5 scope fields), body FROM
        FILE (fresh every call — no cache), ``## Loads`` (each
        dependency's ``pf:`` address + one-line description, Q6 v1)
        and ``## Assets`` sections. Uniform 8000-char display cap with
        the body_path + ``read_file`` hint (file skills have real
        paths); the BODY ON DISK stays full and the INJECTION path
        renders it uncapped (FR-P5-3 parity).
        """
        manifest = getattr(file_skill, "manifest", None) or {}
        root = getattr(file_skill, "root_path", "") or ""
        body_path = getattr(file_skill, "body_path", "") or ""
        abs_body = os.path.join(root, body_path) if root and body_path else ""
        lines: list[str] = []
        lines.append(f"# {getattr(file_skill, 'name', '<unknown>')}")
        lines.append("")
        lines.append(f"- address: {getattr(file_skill, 'address', '')}")
        # E3.3 (board wi-d340f16c bullet 3 — display hygiene): the
        # agent-facing ``- id:`` line must carry the CONSUMABLE address
        # (``pf:<ns>:<name>``), never the internal ``pf-<sha16>``
        # pseudo-id. The pseudo-id stays available ADDITIVELY under
        # ``- pseudo_id:`` (the internal key continuity the injection
        # path and the embedding rows depend on).
        lines.append(f"- id: {getattr(file_skill, 'address', '')}")
        lines.append(f"- pseudo_id: {getattr(file_skill, 'id', '')}")
        lines.append(f"- namespace: {getattr(file_skill, 'namespace', '')}")
        lines.append(f"- project_id: {getattr(file_skill, 'project_id', '')}")
        lines.append(f"- root_path: {root}")
        lines.append(f"- version: {str(manifest.get('version', '') or '?')}")
        lines.append(
            f"- category: {getattr(file_skill, 'category', None) or '(none)'}"
        )
        tiers = manifest.get("tiers", [])
        lines.append(f"- tiers: {tiers if tiers else '(none)'}")
        lines.append(f"- body_path: {abs_body or body_path}")
        lines.append(f"- digest: {getattr(file_skill, 'digest', '')}")
        lines.append("- source: project-file")
        lines.append(
            "- evolves via: git PR against `.agents/<ns>/skills/` "
            "(skill_fix does not apply, P7)"
        )
        description = getattr(file_skill, "description", "") or ""
        if description:
            lines.append("")
            lines.append(f"> {description}")
        content = getattr(file_skill, "content", "") or ""
        if content:
            lines.append("")
            lines.append("## Content")
            lines.append("")
            if len(content) > 8000:
                lines.append(content[:8000])
                lines.append("")
                lines.append(
                    "... (truncated at 8000 chars)"
                )
            else:
                lines.append(content)
        # ---- ## Loads (task 7 / Q6 v1) ----------------------------
        loads = manifest.get("loads") or []
        if loads:
            lines.append("")
            lines.append("## Loads")
            lines.append("")
            ns = getattr(file_skill, "namespace", "")
            for dep in loads:
                dep_address = mint_address(ns, dep)
                dep_desc = ""
                try:
                    dep_skill = await file_source.lookup_explicit(dep, pid)
                    if dep_skill is not None:
                        dep_desc = getattr(dep_skill, "description", "") or ""
                except Exception as e:
                    logger.debug(
                        "[skill_view] load-description lookup failed for "
                        "%s: %s",
                        dep_address,
                        e,
                    )
                lines.append(
                    f"- {dep_address}"
                    f"{' — ' + dep_desc if dep_desc else ''}"
                )
            lines.append(
                "    (explicit load_skill resolves depth-0 only — loads "
                "are pointers, never auto-injected)"
            )
        assets = manifest.get("assets") or []
        if assets:
            lines.append("")
            lines.append("## Assets")
            lines.append("")
            for asset in assets:
                lines.append(f"- {os.path.join(root, str(asset))}")
        if abs_body:
            lines.append("")
            lines.append(
                f"Full body on disk: {abs_body} — read it via the "
                "read_file tool for the untruncated document."
            )
        return "\n".join(lines)

    async def _feedback_pf_skill(
        skill_id: str,
        applied: bool | None,
        usefulness: int | None,
        note: str,
        improvement_note: str,
    ) -> str:
        """D2 companion metrics-only path (task 6, P7).

        ``pf:`` addresses (and the ``pf-<hex>`` pseudo-ids explicit
        injection renders) record feedback into
        ``project_skill_usage_records`` — NO lineage rows, NO
        evolution dispatch, NO ``SkillMetricsService`` call. Works
        with the DB evolution stack absent (A9 degraded mode). The
        companion table has no dedicated usefulness/improvement
        columns (D2 migration is frozen), so those fields fold into
        ``feedback_note`` — the usage signal survives; nothing else
        is written.
        """
        if usefulness is not None:
            if not isinstance(usefulness, int) or isinstance(usefulness, bool):
                return (
                    f"ERROR: skill_feedback 'usefulness' must be an int "
                    f"1-10, got {type(usefulness).__name__}: {usefulness!r}"
                )
            if usefulness < 1 or usefulness > 10:
                return (
                    f"ERROR: skill_feedback 'usefulness' must be in "
                    f"range 1-10, got {usefulness}"
                )
        usage_repo = _get_pf_usage_repo()
        if usage_repo is None:
            return (
                "Project-skill usage repository not yet available. "
                "File-skill feedback (metrics-only) will be enabled "
                "in a later phase."
            )
        pid, roots = await _resolve_pf_scope(_get_project_id())
        file_source = _get_file_source()
        pseudo_id: str | None = None
        address: str = skill_id
        if skill_id.startswith(ADDRESS_PREFIX):
            if file_source is None or not pid or not roots:
                return (
                    f"ERROR: skill_feedback: no project scope resolved "
                    f"for file skill '{skill_id}' — metrics cannot be "
                    "recorded (fail-closed)."
                )
            await _ensure_pf_index(roots, pid)
            try:
                fs = await file_source.lookup_explicit(skill_id, pid)
            except Exception as e:
                return f"ERROR: skill_feedback failed: {e}"
            if fs is None:
                return (
                    f"ERROR: skill_feedback: no file skill with address "
                    f"'{skill_id}' in this project scope."
                )
            pseudo_id = str(getattr(fs, "id", "") or "")
            address = getattr(fs, "address", skill_id)
        else:
            # The ``pf-<hex16>`` pseudo-id the injection path renders.
            #
            # W5 (code-review fix-cycle): this branch USED to write a
            # usage row for ANY caller string shaped ``pf-…`` — the
            # "nothing is written under OFF" invariant was false, and a
            # typo'd/hostile id could mint a durable row. Require (a)
            # the knob to be ON (a file source exists) and (b) the id
            # to be a REAL indexed file skill of the resolved project.
            if file_source is None or not pid or not roots:
                return (
                    f"ERROR: skill_feedback: no project scope resolved "
                    f"for file skill '{skill_id}' — metrics cannot be "
                    "recorded (fail-closed)."
                )
            await _ensure_pf_index(roots, pid)
            pf_file_repo = _get_pf_file_repo()
            if pf_file_repo is None:
                return (
                    "ERROR: skill_feedback: project-skill index "
                    f"unavailable — cannot validate '{skill_id}' "
                    "(fail-closed)."
                )
            try:
                known_row = await asyncio.to_thread(pf_file_repo.get, skill_id)
            except Exception as e:
                return f"ERROR: skill_feedback failed: {e}"
            if known_row is None or (
                getattr(known_row, "project_id", None) != pid
            ):
                return (
                    f"ERROR: skill_feedback: no indexed file skill with "
                    f"pseudo id '{skill_id}' in this project scope."
                )
            pseudo_id = skill_id
        parts: list[str] = []
        if note:
            parts.append(str(note))
        if improvement_note:
            parts.append(f"improvement: {improvement_note}")
        if usefulness is not None:
            parts.append(f"usefulness: {usefulness}/10")
        note_composite: str | None = " | ".join(parts) or None
        project_id = pid or _get_project_id() or ""
        agent_id = _get_agent_id() or ""
        try:
            latest = await asyncio.to_thread(
                usage_repo.get_latest_for_skill_instance,
                pseudo_id,
                current_instance_id,
            )
            if latest is None:
                row = ProjectSkillUsageRecord(
                    skill_id=pseudo_id,
                    project_id=project_id,
                    instance_id=current_instance_id,
                    agent_id=agent_id,
                    selected=False,
                    applied=False,
                    feedback_applied=applied,
                    feedback_note=note_composite,
                )
                await asyncio.to_thread(usage_repo.insert, row)
            else:
                await asyncio.to_thread(
                    usage_repo.update_feedback,
                    latest.id,
                    applied,
                    note_composite,
                )
        except Exception as e:
            return f"ERROR: skill_feedback failed: {e}"
        usefulness_line = (
            f" Usefulness: {usefulness}/10." if usefulness is not None else ""
        )
        return (
            f"\u2705 Feedback recorded (metrics-only) for file skill "
            f"{address}. Applied={applied}.{usefulness_line} No lineage "
            "rows, no evolution dispatch (P7)."
        )

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_search(query: str, limit: int = 10) -> str:
        """Search skills by natural-language query (delegates to skill_search_service).

        Args:
            query: Natural-language query to match against active skills.
            limit: Maximum number of injected skills to surface (default 10).
                Maps to ``SkillSearchService.search(max_results=...)``.
        """
        project_id = _get_project_id()
        service = getattr(manager, "_skill_search_service", None)
        if service is None:
            # A9 degraded-mode guard (task 10): DB stack absent but a
            # file source IS available ⇒ answer file-only (BM25-only,
            # standard shape + ``degraded: "file_only"`` marker) instead
            # of the blanket "not yet available". Both absent ⇒ soft-fail
            # as today.
            file_source = _get_file_source()
            if file_source is not None:
                try:
                    result = await _file_only_bm25_search(
                        file_source,
                        query,
                        current_instance_id,
                        limit,
                        project_id,
                    )
                    return json.dumps(
                        result, default=_json_default, indent=2
                    )
                except Exception as e:
                    return f"ERROR: skill_search failed: {e}"
            return (
                "Skill search service not yet available. "
                "Search will be enabled in a later phase."
            )
        try:
            result = await service.search(
                query,
                project_id=project_id,
                max_results=limit,
                agent_id=_get_agent_id(),
                # ACTIVATION (Phase-3, project-skill-loader): threading
                # the closure's ``current_instance_id`` turns the
                # Phase-2 fusion from dark to agent-lane-LIVE — the
                # service's scope resolver derives the caller's project
                # from it (A6/A13); ``None`` (pre-Phase-3) kept the file
                # corpus invisible on every tool call.
                instance_id=current_instance_id,
            )
            return json.dumps(result, default=_json_default, indent=2)
        except Exception as e:
            return f"ERROR: skill_search failed: {e}"

    skill_search._full_doc_ = """\
Search the project's active skills (plus the global library) by
natural-language query. Delegates to
:class:`SkillSearchService.search` — the three-stage pipeline (BM25
prefilter → embedding re-rank → LLM selection) — and returns the
standard ``{"injected": [...], "low_match": [...]}`` payload as a
JSON string.

PROJECT-FILE FUSION (Phase-3 activation): the tool threads its
closure ``current_instance_id`` into ``search(instance_id=...)`` so
the service's sanctioned scope resolver derives the caller's project
— file-backed skills (``.agents/*/skills/`` canon) fuse into the
corpus with ``source: "project-file"`` entries. No instance-derived
project scope ⇒ the file corpus is invisible for that call (DB
results unaffected).

DEGRADED FILE-ONLY MODE (A9): when the DB search service is absent
but a file source is wired, the tool answers file-only — stage-1
BM25 over the FILE corpus (same tokenizer), ``top_k = limit``, no
stage 2, no stage-3 LLM — in the standard shape with a
``degraded: "file_only"`` marker on the result dict.

Args:
    query: Natural-language query to match against active skills.
        Tokenized lowercased by the BM25 stage; semantic intent is
        captured by the embedding + LLM stages.
    limit: Maximum number of skills to surface in the ``injected``
        list. Default ``10`` (the tool-level cap; the underlying
        service's default is ``2``). Capped at the
        ``SkillEvolutionConfig.max_inject_skills`` value once that
        config is wired in.

Returns:
    JSON string on success. The shape is::

        {
          "injected": [
            {"skill": <Skill.to_dict()>, "score": <0.0-1.0>},
            ...
          ],
          "low_match": [
            {"name": str, "score": float, "description": str},
            ...
          ]
        }

    Empty ``injected`` and ``low_match`` lists when the corpus is
    empty or no skill scored above zero. Each ``Skill`` is rendered
    via its ``to_dict()`` method when present; bare ``str()`` repr is
    only used as a last-resort fallback. Soft-fails with the
    ``"Skill search service not yet available..."`` message when
    ``manager._skill_search_service`` is absent.
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_list(
        category: str | None = None, active_only: bool = True
    ) -> str:
        """List skills in the active project scope (delegates to skill_store_service).

        Args:
            category: Optional category filter (e.g. "workflow", "test").
            active_only: If True (default), only return skills with status="active".
        """
        project_id = _get_project_id()
        service = getattr(manager, "_skill_store_service", None)
        file_source = _get_file_source()
        if service is None and file_source is None:
            return (
                "Skill store service not yet available. "
                "List will be enabled in a later phase."
            )
        lines: list[str] = []
        if service is not None:
            try:
                items, total = await service.list_skills(
                    project_id=project_id, active_only=active_only, limit=100
                )
                if category is not None:
                    items = [it for it in items if it.get("category") == category]
                lines.append(f"Skills ({len(items)} of {total} total):")
                # W4 (code-review fix-cycle): the ``source: db`` tag is
                # ADDITIVE to the pre-lane line shape. Under the knob
                # OFF no file source exists, so no cross-source
                # disambiguation is needed — emit NOTHING extra and the
                # line stays byte-identical to the pre-lane output
                # (A11's "byte-identical RESULTS" claim restored).
                db_suffix = " — source: db" if file_source is not None else ""
                for it in items:
                    skill_id = it.get("id", "")
                    short_id = skill_id if isinstance(skill_id, str) else "????????"
                    name = it.get("name", "")
                    cat = it.get("category", "")
                    status = it.get("status", "")
                    lines.append(
                        f"- [{short_id}] {name} — {cat} — {status}{db_suffix}"
                    )
            except Exception as e:
                return f"ERROR: skill_list failed: {e}"
        # ---- File-backed project skills (task 4) ------------------
        # A9: when the DB store is absent but a file source exists,
        # the file section is the WHOLE answer (file-only list) rather
        # than the blanket "not yet available".
        if file_source is not None:
            try:
                pid, roots = await _resolve_pf_scope(project_id)
                file_lines: list[str] = []
                if pid and roots:
                    await _ensure_pf_index(roots, pid)
                    by_ns: dict[str, list[Any]] = {}
                    for root in roots:
                        try:
                            # A6: METADATA-ONLY — the list renders
                            # name/address/category/version, so no body
                            # read is paid (same W1 project-binding
                            # re-assert inside the accessor).
                            for fs in await file_source.metadata_for(pid, root) or []:
                                if category is not None and (
                                    getattr(fs, "category", None) != category
                                ):
                                    continue
                                by_ns.setdefault(
                                    getattr(fs, "namespace", ""), []
                                ).append(fs)
                        except Exception as e:  # per-root resilience
                            logger.warning(
                                "[skill_list] corpus failed for root=%s: %s",
                                root,
                                e,
                            )
                    # D-P4 same-name precedence within the file corpus:
                    # first contributing root wins (mirrors the search
                    # seam's dedupe rule).
                    seen_names: set[str] = set()
                    total_files = 0
                    for ns in sorted(by_ns):
                        for fs in by_ns[ns]:
                            name_key = (
                                str(getattr(fs, "name", "") or "")
                                .strip()
                                .lower()
                            )
                            if name_key and name_key in seen_names:
                                continue
                            if name_key:
                                seen_names.add(name_key)
                            total_files += 1
                            file_lines.append(
                                f"- [{ns}] {getattr(fs, 'address', '')} — "
                                f"{getattr(fs, 'category', None) or '(no category)'}"
                                f" — v{getattr(fs, 'version', '') or '?'} — "
                                "source: project-file"
                            )
                    header = (
                        f"Project-file skills ({total_files} in "
                        f"{len(by_ns)} namespaces):"
                    )
                    # Q7 fail-loud: invalid entries surfaced with
                    # reasons (indexed-as-listed mirror, D-Q2).
                    invalid_lines: list[str] = []
                    pf_file_repo = _get_pf_file_repo()
                    if pf_file_repo is not None:
                        try:
                            rows = await asyncio.to_thread(
                                pf_file_repo.list_by_project, pid
                            )
                            for row in rows or []:
                                if getattr(row, "valid", True):
                                    continue
                                invalid_lines.append(
                                    f"- {getattr(row, 'address', '')} — "
                                    f"invalid: {getattr(row, 'last_error', '')}"
                                )
                        except Exception as e:
                            invalid_lines.append(
                                f"- (invalid-row scan failed: {e})"
                            )
                    if file_lines or not lines:
                        lines.append("")
                        lines.append(header)
                        lines.extend(file_lines)
                    if invalid_lines:
                        lines.append("")
                        lines.append(
                            f"Invalid project-file skills ({len(invalid_lines)}, "
                            "excluded from corpus):"
                        )
                        lines.extend(invalid_lines)
            except Exception as e:
                logger.warning(
                    "[skill_list] project-file section failed: %s", e
                )
        return "\n".join(lines)

    skill_list._full_doc_ = """\
List skills in the active project scope (project + global overlay).

FILE SKILLS (Phase-3): file-backed project skills are listed
alongside DB skills — namespace-grouped ``[<ns>] pf:<ns>:<name>``
rows tagged ``source: project-file``. Invalid entries (failed
index-time validation) are surfaced with reasons under an
"Invalid project-file skills" section (Q7 fail-loud visibility) and
are excluded from the search corpus. When the DB store is absent
but a file source is wired, the file section is the whole answer
(A9 file-only mode) instead of a blanket "not yet available".

Delegates to :meth:`SkillStoreService.list_skills`, which returns a
metadata-only projection (``id``, ``name``, ``description``, ``category``,
``status``, ``created_at``, ``updated_at``) — no ``content`` body. Use
:meth:`skill_view` to fetch the full body of a single skill.

Project-scope semantics (from the underlying service):

* ``project_id=None`` → returns ONLY global skills.
* ``project_id="abc"`` → returns BOTH project-scoped and global skills.

Args:
    category: Optional free-form category filter (e.g. ``"workflow"``,
        ``"test"``). Applied client-side after the service returns
        because the SQL filter is on the underlying repo, not the
        service surface.
    active_only: When ``True`` (default), only skills with
        ``status="active"`` are returned. Set ``False`` to include
        ``inactive`` and other statuses.

Returns:
    Human-readable bullet list formatted as::

        Skills (N of TOTAL total):
        - [<id>] <name> — <category> — <status>
        - ...

    Soft-fails with the ``"Skill store service not yet available..."``
    message when ``manager._skill_store_service`` is absent.
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_view(skill_id: str) -> str:
        """View a skill's full body + lineage graph (delegates to skill_store_service).

        Args:
            skill_id: The skill's UUID4 identifier OR a ``pf:<ns>:<name>``
                project-file address.
        """
        # ---- File-skill branch (task 3) ---------------------------
        # ``pf:`` addresses never touch the DB store. A13: resolution
        # goes through the SANCTIONED resolver(current_instance_id) —
        # NO caller-supplied project hint.
        if is_pf_address(skill_id):
            file_source = _get_file_source()
            if file_source is None:
                return (
                    "Project skill source not yet available. "
                    "File-skill views will be enabled in a later phase."
                )
            try:
                pid, roots = await _resolve_pf_scope()
                if not pid or not roots:
                    return (
                        f"ERROR: skill_view: no project scope resolved for "
                        f"file skill '{skill_id}' — file skills are "
                        "invisible without project context (fail-closed)."
                    )
                await _ensure_pf_index(roots, pid)
                file_skill = await file_source.lookup_explicit(skill_id, pid)
            except ProjectSkillBodyUnavailable as exc:
                # Task 9 fail-closed: deleted/corrupt body between
                # index and view — never a stale body, never empty.
                return (
                    f"ERROR: skill_view: body unreadable: {exc.reason}; "
                    "re-index scheduled."
                )
            except Exception as e:
                return f"ERROR: skill_view failed: {e}"
            if file_skill is None:
                return (
                    f"ERROR: skill_view: no file skill with address "
                    f"'{skill_id}' in this project scope."
                )
            try:
                return await _render_pf_view(file_skill, file_source, pid)
            except Exception as e:
                return f"ERROR: skill_view failed: {e}"
        # ---- DB branch (unchanged behavior) -----------------------
        service = getattr(manager, "_skill_store_service", None)
        if service is None:
            # A9: DB store absent + file source available ⇒ pf:-shaped
            # ids were handled above; a UUID here has nothing to serve.
            return (
                "Skill store service not yet available. "
                "View will be enabled in a later phase."
            )
        try:
            result = await service.view_skill(skill_id)
            if result is None:
                return f"ERROR: skill_view: no skill with id '{skill_id}'"
            skill = result.get("skill", {}) or {}
            lineage = result.get("lineage", {}) or {}
            parents = lineage.get("parents", []) or []
            children = lineage.get("children", []) or []

            lines: list[str] = []
            name = skill.get("name", "<unknown>")
            lines.append(f"# {name}")
            lines.append("")
            lines.append(f"- id: {skill.get('id', '')}")
            lines.append(f"- category: {skill.get('category', '')}")
            lines.append(f"- status: {skill.get('status', '')}")
            project_id_val = skill.get("project_id")
            lines.append(
                f"- project_id: {project_id_val if project_id_val else 'global'}"
            )
            lines.append(f"- created_at: {skill.get('created_at', '')}")
            lines.append(f"- updated_at: {skill.get('updated_at', '')}")
            description = skill.get("description", "") or ""
            if description:
                lines.append("")
                lines.append(f"> {description}")
            content = skill.get("content", "") or ""
            if content:
                lines.append("")
                lines.append("## Content")
                lines.append("")
                if len(content) > 8000:
                    lines.append(content[:8000])
                    lines.append("")
                    lines.append("... (truncated at 8000 chars)")
                else:
                    lines.append(content)
            # Lineage section.
            if parents or children:
                lines.append("")
                lines.append("## Lineage")
                if parents:
                    lines.append("")
                    lines.append("Parents:")
                    for p in parents:
                        lines.append(
                            f"- from_skill_id={p.get('from_skill_id', '')} "
                            f"relation_type={p.get('relation_type', '')}"
                        )
                if children:
                    lines.append("")
                    lines.append("Children:")
                    for c in children:
                        lines.append(
                            f"- to_skill_id={c.get('to_skill_id', '')} "
                            f"relation_type={c.get('relation_type', '')}"
                        )
            return "\n".join(lines)
        except Exception as e:
            return f"ERROR: skill_view failed: {e}"

    skill_view._full_doc_ = """\
View a single skill's full body plus its lineage graph.

FILE SKILLS (Phase-3): ``skill_id`` may be a ``pf:<ns>:<name>``
address. Resolution goes through the sanctioned scope resolver on
the CALLING INSTANCE (A13 — no caller-supplied project hint). The
view renders header metadata (name / version / category / tiers /
namespace / address / body_path / digest / project scope), the body
FRESH FROM DISK (never cached), ``## Loads`` (each dependency's
``pf:`` address + one-line description; explicit loads resolve
depth-0 only) and ``## Assets`` sections. The displayed body keeps
the uniform 8000-char cap; file skills append the real
``body_path`` + a ``read_file`` hint (the injection path renders
the body UNCAPPED — FR-P5-3 parity). A missing/corrupt body fails
closed ("body unreadable: <reason>; re-index scheduled") — never a
stale body, never empty.

Bundles :meth:`SkillStoreService.view_skill` into a Markdown
document the agent can read directly. The service returns
``{"skill": {...}, "lineage": {"parents": [...], "children": [...]}}``;
the tool renders that into a structured doc.

Args:
    skill_id: The skill's UUID4 identifier (the ``id`` column, not
        the ``name``).

Returns:
    A Markdown document with sections for header, metadata
    (id / category / status / project_id / timestamps), description
    (quoted), full content body, and a Lineage section listing
    parents and children (when present). The content body is
    truncated at 8000 chars with a ``... (truncated at 8000 chars)``
    marker to keep tool responses bounded; the underlying
    ``content`` column is fully available via the
    ``SkillStoreService.view_skill`` API for callers that need it.

    Returns ``ERROR: skill_view: no skill with id '<id>'`` when the
    skill does not exist. Soft-fails with the
    ``"Skill store service not yet available..."`` message when
    ``manager._skill_store_service`` is absent.
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_create(
        name: str,
        description: str,
        content: str,
        category: str = "workflow",
    ) -> str:
        """Create a new skill (delegates to skill_store_service.create_skill).

        Args:
            name: Human-readable skill name.
            description: One-line summary.
            content: The skill body (markdown / instructions).
            category: Free-form category string. Defaults to "workflow".
        """
        # FR-P7-4 clause 2 (task 5): skill_create MUST NOT accept a
        # ``source: project-file`` body. VACUITY today: the signature
        # has NO ``source`` parameter. INVARIANT (pinned): any future
        # signature change must keep refusing file-skill sources —
        # a ``pf:``-shaped name is refused here with the same git-PR
        # guidance the skill_fix refusal carries.
        if is_pf_address(name):
            return _pf_refusal("skill_create")
        project_id = _get_project_id()
        service = getattr(manager, "_skill_store_service", None)
        if service is None:
            return (
                "Skill store service not yet available. "
                "Create will be enabled in a later phase."
            )
        if not name.strip() or not description.strip() or not content.strip():
            return (
                "ERROR: skill_create: name, description, and content must be non-empty."
            )
        try:
            created = await service.create_skill(
                name=name,
                description=description,
                content=content,
                project_id=project_id,
                category=category,
            )
            if created is None:
                return (
                    f"ERROR: skill_create: service returned no row for '{name}' "
                    "(SkillStoreService.create_skill contract violation)."
                )
            skill_id = (
                created.id
                if getattr(created, "id", None)
                else "unknown"
            )
            return (
                f"\u2705 Skill '{name}' created with id {skill_id}.\n"
                f"Use skill_search() to find it."
            )
        except Exception as e:
            return f"ERROR: skill_create failed: {e}"

    skill_create._full_doc_ = """\
Create a new skill row in the active project (or as a global skill when
no project context is resolved).

FR-P7-4 CLAUSE 2 (Phase-3, pinned): this tool does NOT accept a
``source: project-file`` body — the signature has no ``source``
parameter (vacuously true today). INVARIANT: any future signature
change must keep refusing file-skill sources; a ``pf:``-shaped
``name`` is refused here with the same git-PR guidance
``skill_fix`` carries.

Delegates to :meth:`SkillStoreService.create_skill`, which writes the
``skills`` row via :meth:`SkillRepository.create` and then triggers a
best-effort embedding refresh. The embedding call is allowed to fail
without aborting the create — skills remain usable via BM25 full-text
search even without cached embeddings.

Args:
    name: Human-readable skill name. Must be unique within the
        ``(project_id, name, generation)`` tuple per the underlying
        UNIQUE constraint.
    description: One-line summary. Shown in tooltips and resolver
        candidate lists.
    content: The skill body (markdown / instructions). May be large.
    category: Free-form category string. Defaults to ``"workflow"``.

Returns:
    Confirmation string::

        ✅ Skill '<name>' created with id <id>.
        Use skill_search() to find it.

    Where ``<id>`` is the skill's full UUID4 primary key. Returns
    ``ERROR: skill_create: name, description, and content must be
    non-empty.`` when any input is blank. Soft-fails with the
    ``"Skill store service not yet available..."`` message
    when ``manager._skill_store_service`` is absent.

Note:
    Unlike :func:`skill_fix`, this tool does NOT route through the
    skill-job dispatcher — it writes inline because the create is a
    single synchronous DB insert + embedding refresh.
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_fix(
        skill_id: str,
        issue_description: str,
        suggested_fix: str | None = None,
    ) -> str:
        """Report a skill defect into the skill-evolution pipeline.

        TRUTHFUL DISPATCH SEMANTICS (2026-10-22): when the
        skill-evolution lane is ENABLED, this tool dispatches a Tier-3
        ``skill_evolution`` job IMMEDIATELY via
        ``SkillJobDispatcher.dispatch_fix`` — it does NOT merely record
        a request for a later analysis pass. The job is admitted to the
        queue and a skill-keeper agent picks it up. When the lane is
        DISABLED (kill-switch ``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` off /
        ``SkillEvolutionConfig.evolution_lane_enabled=False``, the
        default) or the per-skill FIX cooldown is active, NO job is
        created and the response says so explicitly.

        Args:
            skill_id: The skill's UUID4 identifier.
            issue_description: Plain-language description of the issue.
            suggested_fix: Optional proposed change.
        """
        # P7 refusal (task 5): file-backed skills evolve via git PRs —
        # the evolution dispatcher is NEVER invoked for a ``pf:``
        # address (mirrored on the A14 surfaces: skill_analyze /
        # skill_evolve / skill_get_metrics + REST POST /{id}/fix).
        #
        # A4 (tidier fold): the predicate is the BROAD prefix form —
        # ``is_pf_address`` below is the STRICT ``pf:<ns>:<name>``
        # parser, so a malformed ``pf:foo`` / ``pf:`` slipped through
        # to ``dispatch_fix`` as if it were a DB id: a real P7 bypass.
        # Refusal keys on the prefix, never on parseability (the REST
        # endpoint has always refused this way).
        if isinstance(skill_id, str) and skill_id.startswith(ADDRESS_PREFIX):
            return _pf_refusal("skill_fix")
        project_id = _get_project_id()
        dispatcher = getattr(manager, "_skill_job_dispatcher", None)
        dispatch_error: str | None = None
        dispatched_job_id: str | None = None
        # H2 (tidier): the durable re-fix burst cap is a DESIGNED
        # refusal, not a dispatcher fault. It is caught STRUCTURALLY,
        # BEFORE the blanket ``except Exception`` below.
        burst_cap_refusal: SkillFixBurstCapRefused | None = None
        if dispatcher is not None:
            dispatch_method = getattr(dispatcher, "dispatch_fix", None)
            if dispatch_method is not None and callable(dispatch_method):
                try:
                    result = dispatch_method(
                        skill_id=skill_id,
                        issue_description=issue_description,
                        suggested_fix=suggested_fix,
                        project_id=project_id,
                        current_instance_id=current_instance_id,
                    )
                    # Tolerate async dispatchers via ``await``.
                    if hasattr(result, "__await__"):
                        result = await result
                    # Capture the admission outcome so the response can
                    # be TRUTHFUL about what happened: ``None`` means
                    # the evolution-lane gate refused (kill-switch OFF
                    # or FIX cooldown) — no job exists. A job_id means
                    # a Tier-3 job was admitted.
                    dispatched_job_id = (
                        result
                        if isinstance(result, str) and result
                        else None
                    )
                except SkillFixBurstCapRefused as e:
                    # H2 (tidier) — the DESIGNED refusal, caught BEFORE
                    # the blanket catch. The blanket catch swallowed it
                    # and rendered the generic note, including the
                    # UNTRUE "request is still recorded above for the next
                    # skill-keeper run": the cap REFUSED the dispatch, so
                    # NO job exists and NOTHING will pick it up. An
                    # agent told "your request is recorded" believes a
                    # fix is pending when nothing was recorded.
                    #
                    # The REST route already handles this exception
                    # structurally (HTTP 429, M2-pinned) and reads the
                    # stable token off the refusal class (``e.reason``)
                    # rather than hard-coding a literal — the agent-facing
                    # envelope mirrors that vocabulary, so the two can
                    # never drift (M11: one token per concept).
                    logger.warning(
                        "skill_fix burst cap refused for skill %s: %s",
                        skill_id,
                        e,
                    )
                    burst_cap_refusal = e
                except Exception as e:
                    logger.warning(
                        "skill_fix dispatcher raised for skill %s: %s",
                        skill_id,
                        e,
                    )
                    dispatch_error = str(e)
            else:
                # Dispatcher object exists but lacks ``dispatch_fix``
                # (or it's not callable) — treat as "not yet
                # available" for the fix-flow but still record the
                # request in the response.
                logger.debug(
                    "skill_fix dispatcher missing/callable dispatch_fix "
                    "method; falling back to log-only"
                )
                dispatch_method = None
        # E3.1 (board wi-d340f16c bullet 1 — display hygiene): echo the
        # FULL id, never the 8-hex prefix. The 8-hex form is NOT
        # ``skill_view``-consumable, so an agent that copies it back into
        # ``skill_view`` fails (the wi-76d89e11 verbatim-id contract).
        echo_id = skill_id if isinstance(skill_id, str) else str(skill_id)
        lines: list[str] = [
            f"\U0001f4dd Skill fix request recorded for skill '{echo_id}'.",
            "",
            "User-reported issue:",
            "---",
            f"{issue_description}",
            "---",
        ]
        if suggested_fix:
            lines.extend([
                "",
                "User-suggested fix:",
                "---",
                f"{suggested_fix}",
                "---",
            ])
        else:
            lines.append("")
            lines.append("User-suggested fix: (none)")
        if dispatched_job_id is not None:
            lines.append("")
            lines.append(
                f"\u2705 Dispatched: Tier-3 skill_evolution job "
                f"{dispatched_job_id} was admitted — the skill-keeper "
                "agent will pick it up from the job queue."
            )
        elif burst_cap_refusal is not None:
            # H2 (tidier): the durable re-fix burst cap REFUSED the
            # admission. The lane is ON — the CAP refused. The envelope
            # is a FIRST-CLASS refusal: NO job was created, NOTHING will
            # be picked up, and the stable reason token travels to the
            # agent (the REST route reads the same ``e.reason``).
            lines.append("")
            lines.append(
                "\u26d4 NOT dispatched: the durable re-fix burst cap "
                "REFUSED this FIX admission "
                f"(reason={burst_cap_refusal.reason}). The request was "
                "REFUSED at dispatch — NO skill-keeper job was created "
                "and NOTHING will be picked up. This message is the only "
                "record of the request."
            )
        elif (
            dispatcher is not None
            and dispatch_method is not None
            and dispatch_error is None
        ):
            # Dispatcher present + callable + no exception + no job id
            # == the evolution-lane gate refused the admission
            # (kill-switch OFF or FIX cooldown). Be loud and honest.
            lines.append("")
            lines.append(
                "\u26d4 NOT dispatched: the skill-evolution admission lane "
                "is disabled (kill-switch ENSEMBLE_SKILL_EVOLUTION_ENABLED "
                "off, or the per-skill fix cooldown is active). NO "
                "skill-keeper job was created — this message is the only "
                "record of the request."
            )
        if dispatcher is None or dispatch_method is None:
            lines.append("")
            lines.append(
                "Note: dispatcher not yet available; request is queued as a "
                "note for the next skill-keeper run."
            )
        if dispatch_error is not None:
            lines.append("")
            lines.append(
                f"Note: dispatcher raised during dispatch ({dispatch_error}); "
                "request is still recorded above for the next skill-keeper run."
            )
        return "\n".join(lines)

    skill_fix._full_doc_ = """\
Report a skill defect into the skill-evolution pipeline (GATED dispatch).

P7 REFUSAL (Phase-3): a ``pf:<ns>:<name>`` address is REFUSED with
git-PR guidance ("File-backed skills evolve via git PRs against
`.agents/<ns>/skills/` — skill_fix does not apply (P7)") BEFORE any
dispatcher access — the skill-keeper is never invoked for file
skills.

DISPATCH BEHAVIOR (corrected 2026-10-22 — the earlier "record-only"
wording was wrong): when the skill-evolution lane is enabled, this
tool dispatches a Tier-3 ``skill_evolution`` job IMMEDIATELY through
``SkillJobDispatcher.dispatch_fix`` — the job is admitted to the
queue and the skill-keeper agent executes it (a real mutation pass:
FIX creates a new skill generation + opens an A/B test). It is NOT a
passive note.

The admission is gated:

* Kill-switch OFF (``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` unset and
  ``SkillEvolutionConfig.evolution_lane_enabled=False`` — the default)
  → NO job is created; the response carries an explicit
  "NOT dispatched" note.
* Per-skill FIX cooldown (default 1800s) → a repeat fix for the same
  ``(project, skill)`` inside the window is refused; response says so.
* Non-terminal duplicate → collapses onto the live job via the
  ``skill_fix:<project>:<skill>`` idempotency key.

The dispatcher is best-effort: if it is absent or raises, the request
is still logged via the ``logger.warning`` path AND reflected in the
response text so the agent sees a deterministic confirmation.

Args:
    skill_id: The skill's UUID4 identifier.
    issue_description: Plain-language description of the issue.
    suggested_fix: Optional proposed change. ``None`` when the
        caller doesn't have one — the skill-keeper will derive one
        from ``issue_description``.

Behavior:

* Resolves ``project_id`` via the closure helper.
* Looks up ``manager._skill_job_dispatcher`` defensively
  (``getattr`` with ``None`` default).
* When the dispatcher is present AND exposes a ``dispatch_fix``
  callable, awaits it with ``(skill_id, issue_description,
  suggested_fix, project_id, current_instance_id)`` and reports the
  admission outcome TRUTHFULLY (job id when dispatched; "NOT
  dispatched" when the gate refused).
* When the dispatcher is absent (Phase 2 interregnum), the tool
  logs the request as a warning and returns a clear "queued for
  the next skill-keeper run" message.
* When the dispatcher raises, the tool logs a warning and reports
  the error in the response — the request is still recorded.

Returns:
    A multi-line response. Dispatched::

        📝 Skill fix request recorded for skill '<skill_id>'.
        ... issue / suggested-fix fences ...
        ✅ Dispatched: Tier-3 skill_evolution job <job_id> was admitted
        — the skill-keeper agent will pick it up from the job queue.

    Gate refused (lane disabled or cooldown)::

        📝 Skill fix request recorded for skill '<skill_id>'.
        ... issue / suggested-fix fences ...
        ⛔ NOT dispatched: the skill-evolution admission lane is
        disabled (kill-switch ENSEMBLE_SKILL_EVOLUTION_ENABLED off, or
        the per-skill fix cooldown is active). NO skill-keeper job was
        created — this message is the only record of the request.

    (the ``---`` fences guard against prompt injection from the
    echoed user input; mirrors the pattern in
    :mod:`daemon.tools.todo_tools`)
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_feedback(
        skill_id: str,
        applied: bool | None = None,
        usefulness: int | None = None,
        note: str = "",
        improvement_note: str = "",
    ) -> str:
        """Record feedback on a skill's usefulness.

        Delegates to :meth:`SkillMetricsService.record_feedback` (Phase 4
        backend) to stamp ``feedback_applied`` / ``feedback_note`` onto
        the latest :class:`SkillUsageRecord` for ``(skill_id,
        instance_id)``. The closure-injected ``current_instance_id`` is
        forwarded so the metrics service can locate the right usage
        row; ``project_id`` / ``agent_id`` are resolved from the
        instance repository when available, falling back to ``None``.

        Phase 5 (2026-07-21): additionally captures the optional
        ``usefulness`` quality score (1–10) and the ``improvement_note``
        suggestion text. Both feed the per-skill usefulness rollup and
        the skill-keeper evolution loop directly — the agent's
        judgments become training signal for the next skill revision.

        Soft-fail contract: the tool NEVER raises. Every failure mode
        — missing service, missing instance repo, no usage record to
        update, service-raised exception, out-of-range ``usefulness``
        — returns a deterministic string the agent loop can render.

        Args:
            skill_id: The skill's UUID4 identifier, a ``pf:<ns>:<name>``
                project-file address, or the ``pf-<hex16>`` pseudo-id
                the injection path renders for a file skill.
            applied: ``True`` if the skill was directly useful, ``False``
                if it was not relevant, or ``None`` / omitted if unsure.
            usefulness: Optional agent-judged quality score 1-10.
                Be honest — 1 means the skill was unusable or actively
                harmful, 5 means mediocre (it didn't help much), 10
                means excellent and directly helpful. Optional but
                encouraged; out-of-range values (≤0, >10, non-int) are
                rejected with a clear error string instead of being
                clamped, so the agent can correct and retry. ``None``
                = no numeric rating (the ``note`` / ``improvement_note``
                still get recorded).
            note: Optional general context observation about how the
                skill was used. Distinct from ``improvement_note``
                below — ``note`` describes the situation, not what
                the skill should change.
            improvement_note: Optional actionable suggestions for
                improving the skill content itself (e.g. "Should
                mention PACKS.md location", "Add example of timeout
                checklist"). Distinct from ``note`` which is general
                context observation. Feeds the skill-keeper
                evolution loop directly — write specific, actionable
                edits you'd want a human reviewer to apply.
        """
        # D2 companion metrics-only path (task 6): ``pf:`` addresses and
        # the ``pf-`` pseudo-ids injection renders record into
        # ``project_skill_usage_records`` — NO lineage rows, NO
        # evolution dispatch, NO metrics-service call (P7). Runs BEFORE
        # the metrics-service gate: the companion path works with the
        # DB evolution stack absent (A9 degraded mode).
        #
        # A4: this prefix test is BROAD ON PURPOSE and must stay wider
        # than the ``skill_fix`` refusal above — the ``pf-<hex16>``
        # pseudo-id is a legitimately routable file-skill id on this
        # surface (metrics-only), and the branch FAILS CLOSED on an
        # unknown/typo'd id after index validation (W5). The
        # ``skill_fix`` refusal is prefix-only for the same reason: a
        # malformed ``pf:`` spelling must never reach evolution.
        if isinstance(skill_id, str) and (
            skill_id.startswith(ADDRESS_PREFIX)
            or skill_id.startswith(PSEUDO_ID_PREFIX)
        ):
            return await _feedback_pf_skill(
                skill_id, applied, usefulness, note, improvement_note
            )
        metrics_service = getattr(manager, "_skill_metrics_service", None)
        if metrics_service is None:
            return "Skill metrics service is not yet available."
        # Range-validate ``usefulness`` before forwarding — out-of-range
        # values are explicitly rejected (not clamped) so the agent loop
        # can correct and retry rather than silently recording garbage.
        if usefulness is not None:
            if not isinstance(usefulness, int) or isinstance(usefulness, bool):
                return (
                    f"ERROR: skill_feedback 'usefulness' must be an int "
                    f"1-10, got {type(usefulness).__name__}: {usefulness!r}"
                )
            if usefulness < 1 or usefulness > 10:
                return (
                    f"ERROR: skill_feedback 'usefulness' must be in "
                    f"range 1-10, got {usefulness}"
                )
        project_id = _get_project_id()
        agent_id = _get_agent_id()
        # E3.2 (board wi-d340f16c bullet 2 — display hygiene): echo the
        # FULL id, never the 8-hex prefix (same verbatim-id contract as
        # the fix path).
        echo_id = skill_id if isinstance(skill_id, str) and skill_id else str(skill_id)
        try:
            applied_ok = await metrics_service.record_feedback(
                skill_id=skill_id,
                instance_id=current_instance_id,
                agent_id=agent_id,
                project_id=project_id,
                applied=applied,
                note=note,
                usefulness=usefulness,
                improvement_note=improvement_note,
            )
        except Exception as e:
            try:
                logger.warning(
                    "skill_feedback raised for skill %s: %s",
                    skill_id,
                    e,
                )
            except Exception:
                pass
            return f"ERROR: skill_feedback failed: {e}"
        if applied_ok:
            # Compose a multi-line confirmation that surfaces the new
            # Phase 5 fields when present so the agent loop can see
            # exactly what got recorded.
            usefulness_line = (
                f" Usefulness: {usefulness}/10." if usefulness is not None else ""
            )
            improvement_line = (
                f" Improvement: {improvement_note!r}"
                if improvement_note
                else ""
            )
            return (
                f"\u2705 Feedback recorded for skill {echo_id}... "
                f"Applied={applied}.{usefulness_line} Note: {note!r}.{improvement_line}"
            )
        return (
            f"\u26a0\ufe0f No usage record found for skill {echo_id}... "
            "Feedback cannot be applied."
        )

    skill_feedback._full_doc_ = """\
Record feedback on a skill's usefulness.

FILE SKILLS (Phase-3, D2 companion): a ``pf:<ns>:<name>`` address
(or the ``pf-<hex16>`` pseudo-id the injection path renders) records
METRICS-ONLY into ``project_skill_usage_records`` — no lineage rows,
no evolution dispatch, no ``SkillMetricsService`` call (P7). The
``usefulness`` / ``improvement_note`` fields fold into the recorded
``feedback_note`` (the companion table deliberately has no
dedicated columns). Works with the DB evolution stack absent.

Delegates to :meth:`SkillMetricsService.record_feedback` (Phase 4
backend) which locates the latest :class:`SkillUsageRecord` for
``(skill_id, current_instance_id)`` and stamps ``feedback_applied``
plus ``feedback_note`` onto it. When ``applied=True``, the skill
row's ``total_applied`` counter is also bumped by the service.

Phase 5 (2026-07-21): the tool also captures the optional
``usefulness`` quality score (1-10) and ``improvement_note``
suggestion text. These are stamped onto ``feedback_usefulness``
and ``feedback_improvement`` respectively and feed the skill-keeper
evolution loop directly — what you record here directly influences
how the next skill revision gets generated.

The closure-injected ``current_instance_id`` is forwarded as
``instance_id``; ``project_id`` and ``agent_id`` are resolved from
``manager._instance_repository`` via the ``_get_project_id`` /
``_get_agent_id`` helpers (returning ``None`` when the repo is
missing or the lookup fails). The metrics service treats ``None``
and ``""`` symmetrically for both fields.

Args:
    skill_id: The skill's UUID4 identifier.
    applied: ``True`` if the skill was directly useful, ``False``
        if it was not relevant or unhelpful, ``None`` (default) if
        the caller is unsure and is leaving a note only.
    usefulness: Optional agent-judged quality score 1-10.
        Be honest. 1 = skill was unusable or actively harmful,
        5 = mediocre (didn't help much), 10 = excellent and
        directly helpful. Out-of-range values are rejected with
        an ``ERROR: ...`` return string (not clamped) so the
        agent loop can correct and retry. ``None`` (default)
        means no numeric rating — the note / improvement_note
        still get recorded.
    note: Optional general context observation about how the
        skill was used. Defaults to empty string. Distinct
        from ``improvement_note`` which is the actionable
        suggestion about the skill content itself.
    improvement_note: Optional actionable suggestions for
        improving the skill content itself. Be specific —
        e.g. "Should mention PACKS.md location", "Add
        example of timeout checklist", "Troubleshooting
        section is missing the K8s pod restart case".
        Feeds the skill-keeper evolution loop directly.
        Defaults to empty string.

Returns:
    On a record update (``metrics_service.record_feedback(...)``
    returned ``True``)::

        ✅ Feedback recorded for skill <skill_id>... Applied=<applied>. [Usefulness: <n>/10.] Note: '<note>'. [Improvement: '<improvement_note>']

    When the service returns ``False`` (no usage record to stamp)::

        ⚠️ No usage record found for skill <skill_id>... Feedback cannot be applied.

    When ``manager._skill_metrics_service`` is absent (Phase-wiring
    in progress)::

        Skill metrics service is not yet available.

    When ``usefulness`` is out of range::

        ERROR: skill_feedback 'usefulness' must be in range 1-10, got <value>

    When ``usefulness`` is the wrong type::

        ERROR: skill_feedback 'usefulness' must be an int 1-10, got <type>: <value>

    When the service raises, an ``ERROR: ...`` string is returned
    containing the exception message; the underlying exception is
    also logged at WARNING level so the daemon log retains a
    breadcrumb.

Soft-fail contract:
    The tool never raises — every code path returns a string. This
    preserves the agent-loop contract: every tool call yields a
    deterministic string the model can read. The
    "not yet available" check is intentionally outside the
    ``try/except`` so the soft-fail message isn't masked when the
    service attribute is missing.
"""

    return [
        skill_search,
        skill_list,
        skill_view,
        skill_create,
        skill_fix,
        skill_feedback,
    ]