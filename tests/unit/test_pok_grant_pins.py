"""The POK grant-completeness tripwire (POK Phase 1, T5 — B4 sweep).

Mirrors ``tests/unit/test_anomaly_report_grant_pins.py`` for the
``pok`` category (the SAME walk shape, the POK denominators). Pins:

(a) **the grant completeness walk** — every ``agents/*/meta.json``
    with a NON-EMPTY ``tools.allow`` carries ``"pok"``
    (52 explicit / 55 meta, mechanically re-derived 2026-11-25;
    ``watcher`` excluded — empty allow inherits-all, never
    appended; ``kb-writer`` (Emperor ruling of 2026-11-23) and
    ``experiencer`` (Emperor pre-approval of 2026-11-25) excluded —
    the capture duty moved to the OPERATIONAL agents
    (ari / project-manager / leader / devops) and the grant was
    taken away from the knowledge writers, which keep RAG-side
    routing only);
(b) **the ordering discipline** — ``"pok"`` sits IMMEDIATELY BEFORE
    ``"nest-report"`` in every allow that carries both (the
    insert-before rule, never a reorder); ``canary`` (no
    nest-report) carries it APPENDED AT END; ``ari`` carries it
    mid-array with the memoir/prompt-reload tail intact;
(c) **once-per-agent** — the grant appears exactly once;
(d) **the vocab/artifact pair** — ``VOCABULARY_VERSION == 10`` and
    the committed artifact carries 65 categories with ``pok``
    (F-1 pair — the constant and the artifact move in the SAME
    change; the 9 → 10 bump is the guidelines lane's; the
    64 → 65 bump is the 2026-11-14 F-1 completion — the
    project_future category landed at v0.30.7 without regenerating
    the artifact, and the pre-existing pair defect was completed
    in v0.30.8).

Mutation legs (the pack's own mutation-verification duty):

* **M-POK-1** — remove ``"pok"`` from one meta.json → the walk REDs.
* **M-POK-2** — move the entry off its nest-report-adjacent slot →
  the ordering pin REDs.
* **M-POK-3** — revert the artifact count to 64 → the artifact pin
  REDs.
* **M-POK-4** — RE-ADD ``"pok"`` to ``agents/kb-writer/meta.json`` →
  the kb-writer ABSENCE pin REDs (the Emperor ruling of 2026-11-23:
  the capture duty lives with the operational agents, not the
  knowledge writer).
* **M-POK-5** — RE-ADD ``"pok"`` to ``agents/experiencer/meta.json``
  immediately before ``"nest-report"`` → the experiencer ABSENCE pin
  REDs and the census pin REDs (53 vs 52); the ORDERING pin stays
  GREEN (the grant is re-inserted at its insert-before slot).
"""

from __future__ import annotations

import json
from pathlib import Path

from daemon.checkpoint_metrics import VOCABULARY_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"

#: The category key — byte-identical to the registry entry
#: (``daemon/tools/_tool_registry.py`` CATEGORY_MODULES).
CATEGORY = "pok"
#: The grant this one is ordered against (the sweep's insert-before
#: anchor).
ANCHOR_CATEGORY = "nest-report"

