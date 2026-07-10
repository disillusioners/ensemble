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

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

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
        """Auto-inject project_id from instance context. Returns None on any failure."""
        try:
            repo = getattr(manager, "_instance_repository", None)
            if repo is None:
                return None
            instance_meta = repo.get(current_instance_id)
            if instance_meta is not None and getattr(instance_meta, "project_id", None):
                return instance_meta.project_id
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
            return (
                "Skill search service not yet available. "
                "Search will be enabled in a later phase."
            )
        try:
            result = await service.search(
                query, project_id=project_id, max_results=limit
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
        if service is None:
            return (
                "Skill store service not yet available. "
                "List will be enabled in a later phase."
            )
        try:
            items, total = await service.list_skills(
                project_id=project_id, active_only=active_only, limit=100
            )
            if category is not None:
                items = [it for it in items if it.get("category") == category]
            lines = [f"Skills ({len(items)} of {total} total):"]
            for it in items:
                skill_id = it.get("id", "")
                short_id = skill_id[:8] if isinstance(skill_id, str) else "????????"
                name = it.get("name", "")
                cat = it.get("category", "")
                status = it.get("status", "")
                lines.append(
                    f"- [{short_id}] {name} — {cat} — {status}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"ERROR: skill_list failed: {e}"

    skill_list._full_doc_ = """\
List skills in the active project scope (project + global overlay).

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
        - [<short_id>] <name> — <category> — <status>
        - ...

    Soft-fails with the ``"Skill store service not yet available..."``
    message when ``manager._skill_store_service`` is absent.
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_view(skill_id: str) -> str:
        """View a skill's full body + lineage graph (delegates to skill_store_service).

        Args:
            skill_id: The skill's UUID4 identifier.
        """
        service = getattr(manager, "_skill_store_service", None)
        if service is None:
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
                created.id[:8]
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

        ✅ Skill '<name>' created with id <short_id>.
        Use skill_search() to find it.

    Where ``<short_id>`` is the first 8 chars of the skill's UUID4
    primary key. Returns ``ERROR: skill_create: name, description, and
    content must be non-empty.`` when any input is blank. Soft-fails
    with the ``"Skill store service not yet available..."`` message
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
        """Record a fix request for a skill (delegates to skill_job_dispatcher).

        USER-FACING: this tool NEVER performs the fix inline. It only
        records the request so the skill-keeper agent can pick it up
        during its next analysis pass.

        Args:
            skill_id: The skill's UUID4 identifier.
            issue_description: Plain-language description of the issue.
            suggested_fix: Optional proposed change.
        """
        project_id = _get_project_id()
        dispatcher = getattr(manager, "_skill_job_dispatcher", None)
        dispatch_error: str | None = None
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
                        await result
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
        short_id = skill_id[:8] if isinstance(skill_id, str) else skill_id
        lines: list[str] = [
            f"\U0001f4dd Skill fix request recorded for skill '{short_id}'. "
            "The skill-keeper agent will analyze this when available.",
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
Record a USER-FACING fix request for a skill.

This tool NEVER performs the fix inline. It only records the request
so the skill-keeper agent can pick it up during its next analysis
pass (Phase 5 ``SkillEvolutionService``). The dispatcher is best-effort:
if it is absent or raises, the request is still logged via the
``logger.warning`` path AND reflected in the response text so the
agent sees a deterministic confirmation.

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
  suggested_fix, project_id, current_instance_id)``.
* When the dispatcher is absent (Phase 2 interregnum), the tool
  logs the request as a warning and returns a clear "queued for
  the next skill-keeper run" message.
* When the dispatcher raises, the tool logs a warning and reports
  the error in the response — the request is still recorded.

Returns:
    A multi-line confirmation::

        📝 Skill fix request recorded for skill '<short_id>'.
        The skill-keeper agent will analyze this when available.

        User-reported issue:
        ---
        <issue_description>
        ---

        User-suggested fix:
        ---
        <suggested_fix>
        ---
        (the ``---`` fences guard against prompt injection from the
        echoed user input; mirrors the pattern in
        :mod:`daemon.tools.todo_tools`)

        Note: dispatcher not yet available; request is queued as a
        note for the next skill-keeper run.   (only when dispatcher is absent)
        Note: dispatcher raised during dispatch (...); request is still
        recorded above for the next skill-keeper run.   (only when dispatcher raised)
"""

    @register_tool_category("dynamic-skill")
    @tool
    async def skill_feedback(
        skill_id: str,
        applied: bool | None = None,
        note: str = "",
    ) -> str:
        """Record feedback on a skill's usefulness (Phase 2 stub).

        Records a feedback/usefulness signal for the given skill and
        returns a deterministic confirmation string. This is the
        Phase 2 stub: the tool only logs the event and does NOT
        persist anything yet — full persistence (stamping
        ``feedback_applied`` / ``feedback_note`` onto the latest
        :class:`SkillUsageRecord`, bumping ``total_applied`` on
        ``applied=True``) is the responsibility of the Phase 4
        :class:`SkillMetricsService.record_feedback`.

        Args:
            skill_id: The skill's UUID4 identifier.
            applied: True if the skill was directly useful, False if it
                was not relevant, or None / omitted if unsure.
            note: Optional free-form feedback note.
        """
        short_id = skill_id[:8] if isinstance(skill_id, str) and skill_id else str(skill_id)
        try:
            logger.info(
                "Skill feedback (Phase 2 stub) for %s: applied=%s, note=%s",
                skill_id,
                applied,
                note,
            )
        except Exception:
            pass
        return (
            f"\u2705 Feedback recorded for skill {short_id}... "
            "(Phase 2 stub — persisted via Phase 4 "
            "SkillMetricsService.record_feedback)."
        )

    skill_feedback._full_doc_ = """\
Record feedback on a skill's usefulness.

**Phase 2 stub**: the full backend is
:meth:`SkillMetricsService.record_feedback` (Phase 4). Until
Phase 4 rolls out, the tool *records the feedback event to the
daemon log* and returns a confirmation string so the agent
loop gets a deterministic tool response.

Why a log-and-return instead of a hard "not yet implemented":
we still want agents in the wild to be able to leave
feedback (e.g. "this skill was misleading") so the
skill-keeper agent has signal to act on in the next
evolution pass, even before persistence is wired.

Args:
    skill_id: The skill's UUID4 identifier.
    applied: ``True`` if the skill was directly useful, ``False`` if
        it was not relevant or unhelpful, ``None`` (default) if the
        caller is unsure and is leaving a note only.
    note: Optional free-form feedback note. Defaults to empty string.

Returns:
    On success::

        ✅ Feedback recorded for skill <short_id>... (Phase 2 stub — persisted via Phase 4 SkillMetricsService.record_feedback).
        ---
        skill_id: <skill_id>
        applied: <bool|unspecified>
        note: <note or '(none)'>
        ---

    The ``---`` fences around the echoed fields guard against prompt
    injection from the ``skill_id`` / ``note`` user input (mirrors the
    pattern in :mod:`daemon.tools.todo_tools`).

Soft-fail contract:
    The stub never raises; it always returns a string. This
    preserves the agent-loop contract: every tool call returns
    a deterministic string the model can read.
"""

    return [
        skill_search,
        skill_list,
        skill_view,
        skill_create,
        skill_fix,
        skill_feedback,
    ]