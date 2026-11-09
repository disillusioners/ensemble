"""The skill-injection roster (skill-system-revival, workstream F).

**The flag.**  ``agents/<name>/meta.json`` carries a ``skill_injection``
flag with a **fail-closed** default — ``daemon/registry.py:690`` and
``:756`` both read ``skill_injection=meta.get("skill_injection", False)``.
Absent ⇒ ``False``.

**The flag gates TWO axes, and that is the semantic trap this module
exists to make visible.**

* **Capture axis** — the AUTOMATIC capture path (Gate 3 of the capture
  eligibility check, ``daemon/services/skill_metrics_service.py:610-640``).
* **Injection axis** — the dynamic injection lane, gated at the call
  sites in ``daemon/services/instance_messaging.py`` and
  ``daemon/services/context_messages.py``.

It does **not** gate the MANUAL capture tool (``skill_execute_capture``
carries no ``skill_injection`` check — only the kill-switch).

⇒ The roster manifest records the two axes SEPARATELY, so the Phase-2
decision is a per-axis decision rather than a single opaque bit.

**The manifest.**  ``.agents/shared/planning/skill-system-revival/roster.md``
is the authoritative list.  This module RE-DERIVES the roster from the
repository (read-only) and DIFFS it against the manifest, so the next
census is a diff against a known state instead of a re-derivation from
scratch (plan §10, "F ships a roster manifest").

**The staged payload.**  Phase 1 flips **no** agent (DS-25).  The roster
change is a STAGED PAYLOAD: it is ARMED **only when the capture lane is
ON** — the canonical ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` kill-switch
resolver (``daemon/services/skill_job_dispatcher._resolve_capture_enabled``,
the same resolver ``skill_metrics_service`` imports).  With the lane OFF
the payload is EMPTY: the roster is inert and no ``meta.json`` changes.
The payload is COMPUTED here; the ``meta.json`` WRITE is the leader's
Phase-2 route (``agents/**`` is house-fenced in Phase 1).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daemon.services.skill_job_dispatcher import _resolve_capture_enabled

logger = logging.getLogger(__name__)

__all__ = [
    "CENSUS_GLOB",
    "ROSTER_MANIFEST_RELPATH",
    "RosterDrift",
    "RosterEntry",
    "StagedEdit",
    "StagedPayload",
    "capture_lane_enabled",
    "derive_roster",
    "diff_roster",
    "manifest_path",
    "parse_roster_manifest",
    "policy_violations",
    "stage_payload",
]


# The census population: every ``agents/*/meta.json``.  This is the
# method the Phase-0 census used (46 entries) and the method the
# manifest-diff pin re-derives with.  The agent REGISTRY registers one
# fewer (46 minus ``_baby_template``, which ``registry.SKIP_DIRS``
# excludes) — the manifest records both populations.
CENSUS_GLOB = "agents/*/meta.json"

# The authoritative roster manifest (planning dir — plan §18 row 38).
ROSTER_MANIFEST_RELPATH = (
    ".agents/shared/planning/skill-system-revival/roster.md"
)

# Fenced markers around the machine-readable census table inside the
# manifest.  The parser only ever reads the block between them.
_TABLE_BEGIN = "<!-- F-ROSTER-TABLE-BEGIN -->"
_TABLE_END = "<!-- F-ROSTER-TABLE-END -->"

# The registry skips these scaffolding directories (they are not agents).
_REGISTRY_SKIP_DIRS = frozenset(
    {"_trash", "_baby_template", "_prompt_system", "_inner_soul"}
)


def manifest_path(repo_root: Path | None = None) -> Path:
    """Return the absolute path of the roster manifest."""
    root = Path(repo_root) if repo_root is not None else _repo_root()
    return root / ROSTER_MANIFEST_RELPATH


def _repo_root() -> Path:
    """The repository root (``daemon/services/<this file>`` → up 2)."""
    return Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────
# The census
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RosterEntry:
    """One agent's roster row.

    Attributes:
        agent: The agent directory name (e.g. ``approver[v2]``).
        registry: ``agent`` when the registry registers it, or
            ``scaffolding`` when ``registry.SKIP_DIRS`` excludes it.
        declares: Whether ``meta.json`` carries the ``skill_injection``
            key at all.
        live: The EFFECTIVE live value (``False`` when the key is
            absent — the fail-closed default).
        capture_enabled: The capture axis.
        injection_enabled: The injection axis.
        source: Where the row's provenance comes from.
    """

    agent: str
    registry: str = "agent"
    declares: bool = False
    live: bool = False
    capture_enabled: bool = False
    injection_enabled: bool = False
    source: str = ""


@dataclass(frozen=True)
class RosterDrift:
    """One difference between the re-derived census and the manifest."""

    agent: str
    field: str
    expected: Any
    actual: Any

    def __str__(self) -> str:
        return (
            f"{self.agent}: {self.field} — manifest={self.expected!r} "
            f"re-derived={self.actual!r}"
        )


@dataclass(frozen=True)
class StagedEdit:
    """One staged ``meta.json`` edit (never written by this module)."""

    agent: str
    path: str
    key: str
    axis: str
    old: Any
    new: Any
    ready: bool = True
    note: str = ""


@dataclass(frozen=True)
class StagedPayload:
    """The staged roster payload and whether it is ARMED."""

    armed: bool
    reason: str
    edits: tuple[StagedEdit, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.edits


def derive_roster(repo_root: Path | None = None) -> dict[str, RosterEntry]:
    """Re-derive the roster from the repository (READ-ONLY).

    This is the derivation the manifest-diff pin runs against the
    manifest.  It reads ``agents/*/meta.json`` and applies the SAME
    fail-closed default the registry applies
    (``meta.get("skill_injection", False)``).

    Args:
        repo_root: The repository root.  Defaults to the repository
            this module lives in.

    Returns:
        ``{agent_name: RosterEntry}`` for every ``agents/*/meta.json``.
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    entries: dict[str, RosterEntry] = {}
    for meta_path in sorted(root.glob(CENSUS_GLOB)):
        agent = meta_path.parent.name
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[SkillRoster] Could not read %s: %s", meta_path, exc
            )
            continue
        declares = "skill_injection" in meta
        live = bool(meta.get("skill_injection", False))
        registry = "scaffolding" if agent in _REGISTRY_SKIP_DIRS else "agent"
        entries[agent] = RosterEntry(
            agent=agent,
            registry=registry,
            declares=declares,
            live=live,
            # Today the two axes are COUPLED — one bit, read by both
            # gates.  Phase 2 may decouple them; the manifest records
            # them separately so that decision is per-axis.
            capture_enabled=live,
            injection_enabled=live,
            source="agents/*/meta.json",
        )
    return entries


