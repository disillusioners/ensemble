"""Agent Snapshot tools (agent-snapshot v1 — Wave 2b, PR6).

The tool surface for the snapshot subsystem: creation (R9
search-before-create protocol + R12 supersession) and read-only
search, plus the warm-start consumption flow that backs the UNIFIED
``spawn_instance`` tool. Conventions follow the house tool shape:

* Pydantic ``BaseModel`` + ``Annotated[..., Field]`` inputs (mirror
  ``SpawnInstanceInput``, ``daemon/tools/instance.py``).
* dict-with-``error`` returns (mirror ``critical_notes.py``).
* Error strings, never raises.

**Unify-spawn-tools (2026-10-05):** ``spawn_hot_instance`` was
REMOVED as a standalone tool. Warm-start consumption now rides
``spawn_instance`` (``daemon/tools/instance.py``), gated by the
TARGET (being-spawned) agent's ``snapshot_enabled: true`` meta.json
flag. The ~500-line resolution state machine was NOT duplicated —
it lives HERE as module-level helpers with a single source of
truth, imported by the unified spawn tool:

* :func:`resolve_spawn_snapshot` — pre-spawn: explicit-id verify →
  internal search → staleness → ``verify=git`` anchor (R14
  §4.3/§5.2). Never raises: any fault degrades to a ``no-hit`` cold.
* :func:`finalize_spawn_snapshot` — post-spawn: the R6b atomic
  digest stamp BEFORE any enqueue, then the R16 warm counter +
  ``[SnapshotSpawnWarm]`` observability line.
* :func:`format_snapshot_citation` — the ``[snapshot] started:
  warm|cold — …`` citation line appended to the unified tool's
  STRING return.

Category layout — one module, one grant category:

* ``snapshot`` — ``snapshot_create`` + ``snapshot_search``. The
  category exists for CATEGORY_DOC grouping and per-tool grant
  targets; creator agents (worker/coder/tester) hold the two tool
  NAMES via per-tool ``tools.allow`` entries (Rev 5 P2-v1).
  Consumption is gated per-agent by ``snapshot_enabled`` (registry
  field consulted against the TARGET's resolved meta), not by a
  tool grant — the former ``instance``-category ride-along is gone.

R15 settings toggle (write side ONLY): ``snapshot_create`` consults
the async :func:`daemon.services.snapshot_settings_utils.get_snapshot_create_enabled`
directly on the loop (default OFF — opt-in rollout). The module-level
``is_snapshot_create_enabled`` sync stub remains ONLY for genuine sync
contexts (metadata-scan stubs / boot probes) and always answers the
fail-closed default.
``snapshot_search`` (read-only) is NEVER gated, and the warm-start
consumption path inside ``spawn_instance`` is NEVER gated by R15
either — consumption is gated ONLY by the target agent's
``snapshot_enabled`` flag.

R14 fail-soft contract (preserved under the unified tool):
consumption NEVER turns a snapshot miss into an error —
expired/stale/no-hit/verify-failed all degrade to a plain cold
spawn with a ``[snapshot] started: cold`` citation line. Errors are
reserved for authorization failure or system fault (surfaced by the
unified tool's own string error ladder). The former ``started:
"blocked"`` dict shape is gone with the tool: a team-membership
denial returns spawn_instance's plain ERROR string (unchanged
behavior).

R18 auto-dispatch (2026-10-04 — contract trap fix) now lives in
``daemon/tools/instance.py`` (``_auto_dispatch_first_turn``): the
``task`` argument is enqueued as the child's first turn by default
so callers do NOT need a follow-up ``send_message(instance_id,
task)`` call. The enqueue tripwire (non-queued / None result ⇒ loud
ERROR tail) moved with it — see that module for the invariant
pins.

Size rationale (2026-10-04, updated 2026-10-05): the module hosts
two tightly-coupled Wave-2b tools (snapshot_create, snapshot_search)
plus the Wave-3 settings gate, R16 monitoring counters, R12
supersession, and the unified-spawn helper surface (R9 verdict, R6b
ordering, staleness, verify=git anchor, D8 cross-project handoff) —
each is a non-trivial state machine. A split would force shared
helpers (``_overlapping_tags``, ``_git_repo_state``, the
``_safe_inc_*`` wrappers) into a third module and add import-order
complexity. The trigger to split remains the addition of a FOURTH
tool surface or a single tool's comment block exceeding ~500 lines.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from daemon.repositories.instance.repository import _MAX_TRAVERSAL_DEPTH
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services.snapshot_executor import (
    SNAPSHOT_FRESH_MAX_AGE_DAYS,
    SnapshotService,
    normalize_judgment_tags,
)
from daemon.services.snapshot_search_service import SnapshotSearchService
from daemon.services.snapshot_settings_utils import get_snapshot_create_enabled
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "snapshot"
CATEGORY_DOC = """\
Agent Snapshot — task-scoped working-state captures for warm starts.

A snapshot is a distilled digest of one instance's working experience
(decisions, gotchas, conventions, artifact refs). Two tools:

- `snapshot_create` — capture a target instance's experience as a
  snapshot (subject to the daemon settings toggle). Runs the
  search-before-create protocol internally: it searches first and may
  return an existing snapshot instead of creating a new one
  (status "reused-existing-snapshot-id"). Capture promptly after the
  target completes — checkpoint cleanup deletes terminal context at
  the 168h TTL, so a capture started after that window fails soft to
  a "failed" snapshot row (the context is gone).
- `snapshot_search` — read-only search over active snapshots in a
  project. Returns metadata + a digest preview; the full body is
  consumed at warm start.