#: Mechanically re-derived 2026-11-12 (the sonic-team lane): 49 explicit
#: grants (every non-empty allow) / 50 meta-bearing agent dirs
#: (46 at the 2026-11-07 POK B4 sweep + the 4 ``sonic-*`` agents since).
#: Re-derived 2026-11-13 (COMBINED v1.1+v1.2 rework): 50 / 51 — the
#: fifth sonic agent ``sonic-developer`` carries ``pok`` immediately
#: before ``nest-report`` (the insert-before discipline preserved).
#: MERGE 2026-11-13 (video-team -> latest): the video-team scaffold
#: (2026-11-11) adds FOUR agents (video-producer / video-capturer /
#: video-editor / video-verifier), each with a non-empty allow granted
#: mechanically by the SAME insert-before rule → 54 explicit grants /
#: 55 meta-bearing agent dirs.
#: RE-DERIVED 2026-11-25 (feeder-grants module A — the Emperor
#: pre-approval): ``experiencer`` LOSES the ``pok`` grant (the capture
#: duty lives with the operational agents; ``kb-writer`` lost its
#: grant 2026-11-24) → 52 explicit grants / 55 meta-bearing agent dirs.
EXPECTED_EXPLICIT = 52
EXPECTED_TOTAL_META = 55
#: The ONE agent that inherits (empty allow = the default universe
#: grants every NON-privileged category — an explicit entry would
#: STRIP every other tool watcher inherits).
INHERITANCE_EXCEPTIONS: frozenset[str] = frozenset({"watcher"})
#: The agents whose NON-EMPTY allow deliberately does NOT carry the
#: grant (the completeness-walk exception). The Emperor ruling of
#: 2026-11-23 / pre-approval of 2026-11-25 moved the POK capture
#: duty to the operational agents (ari / project-manager / leader /
#: devops); ``kb-writer`` and ``experiencer`` are KNOWLEDGE writers
#: and keep RAG-side routing only. The absences are pinned positively
#: (see :class:`TestTheKbWriterGrantAbsence` and
#: :class:`TestTheExperiencerGrantAbsence`) — the exceptions are not
#: holes in the walk.
GRANT_ABSENT_EXCEPTIONS: frozenset[str] = frozenset({"kb-writer", "experiencer"})
#: Non-empty allow WITHOUT a nest-report entry (arrived post-sweep):
#: its grant is APPENDED AT END (the sweep's append-end exception).
APPEND_END_EXCEPTIONS: frozenset[str] = frozenset({"canary"})
#: The allow whose grant is NOT nest-report-adjacent — ``ari``'s
#: memoir/prompt-reload tail. The sweep inserted mid-array, before
#: nest-report; adjacency with the tail preserved is asserted
#: explicitly instead.
MID_ARRAY_EXCEPTIONS: frozenset[str] = frozenset({"ari"})


def _agent_dirs() -> list[Path]:
    return sorted(
        entry
        for entry in AGENTS_DIR.iterdir()
        if entry.is_dir() and (entry / "meta.json").is_file()
    )


def _allow(agent_dir: Path) -> list[str]:
    data = json.loads((agent_dir / "meta.json").read_text(encoding="utf-8"))
    return (data.get("tools") or {}).get("allow") or []


class TestThePokGrantCompletenessWalk:
    def test_every_meta_bearing_agent_is_walked(self) -> None:
        assert len(_agent_dirs()) == EXPECTED_TOTAL_META, (
            f"{len(_agent_dirs())} agent dirs carry a meta.json, expected "
            f"{EXPECTED_TOTAL_META} — a new agent must be granted (or "
            "registered as an inheritance exception) in this pack"
        )

    def test_exactly_52_carry_the_grant_explicitly(self) -> None:
        explicit = [d for d in _agent_dirs() if CATEGORY in _allow(d)]
        assert len(explicit) == EXPECTED_EXPLICIT, (
            f"{len(explicit)} agents carry {CATEGORY!r} explicitly, "
            f"expected {EXPECTED_EXPLICIT}"
        )

    def test_every_non_empty_allow_carries_the_grant(self) -> None:
        missing = []
        for agent_dir in _agent_dirs():
            allow = _allow(agent_dir)
            if not allow:
                continue
            if agent_dir.name in GRANT_ABSENT_EXCEPTIONS:
                continue
            if CATEGORY not in allow:
                missing.append(agent_dir.name)
        assert not missing, (
            "these agents have a NON-EMPTY tools.allow but no "
            f"{CATEGORY!r} entry: {missing}"
        )

    def test_the_grant_appears_exactly_once_per_agent(self) -> None:
        offenders = [d.name for d in _agent_dirs() if _allow(d).count(CATEGORY) > 1]
        assert not offenders, (
            f"the grant appears MORE THAN ONCE in: {offenders}"
        )

    def test_watcher_inherits_and_is_not_edited(self) -> None:
        allow = _allow(AGENTS_DIR / "watcher")
        assert allow == [], (
            "watcher's tools.allow must stay EMPTY (it inherits the "
            f"default universe) — it is {allow!r}"
        )
        assert "watcher" in INHERITANCE_EXCEPTIONS