# ─────────────────────────────────────────────────────────────
# The manifest
# ─────────────────────────────────────────────────────────────


def _clean_cell(cell: str) -> str:
    return cell.strip().strip("`").strip()


def parse_roster_manifest(text: str) -> dict[str, RosterEntry]:
    """Parse the machine-readable census table out of the manifest.

    The table is fenced by ``<!-- F-ROSTER-TABLE-BEGIN -->`` /
    ``<!-- F-ROSTER-TABLE-END -->`` so the prose around it can never
    be mistaken for data.

    Args:
        text: The manifest's full text.

    Returns:
        ``{agent_name: RosterEntry}``.

    Raises:
        ValueError: The fenced table is missing or malformed.
    """
    begin = text.find(_TABLE_BEGIN)
    end = text.find(_TABLE_END)
    if begin == -1 or end == -1 or end < begin:
        raise ValueError(
            "roster manifest: the fenced census table "
            f"({_TABLE_BEGIN} … {_TABLE_END}) is missing"
        )
    block = text[begin + len(_TABLE_BEGIN):end]
    entries: dict[str, RosterEntry] = {}
    header_seen = False
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [_clean_cell(c) for c in line.strip("|").split("|")]
        if not header_seen:
            header_seen = True
            continue
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue  # the markdown separator row
        if len(cells) < 6:
            raise ValueError(
                f"roster manifest: malformed row ({len(cells)} cells): {raw_line!r}"
            )
        agent, registry, declares, live, capture, injection = cells[:6]
        entries[agent] = RosterEntry(
            agent=agent,
            registry=registry,
            declares=declares.lower() in {"yes", "true"},
            live=live.lower() in {"true", "yes"},
            capture_enabled=capture.lower() in {"true", "yes"},
            injection_enabled=injection.lower() in {"true", "yes"},
            source=cells[6] if len(cells) > 6 else "",
        )
    if not header_seen:
        raise ValueError("roster manifest: the census table has no header row")
    return entries


