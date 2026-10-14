"""Markdown loader for agent prompts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .rag.config import is_rag_enabled
from .registry import ToolFilter

if TYPE_CHECKING:  # pragma: no cover — runtime keeps zero DB imports (A6/C7)
    from .repositories.prompt_version.repository import PromptVersionRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt-version DB hook (Phase A T6 / T7 / T8)
# ---------------------------------------------------------------------------
# Module-level singleton reference to the prompt-version repository. Set
# by :class:`daemon.manager.InstanceManager` at init time (the manager
# already owns the repo via ``create_prompt_version_repository`` at
# manager.py:1171-1173). The loader MUST NOT import the repository class
# at module load (it has zero DB imports today; A6/C7 keeps that invariant)
# — the value is read lazily inside ``_get_prompt_version_repo`` on every
# cache check. ``None`` means "DB layer unavailable" (test fixtures,
# daemon pre-init, fresh-boot race) — the hook is a no-op in that case
# (FAIL-OPEN).
_PROMPT_VERSION_REPO: Any = None

# Module-level failure counter (A6/C7) — incremented on every hook
# exception so an operator can detect "DB layer keeps failing" without
# scraping log lines. Reset on daemon restart.
_PROMPT_VERSION_HOOK_FAILURES: int = 0


def _get_prompt_version_repo() -> Any:
    """Return the prompt-version repository (set at InstanceManager init).

    Returns ``None`` when the manager has not yet initialized the repo
    (early-boot race, test fixture without a manager, etc.). Callers
    treat ``None`` as "DB layer unavailable" and fall back to file-only
    content (FAIL-OPEN — A6/C7).
    """
    return _PROMPT_VERSION_REPO


def set_prompt_version_repo(repo: Any) -> None:
    """Register the prompt-version repo at InstanceManager init.

    Idempotent; safe to call more than once (e.g. on manager re-init
    in tests). The manager is the single writer; the loader only reads
    via :func:`_get_prompt_version_repo`.
    """
    global _PROMPT_VERSION_REPO
    _PROMPT_VERSION_REPO = repo


def get_prompt_version_hook_failures() -> int:
    """Return the cumulative hook failure counter (A6/C7)."""
    return _PROMPT_VERSION_HOOK_FAILURES


def _bump_prompt_version_hook_failures() -> None:
    """Increment the hook failure counter (A6/C7)."""
    global _PROMPT_VERSION_HOOK_FAILURES
    _PROMPT_VERSION_HOOK_FAILURES += 1


# The conservative artifact set the hook replaces. Mirrors the F2
# set in ``daemon.repositories.prompt_version.models.PROMPT_ARTIFACTS``
# (soul / rule / workflow / tools_note / tools / skill — the CORE
# set per User Directive #3). User Directive #3 (2026-10-14,
# RESOLUTION-CORE) EXTENDS the loader's hook to the memory family
# for MEMORY-class rows: the loader resolves ``memory``,
# ``memories/<slug>``, ``user``, ``growth`` content via the DB
# override chain (selected > latest > template-file fallback).
# ``user.md`` / ``growth.md`` are NEVER loaded into the system
# prompt — the hook only fires on the artifact family for which
# the loader already has content-merge semantics. The carve-out
# was removed for ``memory.md`` and the per-file ``memories/<slug>``
# family (decisions.md §7 binding block swept accumulated
# pre-directive content; the empty-file boot-tolerance proven
# for the loader).
#
# Keys in the returned prompts dict are filenames WITHOUT the
# ``.md`` extension (matches the loader's :func:`load_agent_prompts`
# convention): ``soul`` ↔ ``soul.md`` etc. The ``memory`` key ↔
# ``memory.md`` (loader §8 boot-prompt). Per-file MEMORY keys
# (``memories/<slug>``) live in a separate index — they surface
# via the recent-memories filename list (loader.py:560)
# rather than the prompt-section dict.
_HOOK_ARTIFACT_KEYS: frozenset[str] = frozenset({
    # CORE set (Phase A conservative default)
    "soul",
    "rule",
    "workflow",
    "tools_note",
    "tools",
    "skill",
    # MEMORY family (User Directive #3, 2026-10-14). ``user.md``
    # / ``growth.md`` are not in the loader's prompt-key map —
    # they NEVER appear in the loader's ``prompts`` dict, so the
    # hook skips them (the dict-key lookup is the natural
    # no-op). Listed here as canonical references for the
    # round-trip tests.
    "memory",
    "user",
    "growth",
})


# Empty-file fallback sentinel — when a MEMORY-class artifact has
# no DB rows, the file is empty (per decisions.md §7 binding
# block swept accumulated content; the loader's
# ``load_agent_prompts`` skips missing ``memory.md`` and reads an
# empty body). The hook REPLACES this fallback the same way for
# MEMORY as it does for CORE: ``resolve_prompts_from_db`` returns
# the DB-effective content for the artifact when present;
# otherwise the loader's existing empty-string handling fires.
#
# The MEMORY-class loader path is ALSO boot-tolerant for the
# ``memory.md`` §8 section — see the proven empty-file path at
# ``daemon/loader.py:663-668`` (silently skip when ``memory.md``
# missing) and ``load_recent_memories:565-567`` (skip when
# ``memories/`` dir absent).
_MEMORY_BOOT_TOLERANCE_PROVEN = True


class NoPromptContentError(LookupError):
    """Raised when a custom agent with zero versions has no usable prompt.

    User Directive #5A (2026-10-14, RESOLUTION-CORE — the
    ``custom agents`` groundwork, sibling lifetime ownership).
    Custom agents have NO template fallback (the A2/R-A2 ruling
    lands with the lifecycle coder; this module extends the
    resolution-side hook). A ``get_effective(agent_id,
    version_tag, artifact)`` call for a custom agent with ZERO
    rows returns ``None`` (no DB content); the loader's read path
    raises the typed error so spawn-time can surface an
    actionable message inviting the operator to create v1.

    Inherits from :class:`LookupError` (Python stdlib's "this
    thing you looked for is not present" — distinct from
    :class:`ValueError` so callers can use ``except LookupError``
    to discriminate custom-agent empty-state from validation
    errors). The ``agent_id`` + ``version_tag`` fields are
    surfaced on the typed instance so the HTTP / HTTP-shaped
    boundary can map a clean 404 / 422 with the actionable
    message.

    Attributes:
        agent_id: Resolved custom-agent identity.
        version_tag: Version tag (``""`` for base; F9).
        artifact: The artifact key the loader tried to resolve.
        message: An actionable message inviting v1 creation.
    """

    def __init__(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> None:
        self.agent_id = agent_id
        self.version_tag = version_tag
        self.artifact = artifact
        super().__init__(
            f"prompt_versions has no rows for custom agent "
            f"({agent_id!r}, {version_tag!r}, {artifact!r}) — "
            f"custom agents have NO template fallback (User "
            f"Directive #5A, 2026-10-14: A2/R-A2 lifecycle ruling); "
            f"create v1 via prompt_version_service to populate "
            f"the artifact before spawning this agent."
        )


def _compute_db_versions_fingerprint(rows: list[Any] | None) -> str:
    """Compute the synthetic ``db_versions_fingerprint`` from one
    ``get_effective_rows`` result.

    The fingerprint is the sha256 of the sorted, joined
    ``(artifact, artifact_class, selected_version_id,
    latest_approved_version_id, latest_version_id)`` tuples — the
    class dimension is folded into the fingerprint so a CORE-only
    write doesn't bust the MEMORY slot and vice versa. Stable
    across calls that return the same DB state; it changes ONLY
    when the effective-content identity changes — i.e. when a
    selected or latest-approved (CORE) / latest (MEMORY) row
    identity changes (approve a new version, change a selection,
    clear a selection, persist a new MEMORY row via
    ``create_memory_version``, R-B2 auto-reset re-points).

    Empty / None input yields the ``""`` sentinel — same as a
    missing repo — so the cache-hit check stays symmetric (N4
    sentinel unification).

    Pure function over the rows; does NOT hit the DB.
    """
    if not rows:
        # N4 sentinel unification: empty rows == no DB rows == no
        # fingerprint available. Keep the "" convention so a missing
        # repo and a zero-rows repo produce the SAME fingerprint
        # (no benign boot-moment cache bust).
        return ""
    triples = []
    for r in rows:
        triples.append((
            r.artifact,
            getattr(r, "artifact_class", ARTIFACT_CLASS_CORE_FALLBACK),
            r.selected_version_id or "",
            getattr(r, "latest_approved_version_id", None) or "",
            getattr(r, "latest_version_id", None) or "",
        ))
    triples.sort()
    payload = "|".join(
        f"{a}:{cls}:{s}:{l}:{m}" for a, cls, s, l, m in triples
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Local fallback when the row doesn't carry ``artifact_class``
# (Phase A rows without the column added; tests that don't go
# through the full getter — we keep the fingerprint shape
# stable across the migration window). Mirrors
# ``ARTIFACT_CLASS_CORE`` in the repository module; defined
# locally so the loader doesn't widen its import surface for a
# constant used only on the missing-column path.
ARTIFACT_CLASS_CORE_FALLBACK = "CORE"


def _fetch_effective_rows_safe(
    repo: "PromptVersionRepository", agent_id: str, version_tag: str,
) -> list[Any] | None:
    """Fetch effective rows with A6/C7 FAIL-OPEN semantics (N3 helper).

    Wraps ``repo.get_effective_rows(agent_id, version_tag)`` so the
    loader can call it ONCE per cache-miss and feed both the
    fingerprint computation and the content replacement — closes
    the two-SELECT waste (single-SELECT powers both, A8).

    Returns:
        The rows list on success (possibly empty — see N4). Returns
        ``None`` on any hook exception (A6/C7 FAIL-OPEN): WARN
        logged, failure counter bumped, caller falls through to
        file-only content. NEVER re-raises.
    """
    try:
        return repo.get_effective_rows(agent_id, version_tag)
    except Exception as exc:  # noqa: BLE001 — A6/C7 FAIL-OPEN
        logger.warning(
            "prompt-version resolution unavailable, serving template: %s",
            exc,
        )
        _bump_prompt_version_hook_failures()
        return None


def resolve_prompts_from_db(
    prompts: dict[str, str],
    agent_id: str,
    version_tag: str,
    repo: "PromptVersionRepository",
    rows: list[Any] | None = None,
    *,
    is_custom_agent: bool = False,
) -> dict[str, str]:
    """Replace prompts[artifact] with DB-effective content (A4 / T6).

    Single source of truth for the Phase A loader hook. Takes the
    **SYNC repository** (NOT the async service facade — A4/C6/G-4b
    pins this). All 4 call sites of ``load_and_cache_prompt``
    (``instance_lifecycle.py:2098`` spawn, ``:4561`` restore,
    ``persistence.py:1064`` and ``:1141`` reconstruction) are sync;
    routing through the ``asyncio.to_thread`` facade would deadlock
    the event loop or force a mid-build refactor.

    User Directive #3 (2026-10-14) — class-aware precedence per
    ``EffectiveRow.artifact_class``:

    * **CORE** (soul / rule / workflow / tools_note / tools /
      skill) — ``selected_version.content`` >
      ``latest_approved.content`` > ``None``.
    * **MEMORY** (memory / user / growth / ``memories/<slug>``) —
      ``selected_version.content`` > **LATEST row content**
      (any state value, since MEMORY rows are state-agnostic;
      the loader resolves ``created via create_memory_version``
      directly as effective) > ``None`` (template-file fallback;
      empty post-sweep; the loader's existing empty-string
      handling for missing ``memory.md`` is unchanged).

    User Directive #5A (custom-agent resolution-side hook) —
    ``is_custom_agent=True`` flips the empty-state posture:
    when ``get_effective_rows`` returns ``[]`` (custom agent
    with zero versions), this function raises
    :class:`NoPromptContentError` so spawn-time surfaces an
    actionable message inviting the operator to create v1. The
    typed error carries ``(agent_id, version_tag, artifact)``
    for the HTTP / prompt-message boundary to map a clean
    404 / 422.

    User Directive #3 also extends the conservative artifact
    set: ``memory`` / ``user`` / ``growth`` now reach the DB
    layer (the carve-out for those artifacts was removed per
    the §7 binding block). Per-file ``memories/<slug>``
    rows surface via the recent-memories filename list, NOT
    the prompts dict (they are pure memory state; the
    filename index keeps the prompt-token cost low).

    **A8 single-SELECT guarantee**: ONE call to
    ``repo.get_effective_rows(agent_id, version_tag)`` powers the
    content replacement for ALL artifacts at once — no per-artifact
    queries. Closes the fingerprint×content TOCTOU window (C2).
    The internal ``load_and_cache_prompt`` caller (N3) passes a
    pre-fetched ``rows`` list so the fingerprint computation AND
    this function share the SAME single SELECT.

    **A6/C7 FAIL-OPEN posture**: this function is the ONLY new
    hot-path DB dependency introduced by Phase A. Any exception
    (DB unreachable, schema drift, repo misconfigured) → log a WARN
    with the exact message ``prompt-version resolution unavailable,
    serving template`` + increment the failure counter + return the
    original prompts UNMODIFIED. NO retries (rely on pool checkout
    timeout). The cache-hit check below also catches hook exceptions
    via its own try/except — this function is the LAST line of
    defense.

    **T10 — F1 OVERRIDES-ONLY seeding (Phase B).** The template file
    (``agents/<name>/<artifact>.md``) stays the LIVE FALLBACK — there
    is NO boot-time full-seed and NO hash-guarded lazy-seed. The
    first ``create_draft`` for a given ``(agent_id, version_tag,
    artifact)`` key inserts ``version = 1`` (monotonic per the
    ``Skill.generation`` precedent); the resolver above returns
    ``selected_version.content`` → ``latest_approved.content`` →
    ``None`` (and only then reads the file). Rejected alternatives
    (full-seed-at-boot = boot blast radius on every template edit;
    hash-guarded lazy-seed = more state, deferred) are documented
    in ``phaseB-plan.md`` OQ2 for morning review.

    Args:
        prompts: The current prompts dict (output of
            ``load_agent_prompts(agent_dir)``). NOT mutated in place;
            a shallow copy is returned so the caller's reference is
            preserved.
        agent_id: Resolved agent identity (the registry base name,
            e.g. ``"developer"`` — NOT ``"developer[v2]``).
        version_tag: Version tag (``""`` for base). Tag normalization
            (NULL/empty → ``""``) is handled by the repo.
        repo: The sync :class:`PromptVersionRepository` (or a duck
            typed equivalent exposing ``get_effective_rows`` /
            ``get_effective``).
        rows: Pre-fetched rows from a single ``get_effective_rows``
            call (N3 — passed by the internal ``load_and_cache_prompt``
            caller so the fingerprint and the resolution share ONE
            SELECT). When ``None``, this function fetches its own
            rows (backward-compat for external callers + tests).
        is_custom_agent: User Directive #5A resolution-side hook —
            when ``True``, an empty ``rows`` raise
            :class:`NoPromptContentError` (carries ``(agent_id,
            version_tag, artifact)`` fields). The lifecycle coder
            owns the full A2/R-A2 ruling.

    Returns:
        A new dict with DB-resolved content replacing file content
        for overridden artifacts. Falls through to the original
        prompts on any hook error.

    Raises:
        NoPromptContentError: When ``is_custom_agent=True`` and the
            effective-rows walk yields zero rows for this (agent_id,
            version_tag, artifact) tuple (the actionable spawn-time
            failure).
    """
    if rows is None:
        rows = _fetch_effective_rows_safe(repo, agent_id, version_tag)
        if rows is None:
            # A6/C7 FAIL-OPEN — already warned + counter bumped.
            return prompts

    if not rows:
        # No DB rows → no-op; preserve pre-Phase-A contract,
        # unless this is a custom agent (User Directive #5A).
        if is_custom_agent:
            # Raise the actionable typed error — the spawn-time
            # boundary maps this to a clean failure. We raise on
            # the most-likely-failed artifact (any one in the
            # canonical CORE set works; ``soul`` is the canonical
            # fall-through — the agent typically has no ``soul``
            # content at all yet).
            raise NoPromptContentError(
                agent_id=agent_id,
                version_tag=version_tag,
                artifact="soul",
            )
        return prompts

    out = dict(prompts)
    for row in rows:
        artifact = row.artifact
        if artifact not in _HOOK_ARTIFACT_KEYS:
            # Carved-out artifact (legacy file-only carve-out or
            # an unknown key — leave the file content alone).
            # Per-file ``memories/<slug>`` keys live here too: the
            # loader's prompts dict doesn't carry per-file
            # entries; the recent-memories index surfaces them as
            # filenames.
            continue
        # Class-aware resolution (User Directive #3).
        if getattr(row, "artifact_class", ARTIFACT_CLASS_CORE_FALLBACK) == "MEMORY":
            # M16 — explicit ``is not None`` chain so an empty
            # ``selected_content`` / ``latest_content`` row is
            # served as the empty string, NOT silently falling
            # through to the template file. Truthy ``or`` would
            # treat ``""`` as missing.
            if row.selected_content is not None:
                resolved = row.selected_content
            elif getattr(row, "latest_content", None) is not None:
                resolved = row.latest_content
            else:
                # No DB content yet — leave the file alone
                # (empty fallback; proven boot-tolerant).
                continue
            out[artifact] = resolved
            continue
        # CORE class — F6 precedence. Same M16 fix.
        if row.selected_content is not None:
            resolved = row.selected_content
        elif row.latest_approved_content is not None:
            resolved = row.latest_approved_content
        else:
            # Drafts / pending rows that never got approved → no DB
            # content for this artifact; leave the file alone.
            continue
        out[artifact] = resolved
    return out


# Path to shared files (injected into all agents)
# Handle PyInstaller frozen state - use executable parent for prod
if getattr(sys, 'frozen', False):
    _base_dir = Path(sys.executable).parent
else:
    _base_dir = Path(__file__).parent.parent
PROJECT_EXPERIENCE_FILE = _base_dir / "agents" / "_prompt_system" / "project-experience.md"
KNOWLEDGE_FILE = _base_dir / "agents" / "_prompt_system" / "knowledge.md"
KNOWLEDGE_NO_FORCE_EXPLORE_FILE = _base_dir / "agents" / "_prompt_system" / "knowledge_no_force_explore.md"


# Threshold above which ``_tool_metadata`` is considered fully populated
# by the comprehensive warm scan. This is a COUNT, not a truthy check,
# because the dict is NOT empty at import time: module-level
# ``@register_tool`` sites (e.g. ``language_skip_check``, imported
# transitively via ``daemon.tools.instance``) pre-populate one entry,
# which made the old ``if _tool_metadata: return`` guard short-circuit
# forever — the warm scan never ran, and cold-boot tool docs for
# factory categories (snapshot, db, ens-db, service, system-log, ...)
# rendered EMPTY and pinned into the no-TTL PromptCache (Wave 2b
# discovery; the maintenancer W1-fix list was silently dead under this
# ``guard``. The warm scan registers ~60 entries; the stale-entry case
# (import-time @register_tool sites) contributes 1. A threshold of 20
# separates the two states with wide margin; a false "not yet run" only
# costs one redundant (idempotent) scan.
_WARM_SCAN_POPULATED_MIN_ENTRIES = 20


def _ensure_tool_metadata_populated() -> None:
    """Ensure _tool_metadata is populated by importing and scanning all tool modules.

    This enables category expansion in resolve_tool_filter() and ensures
    CATEGORY_DOC is available for load_tools_doc_for_agent().

    Safe to call multiple times - subsequent calls are no-ops once the
    metadata universe is fully populated (see
    _WARM_SCAN_POPULATED_MIN_ENTRIES: the mere PRESENCE of entries is
    not proof the scan ran — module-level @register_tool sites
    pre-populate entries at import time; a full clear via
    ``clear_registry()`` correctly re-triggers the scan).
    """
    from .tools._tool_registry import _tool_metadata, scan_tools_for_full_docs

    if len(_tool_metadata) >= _WARM_SCAN_POPULATED_MIN_ENTRIES:
        return  # Comprehensive warm scan already ran (or equivalent).
    
    # Import all tool modules to trigger @register_tool_category decorators
    # and collect @tool decorated functions
    from .tools.bash import bash
    from .tools.filesystem import (
        list_directory, read_file, glob_files, write_file, grep_files, edit_file
    )
    from .tools.time import time
    from .tools.inner_soul import create_inner_soul_tool
    from .tools.access_memory import create_access_memory_tool
    from .tools.help import create_help_tool
    from .tools.project import create_project_tools
    # W1 FIX-NOW item 5: the maintenancer-relevant category modules were
    # absent from this warm list, so cold-boot
    # load_tools_doc_for_agent("maintenancer") returned EMPTY for its
    # categories (system-log, ens-db, knowledge, system_upgrade, db) and
    # the no-TTL PromptCache could pin the empty result. Additive only:
    # factories tolerate None manager / empty ids at construction time
    # (metadata scanning never invokes the closures).
    from .tools.system_log_tools import create_system_log_tools
    from .tools.ens_db_tools import create_ens_db_tools
    from .tools.knowledge_tools import create_knowledge_tools
    from .tools.upgrade_tools import create_upgrade_tools
    from .tools.db_tools import create_db_tools
    from .tools.service_tools import create_service_tools
    # Agent Snapshot (Wave 2b): the snapshot tools are factory-created
    # (@register_tool_category decorators never run at import time).
    # Skipping this warm-list entry would pin an EMPTY "snapshot"
    # category into the no-TTL prompt cache on cold boot — the
    # load_tools_doc_for_agent("worker") path would render no Snapshot
    # section until some instance build happened to repopulate the
    # cache. Same maintenancer W1-fix pattern as the modules above.
    from .tools.snapshot_tools import create_snapshot_tools

    # Create dummy instances to get the tools (these create closures with None manager)
    # We just need the tool objects themselves for metadata scanning
    inner_soul = create_inner_soul_tool(None, "", "")
    access_memory = create_access_memory_tool("")
    project_tools = create_project_tools(None, "", "")

    # Create help tool with empty tool list first. version_tag is intentionally
    # omitted here — this is a metadata-scanning stub (agent_id=""), so tool
    # filtering never executes. The production path is in create_instance_tools()
    # which forwards the instance's bound version_tag.
    help_tool = create_help_tool([], "")

    # Maintenancer-category metadata-scan stubs (same pattern as above):
    # the factories only build closures — manager / repository /
    # pool_manager are dereferenced at CALL time, never at construction.
    system_log_tools = create_system_log_tools(None, "")
    ens_db_tools = create_ens_db_tools(None, "")
    knowledge_tools = create_knowledge_tools(None, "")
    upgrade_tools = create_upgrade_tools(None, "")
    # service (service-tool Phase 1, 1.C.6): the factory's F-guard
    # returns [] for a FALSY current_instance_id (proc_tools
    # precedent), so the warm-list stub passes a truthy placeholder
    # id — construction only builds closures (manager is
    # dereferenced at CALL time, never at construction), and the
    # @register_tool_category("service") decorators must run HERE so
    # the cold-boot metadata scan registers the category.
    service_tools = create_service_tools(None, "metadata-scan")
    # Snapshot tools (Wave 2b): construction only builds closures
    # (manager / services dereferenced at CALL time) — the
    # @register_tool_category("snapshot") decorator must run HERE so
    # the cold-boot metadata scan registers the category before the
    # no-TTL prompt cache pins tool docs.
    snapshot_tools = create_snapshot_tools(None, "", "")
    # create_db_tools reads manager.credential_manager at construction
    # (N1 — shared Fernet handle), so it needs a minimal attribute stub;
    # None is sufficient — the credential manager is only invoked inside
    # the db_conn_* closures at call time.
    _db_meta_stub = type(
        "_MetaScanManagerStub", (), {"credential_manager": None}
    )()
    db_tools = create_db_tools(_db_meta_stub, "", None, None)

    # Scan all discovered tools
    all_tools = [
        bash,
        list_directory, read_file, glob_files, write_file, grep_files, edit_file,
        time,
        inner_soul, access_memory, help_tool,
    ]

    # Add project tools if any
    if project_tools:
        all_tools.extend(project_tools)

    # W1 FIX-NOW item 5: flatten the maintenancer-category factory
    # lists (each create_*_tools returns a list of closures — appending
    # the list object itself would stringify it during the scan).
    for factory_tools in (
        system_log_tools, ens_db_tools, knowledge_tools,
        upgrade_tools, db_tools, service_tools, snapshot_tools,
    ):
        if factory_tools:
            all_tools.extend(factory_tools)

    scan_tools_for_full_docs(all_tools)


def load_tools_doc_for_agent(
    agent_id: str,
    mcp_tool_names: list[str] | None = None,
    version_tag: str | None = None,
) -> str:
    """Build tool documentation for an agent based on their allowed tools.
    
    Dynamically generates tool documentation by:
    1. Getting the agent's tool filter from the registry
    2. Resolving allowed tool names using resolve_tool_filter()
    3. For each allowed category, building a section with CATEGORY_NAME and tool list
    
    Args:
        agent_id: The agent identifier to get tool documentation for.
        mcp_tool_names: Optional list of MCP tool names for "mcp" category expansion.
        version_tag: Optional version tag (e.g., ``"v2"``) used to resolve the
            versioned tool filter (``tools.allow`` / ``tools.deny``). When
            ``None``, falls back to the base resolved agent meta so versioned
            agents see the correct allow/deny list (C1 fix — base/v1 was being
            applied to v2 instances).
        
    Returns:
        Formatted string with tool documentation sections.
    """
    from .registry import get_registry
    from .tools.instance import resolve_tool_filter, expand_allow_for_innate_skills
    from .tools._tool_registry import get_tool_categories, get_category_doc, _tool_metadata
    from .tools.help import _default_documented_tools

    # Ensure _tool_metadata is populated by scanning tool modules
    # This is needed because load_tools_doc_for_agent may be called before
    # create_instance_tools (which also scans tools). Called
    # UNCONDITIONALLY — the function self-guards via the populated-count
    # predicate (the old ``if not _tool_metadata`` predicate never fired
    # because a module-level @register_tool site pre-populates one entry
    # at import time).
    _ensure_tool_metadata_populated()

    # Get agent's tool filter from registry.
    # Prefer versioned meta when a version_tag is provided; fall back to
    # base resolved meta so versioned agents see the correct
    # allow/deny list (C1 fix — base/v1 was being applied to v2 instances).
    tool_filter: ToolFilter | None = None
    agent_innate_skills: list[str] | None = None
    try:
        registry = get_registry()
        agent_meta = (
            registry.get_version(agent_id, version_tag)
            or registry.get_resolved(agent_id)
        )
        if agent_meta is not None:
            tool_filter = agent_meta.tools
            agent_innate_skills = agent_meta.innate_skills
    except (KeyError, ValueError, RuntimeError) as e:
        logger.debug(f"Registry lookup failed for {agent_id}: {e}")
        return ""

    # Build all_tool_names set for MCP category expansion
    all_tool_names: set[str] | None = None
    if mcp_tool_names:
        all_tool_names = set(mcp_tool_names)
        logger.debug(f"Including {len(mcp_tool_names)} MCP tools in tool filter resolution for {agent_id}")

    # Resolve filter to set of allowed tool names.
    # Innate skills (e.g. "chart") implicitly grant their tool categories
    # so the system prompt lists them even when `tools.allow` omits them.
    if tool_filter is None:
        # No filter → all tools allowed — EXCEPT privileged categories
        # (R-SR16): docs mirror the execution side, which strips them from
        # the default-allow universe. Document the non-privileged universe.
        allowed_tools: set[str] | None = _default_documented_tools(mcp_tool_names)
    else:
        effective_allow = expand_allow_for_innate_skills(
            tool_filter.allow,
            agent_innate_skills,
        )
        allowed_tools = resolve_tool_filter(
            allow=effective_allow,
            deny=tool_filter.deny,
            all_tool_names=all_tool_names,
        )
        # If None returned (empty allow+deny → all tools), all tools are
        # allowed — EXCEPT privileged categories (R-SR16), same as the
        # no-filter path above.
        if allowed_tools is None:
            allowed_tools = _default_documented_tools(mcp_tool_names)
    
    # Get categories with their tools
    categories = get_tool_categories(allowed_tools)
    
    if not categories:
        return ""
    
    # Build sections for each category
    sections: list[str] = []
    for category_key, tool_names in sorted(categories.items()):
        try:
            cat_result = get_category_doc(category_key)
            if cat_result is not None:
                category_name, category_doc = cat_result
            else:
                category_name = category_key
                category_doc = ""
        except KeyError:
            # Category not in CATEGORY_MODULES - use key as name
            category_name = category_key
            category_doc = ""
        
        # Sort tools for deterministic output
        sorted_tools = sorted(tool_names)
        tools_list = ", ".join(sorted_tools)
        
        section = f"## {category_name}\n"
        if category_doc:
            section += f"{category_doc}\n\n"
        section += f"**Available tools:** {tools_list}\n"
        section += "Use tool_help(\"tool_name\") for detailed documentation."
        
        sections.append(section)
    
    return "\n\n".join(sections)


def load_project_experience() -> str:
    """Load project experience instructions shared by all agents.
    
    Returns:
        Content of project-experience.md or empty string if not found.
    """
    if PROJECT_EXPERIENCE_FILE.exists():
        return PROJECT_EXPERIENCE_FILE.read_text(encoding="utf-8")
    return ""


def load_shared_knowledge(no_force_explore: bool = False) -> str:
    """Load shared knowledge base instructions. Only loaded when RAG is enabled.
    
    Args:
        no_force_explore: If True, load knowledge_no_force_explore.md instead
                         (for leader/orchestrator agents that delegate exploration).
    
    Returns:
        Content of knowledge file or empty string if not found/disabled.
    """
    if not is_rag_enabled():
        return ""
    knowledge_file = KNOWLEDGE_NO_FORCE_EXPLORE_FILE if no_force_explore else KNOWLEDGE_FILE
    if knowledge_file.exists():
        return knowledge_file.read_text(encoding="utf-8")
    return ""


def load_recent_memories(
    agent_dir: Path,
    limit: int = 5,
    include_archived: bool = False,
    archive_limit: int = 5,
    db_memory_keys: Optional[list[str]] = None,
) -> str:
    """Load list of recent memory filenames from memories/ directory.

    Returns filenames only (not content) to minimize token usage.
    When include_archived=True, also lists files from memories/archive/YYYY/MM/.

    User Directive #3 (2026-10-14) — ``db_memory_keys`` extension:
    the per-file MEMORY-class DB rows (artifact=``memories/<slug>``)
    surface in this filename index alongside the on-disk files.
    Each memory file is its own key (versions = its revisions;
    latest = current content; selection = per-file pin per R-B3).
    The merge rule: any ``memories/<slug>`` key present in
    ``db_memory_keys`` that has no on-disk file is rendered as
    ``memories/<slug>`` in the output; keys with on-disk files
    are de-duplicated (the on-disk file wins, matches today's
    behavior — the DB row is for resolution content, not for
    filename tracking).

    The merge is FILE-WINS — filenames list the existence of
    memory surface; the DB content lives separately in
    ``EffectiveRow.latest_content`` (loader.py:resolve_prompts_from_db
    MEMORY branch). The de-dup keeps the filename list consistent
    with the loader's existing single-pass behavior.
    """
    memories_dir = agent_dir / "memories"
    on_disk_lines: list[str] = []
    on_disk_keys: set[str] = set()

    if memories_dir.exists() and memories_dir.is_dir():
        # Active memory files
        memory_files = sorted(
            [f for f in memories_dir.iterdir() if f.suffix == ".md" and not f.is_symlink() and f.is_file()],
            key=lambda p: p.name,
            reverse=True
        )[:limit]

        for f in memory_files:
            on_disk_lines.append(f"- {f.name}")
            # Strip the .md suffix to derive the "<slug>" key —
            # used for de-duping against db_memory_keys whose
            # shape is "memories/<slug>" (NOT "memories/<slug>.md").
            on_disk_keys.add(f"memories/{f.name[:-3]}")

        # Archived memory files
        if include_archived:
            archive_dir = memories_dir / "archive"
            if archive_dir.exists() and archive_dir.is_dir():
                archive_files = []
                for year_dir in sorted(archive_dir.iterdir(), reverse=True):
                    if not year_dir.is_dir() or not year_dir.name.isdigit():
                        continue
                    for month_dir in sorted(year_dir.iterdir(), reverse=True):
                        if not month_dir.is_dir() or not month_dir.name.isdigit():
                            continue
                        for f in month_dir.iterdir():
                            if f.suffix == ".md" and not f.is_symlink() and f.is_file():
                                archive_files.append(f)

                # Sort by full relative path (YYYY/MM/filename.md) for correct chronological order
                archive_files.sort(key=lambda p: str(p.relative_to(archive_dir)), reverse=True)
                archive_files = archive_files[:archive_limit]

                for f in archive_files:
                    # Format as archive/YYYY/MM/filename.md
                    relative = f.relative_to(archive_dir)
                    on_disk_lines.append(f"- archive/{relative}")

    # Merge DB-only MEMORY keys. Filter to keys whose MEMORY
    # row exists in the DB but has no on-disk file
    # (``memories/<slug>`` not in ``on_disk_keys``). Sorted for
    # deterministic output; respects ``limit`` budget roughly
    # (the budget is consulted LATER if the merge overflows —
    # see the floor above).
    db_only_lines: list[str] = []
    if db_memory_keys:
        for key in sorted(db_memory_keys):
            # Strip "memories/" prefix; the line shape is the
            # filename only ("<slug>.md"). If the on-disk set
            # already covers this key (a file exists), skip it
            # (file wins — content lives separately).
            if not key.startswith("memories/"):
                continue
            slug = key[len("memories/"):]
            if f"memories/{slug}" in on_disk_keys:
                continue
            db_only_lines.append(f"- {slug}.md")

    lines = on_disk_lines + db_only_lines
    if not lines:
        return ""

    return "\n".join(lines)


def _resolve_innate_skill_paths(agent_dir: Path, meta: dict) -> list[tuple[str, Path]]:
    """Resolve innate skill file paths from meta config."""
    innate_skills_dir = agent_dir.parent / "_prompt_system" / "innate-skills"
    return [
        (name, innate_skills_dir / name / "skill.md")
        for name in sorted(set(meta.get("innate_skills", [])))
    ]


def load_agent_skills(agent_dir: Path, meta: dict | None = None) -> dict[str, str]:
    """Load agent skills from centralized innate-skills or local skills/ directory.

    When meta is provided with a non-empty innate_skills list, loads from the
    centralized agents/_prompt_system/innate-skills/ directory. Otherwise falls back to scanning
    the agent's own skills/ directory for backward compatibility.

    An empty innate_skills array ([]) is treated as absent, triggering legacy mode.
    """
    skills: dict[str, str] = {}

    # New path: load from centralized innate-skills registry
    # NOTE: truthy check (not just "in") ensures empty array [] falls through to legacy
    if meta and meta.get("innate_skills"):
        for skill_name, skill_file in _resolve_innate_skill_paths(agent_dir, meta):
            if skill_file.exists():
                skills[skill_name] = skill_file.read_text(encoding="utf-8")
            else:
                logger.warning(f"Innate skill '{skill_name}' declared in meta.json but not found at {skill_file}")
        return skills

    # Legacy fallback: load from agent's own skills/ directory
    skills_dir = agent_dir / "skills"
    if not skills_dir.exists() or not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if skill_dir.is_dir():
            skill_file = skill_dir / "skill.md"
            if skill_file.exists():
                skills[skill_dir.name] = skill_file.read_text(encoding="utf-8")
    return skills


def load_agent_prompts(agent_dir: Path) -> dict[str, str]:
    """Load all markdown files from agent directory.
    
    Loads base prompts (soul.md, tools.md, workflow.md, rule.md, memory.md) and optional skill.md.
    For multiple skills, use load_agent_skills() which loads from skills/ directory.
    
    Args:
        agent_dir: Path to the agent directory containing prompt files.
        
    Returns:
        Dict with filename (without .md) as key, content as value.
        Skips missing files.
    """
    prompt_files = ["soul.md", "skill.md", "workflow.md", "rule.md", "memory.md"]
    prompts: dict[str, str] = {}
    
    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            prompts[filename.replace(".md", "")] = filepath.read_text(encoding="utf-8")
    
    # Load tools_note.md with fallback to tools.md (for backward compatibility)
    tools_note_path = agent_dir / "tools_note.md"
    tools_fallback_path = agent_dir / "tools.md"
    
    if tools_note_path.exists():
        prompts["tools"] = tools_note_path.read_text(encoding="utf-8")
    elif tools_fallback_path.exists():
        prompts["tools"] = tools_fallback_path.read_text(encoding="utf-8")
    
    return prompts


def compose_system_prompt(
    prompts: dict[str, str], 
    skills: dict[str, str] | None = None,
    dynamic_tools: str = "",
    project_experience: str = "",
    recent_memories: str = "",
    shared_knowledge: str = ""
) -> str:
    """Compose system prompt from prompts dict and optional skills.
    
    Args:
        prompts: Dict with filename (without .md) as key, content as value.
                 Expected keys: soul, rule, skill, tools, workflow, memory
        skills: Optional dict with skill name as key, skill.md content as value.
                Loaded from agent's skills/ directory.
        dynamic_tools: Dynamic tools content from load_tools_doc_for_agent() (agent's available tools).
        project_experience: Project experience content from project-experience.md (shared by all agents).
        recent_memories: List of recent memory filenames.
        shared_knowledge: Shared knowledge base content from knowledge.md.
        
    Returns:
        Composed system prompt with sections in order:
        1. soul.md (identity/personality - who I am)
        2. rule.md (constraints - highest priority)
        3. skill.md (base skill, if exists - backward compat)
        4. All skills from skills/ directory (each as separate section)
        5. dynamic_tools (from load_tools_doc_for_agent - available tools based on agent config)
        6. tools.md (agent-specific tools note)
        7. workflow.md (methodology)
        8. memory.md (knowledge)
        9. Recent memories (filenames only)
        10. shared_knowledge (from _prompt_system/knowledge.md)
        11. project-experience.md (how to use .agents directory for project knowledge)
        Separated by "\n\n---\n\n". Headers come from the file content itself.
    """
    sections: list[str] = []
    
    # 1. Add soul section first (identity - who I am)
    if "soul" in prompts:
        content = prompts["soul"].strip()
        if content:
            sections.append(content)
    
    # 2. Add rule section (constraints - highest priority)
    if "rule" in prompts:
        content = prompts["rule"].strip()
        if content:
            sections.append(content)
    
    # 3. Add base skill if exists (backward compatibility)
    if "skill" in prompts:
        content = prompts["skill"].strip()
        if content:
            sections.append(content)
    
    # 4. Add all skills from skills/ directory (sorted for deterministic order)
    if skills:
        for skill_name in sorted(skills.keys()):
            skill_content = skills[skill_name]
            content = skill_content.strip()
            if content:
                sections.append(content)
    
    # 5. Add dynamic tools section (from load_tools_doc_for_agent)
    if dynamic_tools.strip():
        sections.append(dynamic_tools.strip())
    
    # 6. Add agent-specific tools note (tools_note.md with fallback to tools.md)
    if "tools" in prompts:
        agent_tools = prompts["tools"].strip()
        if agent_tools:
            sections.append(agent_tools)
    
    # 7-8. Add workflow and memory sections
    for key in ["workflow", "memory"]:
        if key in prompts:
            content = prompts[key].strip()
            if content:
                sections.append(content)
    
    # 9. Add recent memories section (filenames only, max 5)
    if recent_memories:
        sections.append(f"## Recent Memories\n\n{recent_memories}")
    
    # 10. Add shared knowledge section (from _prompt_system/knowledge.md)
    if shared_knowledge.strip():
        # Strip leading H1 heading from knowledge content (file has its own, we add section heading)
        content = shared_knowledge.strip()
        content = re.sub(r'^#\s+.*\n*', '', content, count=1)
        sections.append(f"## Knowledge Base\n\n{content}")
    
    # 11. Add project experience section (shared .agents directory usage)
    if project_experience.strip():
        sections.append(f"## Project Experience\n\n{project_experience.strip()}")
    
    return "\n\n---\n\n".join(sections)


def estimate_tokens(text: str) -> int:
    """Estimate token count using tiktoken with cl100k_base encoding.
    
    Args:
        text: Text to count tokens for.
        
    Returns:
        Estimated token count.
    """
    import tiktoken
    
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total token count for a list of LangChain messages.

    Accounts for per-message overhead (role tokens, formatting) that LLMs add.
    Uses rough overhead estimates based on OpenAI token accounting:
    - Each message: +4 tokens (role markers, separators)
    - Tool calls: additional tokens for function call formatting

    Escape-Hatch Hardening round 2 (Lane 3) — estimator realism
    for multi-modal content blocks. Pre-Lane-3 dict-content blocks
    counted ONLY by ``block.get("text", "")`` — image / file / audio
    blocks counted ≈ 0 tokens, which understated the true
    provider-billed payload. Lane 3 reads the per-block-type
    weights from :class:`daemon.config.CompactionConfig` (lazy
    import — kept lazy to avoid the
    ``loader -> config -> loader`` cycle):
      * ``image_block_tokens`` (default 170) — per OpenAI's
        documented high-detail image size.
      * ``file_block_tokens`` (default 100) — per-file overhead
        + a small body heuristic.
      * ``audio_block_tokens`` (default 100) — flat per-second
        approximation.
    Detection: a block is classified as image / file / audio by the
    presence of the canonical OpenAI / Anthropic keys (``type``,
    ``source_type``, ``mime_type``); the per-block cost is read off
    the matching config field, falling back to the text estimate
    when the block carries text (some blocks — multimodal —
    include both).

    Tool-schema counting is OUT of scope (deferred per the brief).

    Args:
        messages: List of LangChain BaseMessage objects.

    Returns:
        Estimated total token count including overhead.
    """
    if not messages:
        return 0

    # Lazy config read so the loader module remains importable
    # without the daemon stack initialized (test fixtures,
    # snapshot_executor, offline REPL, etc.). The cache prevents
    # hitting pydantic-settings per message in tight loops.
    _block_weights: tuple[int, int, int] | None = None

    def _get_block_weights() -> tuple[int, int, int]:
        nonlocal _block_weights
        if _block_weights is not None:
            return _block_weights
        try:
            from .config import CompactionConfig
            cfg = CompactionConfig()
            weights = (
                int(getattr(cfg, "image_block_tokens", 170)),
                int(getattr(cfg, "file_block_tokens", 100)),
                int(getattr(cfg, "audio_block_tokens", 100)),
            )
        except Exception:
            weights = (170, 100, 100)
        _block_weights = weights
        return _block_weights

    def _classify_block_type(block: dict) -> str:
        """Return one of ``"image" / "file" / "audio" / "text"``
        for a content block. Detection is key-based; we honor
        the OpenAI ``type`` field first, fall back to the
        Anthropic ``source_type`` field, fall back to
        ``mime_type`` substring. Any unrecognized shape returns
        ``"text"`` so the text estimator (which already
        counted the block via ``block.get("text", "")``) is
        the safety net — pre-Lane-3 behavior preserved for
        unfamiliar block shapes.
        """
        if not isinstance(block, dict):
            return "text"
        # OpenAI / Anthropic common schema: "type": "image" | "file" | "audio"
        otype = block.get("type")
        if isinstance(otype, str):
            ot_norm = otype.strip().lower()
            if ot_norm in ("image", "input_image", "image_url", "image_block"):
                return "image"
            if ot_norm in ("file", "input_file", "file_block", "document"):
                return "file"
            if ot_norm in ("audio", "input_audio", "audio_block"):
                return "audio"
        # Anthropic-style: "source_type": "image" | "document" | ...
        stype = block.get("source_type")
        if isinstance(stype, str):
            st_norm = stype.strip().lower()
            if "image" in st_norm:
                return "image"
            if "audio" in st_norm:
                return "audio"
            if "document" in st_norm or "file" in st_norm:
                return "file"
        # MIME type fallback: data: URLs / file refs sometimes
        # carry only the mime_type key.
        mime = block.get("mime_type") or block.get("mimetype")
        if isinstance(mime, str):
            ml = mime.lower()
            if ml.startswith("image/"):
                return "image"
            if ml.startswith("audio/"):
                return "audio"
            # Treat all other mime types as file (PDF, video, etc.)
            if (
                ml.startswith("application/")
                or ml.startswith("video/")
                or ml.startswith("text/")
            ):
                return "file"
        # Check for an OpenAI image_url structure: nested
        # ``image_url`` key.
        if "image_url" in block or "image" in block:
            return "image"
        return "text"

    def _block_tokens(block: dict, weights: tuple[int, int, int]) -> int:
        """Estimate tokens for a single dict block (Lane 3).

        Pre-Lane-3 behavior counted only the ``block.get("text",
        "")`` part. Lane 3 ADDS the per-block-type weight for
        non-text blocks (image / file / audio) on top of any
        nested text. The text cost still happens for blocks
        that carry both an inline caption and a heavy
        binary reference — the multi-modal case.
        """
        text_part = ""
        if isinstance(block, dict):
            text_part = block.get("text", "") or ""
        text_cost = estimate_tokens(str(text_part)) if text_part else 0
        btype = _classify_block_type(block)
        if btype == "image":
            return text_cost + weights[0]
        if btype == "file":
            return text_cost + weights[1]
        if btype == "audio":
            return text_cost + weights[2]
        # Text block — no additional cost; matches pre-Lane-3.
        return text_cost

    total = 0
    block_weights = _get_block_weights()
    for msg in messages:
        # Content tokens
        content = getattr(msg, "content", "") or ""
        if isinstance(content, list):
            # Some models return content as list of blocks.
            # Lane 3: each block is now measured by its
            # classified weight, not just its ``text`` field.
            for block in content:
                if isinstance(block, dict):
                    total += _block_tokens(block, block_weights)
                else:
                    total += estimate_tokens(str(block))
        else:
            total += estimate_tokens(str(content))

        # Per-message overhead (~4 tokens for role markers, separators)
        total += 4

        # Tool calls overhead
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    total += estimate_tokens(str(tc.get("args", {})))
                    total += estimate_tokens(tc.get("name", ""))
                else:
                    total += estimate_tokens(str(getattr(tc, "args", {})))
                    total += estimate_tokens(getattr(tc, "name", ""))
                total += 3  # function call formatting overhead

        # Tool response metadata
        if hasattr(msg, "name") and msg.name:
            total += estimate_tokens(msg.name) + 2

        # Additional kwargs (thinking, reasoning)
        if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
            for key, val in msg.additional_kwargs.items():
                if key in ("reasoning_content", "thinking"):
                    total += estimate_tokens(str(val))

    return total


class PromptCache:
    """In-memory cache for compiled prompts.
    
    Uses agent_id and MCP tool names as cache key for logical identification
    rather than filesystem path. MCP tool names are included because different
    instances may have different MCP tools, which affects the tools section.
    """
    
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, int, dict[str, float]]] = {}
    
    def _make_key(self, agent_id: str, mcp_tool_names: list[str] | None, version_tag: str | None = None) -> str:
        """Create a cache key from agent_id, version_tag, and MCP tool names.

        D15: version_tag is included to prevent base/tagged cache collision.
        Base "developer" and tagged "developer[v2]" get different keys.
        When version_tag is None (or empty), the key format is identical to
        the pre-D15 format ``f"{agent_id}::{normalized_mcp}"``, preserving
        backward compatibility for existing cached entries.

        Args:
            agent_id: The agent identifier.
            mcp_tool_names: Optional list of MCP tool names.
            version_tag: Optional version tag (e.g., "v2") from agent metadata.
                When set, the cache key becomes ``f"{agent_id}[{version_tag}]::{normalized_mcp}"``.

        Returns:
            Cache key string.
        """
        # Normalize MCP tool names: sort and join, use empty string for None/empty
        if mcp_tool_names:
            normalized_mcp = ",".join(sorted(mcp_tool_names))
        else:
            normalized_mcp = ""
        # Include version_tag to prevent base/tagged cache collision (D15).
        # Backward compat: when version_tag is falsy, omit the bracket suffix entirely.
        tag_suffix = f"[{version_tag}]" if version_tag else ""
        return f"{agent_id}{tag_suffix}::{normalized_mcp}"

    def get(self, agent_id: str, mcp_tool_names: list[str] | None = None, version_tag: str | None = None) -> tuple[str, int] | None:
        """Get cached prompt for agent.

        Args:
            agent_id: The agent identifier (e.g., "developer").
            mcp_tool_names: Optional list of MCP tool names.
            version_tag: Optional version tag (e.g., "v2") to distinguish tagged
                versions from base (D15). Defaults to None for backward compat.

        Returns:
            Tuple of (compiled_prompt, token_count) or None if not cached.
        """
        key = self._make_key(agent_id, mcp_tool_names, version_tag)
        if key not in self._cache:
            return None

        return (self._cache[key][0], self._cache[key][1])

    def set(self, agent_id: str, prompt: str, tokens: int, mtimes: dict[str, float], mcp_tool_names: list[str] | None = None, version_tag: str | None = None) -> None:
        """Store prompt in cache.

        Args:
            agent_id: The agent identifier (e.g., "developer").
            prompt: Compiled system prompt.
            tokens: Token count.
            mtimes: Dict of filename to modification time.
            mcp_tool_names: Optional list of MCP tool names.
            version_tag: Optional version tag (e.g., "v2") to distinguish tagged
                versions from base (D15). Defaults to None for backward compat.
        """
        key = self._make_key(agent_id, mcp_tool_names, version_tag)
        self._cache[key] = (prompt, tokens, mtimes)

    def invalidate(self, agent_id: str, mcp_tool_names: list[str] | None = None, version_tag: str | None = None) -> None:
        """Remove agent from cache.

        Args:
            agent_id: The agent identifier (e.g., "developer").
            mcp_tool_names: Optional list of MCP tool names.
            version_tag: Optional version tag (e.g., "v2") to distinguish tagged
                versions from base (D15). Defaults to None for backward compat.
        """
        key = self._make_key(agent_id, mcp_tool_names, version_tag)
        self._cache.pop(key, None)


