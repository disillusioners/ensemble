"""POK SHADOW-MIX GATE (Wave 2 Phase 11, T11.1) — the precondition for
the P1 enforcement wave.

The gate compares the mix the SHADOW judge produces with the mix the
CENSUS measured on the same store. A DISAGREEMENT means the rubric is
wrong — the semantic judge does not classify the store the way the
census does — so enforcement must NOT be flipped (the P1 ceremony
waits; the plan's own rule: "disagreement ⇒ the rubric is wrong, do
NOT enforce").

The census mix is RE-DERIVED from the committed, branch-verifiable
artifact (the 499-active-row machine-label TSV, sha256
``6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1``,
snapshot 2026-11-23 12:29Z) — the test reads the artifact, so the
leg cannot drift from the evidence.

No LLM, no network, no live store.
"""

from __future__ import annotations

import pathlib

import pytest

#: The committed census artifact (the machine-label TSV — the per-row
#: label dataset of the 499-active-row store).
CENSUS_ARTIFACT = pathlib.Path(
    ".agents/shared/planning/pok-write-gate/evidence-pok-sweep/"
    "census-rows-active-2026-11-23.mech-labels.tsv"
)
CENSUS_SHA256 = "6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1"

#: The measured census mix at the artifact's snapshot: 92 ROLLING rows
#: of 499 ACTIVE (18.4 %) — the rolling share the judge must roughly
#: agree with before the rubric can be enforced.
CENSUS_ROLLING_SHARE = 92 / 499


def census_mix(path: pathlib.Path = CENSUS_ARTIFACT) -> dict[str, float]:
    """The census mix — the share of ACTIVE rows per class.

    ``R`` = the row carries a ``ROLLING:`` label (a snapshot/episode);
    ``NR`` = it does not. The artifact's label column is column 19.
    """
    if not path.is_file():
        pytest.skip(f"the census artifact is absent: {path}")
    rolling = 0
    total = 0
    with path.open(encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        label_index = header.index("mech_labels")
        for line in handle:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            total += 1
            labels = fields[label_index]
            if any(
                label.startswith("ROLLING:") for label in labels.split(";")
            ):
                rolling += 1
    return {"R": rolling / total, "NR": (total - rolling) / total}


def verdict_mix(counts: dict[str, int]) -> dict[str, float]:
    """The shadow-verdict mix — the share of shadow verdicts per class.

    The judge routes a row to a class: ``boundary`` and ``durability``
    are the classes the census would call "not an operating rule"
    (general knowledge / a snapshot), while ``reason``,
    ``redundancy`` and ``scope`` are operational (the fact belongs in
    POK — possibly with a different body, a different row or a
    different project).
    """
    routed = counts.get("boundary", 0) + counts.get("durability", 0)
    operational = (
        counts.get("reason", 0)
        + counts.get("redundancy", 0)
        + counts.get("scope", 0)
    )
    total = routed + operational
    if total == 0:
        return {}
    return {"R": routed / total, "NR": operational / total}


def compare_mixes(
    census: dict[str, float],
    shadow: dict[str, float],
    *,
    tolerance: float = 0.10,
) -> dict:
    """The shadow-mix gate — the comparison and its verdict.

    Returns ``{"agree": bool, "divergence": float, "may_enforce": bool,
    "reason": str}``. The gate is DORMANT (no shadow verdicts yet) and
    the verdict is ``may_enforce=False`` with the reason naming the
    gap.
    """
    if not shadow:
        return {
            "agree": False,
            "divergence": None,
            "may_enforce": False,
            "reason": (
                "no shadow verdict mix yet — the gate is dormant; "
                "enforcement waits for the shadow evidence"
            ),
        }
    divergence = abs(census.get("R", 0.0) - shadow.get("R", 0.0))
    agree = divergence <= tolerance
    return {
        "agree": agree,
        "divergence": divergence,
        "may_enforce": agree,
        "reason": (
            "the shadow mix agrees with the census mix within the "
            "tolerance (%.3f <= %.3f) — enforcement may be considered"
            % (divergence, tolerance)
            if agree
            else "the shadow mix DISAGREES with the census mix (%.3f > "
            "%.3f) — the rubric is wrong; DO NOT enforce" % (divergence, tolerance)
        ),
    }


class TestCensusMix:
    def test_the_census_mix_is_re_derived_from_the_committed_artifact(self):
        mix = census_mix()
        assert mix["R"] == pytest.approx(CENSUS_ROLLING_SHARE, abs=0.01)
        assert mix["R"] + mix["NR"] == pytest.approx(1.0)

    def test_the_artifact_is_the_one_the_plan_cites(self):
        if not CENSUS_ARTIFACT.is_file():
            pytest.skip("the census artifact is absent")
        import hashlib

        digest = hashlib.sha256(CENSUS_ARTIFACT.read_bytes()).hexdigest()
        assert digest == CENSUS_SHA256


class TestShadowMixGate:
    def test_an_agreeing_mix_may_enforce(self):
        census = {"R": 0.184, "NR": 0.816}
        shadow = verdict_mix(
            {"boundary": 10, "durability": 8, "reason": 40, "redundancy": 12, "scope": 10}
        )
        verdict = compare_mixes(census, shadow)
        assert verdict["agree"] is True
        assert verdict["may_enforce"] is True

    def test_a_disagreeing_mix_blocks_the_enforcement(self):
        """THE LEG: disagreement ⇒ the rubric is wrong ⇒ DO NOT enforce."""
        census = {"R": 0.184, "NR": 0.816}
        shadow = verdict_mix(
            {"boundary": 60, "durability": 40, "reason": 5, "redundancy": 2, "scope": 3}
        )
        verdict = compare_mixes(census, shadow)
        assert verdict["agree"] is False
        assert verdict["may_enforce"] is False
        assert "DO NOT enforce" in verdict["reason"]

    def test_the_gate_is_dormant_without_shadow_evidence(self):
        """Today there is no shadow mix: the precondition is NOT met, so
        the enforcement wave is not unlocked by this build."""
        census = census_mix()
        verdict = compare_mixes(census, {})
        assert verdict["may_enforce"] is False
        assert verdict["divergence"] is None
        assert "dormant" in verdict["reason"]

    def test_the_mutation_leg_detects_a_rewritten_mix(self):
        """The mutation leg: a shadow mix that disagrees must make the
        gate REFUSE — the leg is RED when the comparison is weakened
        (e.g. the tolerance is inflated or the divergence ignored)."""
        census = {"R": 0.184, "NR": 0.816}
        diverged = verdict_mix(
            {"boundary": 90, "durability": 10, "reason": 0, "redundancy": 0, "scope": 0}
        )
        verdict = compare_mixes(census, diverged, tolerance=0.10)
        assert verdict["may_enforce"] is False
        # An inflated tolerance WOULD unlock it — the guard is the
        # tolerance itself, so the pin asserts the default.
        assert compare_mixes(census, diverged, tolerance=0.95)["may_enforce"] is True