class TestThePokGrantOrdering:
    def test_grant_is_immediately_before_nest_report_everywhere(
        self, 
    ) -> None:
        """The insert-before discipline — ``"pok"`` sits IMMEDIATELY
        BEFORE ``"nest-report"`` in every allow carrying both, except
        the documented exceptions (canary append-end; ari mid-array
        with the tail pinned separately)."""
        offenders = []
        for agent_dir in _agent_dirs():
            allow = _allow(agent_dir)
            if not allow or CATEGORY not in allow:
                continue
            if agent_dir.name in APPEND_END_EXCEPTIONS:
                continue
            if agent_dir.name in MID_ARRAY_EXCEPTIONS:
                continue
            if ANCHOR_CATEGORY not in allow:
                continue
            if allow.index(CATEGORY) != allow.index(ANCHOR_CATEGORY) - 1:
                offenders.append((agent_dir.name, allow))
        assert not offenders, (
            f"{CATEGORY!r} must sit immediately before "
            f"{ANCHOR_CATEGORY!r} — offenders: {offenders}"
        )

    def test_canary_grant_is_the_append_end_exception(self) -> None:
        allow = _allow(AGENTS_DIR / "canary")
        assert allow, "canary's allow must be non-empty"
        assert allow[-1] == CATEGORY, (
            f"canary (no nest-report) carries the grant APPENDED AT "
            f"END — the allow is {allow}"
        )
        assert ANCHOR_CATEGORY not in allow

    def test_ari_tail_is_preserved_after_the_grant(self) -> None:
        allow = _allow(AGENTS_DIR / "ari")
        assert CATEGORY in allow
        assert ANCHOR_CATEGORY in allow
        assert allow.index(CATEGORY) == allow.index(ANCHOR_CATEGORY) - 1
        tail = allow[allow.index(CATEGORY) + 2 :]
        # The tail grew 6 → 7 with the group-chat P5 lane (2026-11-16):
        # the ``room`` PRIVILEGED category (#23) was appended at the END
        # of ari's allow. The grant census is the EIGHT-agent design
        # (A-P5-2, decisions.md:379 — ari / leader / sonic-leader /
        # worker plus the video-team pilot quartet), so this is a
        # DELIBERATE CONTRACT UPDATE — the trailing discipline the pin
        # protects (the tail survives UNREORDERED) is unchanged.
        assert tail == [
            "memoir",
            "prompt-reload",
            "prompt-assent",
            "guidelines",
            "instance_read",
            "instance_ops",
            "room",
        ], (
            f"ari's memoir/prompt-reload/prompt-assent/guidelines/instance_read/"
            f"instance_ops/room tail must survive the insert UNREORDERED — tail is {tail}"
        )


class TestTheKbWriterGrantAbsence:
    """T4.4 — the kb-writer ABSENCE pin.

    The Emperor ruling of 2026-11-23 (pok-write-gate Phase 4): the
    ``pok`` grant moves OFF the knowledge writer and ONTO the
    operational agents. This pin asserts the ABSENCE positively — the
    completeness walk's exception is a declared decision, not a hole.
    """

    def test_kb_writer_does_not_hold_the_grant(self) -> None:
        allow = _allow(AGENTS_DIR / "kb-writer")
        assert CATEGORY not in allow, (
            f"kb-writer must NOT hold the {CATEGORY!r} grant — the "
            "capture duty lives with the operational agents (ari / "
            "project-manager / leader / devops); the allow is "
            f"{allow}"
        )

    def test_the_absence_is_a_declared_exception(self) -> None:
        assert "kb-writer" in GRANT_ABSENT_EXCEPTIONS, (
            "the completeness walk must carry kb-writer as a DECLARED "
            f"exception — the set is {sorted(GRANT_ABSENT_EXCEPTIONS)}"
        )

    def test_kb_writer_keeps_its_rag_side_routing(self) -> None:
        """The removal is the POK grant ONLY — the RAG write tool
        stays (kb-writer's job is unchanged: it writes knowledge)."""
        allow = _allow(AGENTS_DIR / "kb-writer")
        assert "rag_insert_text" in allow, (
            "kb-writer must keep its RAG-side routing tool — the "
            f"allow is {allow}"
        )

    def test_the_four_operational_agents_hold_the_grant(self) -> None:
        """The compensating capture duty must be executable — the
        four agents that carry the routing contract hold the grant."""
        for name in ("ari", "project-manager", "leader", "devops"):
            allow = _allow(AGENTS_DIR / name)
            assert CATEGORY in allow, (
                f"{name} carries the POK capture duty but does not "
                f"hold the {CATEGORY!r} grant — the allow is {allow}"
            )


