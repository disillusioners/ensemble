"""Report-integrity prompt guidance tests (wc-wake-report-integrity Phase 2, Wave 1).

Covers candidates (d) parent report-scrutiny guidance and (e) child work-turn
opening discipline, per decisions C2-D2.10 / C2-D2.11 / C2-D2.14:

(a) **Text-presence** — each (d) parent agent's operative files contain the
    marker-conditioned scrutiny guidance (``[REPORT SANITY:`` marker phrase +
    "interim, not completion" + a verify action naming ``send_message``); each
    (e) work-turn agent carries the opening-discipline text
    ("before ending any turn" + "zero tool calls").

(b) **Registry-completeness** — dynamically enumerate ``agents/*/meta.json``,
    resolve v2 shadowing (``agents/<id>[v2]/`` wins over ``agents/<id>/`` when
    present), and assert:
      * every agent with a non-empty ``team_members`` carries (d) guidance
        (rot mitigation: adding a new parent agent without the guidance fails),
      * every D2.11 work-turn agent carries (e),
      * exempt agents (explorer — text-only by design) carry neither.

These are text-presence tests only; the guidance's behavioral effect is
empirical and intentionally not unit-tested (C2-D2.14).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"

# ---------------------------------------------------------------------------
# Recipient sets (C2-D2.10 / C2-D2.11, LOCKED). Keys are BASE agent ids; the
# operative directory is resolved through `_operative_dir` (v2 shadowing).
# ---------------------------------------------------------------------------

D_PARENT_AGENTS: frozenset[str] = frozenset(
    {
        "leader",
        "project-manager",
        "developer",
        "architect",
        "approver",
        "planner",
        "reviewer",
        "tidier",
        "coder",
        "tester",
        "wanderer",
        "governor",
    }
)

E_WORK_TURN_AGENTS: frozenset[str] = frozenset(
    {
        "worker",
        "tester",
        "coder",
        "developer",
        "tidier",
        "planner",
        "reviewer",
        "architect",
        "approver",
        "wanderer",
        "governor",
    }
)

# Text-only / template / specialist agents that carry neither guidance
# (C2-D2.11 exemptions). Underscore-prefixed dirs (_mother, _baby_template, …)
# are skipped outright by the registry walk.
EXEMPT_AGENTS: frozenset[str] = frozenset({"explorer"})

# Canonical-home file expectations per agent (relative to the operative dir).
# Used by the strict text-presence tests; the registry test scans the whole
# prompt-file surface so it stays robust to home relocation.
D_HOME_FILES: dict[str, tuple[str, ...]] = {
    "leader": ("workflow.md",),
    "project-manager": ("rule.md", "workflow.md"),
    "developer": ("rule.md",),
    "architect": ("rule.md", "workflow.md"),
    "approver": ("rule.md",),
    "planner": ("rule.md",),
    "reviewer": ("rule.md",),
    "tidier": ("rule.md",),
    "coder": ("soul.md",),
    "tester": ("rule.md",),
    "wanderer": ("rule.md",),
    "governor": ("rule.md",),
}

E_HOME_FILES: dict[str, tuple[str, ...]] = {
    "worker": ("rule.md",),
    "tester": ("rule.md",),
    "coder": ("soul.md",),
    "developer": ("rule.md",),
    "tidier": ("rule.md",),
    "planner": ("rule.md",),
    "reviewer": ("rule.md",),
    "architect": ("rule.md",),
    "approver": ("rule.md",),
    "wanderer": ("rule.md",),
    "governor": ("rule.md",),
}

# Required substrings.
D_REQUIRED_SNIPPETS: tuple[str, ...] = (
    "[REPORT SANITY:",  # conditions on the visible marker pattern (C2-D2.9)
    "interim, not completion",  # the directive half (moved here from the marker)
    "send_message",  # the verify action
)
E_REQUIRED_SNIPPETS: tuple[str, ...] = (
    "before ending any turn",  # single decision point (C2-D2.11 #4)
    "zero tool calls",  # the bound opening pattern
)

# Files scanned by the registry test (the agent-facing prompt surface).
PROMPT_SURFACE: tuple[str, ...] = (
    "rule.md",
    "workflow.md",
    "soul.md",
)


def _operative_dir(agent_id: str) -> Path:
    """Resolve the operative agent directory, honoring v2 shadowing."""
    v2 = AGENTS_DIR / f"{agent_id}[v2]"
    if v2.is_dir():
        return v2
    return AGENTS_DIR / agent_id


def _iter_agent_ids() -> list[str]:
    """Enumerate concrete agent ids from agents/*/meta.json (skip templates).

    Versioned directories (``agents/foo[v2]/``) are reported under their BASE
    id (``foo``) so each agent appears exactly once and resolves through
    :func:`_operative_dir`.
    """
    ids: set[str] = set()
    if not AGENTS_DIR.is_dir():  # pragma: no cover - repo layout guarantee
        return sorted(ids)
    for entry in sorted(AGENTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if not (entry / "meta.json").is_file():
            continue
        base_id = entry.name[: -len("[v2]")] if entry.name.endswith("[v2]") else entry.name
        ids.add(base_id)
    return sorted(ids)


# Pre-existing parent agents that pre-date Wave 1 and are outside the locked
# C2-D2.10 recipient set. The guidance is to be backfilled to these in a
# separate batch; NEW parent agents added after Wave 1 are NOT grandfathered
# and will fail the registry test until they carry the guidance.
GRANDFATHERED_PARENTS: frozenset[str] = frozenset(
    {"blueprinter", "devops", "giter", "jober"}
)


def _read(*parts: str) -> str:
    path = AGENTS_DIR.joinpath(*parts)
    assert path.is_file(), f"expected prompt file missing: {path}"
    return path.read_text(encoding="utf-8")


def _has_all(text: str, snippets: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return all(snippet.lower() in lowered for snippet in snippets)


# ---------------------------------------------------------------------------
# (a) Text-presence — strict per-agent canonical homes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", sorted(D_PARENT_AGENTS))
def test_parent_carries_scrutiny_guidance_in_canonical_home(agent_id: str) -> None:
    operative = _operative_dir(agent_id)
    texts = [
        _read(operative.name, filename)
        for filename in D_HOME_FILES[agent_id]
        if (operative / filename).is_file()
    ]
    assert texts, f"{operative.name}: no canonical home files found for (d)"
    joined = "\n".join(texts)
    missing = [s for s in D_REQUIRED_SNIPPETS if s.lower() not in joined.lower()]
    assert not missing, (
        f"agent '{agent_id}' (operative dir {operative.name}) is missing the "
        f"(d) report-scrutiny guidance in {D_HOME_FILES[agent_id]}; "
        f"missing snippets: {missing}"
    )


@pytest.mark.parametrize("agent_id", sorted(E_WORK_TURN_AGENTS))
def test_work_turn_agent_carries_opening_discipline_in_canonical_home(
    agent_id: str,
) -> None:
    operative = _operative_dir(agent_id)
    texts = [
        _read(operative.name, filename)
        for filename in E_HOME_FILES[agent_id]
        if (operative / filename).is_file()
    ]
    assert texts, f"{operative.name}: no canonical home files found for (e)"
    joined = "\n".join(texts)
    missing = [s for s in E_REQUIRED_SNIPPETS if s.lower() not in joined.lower()]
    assert not missing, (
        f"agent '{agent_id}' (operative dir {operative.name}) is missing the "
        f"(e) opening-discipline guidance in {E_HOME_FILES[agent_id]}; "
        f"missing snippets: {missing}"
    )


# ---------------------------------------------------------------------------
# (b) Registry-completeness — dynamic walk over agents/*/meta.json
# ---------------------------------------------------------------------------