def diff_roster(
    derived: dict[str, RosterEntry],
    manifest: dict[str, RosterEntry],
) -> list[RosterDrift]:
    """Diff the re-derived census against the manifest.

    The diff covers the CENSUS FACTS only — the agent set, whether the
    agent declares the flag, and the effective live value.  The per-axis
    columns are the DECISION surface (the Emperor's Phase-2 call) and
    are deliberately NOT diffed against the live value; see
    :func:`policy_violations` for the invariant that does constrain
    them.

    Args:
        derived: The re-derived census.
        manifest: The parsed manifest.

    Returns:
        The drift list — EMPTY means the manifest is a faithful record
        of the repository.
    """
    drift: list[RosterDrift] = []
    for agent in sorted(set(derived) | set(manifest)):
        got = derived.get(agent)
        want = manifest.get(agent)
        if want is None:
            drift.append(
                RosterDrift(agent, "present-in-census", True, False)
            )
            continue
        if got is None:
            drift.append(
                RosterDrift(agent, "present-in-census", False, True)
            )
            continue
        if got.declares != want.declares:
            drift.append(
                RosterDrift(agent, "declares", want.declares, got.declares)
            )
        if got.live != want.live:
            drift.append(
                RosterDrift(agent, "live", want.live, got.live)
            )
        if got.registry != want.registry:
            drift.append(
                RosterDrift(agent, "registry", want.registry, got.registry)
            )
    return drift


def policy_violations(
    manifest: dict[str, RosterEntry],
) -> list[RosterDrift]:
    """Check the manifest against the roster policy.

    The one hard invariant: an agent that does NOT declare
    ``skill_injection`` cannot be capture-enabled or injection-enabled
    in the manifest — the fail-closed default (``registry.py:690`` /
    ``:756``) is the only value such an agent can take.

    Returns:
        The violation list — EMPTY means the manifest is policy-clean.
    """
    violations: list[RosterDrift] = []
    for agent in sorted(manifest):
        entry = manifest[agent]
        if entry.declares:
            continue
        if entry.capture_enabled:
            violations.append(
                RosterDrift(agent, "capture-enabled", False, True)
            )
        if entry.injection_enabled:
            violations.append(
                RosterDrift(agent, "injection-enabled", False, True)
            )
        if entry.live:
            violations.append(
                RosterDrift(agent, "live", False, True)
            )
    return violations


# ─────────────────────────────────────────────────────────────
# The staged payload (armed only when the capture lane is ON)
# ─────────────────────────────────────────────────────────────


def capture_lane_enabled() -> bool:
    """Whether the CAPTURE lane is ON.

    Delegates to the canonical resolver
    (``daemon.services.skill_job_dispatcher._resolve_capture_enabled`` —
    the ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` kill-switch, the same
    resolver ``skill_metrics_service`` uses).  Tests monkeypatch THIS
    symbol; the environment variable is never set or unset by the
    code.
    """
    return bool(_resolve_capture_enabled())


def stage_payload(
    manifest: dict[str, RosterEntry],
    derived: dict[str, RosterEntry],
    *,
    capture_lane_on: bool,
) -> StagedPayload:
    """Compute the staged roster payload — ARMED only when the lane is ON.

    **The gate.**  When ``capture_lane_on`` is ``False`` the payload is
    EMPTY and ``armed`` is ``False`` with reason ``capture_lane_off``.
    Nothing is computed, nothing could be applied: the roster is inert
    while the capture lane is dark.  When the lane is ON, the payload
    lists the ``meta.json`` edits the manifest's per-axis intent
    implies.

    **The payload is never WRITTEN here.**  ``agents/**`` is
    house-fenced in Phase 1 and the write is the leader's Phase-2
    route; this function only computes the diff.

    Args:
        manifest: The parsed manifest.
        derived: The re-derived census (the live values).
        capture_lane_on: The capture-lane state.

    Returns:
        The :class:`StagedPayload`.
    """
    if not capture_lane_on:
        return StagedPayload(
            armed=False,
            reason="capture_lane_off",
            edits=(),
        )

    edits: list[StagedEdit] = []
    for agent in sorted(manifest):
        entry = manifest[agent]
        live_entry = derived.get(agent)
        if live_entry is None:
            continue
        if entry.injection_enabled != live_entry.live:
            edits.append(
                StagedEdit(
                    agent=agent,
                    path=f"agents/{agent}/meta.json",
                    key="skill_injection",
                    axis="injection",
                    old=live_entry.live,
                    new=entry.injection_enabled,
                    ready=True,
                    note="the injection axis reads the existing "
                         "`skill_injection` bit",
                )
            )
        if entry.capture_enabled != live_entry.live:
            edits.append(
                StagedEdit(
                    agent=agent,
                    path=f"agents/{agent}/meta.json",
                    key="skill_capture_enabled",
                    axis="capture",
                    old=live_entry.live,
                    new=entry.capture_enabled,
                    ready=False,
                    note="the capture axis has NO key of its own in "
                         "Phase 1 (`skill_injection` is one bit read by "
                         "both gates) — a Phase-2 decision must "
                         "introduce one or pin the axis to the bit",
                )
            )
    return StagedPayload(
        armed=True,
        reason="capture_lane_on",
        edits=tuple(edits),
    )