def load_and_cache_prompt(
    agent_id: str,
    agent_dir: Path,
    cache: PromptCache,
    mcp_tool_names: list[str] | None = None,
    version_tag: str | None = None,
    *,
    is_custom_agent: bool = False,
) -> tuple[str, int]:
    """Load and cache agent prompts including multiple skills.

    Args:
        agent_id: The agent identifier (e.g., "developer").
        agent_dir: Path to the agent directory.
        cache: PromptCache instance.
        mcp_tool_names: Optional list of MCP tool names for "mcp" category expansion.
        version_tag: Optional version tag (e.g., "v2") to distinguish tagged
            versions from base in the cache key (D15). Defaults to None for
            backward compatibility — existing callers do not need to thread it.
        is_custom_agent: User Directive #5A — ``True`` when the caller has
            resolved this agent against a custom_agent_configs row. The
            loader threads the flag into ``resolve_prompts_from_db`` so
            the empty-state posture flips to ``NoPromptContentError`` for
            R-A2 lifecycle customs. Defaults to ``False`` so the existing
            file-defined path stays unchanged (pre-Phase-C call sites).

    Returns:
        Tuple of (system_prompt, token_count).
    """
    # Calculate current mtimes for all prompt files
    prompt_files = ["soul.md", "skill.md", "workflow.md", "rule.md", "memory.md"]
    current_mtimes: dict[str, float] = {}
    
    # Include project experience file mtime for cache invalidation
    if PROJECT_EXPERIENCE_FILE.exists():
        current_mtimes["project-experience.md"] = PROJECT_EXPERIENCE_FILE.stat().st_mtime
    
    # Load meta.json ONCE with mtime tracking and error handling
    meta_path = agent_dir / "meta.json"
    meta = None
    if meta_path.exists():
        current_mtimes["meta.json"] = meta_path.stat().st_mtime
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Failed to parse {meta_path}")
            meta = None

    # Track knowledge file mtime for cache invalidation (variant depends on agent meta)
    no_force_explore = bool(meta and meta.get("no_force_explore"))
    knowledge_file = KNOWLEDGE_NO_FORCE_EXPLORE_FILE if no_force_explore else KNOWLEDGE_FILE
    if knowledge_file.exists():
        current_mtimes[knowledge_file.name] = knowledge_file.stat().st_mtime

    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            current_mtimes[filename] = filepath.stat().st_mtime

    # Include mtime for tools_note.md or tools.md for cache invalidation
    tools_note_path = agent_dir / "tools_note.md"
    tools_fallback_path = agent_dir / "tools.md"
    if tools_note_path.exists():
        current_mtimes["tools_note.md"] = tools_note_path.stat().st_mtime
    elif tools_fallback_path.exists():
        current_mtimes["tools.md"] = tools_fallback_path.stat().st_mtime

    # Include mtimes for all skill files (mode-aware: innate-skills or legacy)
    if meta and meta.get("innate_skills"):
        # Innate-skills mode: track centralized skill files
        for skill_name, skill_file in _resolve_innate_skill_paths(agent_dir, meta):
            if skill_file.exists():
                current_mtimes[f"_prompt_system/innate-skills/{skill_name}/skill.md"] = skill_file.stat().st_mtime
    else:
        # Legacy mode: scan agent's own skills/ directory
        skills_dir = agent_dir / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
                if skill_dir.is_dir():
                    skill_file = skill_dir / "skill.md"
                    if skill_file.exists():
                        current_mtimes[f"skills/{skill_dir.name}/skill.md"] = skill_file.stat().st_mtime
    
    # Track memories/ directory mtimes for cache invalidation
    memories_dir = agent_dir / "memories"
    if memories_dir.exists() and memories_dir.is_dir():
        for memory_file in memories_dir.iterdir():
            if memory_file.is_file() and memory_file.suffix == ".md":
                try:
                    current_mtimes[f"memories/{memory_file.name}"] = memory_file.stat().st_mtime
                except (PermissionError, OSError):
                    pass  # Skip broken symlinks and permission issues
    
    # Phase A T7 (C1) + N3: compute the synthetic
    # ``db_versions_fingerprint`` from the prompt-version DB so the
    # cache self-heals on any DB change (approve / select / clear).
    # Folded into ``current_mtimes`` so the equality check at the
    # cache-hit step busts the cache whenever the fingerprint changes
    # — across every MCP-variant key that the single-key
    # ``invalidate`` cannot reach. FAIL-OPEN: any hook exception →
    # fingerprint = "" + WARN log + counter bump; the mtime equality
    # check below still works. N3 folds the cache-miss path's two
    # ``get_effective_rows`` calls into ONE — fetch once here, feed
    # both the fingerprint and the content replacement. N4 unifies
    # the sentinel so missing repo and zero-rows both produce ``""``
    # (no benign boot-moment cache bust).
    _repo = _get_prompt_version_repo()
    _db_rows: list[Any] | None
    if _repo is not None:
        _db_rows = _fetch_effective_rows_safe(
            _repo, agent_id, version_tag or "",
        )
    else:
        # N4: missing repo → no rows → fingerprint = "".
        _db_rows = None
    _db_fp = _compute_db_versions_fingerprint(_db_rows)
    current_mtimes["__prompt_versions_fingerprint__"] = _db_fp

    # Check cache (include mcp_tool_names and version_tag in cache key)
    cached = cache.get(agent_id, mcp_tool_names, version_tag)
    if cached is not None:
        # Get stored mtimes from cache using the same key
        cache_key = cache._make_key(agent_id, mcp_tool_names, version_tag)
        stored_mtimes = cache._cache.get(cache_key, (None, None, {}))[2]

        # Compare mtimes (incl. the synthetic db_versions_fingerprint —
        # Phase A T7 / C1 mechanism: cache self-heals on any DB change)
        if stored_mtimes == current_mtimes:
            logger.debug(f"Prompt cache hit for {agent_id} (mcp_tools={len(mcp_tool_names) if mcp_tool_names else 0}, version_tag={version_tag})")
            return cached

    # Cache miss or files changed - reload
    prompts = load_agent_prompts(agent_dir)

    # Phase A T6 / T7 / T8 — resolution hook. Inserted between the
    # file-reload (:920 above) and ``compose_system_prompt`` (:926
    # below) so spawn, restore, and reconstruction all see the same
    # DB-resolved content. The hook is FAIL-OPEN (A6/C7): any DB
    # exception returns the file-loaded prompts unchanged. The repo
    # is obtained lazily here so the loader has zero DB imports at
    # module load time. N3: pass the pre-fetched ``_db_rows`` so the
    # fingerprint and the content share ONE SELECT (A8).
    if _repo is not None:
        prompts = resolve_prompts_from_db(
            prompts, agent_id, version_tag or "", _repo, rows=_db_rows,
            is_custom_agent=is_custom_agent,
        )

    skills = load_agent_skills(agent_dir, meta)
    dynamic_tools = load_tools_doc_for_agent(agent_id, mcp_tool_names, version_tag)
    project_experience = load_project_experience()
    recent_memories = load_recent_memories(agent_dir)
    shared_knowledge = load_shared_knowledge(no_force_explore=no_force_explore)
    system_prompt = compose_system_prompt(prompts, skills, dynamic_tools, project_experience, recent_memories, shared_knowledge)
    tokens = estimate_tokens(system_prompt)

    # Update cache (include mcp_tool_names and version_tag in cache key).
    # The mtime snapshot already carries the synthetic
    # db_versions_fingerprint (Phase A T7 / C1) — see the
    # current_mtimes build above.
    cache.set(agent_id, system_prompt, tokens, current_mtimes, mcp_tool_names, version_tag)

    return (system_prompt, tokens)
