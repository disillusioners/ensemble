"""The skill-injection roster manifest (workstream F, M-F4 + M-F5).

**What is pinned here.**

* **M-F4 — the manifest-diff pin.**  The roster is RE-DERIVED from
  ``agents/*/meta.json`` and diffed against the published manifest.
  Editing the manifest without re-deriving it turns this pin RED.  The
  point of the manifest is that the next census is a DIFF against a
  KNOWN STATE, not a re-derivation from scratch.
* **M-F5 — the conditional-payload pin.**  The staged roster payload is
  ARMED **only when the capture lane is ON**.  Removing the gate turns
  this pin RED — even when the manifest asks for a change.
* **The policy pin.**  An agent that does not declare
  ``skill_injection`` can never be capture-enabled or injection-enabled
  in the manifest (the fail-closed default, ``registry.py:690`` /
  ``:756``).
* **The census pins.**  The counts (46 census entries / 45 registered
  agents; 25 declaring = 15 true + 10 false; 21 declaring-nothing) and
  the recorded roster DRIFT.

**Nothing here writes.**  The payload is COMPUTED; ``agents/**`` is
house-fenced in Phase 1 and the write is the leader's Phase-2 route.
The capture-lane state is SIMULATED by monkeypatching the resolver — the
environment variable is never set or unset.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from daemon.services import skill_roster
from daemon.services.skill_roster import (
    RosterEntry,
    capture_lane_enabled,
    derive_roster,
    diff_roster,
    parse_roster_manifest,
    policy_violations,
    stage_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / ".agents/shared/planning/skill-system-revival/roster.md"


@pytest.fixture(scope="module")
def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest(manifest_text: str) -> dict[str, RosterEntry]:
    return parse_roster_manifest(manifest_text)


@pytest.fixture(scope="module")
def derived() -> dict[str, RosterEntry]:
    return derive_roster(REPO_ROOT)


# ─────────────────────────────────────────────────────────────
# M-F4 — the manifest-diff pin
# ─────────────────────────────────────────────────────────────


def test_manifest_matches_the_rederived_census(manifest, derived):
    """M-F4: the manifest is a faithful record of the repository."""
    drift = diff_roster(derived, manifest)
    assert drift == [], "roster manifest drifted from the repository:\n" + "\n".join(
        str(d) for d in drift
    )


def test_manifest_has_no_policy_violations(manifest):
    """A non-declaring agent can never be enabled on either axis."""
    violations = policy_violations(manifest)
    assert violations == [], "\n".join(str(v) for v in violations)


def test_manifest_lists_every_census_entry(manifest, derived):
    assert set(manifest) == set(derived)


# ─────────────────────────────────────────────────────────────
# The census — the known state
# ─────────────────────────────────────────────────────────────


def test_census_counts_are_the_published_ones(manifest):
    """The Phase-1 known state (the plan's recomputed roster)."""
    rows = list(manifest.values())
    declaring = [r for r in rows if r.declares]
    assert len(rows) == 50, "the census population is `agents/*/meta.json`"
    assert len(declaring) == 29
    assert sum(1 for r in declaring if r.live) == 15
    assert sum(1 for r in declaring if not r.live) == 14
    assert sum(1 for r in rows if not r.declares) == 21
    # 35 of the 50 census entries cannot capture.
    assert sum(1 for r in rows if not r.live) == 35


def test_the_registry_population_is_one_smaller(manifest):
    """The registry skips the scaffolding dir ⇒ 45 registered agents."""
    scaffolding = [a for a, r in manifest.items() if r.registry == "scaffolding"]
    assert scaffolding == ["_baby_template"]
    registered = [a for a, r in manifest.items() if r.registry == "agent"]
    assert len(registered) == 49
    assert sum(1 for a in registered if not manifest[a].live) == 34


def test_the_declaring_roster_is_the_published_one(manifest):
    true_agents = sorted(a for a, r in manifest.items() if r.declares and r.live)
    false_agents = sorted(a for a, r in manifest.items() if r.declares and not r.live)
    assert true_agents == [
        "aegis",
        "approver",
        "approver[v2]",
        "architect",
        "critic",
        "doc-maintainer",
        "maintenancer",
        "planner",
        "planner[v2]",
        "reviewer",
        "reviewer[v2]",
        "sketcher",
        "tidier",
        "tidier[v2]",
        "worker",
    ]
    assert false_agents == [
        "blueprinter",
        "coder",
        "designer",
        "developer",
        "developer[v2]",
        "devops",
        "project-manager",
        "tester",
        "video-capturer",
        "video-editor",
        "video-producer",
        "video-verifier",
        "wanderer",
        "watcher",
    ]


def test_the_axes_are_coupled_in_phase_1(manifest):
    """Today ONE bit drives both gates — the manifest records that."""
    for agent, entry in manifest.items():
        assert entry.capture_enabled == entry.live, agent
        assert entry.injection_enabled == entry.live, agent


def test_the_manifest_records_the_roster_drift(manifest_text):
    """The drift that made the earlier census stale must stay recorded."""
    lowered = manifest_text.lower()
    for token in ("blueprinter", "tester", "aegis", "013895287"):
        assert token in lowered, f"the drift record lost {token!r}"
    assert "flipped true" in lowered


# ─────────────────────────────────────────────────────────────
# M-F5 — the conditional payload
# ─────────────────────────────────────────────────────────────


def _fixture_manifest() -> dict[str, RosterEntry]:
    """A manifest whose INTENT differs from the live values."""
    return {
        "coder": RosterEntry(
            agent="coder",
            declares=True,
            live=False,
            capture_enabled=True,
            injection_enabled=False,
            source="fixture",
        ),
    }


def test_capture_lane_uses_the_canonical_resolver(monkeypatch):
    """The gate reads the canonical kill-switch resolver."""
    monkeypatch.setattr(skill_roster, "_resolve_capture_enabled", lambda: True)
    assert capture_lane_enabled() is True
    monkeypatch.setattr(skill_roster, "_resolve_capture_enabled", lambda: False)
    assert capture_lane_enabled() is False


def test_payload_is_unarmed_and_empty_when_the_capture_lane_is_off(
    monkeypatch, manifest, derived
):
    """M-F5: the gate holds even when the manifest asks for a change."""
    monkeypatch.setattr(skill_roster, "_resolve_capture_enabled", lambda: False)
    assert capture_lane_enabled() is False
    payload = stage_payload(
        _fixture_manifest(), derived, capture_lane_on=capture_lane_enabled()
    )
    assert payload.armed is False
    assert payload.reason == "capture_lane_off"
    assert payload.edits == ()


def test_payload_is_armed_when_the_capture_lane_is_on(manifest, derived):
    """With the lane ON the payload carries the manifest's intent."""
    payload = stage_payload(
        _fixture_manifest(), derived, capture_lane_on=True
    )
    assert payload.armed is True
    assert payload.reason == "capture_lane_on"
    assert len(payload.edits) == 1
    edit = payload.edits[0]
    assert edit.agent == "coder"
    assert edit.path == "agents/coder/meta.json"
    assert edit.axis == "capture"
    assert edit.old is False
    assert edit.new is True
    # The capture axis has NO key of its own in Phase 1 — the payload
    # must say so rather than pretend the edit is directly applicable.
    assert edit.ready is False
    assert "no key of its own" in edit.note.lower()


def test_the_real_manifest_yields_an_empty_payload(manifest, derived):
    """Phase 1 flips NOTHING — the payload is empty even with the lane ON."""
    payload = stage_payload(manifest, derived, capture_lane_on=True)
    assert payload.armed is True
    assert payload.edits == ()


def test_the_payload_never_writes_a_meta_json(manifest, derived):
    """The mechanism computes; it never writes (the house fence)."""
    agents_dir = REPO_ROOT / "agents"
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(agents_dir.glob("*/meta.json"))
    }
    stage_payload(_fixture_manifest(), derived, capture_lane_on=True)
    stage_payload(manifest, derived, capture_lane_on=False)
    after = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(agents_dir.glob("*/meta.json"))
    }
    assert before == after
