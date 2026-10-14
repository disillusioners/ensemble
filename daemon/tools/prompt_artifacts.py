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
remains free to read template content). Phase B (commit 4bf53cec8) removed
the in-function ``_check_prompt_artifact_write`` shim from
``inner_soul._update_soul/_update_workflow`` (no file-write path left to
guard — soul/workflow/user route through ``_submit_prompt_version`` →
prompt-version DB drafts); enforcement is (a) **no file-write code path** in
the rewired ``inner_soul``/``agent_mother``, (b) the ``write_file`` /
``edit_file`` surface guards at ``daemon/tools/filesystem.py`` (post-
``_resolve_within_workdir``), and (c) the belt-and-braces legacy
``manager.prompt_cache.invalidate(agent_id, version_tag=version_tag)`` call
on the remaining file-writer carve-outs (``memory.md`` / ``memories/``) so
the cache self-heals even if the prompt-version layer is bypassed.

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

**memory.md / user.md / growth.md — IMMUTABLE TEMPLATE GUARDS (2026-10-14 directive).**
The Phase A disposition carved these three out as file-writable
carve-outs; User Directive #3 + #5B (2026-10-14, RESOLUTION-CORE)
reverses that for ``memory.md`` and ``growth.md`` (they join the
immutable-template regime). ``user.md`` is already in
``_DB_VERSIONED_FILES`` for ``agent_mother`` (Phase B D5) and the
``inner_soul`` DUAL-PATH now writes all three via
``create_memory_version`` (MEMORY-class, transition-free). The
guard surface here is therefore TEMPLATE-side only: the file is
immutable at runtime (defense-in-depth — the actual writer
substrate is the DB). ``memory.md`` joins the artifact set with
the lone carve-out that the ``agent_mother.agent_modify`` D6 lane
no longer writes it directly (the create_memory_version path is
the ONLY writer). ``growth.md`` joins with the historical config-
knob semantics preserved — the writer is the DB row, the file is
read by ``_load_growth_rules`` as a fallback when no row exists
(cold-boot tolerance per decisions.md §7 boot-tolerance proofs).

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

import hashlib
import logging
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
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
    # User Directive #3 + #5B (2026-10-14, RESOLUTION-CORE) —
    # memory.md and growth.md JOIN the versioned-immutable set
    # per the directive's re-disposition ruling. The substrate
    # change in inner_soul (memory/user/growth → create_memory_version)
    # makes these two files MEMORY-class templates: the file is
    # NEVER modified at runtime; the DB row IS the live content;
    # the on-disk copy stays the F6 fallback for cold boots with
    # zero DB rows. user.md was already in ``_DB_VERSIONED_FILES``
    # for agent_mother (Phase B D5) and is handled by the
    # inner_soul DUAL-PATH rewrite — it does NOT need to join
    # this frozenset because the writer is already DB-only
    # (no file-write code path left to guard on user.md).
    "memory.md",
    "growth.md",
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


# ---------------------------------------------------------------------------
# Drift detection (Phase A T13 / S9 — the D-BASH-ESCAPE accepted-risk control)
# ---------------------------------------------------------------------------
# The bash tool cannot be code-level guarded (decisions.md D-BASH-ESCAPE);
# the mitigating control is a committed sha256 baseline over the versioned
# set, re-hashed at stage time (scripts/upgrade/stage.sh leg) and at daemon
# boot (api.py lifespan, next to the prompt-version create_all). Both
# consumers call into THIS module so the artifact set + depth-2 matcher +
# path conventions stay single-sourced.
#
# Baseline format (deterministic, sha256sum -c compatible):
#     "<hex sha256>  agents/<agent_dir>/<artifact>\n"
# sorted by path, one line per existing artifact, NO comments/blank lines
# (GNU sha256sum -c and perl shasum -c both flag non-conforming lines).
# Paths are TREE-ROOT-relative (always "agents/..."), so the same file
# validates a repo checkout (cd <repo> && sha256sum -c
# agents/.prompt-artifacts.sha256) AND a staged release tree.
#
# Failure posture (allow-stale policy, plugin_staleness_policy parity):
# drift is REPORTED (operator-visible WARN + stage journal entry) and
# NEVER gates — no stage/boot failure on mismatch. A missing baseline is
# a normal state (fresh checkout before first generation) → single INFO.

# Baseline filename — lives INSIDE agents/ so it ships with the release
# payload (stage.sh copies the repo agents/ tree verbatim; the manifest
# per-file map covers it like any other agents/ file).
BASELINE_FILENAME = ".prompt-artifacts.sha256"


