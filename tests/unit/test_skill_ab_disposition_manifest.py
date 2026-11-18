"""W7 (2026-11-17) — the declared A/B disposition manifest.

The sweep posture is NO knob: diverts come from an EXPLICIT,
operator/leader-named disposition — never from sweep judgment. This
manifest carries the SIX divert pairs of the E-6 pending-wiring table
(basis ``defective_arm``; winners = the non-defective members).

The pins here are the SEED contract:

* exactly six dispositions, in the leader's order;
* each names the verified-defective member first and the
  non-defective winner second;
* the basis is ``defective_arm`` on every entry;
* the manifest is DATA — it mutates nothing, and no shipped code path
  auto-applies it (the sanctioned resolution stays an explicit,
  operator-driven act with its own reason + receipt);
* import-time inert (no I/O at import).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SOURCE = (
    REPO_ROOT / "daemon" / "services" / "skill_ab_disposition_manifest.py"
)

EXPECTED_PAIRS = (
    ("1b8e4d80", "8bb22eb8"),
    ("8f8cee3a", "08e67030"),
    ("b77128c5", "76e3e1a7"),
    ("604550ac", "c595f7cf"),
    ("4ba4086d", "0757f082"),
    ("f3bc3a3e", "607425ef"),
)


def test_manifest_carries_exactly_the_six_declared_pairs():
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
    )

    assert len(DECLARED_AB_DISPOSITIONS) == 6
    observed = tuple(
        (d.defective_skill_id_prefix, d.winner_skill_id_prefix)
        for d in DECLARED_AB_DISPOSITIONS
    )
    assert observed == EXPECTED_PAIRS


def test_every_disposition_is_defective_arm_with_a_reason():
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
        DISPOSITION_BASIS,
    )

    assert DISPOSITION_BASIS == "defective_arm"
    for disposition in DECLARED_AB_DISPOSITIONS:
        assert disposition.basis == "defective_arm"
        assert disposition.reason.strip()


def test_matcher_resolves_both_members():
    from daemon.services.skill_ab_disposition_manifest import (
        disposition_winner,
        match_ab_disposition,
    )

    defective, winner = EXPECTED_PAIRS[0]
    assert match_ab_disposition(defective) is not None
    assert match_ab_disposition(winner) is not None
    assert disposition_winner(defective) == winner
    assert disposition_winner(winner) == winner
    # An unlisted id matches nothing (the operator then decides).
    assert match_ab_disposition("deadbeef") is None
    assert disposition_winner("deadbeef") is None


def test_manifest_is_import_time_inert():
    """Importing the manifest must not touch the filesystem — the
    house note on import-time side effects (the metrics registry
    precedent)."""
    source = MANIFEST_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("open(", "Path(", "os.environ", "sqlite"):
        assert forbidden not in source, (
            f"the manifest must stay import-time inert ({forbidden})"
        )


def test_no_shipped_path_auto_applies_the_dispositions():
    """The manifest is DATA: no shipped module may call the sanctioned
    resolver BECAUSE of a disposition row (the divert stays an
    explicit operator act)."""
    from daemon.services import skill_ab_disposition_manifest as manifest

    source = MANIFEST_SOURCE.read_text(encoding="utf-8")
    assert "resolve_ab_test_sanctioned" not in source
    assert "resolve_pair" not in source
    # The matcher is not wired into the sweep either.
    sweep_source = (
        REPO_ROOT / "daemon" / "manager.py"
    ).read_text(encoding="utf-8")
    assert "skill_ab_disposition_manifest" not in sweep_source
    assert manifest.MANIFEST_VERSION == 1