def test_registry_enumerates_known_recipients() -> None:
    """Sanity: the dynamic registry actually finds the locked recipient sets."""
    registry = set(_iter_agent_ids())
    missing = (D_PARENT_AGENTS | E_WORK_TURN_AGENTS | EXEMPT_AGENTS) - registry
    assert not missing, f"agents referenced by the locked sets are absent: {missing}"


@pytest.mark.parametrize("agent_id", sorted(set(_iter_agent_ids())))
def test_every_parent_agent_carries_scrutiny_guidance(agent_id: str) -> None:
    """ROT MITIGATION: any agent with a non-empty team must carry (d).

    A new agent added to agents/ whose meta.json declares team_members will
    fail this test until its operative prompt files carry the (d) guidance.
    """
    operative = _operative_dir(agent_id)
    meta = json.loads((operative / "meta.json").read_text(encoding="utf-8"))
    team = meta.get("team_members") or []
    if not team:
        pytest.skip(f"'{agent_id}' has no team_members — not a parent agent")
    if agent_id in GRANDFATHERED_PARENTS:
        pytest.skip(
            f"'{agent_id}' predates Wave 1 (grandfathered parent, guidance "
            f"backfill tracked separately)"
        )

    surface = [
        (operative / f).read_text(encoding="utf-8")
        for f in PROMPT_SURFACE
        if (operative / f).is_file()
    ]
    # The dispatch snippets under skills-template/ also carry the (d) mirror.
    templates = operative / "skills-template"
    if templates.is_dir():
        surface.extend(
            p.read_text(encoding="utf-8") for p in sorted(templates.glob("*.md"))
        )
    joined = "\n".join(surface)
    missing = [s for s in D_REQUIRED_SNIPPETS if s.lower() not in joined.lower()]
    assert not missing, (
        f"parent agent '{agent_id}' (team_members={team}) is missing the (d) "
        f"report-scrutiny guidance; scanned {operative.name}/ "
        f"{PROMPT_SURFACE} + skills-template/*.md; missing snippets: {missing}"
    )


