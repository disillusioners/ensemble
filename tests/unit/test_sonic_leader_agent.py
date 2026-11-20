"""Census pins for the sonic-leader agent definition (roster v1.1.2).

Commission 2026-11-20 — add the EXISTING ``maintenancer`` agent to
sonic-leader's team roster.

House precedent (commit ``35d638662``): a roster change bumps the agent's
``meta.json`` ``version`` and the roster in ONE commit.  This module owns
the ``meta.json`` half; the prompt half (``rule.md`` / ``workflow.md`` /
``tools_note.md``) is a SIBLING module's job and is deliberately NOT
touched here (those files are sealed prompt artifacts).

What is pinned here:

1. The EXACT roster of ``agents/sonic-leader/meta.json`` — seven members,
   ``maintenancer`` appended AFTER ``devops`` (the house ordering rule:
   new members are appended, never reordered).
2. Registry discovery — the REAL ``agents/`` tree (via ``get_registry()``,
   which loads ``daemon/registry.py``'s repo-root ``agents/`` directory)
   exposes that exact roster, and ``maintenancer`` is a REAL, discoverable
   agent (a roster entry pointing at a non-existent agent would be a dead
   roster row).
3. BEHAVIOR — the production spawn-authorization gate
   ``daemon.tools._auth._check_team_membership`` allows
   ``("sonic-leader", "maintenancer")`` and DENIES a non-member
   (``("sonic-leader", "leader")``).  The negative control matters: a gate
   that allows EVERYTHING would pass a positive-only pin.

The meta.json roster pins and the behavioral pins are independent: the
behavioral leg reads the registry (which reads meta.json), so removing
``maintenancer`` from meta.json turns BOTH RED — the counter-mutation
proof for this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SONIC_LEADER_DIR = REPO_ROOT / "agents" / "sonic-leader"
SONIC_LEADER_META = SONIC_LEADER_DIR / "meta.json"
MAINTENANCER_DIR = REPO_ROOT / "agents" / "maintenancer"
MAINTENANCER_META = MAINTENANCER_DIR / "meta.json"

# The EXACT roster sonic-leader must carry (meta.json "version": "1.1.2").
# Order is part of the contract: house precedent appends new members AFTER
# the existing ones (never reorders the roster).
EXPECTED_TEAM_MEMBERS = [
    "sonic-developer",
    "sonic-coder",
    "sonic-reviewer",
    "sonic-tester",
    "giter",
    "devops",
    "maintenancer",
]

# The agent this module ADDED to the roster (commission 2026-11-20).
ADDED_MEMBER = "maintenancer"

# A real agent that is NOT on sonic-leader's roster — the negative control
# for the authorization gate.  "leader" is a distinct agent (agents/leader),
# NOT "sonic-leader", so it must stay outside the roster.
NON_MEMBER_CONTROL = "leader"


def _load_meta(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# =============================================================================
# 1. meta.json — the roster itself
# =============================================================================


class TestSonicLeaderMetaJsonRoster:
    """The meta.json roster pins (the census of record)."""

    def test_meta_json_exists_and_parses(self) -> None:
        """agents/sonic-leader/meta.json exists and is a JSON object."""
        assert SONIC_LEADER_META.exists(), (
            f"meta.json not found at {SONIC_LEADER_META}"
        )
        meta = _load_meta(SONIC_LEADER_META)
        assert isinstance(meta, dict)

    def test_agent_id_is_sonic_leader(self) -> None:
        """The meta's id must match the directory name (registry identity)."""
        assert _load_meta(SONIC_LEADER_META).get("id") == "sonic-leader"

    def test_version_is_1_1_2(self) -> None:
        """The roster change bumps the meta version 1.1.1 -> 1.1.2.

        House precedent: commit 35d638662 bumped version + roster in ONE
        commit, so the version bump is part of the roster contract.
        """
        assert _load_meta(SONIC_LEADER_META).get("version") == "1.1.2"

    def test_team_members_exact_set(self) -> None:
        """The roster is EXACTLY the seven expected members, in order."""
        team = _load_meta(SONIC_LEADER_META).get("team_members")
        assert isinstance(team, list), f"team_members must be a list, got {type(team)}"
        assert team == EXPECTED_TEAM_MEMBERS, (
            f"sonic-leader.team_members mismatch: got {team}, "
            f"expected {EXPECTED_TEAM_MEMBERS}"
        )

    def test_maintenancer_appended_after_devops(self) -> None:
        """maintenancer is present and sits AFTER devops (append, not insert)."""
        team = _load_meta(SONIC_LEADER_META).get("team_members") or []
        assert ADDED_MEMBER in team, (
            f"{ADDED_MEMBER!r} must be on sonic-leader's roster; got {team}"
        )
        assert team.index(ADDED_MEMBER) == len(team) - 1, (
            f"{ADDED_MEMBER!r} must be APPENDED (last); got {team}"
        )
        assert team[-2] == "devops", (
            f"the member before {ADDED_MEMBER!r} must still be 'devops'; got {team}"
        )

    def test_roster_has_no_duplicates(self) -> None:
        """A duplicated roster row is a malformed roster (set != list)."""
        team = _load_meta(SONIC_LEADER_META).get("team_members") or []
        assert len(team) == len(set(team)), f"duplicate roster rows: {team}"

    def test_maintenancer_own_roster_unchanged(self) -> None:
        """agents/maintenancer/** is OUT OF SCOPE for this commission.

        The maintenancer keeps its own team (explorer/worker/coder) — this
        commission only adds the agent to sonic-leader's roster.
        """
        team = _load_meta(MAINTENANCER_META).get("team_members")
        assert team == ["explorer", "worker", "coder"], (
            f"maintenancer's own roster must stay unchanged; got {team}"
        )