Warm-start CONSUMPTION is not a tool here: the unified
`spawn_instance` tool (daemon/tools/instance.py) carries it, gated
by the TARGET agent's `"snapshot_enabled": true` meta.json flag
(unify-spawn-tools, 2026-10-05 — `spawn_hot_instance` was removed).
When the gate is on, callers pass `snapshot_id` / `tags` /
`verify` / `allow_cross_project` to `spawn_instance` and the result
string carries a `[snapshot] started: warm|cold — …` citation line
(cold fallback on any miss/expiry/verify-failure — never an error).
`task` + `auto_dispatch` (R18) auto-enqueue the task as the child's
first turn. Project-less callers (e.g. an instance whose project is
unknown) always get a cold spawn — the internal search has no
project to scope against (D8, permanent).

Judgment tags are typed `dim:value` strings — `kind:` from the fixed
enum (investigation, defect-verification, implementation, review,
refactor, release-gate, design-exploration, environment-setup) plus
2-3 of `subsystem:` / `feature:` / `topic:` (lowercase-kebab values).
"""

# ── R15 settings toggle (write side ONLY — single seam) ────────────────
#: R15 unset-fallback: when no manager is wired (e.g. boot probes,
#: metadata-scan stubs) or when the metadata read fails, the helper
#: MUST read as OFF (fail-closed opt-in rollout). This constant is
#: the only place the default lives — Wave 3 wired the real setting
#: via the FE settings-menu item (``daemon/routers/settings.py
#: snapshot-create`` endpoints) into the helper body below.
_SNAPSHOT_CREATE_ENABLED_DEFAULT = False


def is_snapshot_create_enabled(manager: Any | None = None) -> bool:
    """R15 gate source — SYNC CONTEXTS ONLY; answers the fail-closed default.

    .. deprecated::
        The async tool surface no longer routes through this helper.
        The ``snapshot_create`` call site awaits
        :func:`daemon.services.snapshot_settings_utils.get_snapshot_create_enabled`
        directly (R15 review BLOCKER fix: the previous implementation
        bridged the async read with
        ``run_coroutine_threadsafe(...).result(timeout=2.0)`` — from
        inside the running loop's own thread that schedules the
        coroutine on the very loop it then blocks on, a guaranteed
        TimeoutError that degraded every call to the default and
        stalled the shared daemon loop ~2s).

    This stub remains for GENUINE sync contexts only — metadata-scan
    stubs and boot-time probes that wire no manager (or run with no
    event loop) — where the fail-closed default (OFF, opt-in
    rollout) is the correct answer. Resolution:

    * Any call → ``_SNAPSHOT_CREATE_ENABLED_DEFAULT`` (``False``).
      There is intentionally NO sync DB path: a sync caller that
      needs the real setting must run the async util on a worker
      thread / its own loop, not bridge it here.
    """
    return _SNAPSHOT_CREATE_ENABLED_DEFAULT


# ── Input models (convention: SpawnInstanceInput, instance.py) ─────────


class SnapshotCreateInput(BaseModel):
    """Input model for snapshot_create tool."""

    target_instance_id: Annotated[str, Field(
        description=(
            "The instance whose experience to capture — yourself "
            "(your own instance_id) or a transitive descendant of "
            "yours."
        ),
    )]

    name: Annotated[str, Field(
        description=(
            "Human-readable snapshot name, e.g. "
            "'version-pump-taskpack-after-v0.13.9'."
        ),
    )]

    tags: Annotated[list[str], Field(
        description=(
            "R8 typed judgment tags: exactly one `kind:` tag from the "
            "fixed enum (investigation, defect-verification, "
            "implementation, review, refactor, release-gate, "
            "design-exploration, environment-setup) plus 1-7 of "
            "`subsystem:`/`feature:`/`topic:` (lowercase-kebab "
            "values). 2-4 tags recommended, 8 hard cap. Example: "
            "['kind:implementation', 'subsystem:upgrade-pipeline']."
        ),
    )] = []

    supersedes_snapshot_id: Annotated[str | None, Field(
        default=None,
        description=(
            "Optional R12: explicit id of the snapshot this capture "
            "supersedes (create-mints-successor — the previous row is "
            "flipped to 'superseded' atomically). Usually omitted: "
            "when the target already has an active snapshot, the tool "
            "supersedes it automatically."
        ),
    )] = None


class SnapshotSearchInput(BaseModel):
    """Input model for snapshot_search tool."""

    query: Annotated[str, Field(
        description="Natural-language search text.",
    )]

    project_id: Annotated[str | None, Field(
        default=None,
        description=(
            "Owning project. None = the caller's own project (search "
            "is permanently project-scoped)."
        ),
    )] = None

    tags: Annotated[list[str], Field(
        description="Optional R8 `dim:value` tag filter.",
    )] = []

    tag_mode: Annotated[Literal["all", "any"], Field(
        description=(
            "'all' (default) requires every supplied tag to match; "
            "'any' is OR-semantics."
        ),
    )] = "all"

    freshness_max_age_days: Annotated[int | None, Field(
        default=None,
        description=(
            "Optional post-filter: drop results older than this many "
            "days."
        ),
    )] = None

    limit: Annotated[int, Field(
        description="Maximum number of results (1-50). Default 10.",
    )] = 10


# ── module-level helpers (pure, testable) ──────────────────────────────

_GIT_SUBPROCESS_TIMEOUT_S = 10


def caller_owns_target(
    manager: Any,
    caller_instance_id: str,
    target_instance_id: str,
) -> bool:
    """Rider (b) — caller-owns-target auth.

    ``True`` when the caller IS the target, or the target is a
    transitive descendant of the caller via the PERMANENT ``parent_id``
    chain (survives terminate-to-revive). Depth-capped at the
    repository's traversal cap.
    """
    if not caller_instance_id or not target_instance_id:
        return False
    if caller_instance_id == target_instance_id:
        return True
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return False
    current = target_instance_id
    for _ in range(_MAX_TRAVERSAL_DEPTH):
        try:
            row = repo.get(current)
        except Exception:
            return False
        if row is None:
            return False
        if not row.parent_id:
            return False
        if row.parent_id == caller_instance_id:
            return True
        current = row.parent_id
    return False


def _instance_row(manager: Any, instance_id: str) -> Any | None:
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return None
    try:
        return repo.get(instance_id)
    except Exception:
        return None


def _caller_project_id(manager: Any, caller_instance_id: str) -> str | None:
    """Resolve the caller's project (auto-inherit, mirrors spawn_instance)."""
    row = _instance_row(manager, caller_instance_id)
    if row is not None and getattr(row, "project_id", None):
        return row.project_id
    return None