class TestTheExperiencerGrantAbsence:
    """feeder-grants module A — the experiencer ABSENCE pin.

    The Emperor pre-approval (2026-11-25): the ``pok`` grant moves OFF
    the knowledge extractor and stays ON the operational agents. This
    pin asserts the ABSENCE positively — the completeness walk's
    second exception is a declared decision, not a hole. Mirrors
    :class:`TestTheKbWriterGrantAbsence` exactly.
    """

    def test_experiencer_does_not_hold_the_grant(self) -> None:
        allow = _allow(AGENTS_DIR / "experiencer")
        assert CATEGORY not in allow, (
            f"experiencer must NOT hold the {CATEGORY!r} grant — the "
            "capture duty lives with the operational agents (ari / "
            "project-manager / leader / devops); the allow is "
            f"{allow}"
        )

    def test_the_absence_is_a_declared_exception(self) -> None:
        assert "experiencer" in GRANT_ABSENT_EXCEPTIONS, (
            "the completeness walk must carry experiencer as a DECLARED "
            f"exception — the set is {sorted(GRANT_ABSENT_EXCEPTIONS)}"
        )

    def test_experiencer_keeps_its_rag_side_routing(self) -> None:
        """The removal is the POK grant ONLY — the RAG tool stays
        (experiencer's job is unchanged: it writes knowledge)."""
        allow = _allow(AGENTS_DIR / "experiencer")
        assert "rag" in allow, (
            "experiencer must keep its RAG-side routing tool — the "
            f"allow is {allow}"
        )

    def test_the_four_operational_agents_hold_the_grant(self) -> None:
        """The compensating capture duty must be executable — the
        four agents that carry the routing contract hold the grant."""
        for name in ("ari", "project-manager", "leader", "devops"):
            allow = _allow(AGENTS_DIR / name)
            assert CATEGORY in allow, (
                f"{name} carries the POK capture duty but does not "
                f"hold the {CATEGORY!r} grant — the allow is {allow}"
            )


class TestTheVocabularyArtifactPair:
    def test_vocabulary_version_is_eleven(self) -> None:
        # 10 → 11 with the group-chat lane (2026-11-16): the ``room``
        # PRIVILEGED category (#23) joined the tool-category label set
        # — the F-1 pair contract: the constant and the artifact move
        # in the SAME change (the artifact was regenerated into a
        # scratch path via ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT
        # and copied over — never hand-edited).
        assert VOCABULARY_VERSION == 11

    def test_artifact_carries_66_categories_with_pok(self) -> None:
        artifact_path = (
            REPO_ROOT / ".agents" / "shared" / "metrics" / "tool-categories.json"
        )
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert data["count"] == 66, (
            f"artifact count is {data['count']} — the room category "
            "landed with the group-chat P5 lane without regenerating "
            "the artifact — 65 → 66, F-1: regenerate via "
            "ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT, never hand-edit"
        )
        assert data["vocabulary_version"] == VOCABULARY_VERSION
        assert CATEGORY in data["categories"]
        assert "room" in data["categories"], (
            "the room category (#23) must be in the frozen label set — "
            "a runtime category outside the artifact folds to 'other'"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(
        __import__("pytest").main([__file__, "-q"])  # type: ignore[attr-defined]
    )