# =============================================================================
# 2. Registry discovery — the REAL agents/ tree
# =============================================================================


class TestSonicLeaderRegistryDiscovery:
    """Discovery legs against the REAL agents/ tree (not a fixture)."""

    def _registry(self):
        from daemon.registry import get_registry

        return get_registry()

    def test_sonic_leader_discovered(self) -> None:
        """sonic-leader is discovered by the real registry."""
        assert self._registry().exists("sonic-leader"), (
            "sonic-leader should be discovered in the real agents/ tree"
        )

    def test_maintenancer_is_a_real_discoverable_agent(self) -> None:
        """A roster row must point at a REAL agent (no dead roster rows)."""
        registry = self._registry()
        assert registry.exists(ADDED_MEMBER), (
            f"{ADDED_MEMBER!r} must exist in the real agents/ tree — "
            "a roster entry pointing at a missing agent is a dead row"
        )
        meta = registry.get(ADDED_MEMBER)
        assert meta is not None
        assert meta.id == ADDED_MEMBER

    def test_registry_roster_matches_meta_json_exactly(self) -> None:
        """The registry-parsed roster equals the meta.json roster exactly."""
        meta = self._registry().get("sonic-leader")
        assert meta is not None, "sonic-leader must be retrievable from the registry"
        assert list(meta.team_members or []) == EXPECTED_TEAM_MEMBERS, (
            f"registry roster mismatch: got {meta.team_members}, "
            f"expected {EXPECTED_TEAM_MEMBERS}"
        )

    def test_registry_roster_contains_maintenancer(self) -> None:
        """The registry roster carries maintenancer (set-level, order-free)."""
        meta = self._registry().get("sonic-leader")
        assert meta is not None
        assert ADDED_MEMBER in set(meta.team_members or []), (
            f"{ADDED_MEMBER!r} must be on the registry roster; "
            f"got {meta.team_members}"
        )


# =============================================================================
# 3. Behavioral — the production spawn-authorization gate
# =============================================================================


class TestSonicLeaderTeamMembershipBehavior:
    """Behavioral legs through ``daemon.tools._auth._check_team_membership``."""

    def test_maintenancer_spawn_is_authorized(self) -> None:
        """("sonic-leader", "maintenancer") is ALLOWED (returns None)."""
        from daemon.tools._auth import _check_team_membership

        err = _check_team_membership("sonic-leader", ADDED_MEMBER)
        assert err is None, (
            f"sonic-leader must be allowed to spawn {ADDED_MEMBER!r}; got: {err}"
        )

    @pytest.mark.parametrize("member", EXPECTED_TEAM_MEMBERS)
    def test_every_roster_member_is_authorized(self, member: str) -> None:
        """Every member of the pinned roster is an authorized spawn target."""
        from daemon.tools._auth import _check_team_membership

        err = _check_team_membership("sonic-leader", member)
        assert err is None, (
            f"sonic-leader must be allowed to spawn {member!r}; got: {err}"
        )

    def test_non_member_target_is_denied(self) -> None:
        """NEGATIVE CONTROL — a non-member is denied with an error string."""
        from daemon.tools._auth import _check_team_membership

        err = _check_team_membership("sonic-leader", NON_MEMBER_CONTROL)
        assert err is not None, (
            f"sonic-leader must NOT be allowed to spawn "
            f"{NON_MEMBER_CONTROL!r} (not on the roster)"
        )
        assert isinstance(err, str) and err.strip(), (
            f"denial must carry a human-readable error string; got {err!r}"
        )

    def test_maintenancer_is_not_a_leader_escape_hatch_removal(self) -> None:
        """The roster grew — the pre-existing members all stay spawnable.

        Guards against a 'roster rewrite' that silently drops an earlier
        member while adding maintenancer.
        """
        from daemon.tools._auth import _check_team_membership

        for member in ("sonic-developer", "sonic-coder", "sonic-reviewer",
                        "sonic-tester", "giter", "devops"):
            err = _check_team_membership("sonic-leader", member)
            assert err is None, (
                f"pre-existing roster member {member!r} must stay spawnable; got: {err}"
            )
