"""Duty-prompt completeness pins for the ``nest-report`` write surface.

The nest-anomaly-report lane (2026-11-05, board ``wi-2d691ed2``) opens
ONE write surface in a NON-privileged category: ``report_anomaly``.  An
agent can only use a tool it knows about — so phase 5 gives EVERY agent
(41 of them) the canonical duty line in ITS OWN canonical-home artifact,
and the duty text is BYTE-IDENTICAL everywhere.

This pack pins the ceremony (phase5-plan "The Two New Pins" / AC-5.1 ..
AC-5.6):

(a) **the decomposition** — ``45 = 43 walk + 2 underscore specials``
    (41 = 39 + 2 before the MERGE-FIRST step added the four aegis-lane
    agents; the count pin is the "a new agent must carry the duty"
    tripwire).  The
    walk enumerates DIRS (never collapsed ids): the report-integrity
    precedent's id-collapsed walk reaches only 34 of the 41 (it skips
    every underscore dir and resolves a ``[v2]`` pair to ONE operative
    dir), which would leave the 5 ``[v2]`` base counterparts + the 2
    underscore specials unpinned — a LEGALIZED silent loss.
(b) **byte-identity** — every one of the 41 files ends with the SAME
    block (one template constant; never a per-agent hand-write).
(c) **placement** — the block is the LAST top-level section of its file.
(d) **the 5 v2 mirror pairs** — ``rule.md`` stays byte-identical between
    a base dir and its ``[v2]`` mirror.
(e) **the forbidden-token closure** (AC-5.1) — the duty prose carries NO
    filename, path, category name, allow-list token or system-internal
    token (``docs/agent-prompt-writing-guide.md`` §1 / §10).
(f) **the Cardinal count is UNCHANGED** (AC-5.2) — the duty section is a
    NEW top-level section, so it adds ZERO Cardinal entries.
(g) **the seal map is 0-stale** and the re-pin touched EXACTLY the rows
    whose bytes moved — the anti-blanket-re-stamp invariant.
(h) **the prompt-artifact baseline** agrees with the real bytes — the
    baseline row and the file content it pins never disagree across a
    commit boundary.

Mutation legs (phase6 §(b)):

* **M-P5-1** — remove the duty block from one agent → the completeness
  walk REDs.
* **M-P5-4** — add a NEW agent dir with no duty line → the completeness
  walk REDs ("adding a new agent without the guidance fails").
* **M-P5-5** — perturb one edited ``rule.md`` byte → the seal tripwire AND
  the baseline check RED (proves the two fences are live).
* **M-P5-6** — perturb one v2 mirror → the byte-sync pin REDs.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"

# ---------------------------------------------------------------------------
# THE duty block — ONE template constant (AC-5.5).  Byte-identical in all
# 41 files; the completeness pin below is the mechanical proof that no
# hand-edit crept in (a variant that differs by one character fails it).
# ---------------------------------------------------------------------------

DUTY_HEADER = "## Reporting Anomalies"

DUTY_BLOCK = (
    "## Reporting Anomalies\n"
    "\n"
    "When I see something abnormal in my work — a defect, a contradiction, "
    "a stuck step — I report it\n"
    "with `report_anomaly`: what kind of problem, what I saw, how serious, "
    "what I think caused it. One\n"
    "report per distinct problem, never the same one twice. The tool "
    "collects the evidence itself.\n"
)

DUTY_BLOCK_SHA256 = hashlib.sha256(DUTY_BLOCK.encode("utf-8")).hexdigest()

#: The canonical-home resolution order (Ruling P5-D3).
HOME_ORDER: tuple[str, ...] = ("rule.md", "workflow.md", "soul.md")

#: The pinned decomposition (AC-5.5 / the plan's "41 = 39 walk + 2",
#: EXTENDED to the merged population).
#:
#: The MERGE-FIRST step (origin/latest 97b76e20e — the v0.28.12 aegis
#: release) added FOUR agents to the nest (``aegis`` / ``cracker`` /
#: ``hacker`` / ``sweeper``), each with its own ``rule.md`` canonical
#: home.  The ceremony was extended to them mechanically (the SAME
#: template constant, the SAME append), so the decomposition moves from
#: 41 = 39 walk + 2 specials to 45 = 43 walk + 2 specials.  The pins
#: RED without this — the count pin IS the "a new agent must carry the
#: duty" tripwire (mutation leg M-P5-4).
EXPECTED_TOTAL = 45
EXPECTED_WALK = 43
EXPECTED_SPECIALS = 2

#: The underscore specials — the two dirs the id-collapsed walk skips.
EXPECTED_SPECIAL_NAMES: tuple[str, ...] = ("_baby_template", "_mother")

#: The 5 ``[v2]`` mirror pairs whose ``rule.md`` must stay byte-identical.
V2_PAIRS: tuple[str, ...] = (
    "approver",
    "developer",
    "planner",
    "reviewer",
    "tidier",
)

#: The forbidden-token closure (AC-5.1 — the guide's §10 checklist).
FORBIDDEN_TOKENS: tuple[str, ...] = (
    ".md",
    "meta.json",
    "tools.allow",
    "daemon/",
    "agents/",
    "_tool_registry",
    "skill-set.yaml",
    "agent_id=",
    "seed_all",
    "innate_skills",
    "default_agent_versions",
    "nest-report",
)

# ---------------------------------------------------------------------------
# The seal map (g) — the tripwire's OWN table, read from the source (never
# imported: the chart e2e module drags the whole rendering stack).
# ---------------------------------------------------------------------------

SEAL_FILE = REPO_ROOT / "tests" / "test_chart_image_delivery_e2e.py"

#: The rows the C8 re-pin MOVED (their base value → the value the re-pin
#: replaced).  The anti-blanket pin asserts these MOVED — a re-pin that
#: did not move them would be cosmetic.
SEAL_REPINNED_BASE_VALUES: dict[str, str] = {
    "agents/approver/rule.md": "c09bfb29441e843bd87f8848b8b666c3b9fd4df8ada1b62f9ea5b57cf3d666ae",
    "agents/approver[v2]/rule.md": "c09bfb29441e843bd87f8848b8b666c3b9fd4df8ada1b62f9ea5b57cf3d666ae",
    "agents/architect/rule.md": "ce96896142f568c480a2f92b9b92fa0775be641d877fa386205d8438d55b21b4",
    "agents/ari/rule.md": "7b056aedb1e9c4b7ff4071e48c64fe2771f7eb599c1b0ef5a91bf5b0ca4073fe",
    "agents/ari/workflow.md": "26f68f44ce975839a5d3dea7b3617909e1284c719a1a266bab15f3a34d461c43",
    "agents/charter/rule.md": "2cb6e01b3bdfd07865aed967d524b98d7bcd965f60ffd741e52a7ee71c44573c",
    "agents/developer/rule.md": "9b06c29c811e3636765f8ee09dd1a2b10901554e542723f3de6b851a85a97249",
    "agents/devops/rule.md": "100d285927283b0276b8064520d8dfbc52b675d19e5dea78a608ca4cb13f7a1d",
    "agents/doc-writer/rule.md": "350dd33cca327e5a96941633aea6813d18b98b82a2f443cb13b270e03deabdc5",
    "agents/governor/rule.md": "07aaed75b0290d2d857ede82f876b6d55979d2f67ba29b861781531f815b41f2",
    "agents/leader/rule.md": "f0bda673c7b112ef7a43f17f1166f99fcb11f1562aabf0d7a5c9879e924a231a",
    "agents/maintenancer/rule.md": "abe43bae715c62a341788fbe5d610138936b7b4e83df87c8bbfd3611bf446aec",
    "agents/planner/rule.md": "4282ce61b58736026b5d2e72b7e2112925e4515bf95ffc88032104b4e4c4b6fb",
    "agents/reviewer/rule.md": "77c75b6dd1f17431ab0e4a7927863cdfd5cc5975658686e2adb9fd293230200b",
    "agents/reviewer[v2]/rule.md": "77c75b6dd1f17431ab0e4a7927863cdfd5cc5975658686e2adb9fd293230200b",
    "agents/tidier/rule.md": "73624de075b1f599a4d39ab703e278050066dc8d850d14d30f3c49aff6cf604c",
    "agents/tidier[v2]/rule.md": "73624de075b1f599a4d39ab703e278050066dc8d850d14d30f3c49aff6cf604c",
    "daemon/constants.py": "464a2459fb4c91c6a1add2bc6bafc3b1242415996597f4e16bef44b3387c8e92",
    "daemon/sources/registry.py": "f90c57f73bffd28e4a1992049d617d7bb791b8d792807308b40d2720d9edf624",
}

#: The rows the C8 re-pin did NOT touch — they must keep their base value
#: VERBATIM (a blanket re-stamp that silently legalised a change to a
#: sealed segment fails here).  ``agents/charter/meta.json`` is NOT here:
#: the C9 grant ceremony re-pins it (the grant touches that file), and
#: the grant pack owns that row.
SEAL_UNTOUCHED_BASE_VALUES: dict[str, str] = {
    "agents/_prompt_system/innate-skills/chart/skill.md": "fa29084d7dc0f86c87d667ef46dcfc060fa11e20e0b647295cdd75ff43353515",
    "agents/charter/skills-template/install-mermaid-cli.lib.sh": "fcd6f4612d6077a81fe521341e6e2332d1f5da31880d3fc710decbc0579fedbf",
    "agents/charter/skills-template/install-mermaid-cli.md": "ee43576c9b9d746c8ce6563c85ed289567e9fcebadc75c0bd7ede417aaaa667e",
    "agents/charter/soul.md": "458f65a608b495df0cac8c565a83663595ea85c3d13a200295dc611189387aad",
    "agents/charter/workflow.md": "2a5078f93bd2dcfe427900e85c075ee9671b8aedae17d07c7ef6abb6df4603ab",
    "agents/coder/soul.md": "6263b648cfee8eeb84bb90361687e45728bc8bdae60702e965a84a23a1b46968",
    "agents/developer[v2]/tools_note.md": "62882063ca109e7829c097201e110a0edb872ade000415372ab7ca016bee5748",
    "agents/planner[v2]/tools_note.md": "113547dcd535656316802ef8b066c6134ea09c36f0caec4f79fc21a1ecb3b31f",
    "agents/project-manager/workflow.md": "357263bd9ce19f2cd264717e9e8054293f89335c8c0ca5ea1ed264bbb951b0d2",
    "agents/wanderer/soul.md": "98a1f4b4de7657d37c3367fffcfbd3d08b3b66aacafcfa3a362a25a5be126dc2",
    "daemon/sources/adapters/discord/adapter.py": "87351d95d35410f8ac507c305ad74efe02bcea566de609a4d423d4979f41ab43",
    "daemon/sources/adapters/slack/adapter.py": "476ba0b5a17a297f652125ab9060af125ba3955d4b71e324e3de3bf177e1ef1e",
    "daemon/sources/adapters/telegram.py": "7e08882fc2fcc4291bed994ab992cb5afdc7a232a1444839f42da86fbfae063c",
    "daemon/sources/base.py": "ca2762ac8840bd8f7050955744af25f16325ed6806d09ba1fe3d020383581f3a",
    "daemon/sources/dispatcher.py": "4282ddc0396f9103ff7e14af9077c721d29e5329fac702ad3dfbfafd7083e53a",
}

#: The baseline manifest (h) — the committed versioned-artifact anchor.
#:
#: 161 entries at the ceremony; 177 after the MERGE-FIRST step
#: (origin/latest 97b76e20e — the v0.28.12 aegis release) added the four
#: aegis-lane agents' artifacts (+16 versioned files).  The COUNT is
#: pinned so a NEW versioned artifact cannot appear silently.
BASELINE_FILE = AGENTS_DIR / ".prompt-artifacts.sha256"
BASELINE_EXPECTED_ENTRIES = 177


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _agent_dirs() -> list[Path]:
    """Every ``agents/<dir>/`` that carries a ``meta.json`` (an AGENT).

    ``_inner_soul`` and ``_prompt_system`` carry no ``meta.json`` — they
    are shared scaffolding, not agents, so they get no duty (the duty is
    addressed to "I report", and there is no addressee).
    """
    return sorted(
        entry
        for entry in AGENTS_DIR.iterdir()
        if entry.is_dir() and (entry / "meta.json").is_file()
    )


def _canonical_home(agent_dir: Path) -> Path:
    """That dir's OWN canonical home (rule.md → workflow.md → soul.md).

    NO ``[v2]`` collapsing and NO operative-dir resolution: a base dir and
    its ``[v2]`` mirror are INDEPENDENT files and are checked
    independently.
    """
    for home in HOME_ORDER:
        candidate = agent_dir / home
        if candidate.is_file():
            return candidate
    raise AssertionError(f"no canonical home for {agent_dir.name}")


def _duty_files() -> list[tuple[Path, Path]]:
    """``(agent_dir, duty_file)`` for every agent — the walk's unit."""
    return [(d, _canonical_home(d)) for d in _agent_dirs()]


