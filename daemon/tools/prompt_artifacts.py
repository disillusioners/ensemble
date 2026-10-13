"""Single-source immutability guard for versioned prompt artifacts (Phase A T9/S10).

**The conservative default — frozen at import time.** The set of artifacts
that are runtime-immutable is a hardcoded frozenset (not a DB config table).
Adding an artifact to this set is a code change; removing one is also a code
change. The rationale lives in `phaseA-plan.md` (T9) and
`architecture-recommendation.md` (S10): a runtime flip without matching code
semantics would ship inconsistency (e.g. a guarded file that the loader
still reads into the system prompt).

**Wire sites.** Phase A invokes ``check_write()`` at exactly TWO post-resolve
sites (C3 binding):

    * ``daemon.tools.filesystem.write_file`` — post-``_resolve_within_workdir``
      (:889) pre-open.
    * ``daemon.tools.filesystem.edit_file`` — post-:1243 pre-write.

The resolver is shared with ``read_file`` (:665) and ``list_directory`` (:591);
a resolver-INTERNAL guard would fire on reads and break agent self-inspection.
Reads are NOT guarded (this is a write-side immutability regime; the loader
remains free to read template content). Belt-and-braces Phase-A shim at
``daemon.tools.inner_soul._update_soul`` / ``_update_workflow`` routes the
pre-write path through this helper too — closes the Phase-A unguarded window
even if merge order shifts (S11).

**Containment.** Both agents-root candidates (registry base + ``BASE_DIR /
agents``) are ``resolve(strict=False)``-d and checked against the resolved
target via ``WorkspaceGuard._normed_contains`` (realpath + normcase; defeats
``..`` escapes and symlink drift). The depth-2 filename-anchored match is
applied AFTER containment, so a file like ``agents/notes/soul.md`` does NOT
trigger (it's not under an agent subtree at depth-2). ``_``-dirs (``_baby_template``,
``_prompt_system``, ``_mother``, ``_trash``, ``_inner_soul``) are matched by the
same depth-2 rule — a crafty agent that creates ``_baby_template/soul.md``
would otherwise evade the guard.

**Failure posture.** The guard's OWN exception (e.g. realpath resolution
raises) is fail-closed: ``check_write`` returns a ``GUARD_ERROR`` refusal
rather than letting ``open()`` see an unverified path. Never reach ``open()``
unverified.

**Refusal shape (F8, typed):** agents must be able to branch on this. The
literal prefix ``ERROR: REFUSED —`` is the contract token; the substring
``submit a prompt-version draft`` is the actionable hint; ``(file_path=…)``
is the evidence link.

**Bash tool = accepted-risk escape hatch (R6 / D-BASH-ESCAPE).** No OS-level
fence ships in Phase A. The drift-detection leg (S9 / T13) provides the
mitigating control: a committed baseline ``agents/.prompt-artifacts.sha256``
over the versioned set, re-hashed at stage time and at boot. The bash tool
is held by 26/43 agents; the practical alternative is a documented
operator + skill-evolution policy lane (Phase B+).

**memory.md / user.md / growth.md = CARVED OUT (R7 / R11).** The disposition
fork (``decisions.md`` worker, Phase-A decision still pending at the time
of this commit) conservatively excludes these from the versioned-immutable
set. Schema + resolution hook + service + repo are artifact-agnostic, so a
later ``decisions.md`` inversion is a config-only change.

**Allowed-time exceptions (preserved, NOT covered by guard):**
    * ``agent_mother.agent_create`` — template copy from ``_baby_template``.
    * ``routers/agents.py`` POST — same template copy, API lane.
    * ``agent_mother.agent_delete`` + ``routers/agents.py`` DELETE — move to
      ``_trash``.
    * History file writes (``history/*_soul_change.md``) — audit trail.
    * ``agents/_trash/`` subtree moves — preserved by the ``_``-dir
      exclusion (the deletion moves the WHOLE agent dir, not its individual
      artifacts).

These exceptions are NOT enforced here — they're structurally invisible to
this layer (they go through dedicated tool paths, not ``write_file`` /
``edit_file``). Documented in the module docstring as a defensive cross-link.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conservative default versioned-immutable set (S10).
# ---------------------------------------------------------------------------
# Frozen at import — adding an artifact here is a code change (and requires
# matching loader + repository + audit work). The skill.md legacy loader is
# included for the pre-2026 single-skill agent layout (skill-tables are
# versioned via a separate path; skill.md is the legacy single-file loader
# consumed at daemon/tools/loader.py).
#
# The .md extension is preserved (lowercase); Windows-compatible.
# The "tools.md" entry covers the legacy fallback for tools_note.md (the
# loader reads tools_note.md first, then tools.md if missing — both are
# versioned).
VERSIONED_PROMPT_ARTIFACTS: frozenset[str] = frozenset({
    "soul.md",
    "rule.md",
    "workflow.md",
    "tools_note.md",
    "tools.md",  # legacy fallback for tools_note.md
    "skill.md",  # legacy single-skill loader
})

# ---------------------------------------------------------------------------
# Agents-root candidates — both shapes per phaseA-plan.md §"Critical Files".
# ---------------------------------------------------------------------------
# Registry base: ``registry.py:1217-1219`` uses ``Path(__file__).parent.parent``
# for the non-frozen case (= repo root) and ``Path(sys.executable).parent``
# for the frozen case (= install dir root). Both produce ``<root>/agents/``.
# BASE_DIR shape: ``agent_mother.py:30`` / ``api.py:190`` use the same
# pattern. We resolve BOTH at check time so a divergent path (one of them
# symlinked, or worktree confusion) still gets caught.
#
# Tested isolation: tests pass a tmp_path-derived ``agents_root`` override
# via the optional ``agents_roots`` parameter; production code lets the helper
# resolve both automatically.

_DEFAULT_AGENTS_ROOTS_CACHE: tuple[Path, ...] | None = None


def _default_agents_roots() -> tuple[Path, ...]:
    """Resolve both canonical agents-root candidates (registry + BASE_DIR)."""
    roots: list[Path] = []

    # Shape 1: registry.py's AgentRegistry._agents_dir
    # Non-frozen: Path(registry.py).parent.parent / "agents" = <repo>/agents
    # Frozen: Path(sys.executable).parent / "agents" = <install>/agents
    try:
        if getattr(sys, "frozen", False):
            registry_root = Path(sys.executable).parent
        else:
            registry_root = Path(__file__).parent.parent.parent  # daemon/tools/prompt_artifacts.py → repo root
        registry_agents = (registry_root / "agents").resolve(strict=False)
        roots.append(registry_agents)
    except (OSError, RuntimeError) as exc:
        logger.warning("prompt_artifacts: registry_root resolution failed: %s", exc)

    # Shape 2: agent_mother / api BASE_DIR / "agents"
    # Same non-frozen/frozen logic — same effective path, kept as a distinct
    # candidate so a divergent path resolves correctly without surprise.
    try:
        if getattr(sys, "frozen", False):
            base_root = Path(sys.executable).parent
        else:
            # daemon/tools/prompt_artifacts.py → up 3 = repo root
            base_root = Path(__file__).parent.parent.parent
        base_agents = (base_root / "agents").resolve(strict=False)
        if not roots or base_agents != roots[0]:
            roots.append(base_agents)
    except (OSError, RuntimeError) as exc:
        logger.warning("prompt_artifacts: BASE_DIR resolution failed: %s", exc)

    return tuple(roots)


def get_agents_roots() -> tuple[Path, ...]:
    """Return the agents-root candidates (cached after first call).

    Tests can monkey-patch this via ``monkeypatch.setattr`` on the cached
    tuple, or pass an explicit ``agents_roots`` to ``check_write()``.
    """
    global _DEFAULT_AGENTS_ROOTS_CACHE
    if _DEFAULT_AGENTS_ROOTS_CACHE is None:
        _DEFAULT_AGENTS_ROOTS_CACHE = _default_agents_roots()
    return _DEFAULT_AGENTS_ROOTS_CACHE


def reset_agents_roots_cache() -> None:
    """Clear the cached agents-roots tuple (test helper)."""
    global _DEFAULT_AGENTS_ROOTS_CACHE
    _DEFAULT_AGENTS_ROOTS_CACHE = None


# ---------------------------------------------------------------------------
# Guard helper — the single entry point invoked by filesystem.write_file /
# filesystem.edit_file (Phase A T9) and by the inner_soul 2-site shim (T11).
# ---------------------------------------------------------------------------

# Refusal-token marker. Tests grep for this substring to assert refusal.
_REFUSED_PREFIX = "ERROR: REFUSED"
_REFUSAL_HINT = "submit a prompt-version draft via the prompt-versions API instead"
_GUARD_ERROR_TOKEN = "GUARD_ERROR"


def _normed_contains(base: Path, target: Path) -> bool:
    """Realpath containment check (defeats .. and symlink drift).

    Mirrors ``WorkspaceGuard._normed_contains`` semantics but is local to
    this module so the guard has no upstream-import dependency at import
    time. Returns True iff *target* is within *base* after both are
    resolve(strict=False)-d and normcased.
    """
    try:
        normed_target = Path(os.path.normcase(str(target.resolve(strict=False))))
        normed_base = Path(os.path.normcase(str(base.resolve(strict=False))))
        normed_target.relative_to(normed_base)
        return True
    except (ValueError, OSError):
        return False


def _depth2_match(target: Path, agents_root: Path) -> tuple[bool, str | None]:
    """Return (True, artifact_filename) iff *target* matches the depth-2
    pattern ``<agents_root>/<agent_dir>/<artifact>``.

    agent_dir may be a normal agent name (e.g. ``developer``) OR a
    underscore-prefixed special (e.g. ``_baby_template``, ``_prompt_system``,
    ``_mother``, ``_trash``, ``_inner_soul``) — both are depth-2 under
    ``agents_root`` and both trigger if the artifact filename matches
    ``VERSIONED_PROMPT_ARTIFACTS``. Depth-3+ (e.g. ``agents/notes/soul.md``
    is depth-2 but ``agents/<x>/skills/soul.md`` is depth-3 — neither is a
    versioned artifact path; both correctly miss).
    """
    try:
        rel = target.relative_to(agents_root)
    except ValueError:
        return False, None
    parts = rel.parts
    if len(parts) != 2:
        return False, None
    artifact = parts[1]
    if artifact in VERSIONED_PROMPT_ARTIFACTS:
        return True, artifact
    return False, None


def check_write(
    file_path: Path,
    agents_roots: tuple[Path, ...] | None = None,
) -> str | None:
    """Return None if the write is allowed, or a typed-refusal string if not.

    The caller (filesystem.write_file / filesystem.edit_file / inner_soul
    shim) short-circuits on a non-None result — NEVER reaches the underlying
    file open.

    The refusal shape is exact (F8):

        ERROR: REFUSED — {artifact} is an immutable prompt artifact for
        agent '{name}'; submit a prompt-version draft via the
        prompt-versions API instead. (file_path={file_path})

    The guard's OWN exception (e.g. realpath resolution raises) returns
    a ``GUARD_ERROR`` refusal — fail-closed.
    """
    if agents_roots is None:
        agents_roots = get_agents_roots()

    try:
        # Resolve the target realpath so ``..`` escapes and symlinks are
        # caught. strict=False: the file may not exist yet (write_file
        # creates it). The resolve call itself rarely raises, but if it
        # does, we still fail closed (the catch below).
        try:
            target_resolved = file_path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            return (
                f"{_REFUSED_PREFIX} — {_GUARD_ERROR_TOKEN}: target path "
                f"resolution failed: {exc} (file_path={file_path})"
            )

        for agents_root in agents_roots:
            if not _normed_contains(agents_root, target_resolved):
                continue
            matched, artifact = _depth2_match(target_resolved, agents_root)
            if not matched:
                continue
            # Depth-2 match → refuse. Extract agent name from the resolved
            # path's relative parts.
            rel = target_resolved.relative_to(agents_root)
            agent_name = rel.parts[0]
            return (
                f"{_REFUSED_PREFIX} — {artifact} is an immutable prompt "
                f"artifact for agent '{agent_name}'; {_REFUSAL_HINT}. "
                f"(file_path={file_path})"
            )

        return None
    except Exception as exc:
        # Defense-in-depth: any unexpected guard exception → fail-closed.
        logger.exception("prompt_artifacts: unexpected guard error")
        return (
            f"{_REFUSED_PREFIX} — {_GUARD_ERROR_TOKEN}: unexpected guard "
            f"error: {exc} (file_path={file_path})"
        )


__all__ = [
    "VERSIONED_PROMPT_ARTIFACTS",
    "check_write",
    "get_agents_roots",
    "reset_agents_roots_cache",
]