@pytest.mark.parametrize("agent_id", sorted(E_WORK_TURN_AGENTS))
def test_every_work_turn_agent_carries_opening_discipline(agent_id: str) -> None:
    operative = _operative_dir(agent_id)
    surface = [
        (operative / f).read_text(encoding="utf-8")
        for f in PROMPT_SURFACE
        if (operative / f).is_file()
    ]
    joined = "\n".join(surface)
    missing = [s for s in E_REQUIRED_SNIPPETS if s.lower() not in joined.lower()]
    assert not missing, (
        f"work-turn agent '{agent_id}' (operative dir {operative.name}) is "
        f"missing the (e) opening-discipline guidance; missing snippets: "
        f"{missing}"
    )


@pytest.mark.parametrize("agent_id", sorted(EXEMPT_AGENTS))
def test_exempt_agents_carry_neither_guidance(agent_id: str) -> None:
    """explorer is text-only by design — it carries neither (d) nor (e)."""
    operative = _operative_dir(agent_id)
    surface = [
        (operative / f).read_text(encoding="utf-8")
        for f in PROMPT_SURFACE
        if (operative / f).is_file()
    ]
    joined = "\n".join(surface).lower()
    for snippet in D_REQUIRED_SNIPPETS + E_REQUIRED_SNIPPETS:
        assert snippet.lower() not in joined, (
            f"exempt agent '{agent_id}' unexpectedly carries guidance snippet "
            f"{snippet!r}"
        )


def test_v2_shadowing_resolution_prefers_v2_dir() -> None:
    """Versioned agents must resolve to the [v2] directory, not the v1 base."""
    for versioned in ("developer", "approver", "planner", "reviewer", "tidier"):
        assert (AGENTS_DIR / f"{versioned}[v2]").is_dir(), (
            f"expected agents/{versioned}[v2]/ to exist (operative shadow dir)"
        )
        assert _operative_dir(versioned) == AGENTS_DIR / f"{versioned}[v2]"
