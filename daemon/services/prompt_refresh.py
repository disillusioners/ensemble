"""Manager primitive: refresh one instance's prompt (prompt-reload lane).

``refresh_instance_prompt(manager, instance_id, *, force=False)`` performs
the whole validate → compose → compare → pop cycle for ONE instance (the
caller's own). Consumers: the ``prompt_reload`` tool (Phase 2) and,
future/optional, the compaction path (R8 — callable shape only, NOT
wired). The ``InstanceManager.refresh_instance_prompt`` delegate is the
AM-12 pinned skeleton; all logic lives here.

Mechanism (leader ruling R1, probe-validated):

* resolution — registry ``metadata.path`` (spawn parity, F5), NEVER the
  ``current`` symlink; divergence is REPORTED (tri-state), never a
  refusal (AM-5/D2).
* validation — self-env-aware containment (live/sandbox-shaped → under
  ``<install_root>/releases/<v>/agents/``; dev → repo ``agents/``).
* manifest verification — the LOADER'S READ SET for the calling agent
  (AM-1), verified via :mod:`daemon.services.release_manifest` (AM-2)
  against the staged release ``manifest.json``; sandbox is treated
  LIVE-SHAPED (a frozen-binary sandbox runs out of a staged release —
  AM-3).
* composition — ONLY via ``load_and_cache_prompt`` imported through
  ``daemon.manager`` (R2 single source of truth; the house test patch
  seam), with ``mcp_tool_names`` resolved EXACTLY as the restore path
  resolves it (AM-14 parity, private reach W3-authorized below).
* decision — tool-side per-file sha256 + the prompt-versions DB
  fingerprint as a TUPLE against the process-local baseline map
  (AM-8/D5; Rev-3 B1/B4): no baseline → ``unproven`` (force-like);
  files+db equal → ``no_change`` (never pops); db-only change →
  ``reloaded_db`` (F4 honored AT the reload); hashes-differ with the
  loader's own mtime check provably stale → ``PromptCache.invalidate``
  BEFORE composing (a stale loader cache serves the old persona on the
  NEXT RESTORE too — ``force=true`` cannot cure it).
* eviction — the probe-validated BARE POP ``manager.instances.pop(id,
  None)`` (P3). NO seam guard (AM-7/D22: the instances dict is unlocked
  across a 12-site mutation census with awaitless critical spans; a
  guard would add a stuck-flag failure mode to a non-defect). NEVER
  the P4 zombie-maker cached-instance release path, NEVER a tuple
  swap (P5), NEVER closure mutation.
* diff — tier-b exact diff over the LOADER-CACHED persona only (never
  the post-cache-appended persona — runtime content is re-appended
  fresh on each restore), fired ONLY under the stored-mtimes claim
  guard (AM-13/D23); otherwise tier-c ``persona_diff=unavailable``
  (AM-10 — the v1 contract; no structural claim).
* concurrency — NO lock (D23); the one subtlety (report truthfulness)
  is the claim guard. Runbook precondition: the primitive must be
  AWAITED on the daemon's main loop (the awaitless-atomicity argument
  collapses off-loop).

Persistent effects (exactly three, all pinned — Advisory 4):
``instances.pop``, ``cache.set`` (on miss, inside the loader), and
``cache.invalidate`` (the AM-8 bust). Nothing else mutates state.

Refusal vocabulary (AM-6/D6 — FINAL, 9 REFUSED + 1 FAILED): see
:data:`REFUSAL_CODES` / :data:`FAILURE_CODES`. ``not_ari`` is
deliberately ABSENT — the tool's layer-3 non-ari check returns the
notify-exact generic ``ERROR:`` envelope, not a REFUSED code.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..loader import (
    PROJECT_EXPERIENCE_FILE,
    KNOWLEDGE_FILE,
    KNOWLEDGE_NO_FORCE_EXPLORE_FILE,
    _compute_db_versions_fingerprint,
    _get_prompt_version_repo,
    _resolve_innate_skill_paths,
)

logger = logging.getLogger(__name__)

#: The synthetic mtime-map key the loader folds the DB fingerprint
#: under (loader.py:1455) — excluded from the file-mtime comparisons.
FP_KEY = "__prompt_versions_fingerprint__"

# ── Outcomes ────────────────────────────────────────────────────────────────
# D25 (leader ruling 2026-11-06, PINNED): the MARKER success set is the
# FIVE tokens below. The INFO-line outcome enum (D24/T3.7) is the SIX-
# value SUPERSET (these five + ``refused``) — DIFFERENT SETS BY DESIGN;
# never reconcile them by shrinking either.
OUTCOME_NO_CHANGE = "no_change"
OUTCOME_RELOADED = "reloaded"
OUTCOME_RELOADED_DB = "reloaded_db"
OUTCOME_FORCED_RELOAD = "forced_reload"
OUTCOME_UNPROVEN = "unproven"
OUTCOME_REFUSED = "refused"  # INFO-line only — never a marker token.

#: The five-token success set (marker vocabulary, D25).
SUCCESS_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_NO_CHANGE,
        OUTCOME_RELOADED,
        OUTCOME_RELOADED_DB,
        OUTCOME_FORCED_RELOAD,
        OUTCOME_UNPROVEN,
    }
)

#: The six-value INFO-line outcome enum (D24) — the superset.
INFO_OUTCOMES: tuple[str, ...] = (
    OUTCOME_NO_CHANGE,
    OUTCOME_RELOADED_DB,
    OUTCOME_RELOADED,
    OUTCOME_FORCED_RELOAD,
    OUTCOME_UNPROVEN,
    OUTCOME_REFUSED,
)

# ── Refusal vocabulary (AM-6/D6 — 9 REFUSED + 1 FAILED, literally
# enumerated, Advisory 1) ────────────────────────────────────────────────────
ENVELOPE_REFUSED = "REFUSED"
ENVELOPE_FAILED = "FAILED"

CODE_DISABLED = "prompt_reload_disabled"
CODE_AGENT_DIR_UNRESOLVED = "prompt_reload_agent_dir_unresolved"
CODE_DIR_INVALID = "prompt_reload_dir_invalid"
CODE_MANIFEST_MISSING_LIVE = "prompt_reload_manifest_missing_live"
CODE_MANIFEST_UNREADABLE = "prompt_reload_manifest_unreadable"
CODE_MANIFEST_MISMATCH = "prompt_reload_manifest_mismatch"
CODE_AGENT_META_INVALID = "prompt_reload_agent_meta_invalid"
CODE_COMPOSE_FAILED = "prompt_reload_compose_failed"
CODE_DB_UNAVAILABLE = "prompt_reload_db_unavailable"
CODE_INTERNAL_ERROR = "prompt_reload_internal_error"

#: codes 1-9 — the REFUSED envelope (notify_user.py:314-316 shape).
REFUSAL_CODES: frozenset[str] = frozenset(
    {
        CODE_DISABLED,
        CODE_AGENT_DIR_UNRESOLVED,
        CODE_DIR_INVALID,
        CODE_MANIFEST_MISSING_LIVE,
        CODE_MANIFEST_UNREADABLE,
        CODE_MANIFEST_MISMATCH,
        CODE_AGENT_META_INVALID,
        CODE_COMPOSE_FAILED,
        CODE_DB_UNAVAILABLE,
    }
)

#: code 10 — the FAILED envelope (notify_user.py:319-321 shape).
FAILURE_CODES: frozenset[str] = frozenset({CODE_INTERNAL_ERROR})

#: Envelope kind per code — the runbook's envelope column.
CODE_ENVELOPES: dict[str, str] = {
    **{code: ENVELOPE_REFUSED for code in REFUSAL_CODES},
    **{code: ENVELOPE_FAILED for code in FAILURE_CODES},
}

# ── Canonical disclosure literals (plan-overview §10, W5 — ONE literal
# per bullet id; referenced BY ID everywhere else, never paraphrased) ────────
S10_A_TOOLS_NOTE_DEAD = (
    "tools_note: DB override NOT applied — known defect F7i (the loader "
    "composes the file content under key 'tools' while the override hook "
    "writes 'tools_note'); routed, not fixed here."
)
S10_B_USER_GROWTH_NOT_IN_PROMPT = (
    "user/growth: not part of the composed prompt — F7ii (these artifacts "
    "never enter the loader's prompt map); DB rows are write-only today."
)
S10_C_DISPLAY_DIVERGENCE = (
    "The FE-visible synthetic system message may lag or diverge from the "
    "live persona until the next display rebuild — no display-parity "
    "promise (F8)."
)
S10_D_NEXT_TURN = (
    "The reload takes effect on your NEXT turn; this tool return (and its "
    "marker) is the only in-turn signal."
)
S10_E_PROVIDER_CACHE = (
    "Reloading the persona invalidates provider prompt caches — accepted "
    "consequence (R9)."
)
S10_F_INFLIGHT_SURVIVES = (
    "An in-flight turn survives on its own graph reference; no re-cache "
    "happens at turn end (P1/P3)."
)
S10_G_NODE_SET_HAZARD = (
    "Divergent-node-set handover is only a hazard under code drift; this "
    "path evicts, never swaps mid-turn (P1)."
)
S10_H_FILE_TOCTOU = (
    "File-side TOCTOU on the loader cache is bounded one-call staleness "
    "that self-heals on the next call; DB-side TOCTOU is closed by the "
    "loader's single-SELECT."
)
S10_I_PROBE_TO_COMPOSE = (
    "A prompt-versions repo failure between the tool-side probe and "
    "composition still fail-opens inside the loader (sub-second window; "
    "routed, not fixed here)."
)
S10_J_NO_CHANGE_WORDING = (
    "no_change claims FILE-LEVEL freshness, not closure currency — another "
    "instance sharing this agent's cache may have refreshed it; force=true "
    "is the escape."
)
S10_K_MARKER_ERASURE = (
    "The [PROMPT-RELOADED …] marker survives only in-turn; compaction may "
    "erase it — acceptable, the persona is already live."
)
S10_L_UNVERIFIED_ROUTED = (
    "Runtime proof that no path rewrites a live instance's cached tuple "
    "first element is routed to the tester gate (L1)."
)
S10_M_OBSERVABILITY_ROUTED = (
    "Observability is one frozen INFO line per call; no journal, no "
    "ledger, no metrics family in this lane (prompt_reload_total{outcome} "
    "routed to a future metrics lane)."
)
S10_N_COLD_START_DELIBERATE = (
    "Cold-start behavior is DELIBERATE: with no prior baseline record "
    "(first call after ANY restart) the outcome is unproven and the "
    "primitive behaves force-like (recompose + pop). It NEVER returns "
    "no_change without a baseline — by design, not a defect."
)

#: The two DISTINCT fixed divergence disclosure lines (AM-5 — one for
#: ``false``, one for ``unknown``; pinned separately, never collapsed).
DIVERGENCE_LINE_FALSE = (
    "current symlink does not match the running release — the daemon "
    "resolves prompts from the RUNNING release (registry, spawn parity); "
    "operator action (re-point or restart) resolves this, the reload "
    "proceeds."
)
DIVERGENCE_LINE_UNKNOWN = (
    "current-vs-running release could not be determined (no install dir, "
    "no symlink, or a dangling link in this environment) — the reload "
    "proceeds; prompt resolution follows the registry (spawn parity)."
)

#: The in-turn breadcrumb marker (AM-4/D10) — rendered by the TOOL on
#: success outcomes only; token == the structured outcome (D25).
PROMPT_RELOADED_MARKER = "[PROMPT-RELOADED outcome={outcome} next_turn=true]"

# ── The loader's own artifact list (frozen constant; pinned equal to the
# loader's literal by the phase-4 pack — a hand-written DIVERGENT glob is
# the AM-1 test-failure class) ───────────────────────────────────────────────
_OWN_PROMPT_FILES: tuple[str, ...] = (
    "soul.md",
    "skill.md",
    "workflow.md",
    "rule.md",
    "memory.md",
)

# ── The process-local baseline map (D5) ─────────────────────────────────────
# Key: (agent_id, version_tag, tuple(sorted(mcp_tool_names))).
# Value: (read-set file sha256 map, db fingerprint operand).
# Written ONLY by this primitive (Advisory 2: a FORCED pop also writes it —
# every successful compose/fill seeds it, else post-force calls would forever
# return unproven).
_BASELINE_MAP: dict[tuple[str | None, str | None, tuple[str, ...]], tuple[dict[str, str], str]] = {}


@dataclass
class PromptRefreshResult:
    """The structured result — success report or typed refusal.

    ``outcome`` is the SIX-value enum (INFO-line vocabulary): the five
    success tokens (D25 marker set) or ``refused`` when ``code`` is set.
    The marker is rendered ONLY for the five success tokens (code None).
    """

    outcome: str
    code: str | None = None
    message: str = ""
    #: tier-b: persona_diff="exact" + counts + heading sets; tier-c:
    #: persona_diff="unavailable" and nothing else claimed.
    diff: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code is None


def _refused(code: str, message: str, provenance: dict[str, Any]) -> PromptRefreshResult:
    return PromptRefreshResult(
        outcome=OUTCOME_REFUSED, code=code, message=message, provenance=provenance
    )


# ── Self-env / release provenance (T1.2, AM-5) ──────────────────────────────

_RELEASE_NAME_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _release_provenance() -> dict[str, Any]:
    """Tri-state current-vs-running provenance (NEVER refuses — AM-5).

    W3-authorized private reach (leader ruling): the lazy import of
    ``daemon.tools.upgrade_tools`` privates lives ONLY in this module,
    copied from the ``promote_txn_hold.py:145-182`` pattern. Do NOT
    reimplement these resolvers and do NOT hoist the import to module
    level (upgrade_tools is 4k+ lines of upgrade-lane scope; in-function
    import dodges the import cycle).
    """
    from ..tools.upgrade_tools import (
        _current_symlink,
        _resolve_install_dir,
        _self_env_marker,
    )

    self_env = _self_env_marker()
    install_root = _resolve_install_dir(self_env) if self_env else None
    current_target, current_note = _current_symlink(install_root)

    running_release: str | None = None
    if getattr(sys, "frozen", False):
        exe_parent = Path(sys.executable).parent.name
        if _RELEASE_NAME_RE.match(exe_parent):
            running_release = exe_parent

    if current_note or current_target is None or running_release is None:
        matches = "unknown"
        divergence_line = DIVERGENCE_LINE_UNKNOWN
    else:
        target_name = current_target.rsplit("/", 1)[-1]
        matches = "true" if target_name == running_release else "false"
        divergence_line = DIVERGENCE_LINE_FALSE if matches == "false" else ""

    try:
        from .. import __version__ as daemon_version
    except Exception:  # pragma: no cover — defensive; daemon always versions
        daemon_version = "?"

    return {
        "self_env": self_env,
        "install_root": str(install_root) if install_root else None,
        "running_release": running_release,
        "current_target": current_target,  # null-not-empty when unresolved
        "current_matches_running": matches,
        "divergence_line": divergence_line,
        "daemon_version": daemon_version,
    }


# ── The loader's read set (AM-1 — derived by IMPORTING the loader's own
# resolvers; a hand-written glob is a test failure) ──────────────────────────


def _loader_read_set(agent_dir: Path, meta: dict[str, Any] | None) -> dict[str, Path]:
    """Every file the loader reads for this agent, keyed by the loader's
    own mtime-map key convention (so hashes and mtime comparisons line
    up with ``load_and_cache_prompt`` exactly).

    Mirrors the loader's derivation: own artifacts (:1369), meta.json,
    shared ``_prompt_system`` files (constants :449-451, selected via
    ``no_force_explore``), innate-skill files
    (``_resolve_innate_skill_paths`` :880-886), and ``memories/*.md``
    (:1423-1425). Deliberately NOT added: any file the loader does not
    read (Advisory 3 — e.g. ``_prompt_system/critical-notes.md``).
    """
    read_set: dict[str, Path] = {}
    for filename in _OWN_PROMPT_FILES:
        path = agent_dir / filename
        if path.exists():
            read_set[filename] = path
    tools_note = agent_dir / "tools_note.md"
    tools_fallback = agent_dir / "tools.md"
    if tools_note.exists():
        read_set["tools_note.md"] = tools_note
    elif tools_fallback.exists():
        read_set["tools.md"] = tools_fallback
    meta_path = agent_dir / "meta.json"
    if meta_path.exists():
        read_set["meta.json"] = meta_path
    if PROJECT_EXPERIENCE_FILE.exists():
        read_set[PROJECT_EXPERIENCE_FILE.name] = PROJECT_EXPERIENCE_FILE
    no_force_explore = bool(meta and meta.get("no_force_explore"))
    knowledge_file = KNOWLEDGE_NO_FORCE_EXPLORE_FILE if no_force_explore else KNOWLEDGE_FILE
    if knowledge_file.exists():
        read_set[knowledge_file.name] = knowledge_file
    if meta and meta.get("innate_skills"):
        for _skill_name, skill_file in _resolve_innate_skill_paths(agent_dir, meta):
            if skill_file.exists():
                read_set[f"_prompt_system/innate-skills/{_skill_name}/skill.md"] = skill_file
    else:
        skills_dir = agent_dir / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
                skill_file = skill_dir / "skill.md"
                if skill_dir.is_dir() and skill_file.exists():
                    read_set[f"skills/{skill_dir.name}/skill.md"] = skill_file
    memories_dir = agent_dir / "memories"
    if memories_dir.exists() and memories_dir.is_dir():
        for memory_file in memories_dir.iterdir():
            if memory_file.is_file() and memory_file.suffix == ".md":
                read_set[f"memories/{memory_file.name}"] = memory_file
    return read_set


def _manifest_relpath(agent_dir_name: str, key: str) -> str:
    """Map a loader mtime key to its ``agents/``-relative manifest path.

    Shared ``_prompt_system/`` files are ALREADY agents/-relative in the
    manifest; per-agent keys (artifacts, tools_note, memories) are
    prefixed with the agent dir name.
    """
    if key.startswith("_prompt_system/"):
        return key
    return f"{agent_dir_name}/{key}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ── AM-14 mcp_tool_names parity (private reach W3-authorized) ───────────────


def _resolve_mcp_tool_names(
    manager: Any, instance_id: str, stored_mcp: list[str] | None
) -> tuple[list[str], str]:
    """Resolve ``mcp_tool_names`` EXACTLY as the restore path does.

    W3 ruling: the private reach (``manager._lifecycle_service`` +
    stored ``instance_metadata`` + ``_get_mcp_tool_names`` at
    instance_lifecycle.py:1487) is ACCEPTABLE but ONLY inside this
    module, with this comment. Do NOT reimplement it and do NOT widen
    the reach. Provenance reports which branch won:
    ``cache`` (manager's MCP cache) | ``metadata`` (stored fallback) |
    ``empty`` (neither).
    """
    lifecycle = getattr(manager, "_lifecycle_service", None)
    resolver = getattr(lifecycle, "_get_mcp_tool_names", None)
    if resolver is not None:
        resolved = list(resolver(instance_id, stored_mcp) or [])
    else:
        resolved = list(stored_mcp or [])
    if resolved:
        source = "cache"
        if stored_mcp and resolved == list(stored_mcp):
            mcp_service = getattr(manager, "_mcp_service", None)
            cache_lookup = getattr(mcp_service, "get_mcp_tools", None) if mcp_service else None
            if cache_lookup is None or not cache_lookup(instance_id):
                source = "metadata"
    elif stored_mcp:
        source = "metadata"
    else:
        source = "empty"
    return resolved, source


# ── Tier-b diff (T3.1 — over the LOADER-CACHED persona only) ────────────────

_HEADING_RE = re.compile(r"^#{1,2} .*$", re.MULTILINE)
_HEADING_CAP = 10


def _persona_diff(old_persona: str, new_persona: str) -> dict[str, Any]:
    old_lines = old_persona.splitlines()
    new_lines = new_persona.splitlines()
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    old_headings = set(_HEADING_RE.findall(old_persona))
    new_headings = set(_HEADING_RE.findall(new_persona))
    headings_added = sorted(new_headings - old_headings)
    headings_removed = sorted(old_headings - new_headings)

    def _capped(items: list[str]) -> list[str]:
        if len(items) <= _HEADING_CAP:
            return items
        return items[:_HEADING_CAP] + [f"…and {len(items) - _HEADING_CAP} more"]

    return {
        "persona_diff": "exact",
        "lines_added": added,
        "lines_removed": removed,
        "bytes_before": len(old_persona.encode("utf-8")),
        "bytes_after": len(new_persona.encode("utf-8")),
        "headings_added": _capped(headings_added),
        "headings_removed": _capped(headings_removed),
    }


def _tier_c_diff() -> dict[str, Any]:
    """Tier-c honest fallback (AM-10 — the v1 contract): NO claim."""
    return {"persona_diff": "unavailable"}


# ── The INFO line (AM-15/D24 — frozen field order; the ONE telemetry) ───────


def format_info_line(
    instance_id: str,
    agent_id: str,
    outcome: str,
    code: str | None,
    provenance: dict[str, Any],
    *,
    fingerprint_changed: bool,
    hash_divergence: bool,
    cache_busted: bool,
    persona_diff_kind: str,
    was_cached: bool,
    manager_instances: str,
) -> str:
    """The frozen one-line-per-call observability (counts/flags ONLY —
    never persona or diff content). Field order is PINNED (D24); the
    outcome enum is the six-value superset (INFO_OUTCOMES)."""
    release = provenance.get("running_release") or "?"
    daemon_version = provenance.get("daemon_version") or "?"
    return (
        f"prompt_reload: instance={instance_id} agent={agent_id} "
        f"outcome={outcome} code={code or '-'} release={release} "
        f"daemon_version={daemon_version} "
        f"fingerprint_changed={str(fingerprint_changed).lower()} "
        f"hash_divergence={str(hash_divergence).lower()} "
        f"cache_busted={str(cache_busted).lower()} "
        f"current_matches_running={provenance.get('current_matches_running', 'unknown')} "
        f"persona_diff={persona_diff_kind} was_cached={str(was_cached).lower()} "
        f"manager_instances={manager_instances}"
    )


# ── The primitive (T1.5/T1.6/T1.7) ──────────────────────────────────────────


async def refresh_instance_prompt(
    manager: Any, instance_id: str, *, force: bool = False
) -> PromptRefreshResult:
    """Validate → compose → compare → (maybe) pop, for ONE instance.

    Raises nothing — every failure is a typed :class:`PromptRefreshResult`.
    Must be AWAITED on the daemon's main loop (D23 runbook precondition).
    """
    provenance: dict[str, Any] = {
        "instance_id": instance_id,
        "force": bool(force),
        "version_tag": None,  # parity: None on both paths for ari v1 (approver note 4)
        "manager_instances": "",
        "was_cached": instance_id in getattr(manager, "instances", {}),
    }
    provenance.update(_release_provenance())

    try:
        return await _refresh_impl(manager, instance_id, force, provenance)
    except Exception as exc:  # noqa: BLE001 — typed FAILED envelope, never raise
        logger.exception("prompt_reload internal error for %s", instance_id)
        return _refused(
            CODE_INTERNAL_ERROR,
            "an unexpected error occurred (logged server-side); nothing was "
            "popped and no cache was mutated by this call",
            provenance,
        )


async def _refresh_impl(
    manager: Any, instance_id: str, force: bool, provenance: dict[str, Any]
) -> PromptRefreshResult:
    # ── Resolve the instance + its agent (registry, spawn parity) ──────────
    instance_repo = getattr(manager, "_instance_repository", None)
    instance_meta = instance_repo.get(instance_id) if instance_repo is not None else None
    if instance_meta is None:
        return _refused(
            CODE_INTERNAL_ERROR,
            f"instance row not found for {instance_id} — cannot resolve its agent",
            provenance,
        )
    agent_id = getattr(instance_meta, "agent_id", "") or ""
    provenance["agent_id"] = agent_id

    from ..registry import get_registry

    registry = get_registry()
    agent_meta = registry.get_resolved(agent_id) if agent_id else None
    if agent_meta is None:
        return _refused(
            CODE_AGENT_DIR_UNRESOLVED,
            f"registry has no agent metadata for {agent_id!r} (metadata.path "
            "unresolvable — the agent dir the spawn path used is gone)",
            provenance,
        )
    resolved_dir = Path(agent_meta.path)
    provenance["resolved_dir"] = str(resolved_dir)
    version_tag = getattr(agent_meta, "version_tag", None)
    provenance["version_tag"] = version_tag  # parity with the restore call shape

    # ── Self-env-aware validation (D2/AM-5) ─────────────────────────────────
    self_env = provenance.get("self_env")
    if not self_env:
        return _refused(
            CODE_DIR_INVALID,
            "self-env marker unresolvable (ENSEMBLE_SELF_ENV unset and "
            "auto-derivation failed) — cannot validate the agent dir",
            provenance,
        )
    if self_env == "dev":
        agents_root = getattr(registry, "_agents_dir", None)
        if agents_root is None or not _is_under(resolved_dir, Path(agents_root)):
            return _refused(
                CODE_DIR_INVALID,
                f"dev env: resolved dir {resolved_dir} is outside the repo "
                f"agents root {agents_root}",
                provenance,
            )
        manifest_mode = "skip-dev"
    else:
        install_root = Path(provenance["install_root"]) if provenance.get("install_root") else None
        if install_root is None:
            return _refused(
                CODE_DIR_INVALID,
                f"{self_env} env: no install dir resolvable — cannot validate "
                f"resolved dir {resolved_dir}",
                provenance,
            )
        try:
            rel = resolved_dir.resolve().relative_to((install_root / "releases").resolve())
        except ValueError:
            rel = None
        if (
            rel is None
            or len(rel.parts) != 3
            or rel.parts[1] != "agents"
            or not _RELEASE_NAME_RE.match(rel.parts[0])
        ):
            return _refused(
                CODE_DIR_INVALID,
                f"{self_env} env: resolved dir {resolved_dir} is not under "
                f"{install_root / 'releases'} / <vX.Y.Z> / agents",
                provenance,
            )
        manifest_mode = "verify" if self_env in ("live", "sandbox") else "verify-soft"

    # ── meta.json structural validation (D6 code 7 — the loader papers
    # over corrupt meta with a silently skill-less persona; we refuse) ──────
    meta_path = resolved_dir / "meta.json"
    agent_file_meta: dict[str, Any] | None = None
    try:
        agent_file_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(agent_file_meta, dict):
            raise ValueError("meta.json root is not an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _refused(
            CODE_AGENT_META_INVALID,
            f"agent meta.json at {meta_path} is unparseable/structurally "
            f"invalid ({exc}) — the loader would silently compose a "
            "skill-less persona over it; refusing instead",
            provenance,
        )

    # ── Read-set derivation + hashes (AM-1/AM-8) ────────────────────────────
    read_set = _loader_read_set(resolved_dir, agent_file_meta)
    current_hashes = {key: _sha256_file(path) for key, path in read_set.items()}
    current_mtimes = {key: path.stat().st_mtime for key, path in read_set.items()}

    # ── Manifest verification (D3/AM-2/AM-3) ────────────────────────────────
    if manifest_mode == "skip-dev":
        provenance["manifest"] = "absent(dev)"
    else:
        from .release_manifest import ManifestError, load_release_manifest, verify_paths

        release_root = resolved_dir.parent.parent
        try:
            manifest = load_release_manifest(release_root)
        except ManifestError as exc:
            return _refused(
                CODE_MANIFEST_UNREADABLE,
                f"release manifest at {release_root} is torn/invalid: {exc}",
                provenance,
            )
        if manifest is None:
            if manifest_mode == "verify":
                return _refused(
                    CODE_MANIFEST_MISSING_LIVE,
                    f"{self_env}-shaped release {release_root} has no "
                    "manifest.json — cannot verify the loader's read set",
                    provenance,
                )
            provenance["manifest"] = f"absent({self_env})"
        else:
            relpaths = [
                _manifest_relpath(resolved_dir.name, key)
                for key in sorted(read_set)
            ]
            verdict = verify_paths(release_root, manifest, relpaths)
            if not verdict.ok:
                return _refused(
                    CODE_MANIFEST_MISMATCH,
                    "loader read-set files fail manifest verification: "
                    + "; ".join(verdict.mismatches),
                    provenance,
                )
            provenance["manifest"] = (
                f"ok({verdict.checked} checked, {verdict.skipped_unmanifested} "
                "unmanifested-skipped)"
            )

    # ── Tool-side prompt-versions repo probe (AM-9 — BEFORE composition;
    # the loader's own hook FAIL-OPENS and cannot be fail-closed truth) ─────
    repo = _get_prompt_version_repo()
    db_rows: list[Any] | None = None
    if repo is not None:
        try:
            db_rows = repo.get_effective_rows(agent_id, version_tag or "")
        except Exception as exc:  # noqa: BLE001 — fail-closed probe (R6)
            return _refused(
                CODE_DB_UNAVAILABLE,
                f"the prompt-versions repository probe raised ({exc.__class__.__name__}) "
                "— composition skipped (the loader would fail OPEN and "
                "silently downgrade the persona)",
                provenance,
            )
    # N4-unified operand: repo absent and zero-rows both yield "" (loader
    # :210 convention) so benign boot-moment states never false-diff.
    db_fingerprint = _compute_db_versions_fingerprint(db_rows)
    provenance["db_fingerprint"] = "absent" if repo is None else (db_fingerprint or "")
    provenance["db_rows"] = _db_row_states(db_rows)

    # ── mcp_tool_names parity (AM-14) ───────────────────────────────────────
    instance_metadata = getattr(instance_meta, "instance_metadata", None) or {}
    stored_mcp = instance_metadata.get("mcp_tool_names") if isinstance(instance_metadata, dict) else None
    mcp_names, mcp_source = _resolve_mcp_tool_names(manager, instance_id, stored_mcp)
    provenance["mcp_tool_names_source"] = mcp_source
    provenance["mcp_tool_names"] = sorted(mcp_names)

    prompt_cache = getattr(manager, "prompt_cache", None)
    if prompt_cache is None:
        return _refused(
            CODE_INTERNAL_ERROR,
            "manager has no prompt_cache — cannot compose",
            provenance,
        )
    my_cache_key = prompt_cache._make_key(agent_id, mcp_names, version_tag)
    tag_suffix = f"[{version_tag}]" if version_tag else ""
    key_prefix = f"{agent_id}{tag_suffix}::"
    other_keys = [
        key
        for key in prompt_cache._cache
        if key.startswith(key_prefix) and key != my_cache_key
    ]
    # AM-14 downgrade vector: a differently-keyed cache entry means the
    # persona this closure consumed lives under ANOTHER key — diffing
    # against MY key's entry would fabricate a delta (or hide one).
    parity_ok = not other_keys

    # ── Claim-guard snapshot (AM-13/D23): capture ONCE, before composing ───
    snapshot_entry = prompt_cache._cache.get(my_cache_key)

    # ── Baseline-map consult + tuple decision (D5 — hash-based, AM-8) ───────
    baseline_key = (agent_id, version_tag, tuple(sorted(mcp_names)))
    baseline = _BASELINE_MAP.get(baseline_key)
    files_changed = baseline is None or baseline[0] != current_hashes
    # fingerprint_changed (Advisory 8): the DB operand differs from the
    # baseline's stored value; false on unproven (no baseline to compare).
    fingerprint_changed = (
        bool(baseline is not None and baseline[1] != db_fingerprint)
        if baseline is not None
        else False
    )
    hash_divergence = bool(files_changed and baseline is not None)
    cache_busted = False

    # Stale-cache bust (AM-8): files changed while the loader's own
    # mtime+fingerprint equality would still HIT → the cache provably
    # serves the stale persona on THIS compose AND the next restore.
    if files_changed and snapshot_entry is not None:
        stored_mtimes = snapshot_entry[2]
        file_keys_match = {
            key: value for key, value in stored_mtimes.items() if key != FP_KEY
        } == current_mtimes
        fp_match = stored_mtimes.get(FP_KEY) == db_fingerprint
        if file_keys_match and fp_match:
            prompt_cache.invalidate(agent_id, mcp_names, version_tag)
            cache_busted = True
            provenance["cache_busted_reason"] = "hash_mtime_divergence"

    # ── Compose (R2 — through daemon.manager, the test patch seam) ──────────
    from ..manager import load_and_cache_prompt

    try:
        new_persona, _token_count = load_and_cache_prompt(
            agent_id,
            resolved_dir,
            prompt_cache,
            mcp_names,
            version_tag=version_tag,
            is_custom_agent=bool(getattr(agent_meta, "is_custom", False)),
        )
    except Exception as exc:  # noqa: BLE001 — typed refusal, never raise
        return _refused(
            CODE_COMPOSE_FAILED,
            f"load_and_cache_prompt raised {exc.__class__.__name__}: {exc}",
            provenance,
        )

    # ── Outcome decision (D5, Rev-3 B1/B4) ──────────────────────────────────
    if baseline is None:
        # B1/Q2: no baseline record (cold process / first call after ANY
        # restart) → unproven, force-like: recompose + POP + tier-c report.
        # NEVER no_change without a baseline (S10-n — deliberate).
        outcome = OUTCOME_UNPROVEN
    elif not files_changed and not fingerprint_changed:
        outcome = OUTCOME_FORCED_RELOAD if force else OUTCOME_NO_CHANGE
    elif not files_changed and fingerprint_changed:
        outcome = OUTCOME_RELOADED_DB  # B4/Q1 — F4 honored AT the reload
    else:
        outcome = OUTCOME_RELOADED

    # ── Tier-b vs tier-c diff (claim-guard D23 + AM-14 parity) ──────────────
    post_entry = prompt_cache._cache.get(my_cache_key)
    claim_ok = (
        snapshot_entry is not None
        and parity_ok
        and (
            post_entry is snapshot_entry
            or (post_entry is not None and post_entry[0] == new_persona)
        )
    )
    if outcome in (OUTCOME_NO_CHANGE, OUTCOME_FORCED_RELOAD) and not files_changed:
        # Unchanged tree: the old and new personas are the same fill —
        # the tier-b block still reports (zero-delta) when provable.
        diff = (
            _persona_diff(snapshot_entry[0], new_persona)
            if claim_ok
            else _tier_c_diff()
        )
    elif claim_ok:
        diff = _persona_diff(snapshot_entry[0], new_persona)
    else:
        diff = _tier_c_diff()

    file_rows = []
    for key in sorted(current_hashes):
        before = baseline[0].get(key) if baseline else None
        after = current_hashes[key]
        file_rows.append(
            {
                "path": key,
                "sha_before": (before[:12] + "…") if before else "-",
                "sha_after": after[:12] + "…",
                "changed": bool(before is None or before != after),
            }
        )
    diff["files"] = file_rows

    # ── Pop (T1.7 — iff changed, forced, or unproven; NEVER on no_change) ───
    instances = getattr(manager, "instances", {})
    before_count = len(instances)
    if outcome != OUTCOME_NO_CHANGE:
        instances.pop(instance_id, None)
    after_count = len(instances)
    provenance["manager_instances"] = f"{before_count}->{after_count}"
    provenance["hash_mtime_divergence"] = cache_busted
    provenance["cache_busted"] = cache_busted
    provenance["baseline_present"] = baseline is not None
    provenance["parity_ok"] = parity_ok
    provenance["claim_guard"] = "held" if claim_ok else "skew-or-absent"

    # ── Baseline write (Advisory 2 — every successful fill seeds it) ────────
    _BASELINE_MAP[baseline_key] = (dict(current_hashes), db_fingerprint)

    message = _outcome_message(outcome, files_changed, fingerprint_changed, cache_busted)
    return PromptRefreshResult(
        outcome=outcome,
        code=None,
        message=message,
        diff=diff,
        provenance=provenance,
    )


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _db_row_states(db_rows: list[Any] | None) -> list[dict[str, str]]:
    """Per-artifact effective-row state for the provenance payload,
    carrying the F7 honesty marks (D15 — the canonical S10-a/S10-b
    literals, referenced by content here; the report renderer emits
    them verbatim)."""
    states: list[dict[str, str]] = []
    for row in db_rows or []:
        artifact = getattr(row, "artifact", "") or ""
        state = {
            "artifact": artifact,
            "class": getattr(row, "artifact_class", "CORE") or "CORE",
            "selected": getattr(row, "selected_version_id", None) or "-",
        }
        if artifact == "tools_note":
            state["honesty"] = S10_A_TOOLS_NOTE_DEAD
        elif artifact in ("user", "growth"):
            state["honesty"] = S10_B_USER_GROWTH_NOT_IN_PROMPT
        states.append(state)
    return states


def _outcome_message(
    outcome: str, files_changed: bool, fingerprint_changed: bool, cache_busted: bool
) -> str:
    if outcome == OUTCOME_NO_CHANGE:
        return (
            "No changes to the prompt files since the last cache fill "
            "(file-level freshness — not a claim that the running closure "
            "persona is current)."
        )
    if outcome == OUTCOME_FORCED_RELOAD:
        return (
            "Forced reload: the prompt files are unchanged, but force=true "
            "popped the cached graph anyway — the next dispatch recomposes "
            "from the same inputs."
        )
    if outcome == OUTCOME_RELOADED_DB:
        return (
            "Prompt-versions DB content changed (files unchanged) — "
            "recomposed through the loader and popped; the DB override is "
            "honored on the reload itself, not deferred."
        )
    if outcome == OUTCOME_UNPROVEN:
        return (
            "No prior baseline record for this agent in this process "
            "(first call after a restart) — recomposed and popped as a "
            "precaution; the diff below cannot be proven, so no persona "
            "delta is claimed."
        )
    detail = "prompt files changed"
    if cache_busted:
        detail += "; the loader cache was provably stale (hash/mtime divergence) and was BUSTED before composing"
    return f"Reloaded: {detail} — recomposed and the cached graph was popped."