def iter_versioned_artifact_paths(agents_root: Path) -> Iterator[Path]:
    """Yield existing depth-2 versioned-artifact paths under *agents_root*.

    Same match rule as the guard (``_depth2_match``): exactly
    ``<agents_root>/<dir>/<artifact>`` with *artifact* in
    ``VERSIONED_PROMPT_ARTIFACTS`` — ``_``-dirs included, depth-3+
    excluded, non-existent files skipped.
    """
    try:
        if not agents_root.is_dir():
            return
        for child in sorted(agents_root.iterdir()):
            if not child.is_dir():
                continue
            for artifact in sorted(VERSIONED_PROMPT_ARTIFACTS):
                candidate = child / artifact
                try:
                    if candidate.is_file():
                        yield candidate
                except OSError:
                    continue
    except OSError:
        return


def compute_versioned_artifact_hashes(agents_root: Path) -> dict[str, str]:
    """Hash every versioned artifact under *agents_root*.

    Returns ``{"agents/<dir>/<artifact>": "<hex sha256>"}`` — keys are
    TREE-ROOT-relative (the parent of *agents_root* is the tree root),
    matching the committed baseline format.
    """
    hashes: dict[str, str] = {}
    for path in iter_versioned_artifact_paths(agents_root):
        rel = path.relative_to(agents_root)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        hashes[f"agents/{rel.as_posix()}"] = h.hexdigest()
    return dict(sorted(hashes.items()))


def parse_baseline(content: str) -> tuple[dict[str, str], list[str]]:
    """Parse baseline text → (entries, malformed_lines).

    Entries map ``agents/<dir>/<artifact>`` → hex digest. A line is
    malformed unless it is exactly ``<64 lowercase hex>  <path>`` (the
    sha256sum text-mode shape; binary-mode ``*`` separators and
    comments/blank lines are rejected — the generator never emits them).
    """
    entries: dict[str, str] = {}
    malformed: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("  ", 1)
        if (
            len(parts) != 2
            or not re.fullmatch(r"[0-9a-f]{64}", parts[0])
            or not parts[1].startswith("agents/")
            or len(parts[1].split("/")) != 3
        ):
            malformed.append(line)
            continue
        entries[parts[1]] = parts[0]
    return entries, malformed


@dataclass(frozen=True)
class BaselineCheckResult:
    """Outcome of a baseline-vs-tree comparison (T13)."""

    status: str  # "ok" | "drift" | "baseline_absent" | "no_agents_root"
    agents_root: Path | None = None
    baseline_path: Path | None = None
    modified: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    malformed: tuple[str, ...] = ()
    verified_count: int = 0

    @property
    def drift_count(self) -> int:
        return len(self.modified) + len(self.missing) + len(self.added) + len(
            self.malformed
        )

    def summary(self) -> str:
        """One-line human summary (stable token order for log greps)."""
        if self.status == "ok":
            return (
                f"PROMPT-BASELINE: OK — {self.verified_count} versioned "
                f"artifact(s) match {BASELINE_FILENAME}"
            )
        if self.status == "baseline_absent":
            return (
                f"PROMPT-BASELINE: ABSENT — no {BASELINE_FILENAME} under any "
                f"agents root (fresh tree before first baseline generation)"
            )
        if self.status == "no_agents_root":
            return "PROMPT-BASELINE: SKIP — no agents root resolved"
        return (
            f"PROMPT-DRIFT: {self.drift_count} difference(s) vs "
            f"{BASELINE_FILENAME} "
            f"(modified={len(self.modified)} missing={len(self.missing)} "
            f"added={len(self.added)} malformed={len(self.malformed)})"
        )

    def detail_lines(self, cap: int = 20) -> list[str]:
        """Per-file drift lines, capped (operator-visible, journal-safe)."""
        lines: list[str] = []
        for path in self.modified:
            lines.append(f"PROMPT-DRIFT-FILE: {path} (modified)")
        for path in self.missing:
            lines.append(f"PROMPT-DRIFT-FILE: {path} (missing)")
        for path in self.added:
            lines.append(f"PROMPT-DRIFT-FILE: {path} (added)")
        for line in self.malformed:
            lines.append(f"PROMPT-DRIFT-BASELINE-LINE: {line}")
        if len(lines) > cap:
            overflow = len(lines) - cap
            lines = lines[:cap] + [f"... (+{overflow} more)"]
        return lines