def _cardinal_count(text: str) -> int:
    """The number of Cardinal ENTRIES under a ``## Cardinal...`` section.

    A Cardinal entry is a bullet (``- `` / ``* ``) or a numbered
    (``1. ``) line directly under the Cardinal section, up to the next
    ``## `` heading.
    """
    count = 0
    in_cardinal = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_cardinal = line.startswith("## Cardinal")
            continue
        if not in_cardinal:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            count += 1
        elif stripped[:1].isdigit() and ". " in stripped[:4]:
            count += 1
    return count


def _seal_map() -> dict[str, str]:
    """The ``SEALED_SHA_BASELINES`` table, read from the source by AST."""
    assert SEAL_FILE.is_file(), SEAL_FILE
    tree = ast.parse(SEAL_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if not isinstance(target, ast.Name):
            continue
        if target.id != "SEALED_SHA_BASELINES":
            continue
        assert isinstance(node.value, ast.Dict)
        out: dict[str, str] = {}
        for key, value in zip(node.value.keys, node.value.values):
            assert isinstance(key, ast.Constant) and isinstance(value, ast.Constant)
            out[key.value] = value.value
        return out
    raise AssertionError("SEALED_SHA_BASELINES was not found")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# (a) the decomposition
# ---------------------------------------------------------------------------


class TestTheDutyWalkDecomposition:
    """``45 = 43 walk + 2 underscore specials`` — the pinned count.

    The count is pinned so a NEWLY added agent dir cannot silently change
    the total: a new agent MUST carry the duty (M-P5-4) and MUST be
    registered here.  (41 = 39 + 2 at the ceremony; 45 = 43 + 2 after the
    MERGE-FIRST step brought in the four aegis-lane agents.)
    """

    def test_the_total_is_41(self) -> None:
        assert len(_duty_files()) == EXPECTED_TOTAL, (
            f"the duty walk found {len(_duty_files())} agents, expected "
            f"{EXPECTED_TOTAL} — a new agent dir must carry the duty and "
            "be registered in this pack"
        )

    def test_the_walk_is_39_and_the_specials_are_2(self) -> None:
        pairs = _duty_files()
        walk = [p for p in pairs if not p[0].name.startswith("_")]
        specials = [p for p in pairs if p[0].name.startswith("_")]
        assert len(walk) == EXPECTED_WALK, (
            f"the non-underscore walk found {len(walk)} dirs, expected "
            f"{EXPECTED_WALK}"
        )
        assert len(specials) == EXPECTED_SPECIALS, (
            f"the underscore specials are {len(specials)}, expected "
            f"{EXPECTED_SPECIALS}"
        )
        assert sorted(d.name for d, _f in specials) == sorted(
            EXPECTED_SPECIAL_NAMES
        ), [d.name for d, _f in specials]

    def test_the_scaffolding_dirs_are_not_agents(self) -> None:
        """``_inner_soul`` / ``_prompt_system`` carry no duty."""
        for name in ("_inner_soul", "_prompt_system"):
            assert not (AGENTS_DIR / name / "meta.json").is_file(), (
                f"{name} gained a meta.json — it would become an agent and "
                "would need the duty (and the grant)"
            )


# ---------------------------------------------------------------------------
# (b) + (c) byte-identity and placement
# ---------------------------------------------------------------------------


class TestTheDutyBlockIsByteIdenticalEverywhere:
    def test_every_duty_file_carries_the_byte_identical_block(self) -> None:
        for agent_dir, duty_file in _duty_files():
            text = duty_file.read_text(encoding="utf-8")
            assert text.endswith(DUTY_BLOCK), (
                f"{duty_file.relative_to(REPO_ROOT)} does not end with the "
                f"canonical duty block (sha256 {DUTY_BLOCK_SHA256[:12]}…)"
            )
            assert text.count(DUTY_HEADER) == 1, (
                f"{duty_file.relative_to(REPO_ROOT)} carries the duty header "
                f"{text.count(DUTY_HEADER)} times — exactly ONE per agent"
            )

    def test_the_duty_block_is_the_last_top_level_section(self) -> None:
        for _agent_dir, duty_file in _duty_files():
            text = duty_file.read_text(encoding="utf-8")
            head = text[: -len(DUTY_BLOCK)]
            assert head.endswith("\n\n"), (
                f"{duty_file.relative_to(REPO_ROOT)} — the duty section must "
                "be separated from the previous section by a blank line"
            )
            assert f"\n{DUTY_HEADER}\n" not in head, (
                f"{duty_file.relative_to(REPO_ROOT)} carries the duty section "
                "twice"
            )

    def test_the_block_sha256_is_the_pinned_one(self) -> None:
        """The constant in THIS pack is the shipped one (no drift)."""
        assert DUTY_BLOCK_SHA256 == (
            "da18f1f9cf2ccda2c443e0edb5c76c13ec00c3368f8e284a65b61ae0091663e9"
        ), (
            "the duty template constant changed — the 41 shipped blocks are "
            "byte-identical to the PREVIOUS constant; a change here must be "
            "a deliberate re-append of all 41 files"
        )


# ---------------------------------------------------------------------------
# (d) the v2 mirror pairs
# ---------------------------------------------------------------------------


class TestTheV2MirrorPairsStayByteIdentical:
    """The base dir and its ``[v2]`` mirror carry the SAME ``rule.md``.

    Every pair's ``rule.md`` was byte-identical BEFORE the ceremony; the
    append must PRESERVE that (the served prompt differs by version tag
    otherwise — Ruling P5-D6).
    """

    def test_each_pair_is_byte_identical(self) -> None:
        for base in V2_PAIRS:
            base_path = AGENTS_DIR / base / "rule.md"
            mirror_path = AGENTS_DIR / f"{base}[v2]" / "rule.md"
            assert base_path.is_file(), base_path
            assert mirror_path.is_file(), mirror_path
            assert base_path.read_bytes() == mirror_path.read_bytes(), (
                f"the {base} / {base}[v2] rule.md pair diverged — the base "
                "and the mirror must carry the SAME bytes (the append must "
                "preserve the pair's byte-identity)"
            )

    def test_each_pair_carries_the_duty_block(self) -> None:
        for base in V2_PAIRS:
            for suffix in ("", "[v2]"):
                path = AGENTS_DIR / f"{base}{suffix}" / "rule.md"
                assert path.read_text(encoding="utf-8").endswith(DUTY_BLOCK), (
                    f"{path.relative_to(REPO_ROOT)} lost the duty block"
                )


# ---------------------------------------------------------------------------
# (e) the forbidden-token closure
# ---------------------------------------------------------------------------


class TestTheForbiddenTokenClosure:
    """AC-5.1 — the duty prose names ONLY the tool.

    No filename, path, category name, allow-list token or system-internal
    token may appear in the duty block
    (``docs/agent-prompt-writing-guide.md`` §1 / §10).
    """

    def test_the_duty_block_carries_no_forbidden_token(self) -> None:
        for token in FORBIDDEN_TOKENS:
            assert token not in DUTY_BLOCK, (
                f"the duty block carries the forbidden token {token!r} — the "
                "prose must name the tool and its arguments in plain words"
            )

    def test_no_duty_file_carries_a_forbidden_token_inside_its_duty_block(
        self,
    ) -> None:
        for _agent_dir, duty_file in _duty_files():
            text = duty_file.read_text(encoding="utf-8")
            assert text.endswith(DUTY_BLOCK)
            for token in FORBIDDEN_TOKENS:
                assert token not in DUTY_BLOCK, (
                    f"{duty_file.relative_to(REPO_ROOT)} — the duty block "
                    f"carries {token!r}"
                )

    def test_the_duty_block_names_the_tool_exactly_once(self) -> None:
        assert DUTY_BLOCK.count("report_anomaly") == 1, (
            "the duty block names the tool exactly once"
        )


# ---------------------------------------------------------------------------
# (f) the Cardinal count is unchanged
# ---------------------------------------------------------------------------


class TestTheDutyAddsNoCardinalEntry:
    """AC-5.2 — the count is COUNTED BEFORE the edit and asserted after.

    The durable form is SELF-COMPUTING: the count over the file equals
    the count over the file with the duty block removed — the duty
    section is a NEW top-level section, so it contributes ZERO.
    """

    def test_each_duty_file_keeps_its_cardinal_count(self) -> None:
        for _agent_dir, duty_file in _duty_files():
            text = duty_file.read_text(encoding="utf-8")
            assert text.endswith(DUTY_BLOCK)
            before = _cardinal_count(text[: -len(DUTY_BLOCK)])
            after = _cardinal_count(text)
            assert before == after, (
                f"{duty_file.relative_to(REPO_ROOT)} — the Cardinal count "
                f"changed {before} → {after}; the duty section must NOT be "
                "a Cardinal (the guide caps the Cardinal block at 7)"
            )


# ---------------------------------------------------------------------------
# (g) the seal map
# ---------------------------------------------------------------------------


class TestTheSealRePin:
    """0 stale, and the re-pin touched EXACTLY the rows that moved."""

    def test_the_seal_map_has_35_rows(self) -> None:
        seal = _seal_map()
        assert len(seal) == 35, (
            f"the sealed set has {len(seal)} rows, expected 35 — the "
            "membership must not change"
        )

    def test_every_sealed_row_matches_the_real_bytes(self) -> None:
        """0 stale — the tripwire's own invariant, re-asserted here."""
        seal = _seal_map()
        stale = []
        for rel, expected in seal.items():
            path = REPO_ROOT / rel
            assert path.is_file(), rel
            actual = _sha256(path)
            if actual != expected:
                stale.append((rel, expected, actual))
        assert not stale, (
            "the sealed rows are stale (the tripwire is a HARD RED):\n"
            + "\n".join(
                f"  - {rel}: baseline={exp[:12]}… current={cur[:12]}…"
                for rel, exp, cur in stale
            )
        )

    def test_the_re_pinned_rows_moved(self) -> None:
        """The re-pin is LOAD-BEARING — a cosmetic re-pin is a RED."""
        seal = _seal_map()
        for rel, base_value in SEAL_REPINNED_BASE_VALUES.items():
            assert rel in seal, f"{rel} is missing from the sealed map"
            assert seal[rel] != base_value, (
                f"{rel} still carries its BASE value — the re-pin did not "
                "move it"
            )

    def test_the_untouched_rows_keep_their_base_values(self) -> None:
        """The anti-blanket-re-stamp invariant (the plan's risk #5)."""
        seal = _seal_map()
        for rel, base_value in SEAL_UNTOUCHED_BASE_VALUES.items():
            assert rel in seal, f"{rel} is missing from the sealed map"
            assert seal[rel] == base_value, (
                f"{rel} was re-stamped even though its file did NOT change — "
                "a blanket re-stamp silently legalises a change to a sealed "
                "segment"
            )

    def test_the_re_pinned_and_untouched_sets_partition_the_map(self) -> None:
        seal = _seal_map()
        covered = set(SEAL_REPINNED_BASE_VALUES) | set(
            SEAL_UNTOUCHED_BASE_VALUES
        )
        # ``agents/charter/meta.json`` is the C9 row (the grant touches it).
        assert covered | {"agents/charter/meta.json"} == set(seal), (
            "the re-pinned + untouched sets must cover the whole seal map "
            "(plus the C9 row)"
        )


# ---------------------------------------------------------------------------
# (h) the prompt-artifact baseline
# ---------------------------------------------------------------------------


class TestThePromptArtifactBaseline:
    """The baseline row and the file content it pins must NEVER disagree
    across a commit boundary (the ari-voice house rule)."""

    def test_the_baseline_matches_the_real_bytes(self) -> None:
        assert BASELINE_FILE.is_file(), BASELINE_FILE
        entries = []
        for line in BASELINE_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            entries.append((digest, rel))
        assert len(entries) == BASELINE_EXPECTED_ENTRIES, (
            f"the baseline carries {len(entries)} entries, expected "
            f"{BASELINE_EXPECTED_ENTRIES} — the ceremony adds NO artifact"
        )
        bad = []
        for digest, rel in entries:
            path = REPO_ROOT / rel
            if not path.is_file():
                bad.append((rel, "MISSING"))
                continue
            actual = _sha256(path)
            if actual != digest:
                bad.append((rel, f"{digest[:12]}… != {actual[:12]}…"))
        assert not bad, (
            "the prompt-artifact baseline disagrees with the real bytes:\n"
            + "\n".join(f"  - {rel}: {why}" for rel, why in bad)
        )

    def test_the_baseline_covers_every_duty_file(self) -> None:
        baseline_paths = {
            line.partition("  ")[2]
            for line in BASELINE_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        for _agent_dir, duty_file in _duty_files():
            rel = str(duty_file.relative_to(REPO_ROOT))
            assert rel in baseline_paths, (
                f"{rel} is not in the baseline — the ceremony's re-stamp "
                "must cover every edited artifact"
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(
        __import__("pytest").main([__file__, "-q"])  # type: ignore[attr-defined]
    )