def _overlapping_tags(candidate_tags: list[str], query_tags: list[str]) -> set[str]:
    return set(candidate_tags or []) & set(query_tags or [])


def _r9_reuse_verdict(
    candidate: dict[str, Any],
    judgment_tags: list[str],
) -> bool:
    """Deterministic R9 REUSE rule.

    REUSE (strong match) = the candidate shares the SAME ``kind:``
    tag, overlaps at least TWO of the query's judgment tags total,
    and is not expired. Deliberately conservative — a false REUSE
    costs a stale warm start; a missed REUSE only costs one more
    capture (R7 item 5).
    """
    if candidate.get("freshness") == "expired":
        return False
    kind_tags = {t for t in judgment_tags if t.startswith("kind:")}
    overlap = _overlapping_tags(candidate.get("tags") or [], judgment_tags)
    return bool(kind_tags & overlap) and len(overlap) >= 2


def _trim_query_for_hint(query: str, width: int = 80) -> str:
    flat = " ".join(str(query).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _git_repo_state(repo_path: str | None, git_sha: str | None) -> dict[str, Any]:
    """verify=git anchor (§5.2 opt-in) — contained + fail-soft.

    Returns ``{"snapshot_head", "current_head", "diverged_files"}`` on
    success, or ``{"error": "<reason>"}`` when the anchor cannot be
    computed (no repo path / no sha / git failure / timeout). Git usage
    is kept confined here (doc_commit_service precedent:
    ``subprocess.run`` with an argv list, no shell, timeout, behind
    ``asyncio.to_thread`` at the call site).
    """
    if not repo_path or not git_sha:
        return {"error": "snapshot carries no repo path / commit sha"}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_GIT_SUBPROCESS_TIMEOUT_S,
        )
        if head.returncode != 0:
            return {"error": "git rev-parse HEAD failed"}
        count = subprocess.run(
            ["git", "rev-list", "--count", f"{git_sha}..HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_GIT_SUBPROCESS_TIMEOUT_S,
        )
        if count.returncode != 0:
            return {"error": "git rev-list failed"}
        names = subprocess.run(
            ["git", "diff", "--name-only", f"{git_sha}..HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_GIT_SUBPROCESS_TIMEOUT_S,
        )
        if names.returncode != 0:
            return {"error": "git diff --name-only failed"}
        diverged_files = len([ln for ln in names.stdout.splitlines() if ln.strip()])
        return {
            "snapshot_head": git_sha,
            "current_head": head.stdout.strip(),
            "diverged_files": diverged_files,
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"error": f"git anchor unavailable: {type(exc).__name__}"}


# ── factory ────────────────────────────────────────────────────────────


def create_snapshot_tools(
    manager: Any,
    current_instance_id: str,
    agent_id: str,
    version_tag: str | None = None,
) -> list:
    """Create the two snapshot tools bound to a manager + caller.

    Warm-start consumption is NOT a tool here — the unified
    ``spawn_instance`` tool consumes :func:`resolve_spawn_snapshot` /
    :func:`finalize_spawn_snapshot` / :func:`format_snapshot_citation`
    directly (gated by the TARGET agent's ``snapshot_enabled`` flag).

    Args:
        manager: The :class:`InstanceManager` — dereferenced at CALL
            time (construction only builds closures), so metadata-scan
            stubs may pass ``None``.
        current_instance_id: Caller's instance id (auth + spawn parent).
        agent_id: Caller's agent id (team-membership authorization).
        version_tag: Caller's version tag (threaded to the
            team-membership check + default-version resolution — C1
            parity with ``spawn_instance``).
    """
    # Captured BEFORE closure definitions — same regression guard as
    # create_instance_tools (the spawn tool's own ``agent_id`` param
    # must never shadow the caller's identity inside the auth check).
    caller_agent_id: str = agent_id or ""
    caller_instance_id: str = current_instance_id or ""
    caller_version_tag: str | None = version_tag

    def _snapshot_repo() -> SnapshotRepository | None:
        repo = getattr(manager, "_snapshot_repo", None)
        return repo

    def _snapshot_service() -> SnapshotService | None:
        return getattr(manager, "_snapshot_service", None)

    def _search_service() -> SnapshotSearchService | None:
        return getattr(manager, "_snapshot_search_service", None)

    # R16 counter increments are module-level, fail-soft helpers now
    # (``_safe_inc_capture`` / ``_safe_inc_spawn`` below) — shared by
    # ``snapshot_create`` and the unified-spawn warm finalize.

    @register_tool_category(CATEGORY_NAME)
    @tool(args_schema=SnapshotCreateInput)
    async def snapshot_create(
        target_instance_id: Annotated[str, Field(description="The instance whose experience to capture — yourself or a transitive descendant of yours.")],
        name: Annotated[str, Field(description="Human-readable snapshot name.")],
        tags: Annotated[list[str], Field(description="R8 typed judgment tags; exactly one `kind:` from the fixed enum plus `subsystem:`/`feature:`/`topic:` values.")] = [],
        supersedes_snapshot_id: Annotated[str | None, Field(description="Optional explicit predecessor id (R12 create-mints-successor). Usually omitted.")] = None,
    ) -> dict:
        """Capture a target instance's experience as a snapshot. Use tool_help("snapshot_create") for details."""
        # ── R15 gate (write side ONLY) — single call site ─────────────
        # Awaits the REAL async util on the loop (R15 review BLOCKER
        # fix: the former sync helper bridged the read with
        # run_coroutine_threadsafe from the loop thread — guaranteed
        # self-deadlock → TimeoutError → always-OFF + ~2s stall).
        if not await get_snapshot_create_enabled(
            getattr(manager, "_project_repository", None)
        ):
            return {
                "disabled": True,
                "error": "snapshot_create disabled by settings toggle",
            }

        # ── Auth: caller-owns-target (rider (b)) ──────────────────────
        if not caller_owns_target(manager, caller_instance_id, target_instance_id):
            return {
                "snapshot_id": None,
                "status": "refused",
                "verdict": None,
                "digest_preview": None,
                "tags": [],
                "error": (
                    "ERROR: snapshot_create refused — the target "
                    f"instance {target_instance_id} is not you and not "
                    "your transitive descendant (permanent parent_id "
                    "chain)."
                ),
            }

        # ── R9 step 2: normalize judgment tags (fail loud → error str) ─
        try:
            normalized_tags = normalize_judgment_tags(list(tags or []))
        except ValueError as exc:
            return {
                "snapshot_id": None,
                "status": "refused",
                "verdict": None,
                "digest_preview": None,
                "tags": list(tags or []),
                "error": f"ERROR: invalid judgment tags: {exc}",
            }

        target_row = _instance_row(manager, target_instance_id)
        if target_row is None:
            return {
                "snapshot_id": None,
                "status": "refused",
                "verdict": None,
                "digest_preview": None,
                "tags": normalized_tags,
                "error": f"ERROR: target instance {target_instance_id} not found",
            }

        # D8: the snapshot stamps the TARGET's project (§4.3
        # cross-project isolation) — the target lives in the caller's
        # subtree, so this equals the caller's project in practice.
        project_id = getattr(target_row, "project_id", None)
        if not project_id:
            return {
                "snapshot_id": None,
                "status": "refused",
                "verdict": None,
                "digest_preview": None,
                "tags": normalized_tags,
                "error": "ERROR: target instance carries no project_id",
            }

        service = _snapshot_service()
        if service is None:
            return {
                "snapshot_id": None,
                "status": "refused",
                "verdict": None,
                "digest_preview": None,
                "tags": normalized_tags,
                "error": "ERROR: snapshot service not wired on this manager",
            }

        verdict = "new"
        reuse_id: str | None = None
        predecessor: Any | None = None

        try:
            # ── R9 steps 1+3: investigate + search-before-create ──────
            search_service = _search_service()
            derived_query = " ".join(
                [name, str(getattr(target_row, "instance_name", "") or ""),
                 str(getattr(target_row, "agent_id", "") or "")]
            ).strip()
            candidates: list[dict[str, Any]] = []
            if search_service is not None:
                search_result = await search_service.search(
                    derived_query or name,
                    project_id=project_id,
                    tags=normalized_tags,
                    # Candidate DISCOVERY pass: 'any' widens the net;
                    # the deterministic REUSE rule below re-scores
                    # overlap, so recall here is safe.
                    tag_mode="any",
                    limit=5,
                )
                candidates = list(search_result.get("results") or [])

            # ── R9 step 4: verdict ────────────────────────────────────
            for candidate in candidates:
                if _r9_reuse_verdict(candidate, normalized_tags):
                    verdict = "reuse"
                    reuse_id = candidate.get("snapshot_id")
                    # R16 — capture counter increments REGARDLESS of
                    # R9 verdict (REUSE + NEW + SUPERSEDE + CREATE-FRESH
                    # all count). REUSE = the creator exercised the
                    # search-before-create protocol and chose to
                    # reuse; that decision IS a capture event.
                    _safe_inc_capture(manager, caller_agent_id or "unknown")
                    return {
                        "snapshot_id": reuse_id,
                        "status": "reused-existing-snapshot-id",
                        "verdict": "reuse",
                        "digest_preview": candidate.get("summary"),
                        "tags": normalized_tags,
                        "error": None,
                    }

            # SUPERSEDE — same target, previous ACTIVE snapshot exists
            # (or the caller pinned an explicit predecessor). R12
            # create-mints-successor: the successor is minted at
            # terminal write; the predecessor flips atomically.
            repo = _snapshot_repo()
            if supersedes_snapshot_id:
                verdict = "supersede"
                predecessor = None  # validated at the terminal mint
            elif repo is not None:
                predecessor = await asyncio.to_thread(
                    repo.latest_for_target, target_instance_id
                )
                if predecessor is not None:
                    verdict = "supersede"
                    supersedes_snapshot_id = predecessor.id

            # NEW / CREATE-FRESH reach here without a predecessor.
            if verdict == "new" and candidates:
                # Matches existed but every one failed the REUSE rule
                # (stale / expired / low overlap) — the R9 CREATE-FRESH
                # nuance: supersession is for same-target continuity,
                # not for stale cross-target matches; label the
                # fresh capture honestly.
                verdict = "create-fresh"

            capture = await service.capture_async(
                target_instance_id=target_instance_id,
                project_id=project_id,
                created_by_agent_id=caller_agent_id or "unknown",
                title=name,
                judgment_tags=normalized_tags,
                task_summary=derived_query,
                supersedes_snapshot_id=supersedes_snapshot_id,
            )
        except Exception as exc:
            # Superseding a nonexistent id is a caller bug (repository
            # raises at the terminal mint) — surface it, never raise.
            return {
                "snapshot_id": None,
                "status": "failed",
                "verdict": verdict,
                "digest_preview": None,
                "tags": normalized_tags,
                "error": f"ERROR: capture failed to start: {type(exc).__name__}: {exc}",
            }

        snapshot_id = capture.get("snapshot_id")
        capture_error = capture.get("error")
        # R16 — capture counter on the SUCCESS path of the SUPERSEDE /
        # NEW / CREATE-FRESH verdicts. REUSE already increments above
        # (covers all 4 verdicts regardless of R9). The increment
        # only fires on a real ledger-row insertion (snapshot_id
        # non-null); a failed ``capture_async`` does NOT count
        # (the capture never made it to storage).
        if snapshot_id and not capture_error:
            _safe_inc_capture(manager, caller_agent_id or "unknown")
        return {
            "snapshot_id": snapshot_id,
            "status": capture.get("status") or "running",
            "verdict": verdict,
            "digest_preview": None,
            "tags": normalized_tags,
            "error": capture_error,
        }

    @register_tool_category(CATEGORY_NAME)
    @tool(args_schema=SnapshotSearchInput)
    async def snapshot_search(
        query: Annotated[str, Field(description="Natural-language search text.")],
        project_id: Annotated[str | None, Field(description="Owning project; None = the caller's own project.")] = None,
        tags: Annotated[list[str], Field(description="Optional R8 `dim:value` tag filter.")] = [],
        tag_mode: Annotated[Literal["all", "any"], Field(description="'all' (default) = every tag must match; 'any' = OR.")] = "all",
        freshness_max_age_days: Annotated[int | None, Field(description="Optional post-filter: drop results older than this many days.")] = None,
        limit: Annotated[int, Field(description="Maximum number of results (1-50). Default 10.")] = 10,
    ) -> dict:
        """Search active snapshots (read-only, project-scoped). Use tool_help("snapshot_search") for details."""
        # D8 PERMANENT project scoping: None → caller's project.
        resolved_project = project_id or _caller_project_id(
            manager, caller_instance_id
        )
        if not resolved_project:
            return {"results": [], "error": "no project context available to scope the search"}

        clamped_limit = max(1, min(int(limit or 10), 50))

        search_service = _search_service()
        if search_service is None:
            return {"results": [], "error": "snapshot search service not wired on this manager"}

        # Read-only — NEVER gated by R15.
        result = await search_service.search(
            query,
            project_id=resolved_project,
            tags=list(tags or []),
            tag_mode="all" if tag_mode not in ("all", "any") else tag_mode,
            limit=clamped_limit,
        )
        results = list(result.get("results") or [])
        error = result.get("error")

        if freshness_max_age_days is not None:
            try:
                max_age = float(freshness_max_age_days)
                results = [
                    r for r in results
                    if float(r.get("age_days") or 0.0) <= max_age
                ]
            except (TypeError, ValueError):
                pass  # non-numeric filter value → ignore, never raise

        return {"results": results, "error": error}

    return [snapshot_create, snapshot_search]


# ═══════════════════════════════════════════════════════════════════════
# Unified-spawn consumption helpers (unify-spawn-tools, 2026-10-05)
# ═══════════════════════════════════════════════════════════════════════
#
# ``spawn_hot_instance`` was removed as a standalone tool. Warm-start
# consumption now rides ``spawn_instance`` (``daemon/tools/instance.py``),
# gated by the TARGET agent's ``snapshot_enabled`` meta.json flag. The
# R14 state machine below is the SAME code that used to live inside the
# removed tool (explicit-id verify → internal search → staleness →
# verify=git → R6b stamp → R16 counter), factored into module-level
# helpers so there is exactly ONE copy of the resolution logic
# (decision 1 of unify-spawn-tools). Ordering contract (R6b + R18):
# resolve → manager.spawn_instance → finalize (stamp BEFORE enqueue) →
# the unified tool's R18 auto-dispatch (which lives in instance.py —
# it must run even when the snapshot gate is OFF).


@dataclass
class SpawnSnapshotResolution:
    """Mutable outcome of the pre-spawn snapshot resolution (§4.3/§5.2).

    ``consumed`` is the snapshot row the spawn will warm-start from
    (``None`` ⇒ cold). ``cold_reason`` ∈ {``no-hit``, ``expired``,
    ``verify-failed``} mirrors the R14 reason lexemes; ``warnings``
    accumulates the fail-soft diagnostics that surface in the
    citation line. :func:`finalize_spawn_snapshot` mutates the
    instance on a stamp failure (warm → cold downgrade).
    """

    consumed: Any | None = None
    staleness: dict[str, Any] | None = None
    cold_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    searched_desc: str | None = None
    cross_project_consumed: bool = False

    @property
    def started(self) -> str:
        """``"warm"`` when a snapshot survived resolution + stamping."""
        return "warm" if self.consumed is not None else "cold"


SNAPSHOT_STEERING_IGNORED_LINE = (
    '[snapshot] ignored: snapshot_id/tags steering requires the target '
    'agent to have "snapshot_enabled": true in its meta.json — '
    "spawned plain cold."
)


async def resolve_spawn_snapshot(
    manager: Any,
    *,
    task: str | None,
    snapshot_id: str | None,
    tags: list[str] | None,
    verify: str,
    allow_cross_project: bool,
    caller_project_id: str | None,
) -> SpawnSnapshotResolution:
    """Resolve the snapshot to warm-start from (``consumed=None`` ⇒ cold).

    The R14 fail-soft state machine, preserved from the removed
    ``spawn_hot_instance``: explicit-id verify-then-warm (missing /
    non-active / project-mismatch ⇒ ``verify-failed`` cold + warning;
    ``allow_cross_project=True`` consumes a mismatch anyway), else the
    task-based internal search (ACTIVE-only top hit, ``limit=1``;
    search fault / empty results / inactive / expired ⇒ ``no-hit`` /
    ``expired`` cold), then the §5.2 staleness report (expired ⇒ cold)
    and the opt-in ``verify=git`` anchor (warnings ride
    ``staleness.warnings``). D8: ``caller_project_id=None``
    (project-less caller) fails closed on any snapshot that HAS a
    project.

    NEVER raises: an unexpected fault in this phase degrades to a
    ``no-hit`` cold with a warning so the unified spawn still
    proceeds plain-cold (R14 rider h — a snapshot-system fault must
    never break spawning).
    """
    resolution = SpawnSnapshotResolution()
    try:
        repo = getattr(manager, "_snapshot_repo", None)
        service = getattr(manager, "_snapshot_service", None)
        search_service = getattr(manager, "_snapshot_search_service", None)
        project_id = caller_project_id

        if snapshot_id:
            # R14 explicit branch — verify-then-warm; ANY failure is a
            # cold fallback with a warning, NEVER an error.
            if repo is None:
                resolution.cold_reason = "verify-failed"
                resolution.warnings.append("snapshot repository not wired")
            else:
                consumed = await asyncio.to_thread(repo.get, snapshot_id)
                if consumed is None:
                    resolution.cold_reason = "verify-failed"
                    resolution.warnings.append(
                        f"snapshot {snapshot_id} not found"
                    )
                elif getattr(consumed, "status", None) != "active":
                    # Superseded (or failed/interrupted) rows NEVER spawn.
                    resolution.cold_reason = "verify-failed"
                    resolution.warnings.append(
                        f"snapshot {snapshot_id} status is "
                        f"{getattr(consumed, 'status', None)!r} — only "
                        "'active' snapshots spawn"
                    )
                    consumed = None
                elif getattr(consumed, "project_id", None) and not project_id:
                    # D8 project-less arm — the caller has NO project
                    # to scope against while the snapshot HAS one.
                    # Mirror of the mismatch arm below (same §4.3
                    # isolation rule, not new machinery): without it a
                    # project-less caller silently warm-starts from
                    # another project's snapshot. Same consent valve:
                    # only an explicit ``allow_cross_project=True``
                    # consumes.
                    if allow_cross_project:
                        resolution.cross_project_consumed = True
                        resolution.warnings.append(
                            f"snapshot {snapshot_id} belongs to "
                            f"project {consumed.project_id} — "
                            f"cross-project consume opted in via "
                            f"allow_cross_project=True"
                        )
                    else:
                        resolution.cold_reason = "verify-failed"
                        resolution.warnings.append(
                            f"snapshot {snapshot_id} belongs to "
                            f"project {consumed.project_id} — "
                            f"caller instance has no project to "
                            f"scope against (D8 isolation)"
                        )
                        consumed = None
                elif (
                    project_id
                    and getattr(consumed, "project_id", None)
                    and consumed.project_id != project_id
                ):
                    # Cross-project isolation (§4.3). Wave 2b
                    # (D8 handoff) added an explicit opt-in param
                    # — ``allow_cross_project=True`` lets the caller
                    # consume a cross-project snapshot anyway
                    # (staleness is still computed; the citation
                    # line notes the cross-project origin so the
                    # caller records the consent). The default
                    # ``False`` preserves the Wave 2b fail-closed
                    # behavior — a mismatch is a verify-fail cold
                    # fallback with a warning (prevents digest
                    # leakage through a shared leader).
                    if allow_cross_project:
                        resolution.cross_project_consumed = True
                        resolution.warnings.append(
                            f"snapshot {snapshot_id} belongs to "
                            f"project {consumed.project_id} — "
                            f"cross-project consume opted in via "
                            f"allow_cross_project=True"
                        )
                    else:
                        resolution.cold_reason = "verify-failed"
                        resolution.warnings.append(
                            f"snapshot {snapshot_id} belongs to "
                            f"project {consumed.project_id}, not "
                            f"this instance's project {project_id}"
                        )
                        consumed = None
                resolution.consumed = consumed
                resolution.searched_desc = f"snapshot {snapshot_id}"
        else:
            # R14 internal search — ACTIVE-only candidates (the search
            # service filters status='active' + project).
            if search_service is None or repo is None:
                resolution.cold_reason = "no-hit"
                resolution.warnings.append("snapshot services not wired")
            else:
                resolution.searched_desc = task
                search_tags = [
                    t for t in (tags or []) if isinstance(t, str) and t
                ]
                try:
                    search_result = await search_service.search(
                        task or "",
                        project_id=project_id or "",
                        tags=search_tags,
                        # FIXME(ledger): tag_mode=any unwired — see follow-up ledger
                        tag_mode="all" if search_tags else "all",
                        limit=1,
                    )
                    results = list(search_result.get("results") or [])
                except Exception as exc:
                    logger.warning(
                        f"[Snapshot] spawn_instance internal snapshot "
                        f"search failed: {exc}"
                    )
                    results = []
                if not results:
                    resolution.cold_reason = "no-hit"
                else:
                    top = results[0]
                    consumed = await asyncio.to_thread(
                        repo.get, top.get("snapshot_id")
                    )
                    if (
                        consumed is None
                        or getattr(consumed, "status", None) != "active"
                    ):
                        resolution.cold_reason = "no-hit"
                    elif top.get("freshness") == "expired":
                        resolution.cold_reason = "expired"
                        resolution.warnings.append(
                            f"top match {top.get('snapshot_id')} is "
                            "expired — expired digests never warm-start"
                        )
                    else:
                        resolution.consumed = consumed

        # ── Staleness (§5.2) for the consumed snapshot ────────────────
        if resolution.consumed is not None and service is not None:
            try:
                resolution.staleness = await service.staleness_report(
                    resolution.consumed.id
                )
            except Exception as exc:
                resolution.staleness = None
                resolution.warnings.append(
                    f"staleness check failed: {type(exc).__name__}"
                )
            if (
                resolution.staleness is not None
                and resolution.staleness.get("freshness") == "expired"
            ):
                resolution.cold_reason = "expired"
                resolution.warnings.append(
                    "snapshot expired — expired digests never warm-start"
                )
                resolution.consumed = None

        # ── verify=git opt-in (§5.2 anchor — contained + fail-soft) ───
        if resolution.consumed is not None and verify == "git":
            git_state = await asyncio.to_thread(
                _git_repo_state,
                getattr(resolution.consumed, "repo_path", None),
                getattr(resolution.consumed, "git_sha", None),
            )
            if resolution.staleness is None:
                resolution.staleness = {}
            resolution.staleness["repo_state"] = git_state
            # Wave 2b review FIX 4 — git-anchor warnings ride inside
            # ``staleness.warnings``.
            git_warnings: list[str] = []
            if isinstance(git_state, dict) and git_state.get("error"):
                git_warnings.append(
                    f"git anchor unavailable: {git_state['error']}"
                )
            elif isinstance(git_state, dict) and git_state.get(
                "diverged_files"
            ):
                git_warnings.append(
                    f"repo diverged {git_state['diverged_files']} file(s) "
                    "since the snapshot commit"
                )
            if git_warnings:
                resolution.warnings.extend(git_warnings)
                warnings_list = list(
                    resolution.staleness.get("warnings") or []
                )
                warnings_list.extend(git_warnings)
                resolution.staleness["warnings"] = warnings_list

        # ── R14 drift-note mandate — stale-not-expired warm start ────
        if (
            resolution.consumed is not None
            and resolution.staleness is not None
            and resolution.staleness.get("freshness") == "stale"
        ):
            drift_note = (
                f"snapshot is stale (age "
                f"{resolution.staleness.get('snapshot_age_days')}d > "
                f"{SNAPSHOT_FRESH_MAX_AGE_DAYS}d) — verify digest "
                "assumptions against the current state before "
                "acting on them"
            )
            resolution.warnings.append(drift_note)
            warnings_list = list(
                resolution.staleness.get("warnings") or []
            )
            warnings_list.append(drift_note)
            resolution.staleness["warnings"] = warnings_list
    except Exception as exc:  # noqa: BLE001 — fail-soft belt (R14 rider h)
        # The resolve phase must NEVER break the spawn. A fault here
        # (repo.get raising, a malformed row, …) degrades to a
        # no-hit cold; the unified spawn proceeds plain-cold.
        logger.warning(
            f"[Snapshot] resolve_spawn_snapshot fault — degrading to "
            f"cold: {type(exc).__name__}: {exc}"
        )
        resolution.consumed = None
        if resolution.cold_reason is None:
            resolution.cold_reason = "no-hit"
        resolution.warnings.append(
            f"snapshot resolve failed: {type(exc).__name__}"
        )
    return resolution


async def finalize_spawn_snapshot(
    manager: Any,
    resolution: SpawnSnapshotResolution,
    *,
    new_instance_id: str,
) -> SpawnSnapshotResolution:
    """R6b warm-path stamp + R16 counter (POST-spawn, PRE-enqueue).

    The unified spawn tool calls this AFTER ``manager.spawn_instance``
    and BEFORE its R18 auto-dispatch enqueue so the snapshot digest is
    already stamped when the worker picks up the first-turn message
    (``assemble_context_messages`` reads
    ``instance_metadata["snapshot_digest"]`` on turn 1).

    Mutates + returns ``resolution``: on a stamp failure the warm
    start downgrades to cold (``cold_reason="verify-failed"`` plus a
    warning) — the instance exists but carries no digest. A counter /
    log-line failure NEVER downgrades (R16 rider j ordering:
    success-of-stamp first, then counter as observability).
    """
    if resolution.consumed is None:
        return resolution
    try:
        manager.set_metadata_many(
            new_instance_id,
            {
                "snapshot_digest": resolution.consumed.digest or {},
                "spawned_from_snapshot_id": resolution.consumed.id,
            },
        )
    except Exception as exc:
        # The instance exists; a failed stamp degrades to a
        # cold-context instance — surface it, never raise.
        resolution.warnings.append(
            f"digest stamp write failed: {type(exc).__name__}: {exc}"
        )
        resolution.consumed = None
        resolution.cold_reason = "verify-failed"
        return resolution

    # R16 — spawn-warm counter, MONITORING ONLY. Fires ONLY on the
    # WARM path (the stamp succeeded); placement AFTER the stamp so a
    # counter write failure can NEVER downgrade the warm spawn.
    _safe_inc_spawn(manager, getattr(resolution.consumed, "id", "") or "")
    # R16 observability floor (Wave 3 rider j): emit a structured line
    # per WARM spawn with the counter increment as the load-bearing
    # payload. Mirrors the executor's ``[SnapshotCapture]`` JSON
    # payload shape so downstream tools can grep both uniformly.
    try:
        import json as _json

        logger.info(
            "[SnapshotSpawnWarm] "
            + _json.dumps(
                {
                    "snapshot_id": getattr(resolution.consumed, "id", None),
                    "new_instance_id": new_instance_id,
                    "counter": "spawn_warm",
                    "project_id": getattr(
                        resolution.consumed, "project_id", None
                    ),
                }
            )
        )
    except Exception:  # pragma: no cover — defensive belt
        logger.warning(
            "[Snapshot] R16 spawn warm log line encoding failed (non-fatal)"
        )
    return resolution


def format_snapshot_citation(
    resolution: SpawnSnapshotResolution,
) -> str:
    """Assemble the ``[snapshot] started: …`` citation line.

    Appended verbatim as ONE line of the unified ``spawn_instance``
    STRING return (the established citation phrasing — callers cite
    the ``started`` value + snapshot lineage in their reports):

    * warm — ``[snapshot] started: warm — Warm-started from snapshot
      {id} (age {age}d; tags {tags})`` plus the cross-project /
      STALE / warnings tails.
    * cold — ``[snapshot] started: cold — No matching snapshot
      (searched: {q}; reason: {reason})`` (the ``searched:`` fragment
      is omitted when nothing was searched) plus the warnings tail.
    """
    if resolution.started == "warm":
        consumed = resolution.consumed
        # Defensive str-coercion belt: ``domain_tags`` is typed
        # ``list[str]`` on the model, but ``Snapshot`` rows can come
        # from search hits or stored JSONB that round-trip with
        # mixed primitives. A bare ``", ".join(...)`` would raise
        # ``TypeError`` on a non-str element AFTER a successful
        # spawn, masking success — cast each item so the citation
        # always renders. The first 8 are surfaced; cap stays the
        # same.
        tags_text = ", ".join(
            str(t)
            for t in list(getattr(consumed, "domain_tags", None) or [])[:8]
        )
        age = (
            resolution.staleness.get("snapshot_age_days")
            if resolution.staleness is not None
            else None
        )
        # ``age`` stays ``None`` on unwired-service / staleness-raised
        # fail-soft paths; rendering it bare would emit
        # ``age Noned``. Substitute a stable lexeme instead.
        age_text = (
            f"{age}d" if isinstance(age, (int, float)) else "unknown"
        )
        line = (
            f"[snapshot] started: warm — Warm-started from snapshot "
            f"{consumed.id} (age {age_text}; tags {tags_text})"
        )
        # D8 cross-project marker — when the caller explicitly opted
        # in via ``allow_cross_project=True``, surface the
        # cross-project origin so the caller records the consent.
        if resolution.cross_project_consumed:
            line += (
                f" — cross-project consume from "
                f"{getattr(consumed, 'project_id', None)} "
                f"(allow_cross_project=True)"
            )
        # R14 drift-note mandate — a stale-not-expired warm start
        # carries the STALE note in the citation line AND in
        # staleness.warnings (appended in resolve).
        if (
            resolution.staleness is not None
            and resolution.staleness.get("freshness") == "stale"
        ):
            line += (
                " — snapshot is STALE: verify digest assumptions "
                "against the current state before acting on them"
            )
    else:
        searched_part = (
            f"searched: {_trim_query_for_hint(resolution.searched_desc)}; "
            if resolution.searched_desc
            else ""
        )
        line = (
            f"[snapshot] started: cold — No matching snapshot "
            f"({searched_part}reason: "
            f"{resolution.cold_reason or 'no-hit'})"
        )
    if resolution.warnings:
        line += f" — warnings: {'; '.join(resolution.warnings)}"
    return line


def _metrics_service(manager: Any) -> Any | None:
    """R16 — module-level accessor for the monitoring counters service.

    Returns ``None`` when the manager has no metrics wiring (e.g.
    boot probes / metadata scan stubs) so the counters no-op cleanly.
    Production managers always wire one — see ``daemon/manager.py``.
    """
    return getattr(manager, "_snapshot_metrics_service", None)


def _safe_inc_spawn(manager: Any, snapshot_id: str) -> None:
    """R16 spawn-warm counter increment, fail-soft (never raises)."""
    svc = _metrics_service(manager)
    if svc is None:
        return
    try:
        svc.inc_spawn(snapshot_id)
    except Exception as exc:  # pragma: no cover — defensive belt
        logger.warning(
            f"[Snapshot] R16 spawn counter increment failed: "
            f"{type(exc).__name__}: {exc}"
        )


def _safe_inc_capture(manager: Any, agent_id: str) -> None:
    """R16 capture counter increment, fail-soft (never raises)."""
    svc = _metrics_service(manager)
    if svc is None:
        return
    try:
        svc.inc_capture(agent_id)
    except Exception as exc:  # pragma: no cover — defensive belt
        logger.warning(
            f"[Snapshot] R16 capture counter increment failed: "
            f"{type(exc).__name__}: {exc}"
        )