def check_baseline_drift(
    agents_roots: tuple[Path, ...] | None = None,
) -> BaselineCheckResult:
    """Compare the committed baseline against the live tree (read-only).

    Uses the FIRST resolved agents root that carries a baseline file
    (both canonical roots hold the same tree in practice; divergent-path
    installs pick whichever root actually has the payload). Read-only by
    construction — never writes. The *agents_roots* parameter is the
    test seam (mirrors ``check_write``); production callers omit it.
    """
    if agents_roots is None:
        agents_roots = get_agents_roots()

    baseline_path: Path | None = None
    agents_root: Path | None = None
    for root in agents_roots:
        candidate = root / BASELINE_FILENAME
        if candidate.is_file():
            baseline_path = candidate
            agents_root = root
            break

    if baseline_path is None or agents_root is None:
        if not any(root.is_dir() for root in agents_roots):
            return BaselineCheckResult(status="no_agents_root")
        return BaselineCheckResult(status="baseline_absent")

    try:
        baseline_text = baseline_path.read_text(encoding="utf-8")
    except OSError as exc:
        # Unreadable baseline → treat as drift (fail-loud, not fail-silent).
        return BaselineCheckResult(
            status="drift",
            agents_root=agents_root,
            baseline_path=baseline_path,
            malformed=(f"<unreadable baseline: {exc}>",),
        )

    expected, malformed = parse_baseline(baseline_text)
    actual = compute_versioned_artifact_hashes(agents_root)

    modified = sorted(
        path
        for path, digest in expected.items()
        if path in actual and actual[path] != digest
    )
    missing = sorted(path for path in expected if path not in actual)
    added = sorted(path for path in actual if path not in expected)

    if modified or missing or added or malformed:
        return BaselineCheckResult(
            status="drift",
            agents_root=agents_root,
            baseline_path=baseline_path,
            modified=tuple(modified),
            missing=tuple(missing),
            added=tuple(added),
            malformed=tuple(malformed),
        )
    return BaselineCheckResult(
        status="ok",
        agents_root=agents_root,
        baseline_path=baseline_path,
        verified_count=len(actual),
    )


def log_baseline_drift_at_boot(logger_: logging.Logger | None = None) -> str:
    """Boot-time drift report — WARN on drift, INFO otherwise (T13).

    NEVER raises, NEVER blocks boot (allow-stale policy). Returns the
    check status token ("ok" | "drift" | "baseline_absent" |
    "no_agents_root") so the caller can log a non-fatal fallback line on
    its own exceptions. The *logger_* parameter defaults to this module's
    logger; api.py passes its own so the WARN lands in the operator's
    usual log stream.
    """
    log = logger_ if logger_ is not None else logger
    result = check_baseline_drift()
    if result.status == "drift":
        detail = "; ".join(result.detail_lines())
        log.warning(
            "[Startup] Prompt-artifact baseline DRIFT — %d file(s) differ "
            "from the committed %s (allow-stale: informational, NOT "
            "boot-blocking; regenerate via "
            "scripts/protection/generate_prompt_artifact_baseline.py): %s",
            result.drift_count,
            BASELINE_FILENAME,
            detail,
        )
    elif result.status == "baseline_absent":
        log.info(
            "[Startup] Prompt-artifact baseline absent (%s) — drift check "
            "skipped (fresh checkout before first baseline generation)",
            BASELINE_FILENAME,
        )
    elif result.status == "no_agents_root":
        log.info(
            "[Startup] Prompt-artifact drift check skipped: no agents root "
            "resolved"
        )
    else:
        log.info(
            "[Startup] Prompt-artifact baseline clean: %d versioned "
            "artifact(s) match %s",
            result.verified_count,
            BASELINE_FILENAME,
        )
    return result.status


def generate_baseline_text(agents_root: Path) -> str:
    """Render the deterministic baseline body for *agents_root*.

    Sorted ``<sha256>  agents/<dir>/<artifact>`` lines, LF-terminated,
    no header/comment/blank lines (sha256sum -c strict compatibility).
    """
    hashes = compute_versioned_artifact_hashes(agents_root)
    return "".join(f"{digest}  {path}\n" for path, digest in hashes.items())


def baseline_path_for(agents_root: Path) -> Path:
    """Return the canonical baseline path inside *agents_root*."""
    return agents_root / BASELINE_FILENAME


__all__ = [
    "BASELINE_FILENAME",
    "BaselineCheckResult",
    "VERSIONED_PROMPT_ARTIFACTS",
    "baseline_path_for",
    "check_baseline_drift",
    "check_write",
    "compute_versioned_artifact_hashes",
    "generate_baseline_text",
    "get_agents_roots",
    "iter_versioned_artifact_paths",
    "log_baseline_drift_at_boot",
    "parse_baseline",
    "reset_agents_roots_cache",
]