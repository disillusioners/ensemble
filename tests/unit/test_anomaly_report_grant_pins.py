"""Grant-completeness pins for the ``nest-report`` write category.

The nest-anomaly-report lane (2026-11-05, board ``wi-2d691ed2``) opens
ONE write surface in a NON-privileged category: ``report_anomaly``.  Phase
5's grant ceremony gives every agent the category — EXPLICITLY, in its
own ``meta.json`` — so an agent never depends on the default universe
for the tool it is being asked to use.

This pack pins the ceremony (phase5-plan "The Two New Pins"):

(a) **the grant completeness walk** — every ``agents/*/meta.json`` with a
    NON-EMPTY ``tools.allow`` carries ``"nest-report"``, as the **LAST**
    element (the trailing-order discipline — never a reorder).
(b) **the ``watcher`` inheritance exception** — its ``tools.allow`` is
    ``[]``, so it INHERITS every non-privileged category through the
    default universe.  Appending one entry would convert an empty allow
    into a 1-entry allow and therefore SILENTLY STRIP every other tool
    watcher currently inherits (Ruling P5-D8) — so the pin encodes the
    exception explicitly instead of requiring an entry.
(c) **the three (measured: FIVE) re-pinned allow-array constants** — the
    ari trailing set, ``GAIA_TOOLS_ALLOW``, the sketcher ``EXPECTED_ALLOW``
    and the TWO critic exact-set assertions.  Each is re-pinned in the SAME
    commit as the grant it describes.
(d) **the seal map is 0-stale** and ``agents/charter/meta.json`` (the ONE
    sealed file the grant touches) MOVED.

Mutation legs (phase6 §(b)):

* **M-P5-2** — remove ``"nest-report"`` from one ``meta.json`` → the grant
  walk REDs.
* **M-P5-3** — move the entry off the last position → the order pin REDs.
* **M-P5-7** — revert the ari trailing set to its 6-entry value → the ari
  pin REDs (proves the re-pin is load-bearing, not cosmetic).
* **M-P5-8** — remove one grant from a phase-3-touched file
  (``agents/devops/meta.json``) → the grant walk REDs (proves the two-phase
  edit order is verified).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"

#: The category key — byte-identical to the registry entry
#: (``daemon/tools/_tool_registry.py``'s ``CATEGORY_MODULES``).
CATEGORY = "nest-report"

#: The pinned population (Ruling P5-D9 / P5-D8).
#:
#: 40 explicit grants / 41 meta.json at the ceremony; 44 / 45 after the
#: MERGE-FIRST step (origin/latest 97b76e20e — the v0.28.12 aegis
#: release) added FOUR agents (``aegis`` / ``cracker`` / ``hacker`` /
#: ``sweeper``), each with a NON-EMPTY allow list and therefore each
#: granted mechanically by the SAME append.  45 / 46 after the POK
#: Phase-1 lane's mechanical re-derivation (2026-11-07): ``canary``
#: arrived after the last sweep WITHOUT the retroactive grant — it
#: counts toward the meta denominator but stays a documented
#: nest-report exception (its POK grant is the append-end exception).
#: 44 explicit / 48 after the video-team scaffold (2026-11-11) added FOUR agents
#: (video-producer / video-capturer / video-editor / video-verifier), each granted
#: by the SAME append-last rule; 50 total meta-bearing dirs.
EXPECTED_EXPLICIT = 48
EXPECTED_TOTAL_META = 50
#: The ONE agent that inherits instead of carrying an explicit entry.
INHERITANCE_EXCEPTIONS: frozenset[str] = frozenset({"watcher"})
#: Agents whose NON-EMPTY allow predates their sweep class and
#: deliberately carries NO ``nest-report`` entry (the POK B4 sweep
#: grants them ``pok`` via the append-end exception only — never a
#: retroactive nest-report).  ``canary`` arrived post-sweep (2026-11,
#: 4-entry allow, no nest-report); the grant walk skips it BY NAME.
NEST_REPORT_LATE_ARRIVALS: frozenset[str] = frozenset({"canary"})
#: The agents whose allow carries legitimate entries AFTER the grant —
#: the "grant is last" trailing discipline never applied to them.
#: ``ari`` (2026-11-06 prompt-reload lane): memoir + prompt-reload sit
#: after ``nest-report``; the POK sweep inserts BEFORE ``nest-report``
#: (mid-array), preserving that tail.
GRANT_NOT_LAST_EXCEPTIONS: frozenset[str] = frozenset({"ari"})

#: The re-pinned allow-array constants — the file, the constant name and
#: the value the constant MUST carry after the grant.
ALLOW_ARRAY_CONSTANTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "tests/unit/test_board_registration.py",
        '"ari": [',
        (
            "self-alarm",
            "view-write",
            "board-link",
            "mcp_rpc",
            "wedge-capture",
            "notify-user",
            "pok",
            "nest-report",
            "memoir",
            "prompt-reload",
        ),
    ),
    (
        "tests/unit/test_gaia_agent.py",
        "GAIA_TOOLS_ALLOW",
        ("bash", "proc", "filesystem", "help", "system", "pok", "nest-report"),
    ),
    (
        "tests/unit/agents/test_sketcher_agent.py",
        "EXPECTED_ALLOW",
        (
            "od.generate",
            "od.compose_brief",
            "od.lint",
            "od.save",
            "image",
            "dynamic-skill",
            "pok",
            "nest-report",
        ),
    ),
)

#: The TWO critic exact-set assertions (the same array, pinned twice).
CRITIC_EXACT_ALLOW: tuple[str, ...] = (
    "read_file",
    "image",
    "design",
    "nest-report",
)

#: The sealed file the grant touches (the ONE ``meta.json`` in the seal
#: map) — its value MUST move, and MUST match the real bytes.
SEAL_GRANT_ROW = "agents/charter/meta.json"
SEAL_GRANT_BASE_VALUE = (
    "e1034e1d92e7bb30f2d39ab0a625a661287251cf5ca961cbeba3e1893dcfd492"
)

SEAL_FILE = REPO_ROOT / "tests" / "test_chart_image_delivery_e2e.py"


def _agent_dirs() -> list[Path]:
    return sorted(
        entry
        for entry in AGENTS_DIR.iterdir()
        if entry.is_dir() and (entry / "meta.json").is_file()
    )


def _allow(agent_dir: Path) -> list[str]:
    data = json.loads((agent_dir / "meta.json").read_text(encoding="utf-8"))
    return (data.get("tools") or {}).get("allow") or []


def _seal_map() -> dict[str, str]:
    tree = ast.parse(SEAL_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if isinstance(target, ast.Name) and target.id == "SEALED_SHA_BASELINES":
            assert isinstance(node.value, ast.Dict)
            out: dict[str, str] = {}
            for key, value in zip(node.value.keys, node.value.values):
                assert isinstance(key, ast.Constant) and isinstance(value, ast.Constant)
                out[key.value] = value.value
            return out
    raise AssertionError("SEALED_SHA_BASELINES was not found")


# ---------------------------------------------------------------------------
# (a) + (b) the grant walk
# ---------------------------------------------------------------------------


class TestTheGrantCompletenessWalk:
    def test_every_meta_bearing_agent_is_walked(self) -> None:
        assert len(_agent_dirs()) == EXPECTED_TOTAL_META, (
            f"{len(_agent_dirs())} agent dirs carry a meta.json, expected "
            f"{EXPECTED_TOTAL_META} — a new agent must be granted (or "
            "registered as an inheritance exception) in this pack"
        )

    def test_exactly_40_carry_the_grant_explicitly(self) -> None:
        explicit = [
            d for d in _agent_dirs() if CATEGORY in _allow(d)
        ]
        assert len(explicit) == EXPECTED_EXPLICIT, (
            f"{len(explicit)} agents carry {CATEGORY!r} explicitly, expected "
            f"{EXPECTED_EXPLICIT}"
        )

    def test_every_non_empty_allow_carries_the_grant(self) -> None:
        missing = []
        for agent_dir in _agent_dirs():
            allow = _allow(agent_dir)
            if not allow:
                continue
            if agent_dir.name in NEST_REPORT_LATE_ARRIVALS:
                # canary-class: non-empty allow that predates its sweep
                # class — granted ``pok`` by append-end ONLY, never a
                # retroactive nest-report (see the module docstring).
                continue
            if CATEGORY not in allow:
                missing.append(agent_dir.name)
        assert not missing, (
            "these agents have a NON-EMPTY tools.allow but no "
            f"{CATEGORY!r} entry: {missing}"
        )

    def test_the_grant_is_the_last_element(self) -> None:
        """The trailing-order discipline — the grant is APPENDED, never
        reordered into the middle (ari-class tails exempt: memoir +
        prompt-reload legitimately follow ``nest-report`` there)."""
        offenders = []
        for agent_dir in _agent_dirs():
            allow = _allow(agent_dir)
            if not allow or CATEGORY not in allow:
                continue
            if agent_dir.name in GRANT_NOT_LAST_EXCEPTIONS:
                continue
            if allow[-1] != CATEGORY:
                offenders.append((agent_dir.name, allow[-1]))
        assert not offenders, (
            "the grant must be the LAST element of tools.allow (the ari "
            f"trailing-entry precedent) — offenders: {offenders}"
        )

    def test_the_grant_appears_exactly_once_per_agent(self) -> None:
        offenders = [
            d.name
            for d in _agent_dirs()
            if _allow(d).count(CATEGORY) > 1
        ]
        assert not offenders, (
            f"the grant appears MORE THAN ONCE in: {offenders}"
        )

    def test_watcher_inherits_and_is_not_edited(self) -> None:
        """Ruling P5-D8 — ``watcher``'s empty allow is INHERITANCE.

        The default universe grants every NON-privileged category to an
        empty-allow agent, so ``watcher`` already holds the tool.  An
        explicit entry would convert the empty allow into a 1-entry allow
        and STRIP every other tool watcher inherits.
        """
        watcher = AGENTS_DIR / "watcher"
        assert watcher.is_dir(), watcher
        allow = _allow(watcher)
        assert allow == [], (
            f"watcher's tools.allow must stay EMPTY (it inherits the "
            f"default universe) — it is {allow!r}"
        )
        assert "watcher" in INHERITANCE_EXCEPTIONS

    def test_the_inheritance_exception_set_matches_the_empty_allow_agents(
        self,
    ) -> None:
        empty = {d.name for d in _agent_dirs() if not _allow(d)}
        assert empty == set(INHERITANCE_EXCEPTIONS), (
            "the inheritance-exception set must match the agents whose "
            f"tools.allow is empty: {empty}"
        )

    def test_the_three_phase_3_touched_agents_carry_both_entries(self) -> None:
        """Phase 3 appended ``nest-report-read`` to these three; phase 5
        appends ``nest-report`` AFTER it (the two-phase edit order)."""
        for name in ("maintenancer", "devops", "wanderer"):
            allow = _allow(AGENTS_DIR / name)
            assert "nest-report-read" in allow, name
            assert allow[-1] == CATEGORY, (
                f"{name}: the phase-5 grant must come AFTER phase 3's "
                f"'nest-report-read' — the array is {allow}"
            )
            assert allow.index("nest-report-read") < allow.index(CATEGORY), name


# ---------------------------------------------------------------------------
# (c) the re-pinned allow-array constants
# ---------------------------------------------------------------------------


class TestTheRepinnedAllowArrayConstants:
    def test_each_constant_carries_the_grant(self) -> None:
        for rel, const_name, expected in ALLOW_ARRAY_CONSTANTS:
            path = REPO_ROOT / rel
            assert path.is_file(), path
            source = path.read_text(encoding="utf-8")
            assert const_name in source, (
                f"{rel} no longer carries {const_name!r} — the re-pin "
                "target moved"
            )
            assert CATEGORY in source, (
                f"{rel} — the re-pinned constant must carry {CATEGORY!r}"
            )
            for entry in expected:
                assert f'"{entry}"' in source, (
                    f"{rel} — the re-pinned constant must carry {entry!r}"
                )

    def test_the_ari_trailing_set_is_exactly_the_seven(self) -> None:
        """Re-derived 2026-11-07 (POK Phase 1 B4 sweep): the pinned
        literal mirrors ``test_board_registration.py``'s ari allow
        constant. History: 6 entries (pre-grant) → 7 (nest-report
        appended) → 9 (memoir + prompt-reload joined, 2026-11-06) →
        10 (``pok`` inserted BEFORE ``nest-report`` — the sweep's
        insert-before rule, the memoir/prompt-reload tail preserved)
        → 11 (prompt-assent appended at the END, 2026-11-10)."""
        source = (REPO_ROOT / "tests/unit/test_board_registration.py").read_text(
            encoding="utf-8"
        )
        assert (
            '"ari": ["self-alarm", "view-write", "board-link", "mcp_rpc", '
            '"wedge-capture", "notify-user", "pok", "nest-report", '
            '"memoir", "prompt-reload", "prompt-assent"],' in source
        ), (
            "the ari trailing set must carry pok inserted BEFORE "
            "nest-report with the memoir/prompt-reload/prompt-assent tail "
            "intact (the 11-entry re-derived value)"
        )

    def test_the_gaia_constant_is_exactly_the_seven(self) -> None:
        """Re-derived 2026-11-07 (POK B4 sweep): 5 entries (pre-grant)
        → 6 (nest-report appended) → 7 (``pok`` inserted before
        ``nest-report``)."""
        source = (REPO_ROOT / "tests/unit/test_gaia_agent.py").read_text(
            encoding="utf-8"
        )
        assert (
            'GAIA_TOOLS_ALLOW = ["bash", "proc", "filesystem", "help", '
            '"system", "pok", "nest-report"]' in source
        ), (
            "GAIA_TOOLS_ALLOW must carry pok inserted BEFORE nest-report "
            "(the 7-entry re-derived value)"
        )

    def test_the_sketcher_constant_is_exactly_the_seven(self) -> None:
        source = (
            REPO_ROOT / "tests/unit/agents/test_sketcher_agent.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "EXPECTED_ALLOW":
                        assert isinstance(node.value, ast.Set)
                        found = frozenset(
                            elt.value for elt in node.value.elts
                        )
        assert found is not None, "EXPECTED_ALLOW was not found"
        assert found == frozenset(
            {
                "od.generate",
                "od.compose_brief",
                "od.lint",
                "od.save",
                "image",
                "dynamic-skill",
                "pok",
                "nest-report",
            }
        ), f"EXPECTED_ALLOW is {sorted(found)}"

    def test_the_critic_exact_set_is_pinned_twice(self) -> None:
        """The critic's exact allow is asserted in TWO packs — both must
        carry the appended grant (the plan's census named 3 pins; the
        measured set is 5)."""
        for rel in (
            "tests/unit/agents/test_sketcher_agent.py",
            "tests/unit/tools/test_image_gen_tool.py",
        ):
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert (
                '["read_file", "image", "design", "pok", "nest-report"]' in source
            ), (
                f"{rel} — the critic's exact allow assertion must carry the "
                "appended grant (pok inserted before nest-report)"
            )


# ---------------------------------------------------------------------------
# (d) the seal map
# ---------------------------------------------------------------------------


class TestTheSealMapAfterTheGrant:
    def test_the_seal_map_is_zero_stale(self) -> None:
        import hashlib

        seal = _seal_map()
        stale = []
        for rel, expected in seal.items():
            actual = hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()
            if actual != expected:
                stale.append((rel, expected, actual))
        assert not stale, (
            "the sealed rows are stale (the tripwire is a HARD RED):\n"
            + "\n".join(
                f"  - {rel}: baseline={exp[:12]}… current={cur[:12]}…"
                for rel, exp, cur in stale
            )
        )

    def test_the_grant_seal_row_moved(self) -> None:
        """``agents/charter/meta.json`` is the ONE sealed file the grant
        touches — the re-pin is load-bearing."""
        seal = _seal_map()
        assert SEAL_GRANT_ROW in seal, SEAL_GRANT_ROW
        assert seal[SEAL_GRANT_ROW] != SEAL_GRANT_BASE_VALUE, (
            f"{SEAL_GRANT_ROW} still carries its BASE value — the grant "
            "re-pin did not move it"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(
        __import__("pytest").main([__file__, "-q"])  # type: ignore[attr-defined]
    )
