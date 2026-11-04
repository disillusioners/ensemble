"""Aegis security family lane tests (commission aegis-security-family, P4/DP7).

Pins the four new agents (``aegis`` orchestrator + ``hacker``/``cracker``/
``sweeper`` leaf lanes), the leader one-liner, the installer-skill FENCES
contract, and the prompt-artifact baseline arithmetic, per the locked plan
set at ``.agents/shared/planning/aegis-security-family/`` (T1–T28 as
amended, plus the discrete T29 legality-pin matrix and the T30
installer-guard pin pack).

Design notes that matter to every leg:

* **Pure file parsing wherever possible** — no daemon import, no DB, no LLM
  — so the file runs on bare ``python3``. The single exception is the
  registry-scan pair (``test_registry_scan_*``): the fail-open guard
  (tools-block round-trip through ``ToolFilter``) and the boot-scan leg
  need the venv interpreter, so both run as subprocess probes under
  ``sys.executable`` and both carry the ``registry_scan`` name so the
  bare-python subset is ``-k "not registry_scan"``.
* **The T29 matrix is 5 guardrail families x 4 personas = 20 asserted
  cells, every cell positive with its per-persona form** (R1 leader ruling
  2026-11-04). Anchors are CASE-SENSITIVE verbatim substrings of the
  persona cardinals; deleting or mutating the cardinal reds its cell.
  Every anchor is SINGLE-SITE by construction — it occurs exactly ONCE in
  its persona's ``rule.md`` — so the plan's own named acceptance mutation
  (deleting the anchored clause) reds the cell instead of leaving a second
  occurrence behind.
* **T30 closes the B9 false-green class**: uniform deletion of a FENCES
  bullet across all three bodies keeps cross-body byte-identity (T23 green)
  — the per-bullet presence pin reds anyway because it checks each bullet
  against a FIXED expected list, not against the sibling bodies.
* **T30's static and executed legs are separate.** The static leg proves the
  checksum compare is DEFINED and CALL-SITED; the executed leg sources the
  library and really runs the verify function (unset pin => rc=1 +
  ``HARD_BLOCKER``). The same split holds for the corpus-path guard: a
  static call-site leg plus an executed leg that really refuses an
  out-of-sandbox path.
* **The baseline leg asserts file-exists + exactly-177 rows** (AM-11): the
  generator's ``--check`` exits 0 both when clean AND when the file is
  absent, so ``rc=0`` alone proves nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "agents"
BASELINE_PATH = AGENTS_DIR / ".prompt-artifacts.sha256"
# The lane branch's base commit (origin/latest at lane start; T25/G4.6 pin).
LANE_BASE_COMMIT = "1f6898ae2889ff002b4656486f6be3ee1048b649"

FAMILY = ("aegis", "hacker", "cracker", "sweeper")
CHILDREN = ("hacker", "cracker", "sweeper")
PROMPT_SURFACE = ("soul.md", "rule.md", "workflow.md", "tools_note.md")

# --- DP1 tool matrices (decisions.md §B.2 — AM-1/AM-2 corrected) -----------
AEGIS_ALLOW = frozenset({
    "instance", "bash", "proc", "filesystem", "time", "self", "help",
    "context", "shared_meta_kv", "knowledge", "mcp", "question",
    "midflight", "dynamic-skill", "todo",
})
AEGIS_DENY = frozenset({
    "write_file", "edit_file", "terminate_instance", "pause_instance",
    "resume_instance", "set_instance_tunable",
})
CHILD_ALLOW = frozenset({"bash", "proc", "filesystem", "time", "help", "self"})

# The 17 privileged categories (daemon/tools/_tool_registry.py
# PRIVILEGED_TOOL_CATEGORIES — pinned here as data; T8 asserts the family's
# allows never intersect it).
PRIVILEGED_CATEGORIES = frozenset({
    "system_upgrade", "system-log", "ens-db", "ens-env", "view-views",
    "secrets", "settings", "board", "self-alarm", "view-write",
    "board-link", "wedge-capture", "instance_read", "instance_ops",
    "mcp_rpc", "notify-user", "metrics-read",
})

# Guide §1/§10 forbidden system-internals tokens (prompt prose carries none).
SYSTEM_INTERNAL_TOKENS = (
    "meta.json",
    "tools.allow",
    "tools.deny",
    "daemon/",
    "_tool_registry",
    "skill-set.yaml",
    "innate_skills",
    "seed_all",
    "agent_id=",
    "default_agent_versions",
)

# T18: adapted-from provenance (guide §10) EXTENDED per AM-8 with the
# "preserved verbatim from" token and the "per <agent> pattern, job <id>"
# shape (the charter precedent's heading/header forms).
T18_MARKERS = ("adapted from", "migrated from", "verified from", "preserved verbatim from")
T18_JOB_SHAPE_RE = re.compile(r"per\s+\S+\s+pattern,\s*job\s+\S+")

# The three report-scrutiny snippets (B4; the dynamic house test requires
# them on every non-empty-team agent — asserted here over the aegis surface).
D_REQUIRED_SNIPPETS = (
    "[REPORT SANITY:",
    "interim, not completion",
    "send_message",
)

# T17 tone-block subheadings, per agent (guide §5: caller voice + dispatch
# voice + per-severity framing — all three parts in every soul).
TONE_HEADINGS = {
    "aegis": ("Voice to my caller", "Voice in dispatch prompts", "Per-severity framing"),
    "hacker": ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
    "cracker": ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
    "sweeper": ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
}

# Out-of-team peers that must NEVER appear as spawn fallbacks in the
# aegis strategy skill (guide §8 org-chart rule; T19).
OUT_OF_TEAM_PEERS = (
    "developer", "reviewer", "tester", "coder", "planner", "tidier",
    "approver", "architect", "giter", "devops", "ari", "leader-as-spawn",
)

INSTALLER_SKILLS = {
    "hacker": "fuzzing-toolchain",
    "cracker": "credential-bench",
    "sweeper": "sast-toolchain",
}

# ---------------------------------------------------------------------------
# T29 — the legality-cardinal pin matrix (Blocker 4; R1 leader ruling
# 2026-11-04: family 3 is UNIVERSALLY OWNED with per-persona forms —
# 5 families x 4 personas = 20 ASSERTED cells, every cell positive).
# Anchors are case-sensitive verbatim substrings of the persona cardinals;
# each anchor lives inside a numbered cardinal so deleting the cardinal REDS.
# ---------------------------------------------------------------------------
_LEGALITY_MATRIX: dict[tuple[str, str], tuple[str, ...]] = {
    # family 1 — read-only on targets (per-persona phrasing)
    ("aegis", "read-only-on-targets"): ("target tree is never modified",),
    ("hacker", "read-only-on-targets"): ("target tree is never a write target",),
    ("cracker", "read-only-on-targets"): ("the target tree is never a write target",),
    ("sweeper", "read-only-on-targets"): ("the target tree is read, never written",),
    # family 2 — self-owned sandbox / zero external targets
    ("aegis", "sandbox-zero-external"): ("prefix-match of the engagement sandbox root",),
    # The anchor is SINGLE-SITE by construction: every anchor string below
    # must occur exactly ONCE in its persona's rule.md, so the plan's own
    # named acceptance mutation (delete the anchor's clause) REDS the cell.
    # ("No external targets, ever" — the body clause of cardinal 2 — is
    # single-site; the shorter "external targets, ever" occurs twice, in the
    # cardinal TITLE and in the body clause, and therefore does NOT bite.)
    ("hacker", "sandbox-zero-external"): ("No external targets, ever",),
    ("cracker", "sandbox-zero-external"): ("prefix-match of the engagement sandbox root",),
    ("sweeper", "sandbox-zero-external"): ("prefix-match of the engagement sandbox root",),
    # family 3 — sample-data-only, per-persona form (R1 ruling):
    # cracker = synthetic-sample benches only; aegis/hacker/sweeper = never
    # ingest/transcribe real credential material (findings cite LOCATION +
    # TYPE, never content); sweeper's gitleaks form = detect-never-transcribe.
    ("aegis", "sample-data-only"): ("never ingest or transcribe real credential", "LOCATION + TYPE, never content"),
    ("hacker", "sample-data-only"): ("never ingest or transcribe real credential", "LOCATION + TYPE, never content"),
    ("cracker", "sample-data-only"): ("synthetic-sample benches only",),
    ("sweeper", "sample-data-only"): ("never transcribe them into reports", "LOCATION + TYPE, never content"),
    # family 4 — findings private (LEADER-CONFIRMED INTENT 2026-11-04)
    ("aegis", "findings-private"): ("NEVER committed to git history",),
    ("hacker", "findings-private"): ("Findings are private",),
    ("cracker", "findings-private"): ("never committed",),
    ("sweeper", "findings-private"): ("never committed",),
    # family 5 — white-hat ethos / authorisation
    ("aegis", "white-hat-ethos"): ("White-hat ethos",),
    ("hacker", "white-hat-ethos"): ("White-hat authorisation",),
    ("cracker", "white-hat-ethos"): ("White-hat authorisation",),
    ("sweeper", "white-hat-ethos"): ("White-hat authorisation",),
}
LEGALITY_CELLS = sorted(_LEGALITY_MATRIX.keys())

# ---------------------------------------------------------------------------
# T30(a) — per-bullet presence pins for the shared FENCES bullet list
# (Blocker 9 / B9: a FIXED expected list — NOT derived from the sibling
# bodies — so uniform deletion across all three REDS even though T23's
# cross-body byte-identity survives).
# ---------------------------------------------------------------------------
EXPECTED_FENCES_BULLETS = (
    "class (iii) every other user path — the target codebase included — is never written, at any time, by any path",
    "Docker is absent from this host and must never be installed or used",
    "every dependency resolves user-space, and a missing prerequisite is a stop, never an install",
    "a fetched asset is downloaded, sha256-verified, and only then promoted",
    "compared against the pinned expected digest before any promotion",
    "secret-shaped material is referenced by class and location, never by value",
    "variable names only — never a value",
    "no retries, no fallback install, no privilege escalation",
    "verify-only fast path: no install call, no state write",
)

# The ten named finding-record fields (DP4; D-6: names pinned, not the count).
FINDING_RECORD_FIELDS = (
    "id", "title", "severity", "confidence", "category", "location",
    "description", "evidence", "impact", "fix",
)
# A distinctive literal from the canonical finding-record block, used to pin
# the block's single home (T24: declared once, linked by name elsewhere).
FINDING_RECORD_BLOCK_LITERAL = "stable within the engagement (F-001, F-002"

SEVERITY_LEVELS = ("critical", "high", "medium", "low", "info")

# T23 secondary: tool-agnostic FENCES keywords each .lib.sh header must carry
# (AM-10: keyword presence only — explicitly NOT byte-identity vs the .md).
LIB_FENCES_KEYWORDS = ("Docker", "system packages", "fetch-and-execute", "HARD_BLOCKER", "idempotent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_dir(agent: str) -> Path:
    return AGENTS_DIR / agent


def _read(agent: str, *parts: str) -> str:
    path = _agent_dir(agent).joinpath(*parts)
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _meta(agent: str) -> dict:
    return json.loads(_read(agent, "meta.json"))


def _prompt_surface(agent: str) -> str:
    return "\n".join(_read(agent, name) for name in PROMPT_SURFACE)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (data, not prose)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def _skill_bodies(agent: str) -> list[Path]:
    return sorted((_agent_dir(agent) / "skills-template").glob("*.md"))


def _lib_path(agent: str) -> Path:
    name = INSTALLER_SKILLS[agent]
    return _agent_dir(agent) / "skills-template" / f"{name}.lib.sh"


def _skill_body_path(agent: str) -> Path:
    name = INSTALLER_SKILLS[agent]
    return _agent_dir(agent) / "skills-template" / f"{name}.md"


def _fences_bullet_list(path: Path) -> list[str]:
    """Extract the FENCES bullet list from a skill body.

    Mirrors the author-side awk exactly (AM-10/D-1 + council-D4 + N6): lines
    after ``## Fences`` up to the next ``## `` heading, keeping bullet lines
    AND wrapped continuation lines, dropping blank lines — the byte-identical
    unit is the bullet list MODULO BLANK LINES.
    """
    lines: list[str] = []
    in_block = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Fences"):
            in_block = True
            continue
        if in_block and line.startswith("## "):
            break
        if not in_block:
            continue
        if line.startswith("- ") or (line[:1] in (" ", "\t") and line.strip()):
            lines.append(line)
    return lines


def _cardinal_count(agent: str) -> int:
    rule = _read(agent, "rule.md")
    return len(re.findall(r"^\d+\. \*\*", rule, re.MULTILINE))


# ---------------------------------------------------------------------------
# T1–T4 — the aegis tool matrix (AM-1/AM-2 corrected §B.2, authoritative)
# ---------------------------------------------------------------------------


class TestAegisToolMatrix:
    def test_aegis_allow_set_exact(self):  # T1
        allow = set(_meta("aegis")["tools"]["allow"])
        assert allow == set(AEGIS_ALLOW)

    def test_aegis_deny_set_exact(self):  # T2
        tools = _meta("aegis")["tools"]
        deny = set(tools["deny"])
        assert deny == set(AEGIS_DENY)
        assert "write_file" in deny and "edit_file" in deny
        # AM-2: project_create retracted with the project drop
        assert "project_create" not in deny
        # AM-3: no mcp_rpc deny entries — the closed allow is the fence
        assert not any("mcp_rpc" in d for d in deny)

    def test_aegis_deny_spawn_exact(self):  # T3
        assert _meta("aegis")["tools"]["deny_spawn"] == ["knowledge"]

    def test_aegis_team_members_exact(self):  # T4
        assert set(_meta("aegis")["team_members"]) == {"hacker", "cracker", "sweeper", "worker"}

    def test_aegis_innate_expansion_is_noop(self):
        """Both innate skills sit in the allow, so the expansion adds nothing
        and the pinned allow IS the effective surface (D3)."""
        meta = _meta("aegis")
        innate = set(meta.get("innate_skills") or [])
        assert innate <= set(meta["tools"]["allow"])


# ---------------------------------------------------------------------------
# T5–T7 — the children's leaf shape
# ---------------------------------------------------------------------------


class TestChildrenLeafShape:
    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_team_members_empty(self, agent):  # T5
        assert _meta(agent)["team_members"] == []

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_allow_non_empty(self, agent):  # T6 — the E3 fold
        allow = _meta(agent)["tools"]["allow"]
        assert isinstance(allow, list) and len(allow) > 0

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_deny_write_fence(self, agent):  # T7
        deny = set(_meta(agent)["tools"]["deny"])
        assert {"write_file", "edit_file"} <= deny

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_allow_set_exact(self, agent):
        assert set(_meta(agent)["tools"]["allow"]) == set(CHILD_ALLOW)


# ---------------------------------------------------------------------------
# T8–T11 — the family-wide fence invariants
# ---------------------------------------------------------------------------


class TestFamilyFenceInvariants:
    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_privileged_category_in_any_allow(self, agent):  # T8
        allow = set(_meta(agent)["tools"]["allow"])
        overlap = allow & PRIVILEGED_CATEGORIES
        assert not overlap, f"{agent} allow intersects privileged: {sorted(overlap)}"

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_job_category_anywhere(self, agent):  # T9
        assert "job" not in _meta(agent)["tools"]["allow"]

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_instance_read_or_ops(self, agent):  # T10
        allow = set(_meta(agent)["tools"]["allow"])
        assert not (allow & {"instance_read", "instance_ops"})

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_mcp_full_access(self, agent):  # T11 — the E4 fold
        tools = _meta(agent)["tools"]
        assert "mcp_full_access" not in tools
        assert tools.get("mcp_full_access") is None

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_watchover_block(self, agent):
        """AM-5/Q4: the watchover key is inert for every agent except the
        watcher — the family omits it from all four metas."""
        assert "watchover" not in _meta(agent)

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_hold_no_mcp(self, agent):
        """Children are egress-free at the TOOL level (no network tool grant
        — no mcp category; the CVE surface belongs to aegis only)."""
        assert "mcp" not in _meta(agent)["tools"]["allow"]


# ---------------------------------------------------------------------------
# T12 / T13 — the report-integrity fold and the children's exemption
# ---------------------------------------------------------------------------


class TestReportIntegrityFold:
    def test_aegis_carries_report_scrutiny_guidance(self):  # T12 (union, AM-9)
        surface = [_read("aegis", name) for name in PROMPT_SURFACE]
        surface.extend(p.read_text(encoding="utf-8") for p in _skill_bodies("aegis"))
        joined = "\n".join(surface)
        missing = [s for s in D_REQUIRED_SNIPPETS if s not in joined]
        assert not missing, f"aegis surface missing (d) guidance snippets: {missing}"

    def test_scrutiny_snippets_canonical_home_is_rule_md(self):
        """AM-9: the three snippets live in aegis's rule.md (the canonical
        home); the strategy skill carries NO report-delivery boilerplate."""
        rule = _read("aegis", "rule.md")
        for snippet in D_REQUIRED_SNIPPETS:
            assert snippet in rule, f"snippet {snippet!r} not in aegis rule.md"
        strategy = (_agent_dir("aegis") / "skills-template" / "aegis-strategy.md").read_text(encoding="utf-8")
        for snippet in D_REQUIRED_SNIPPETS:
            assert snippet not in strategy, (
                f"aegis-strategy.md carries report-delivery boilerplate {snippet!r} "
                f"(AM-9: the scrutiny snippets live in rule.md, not the seeded skill)"
            )

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_exempt_from_scrutiny_guidance(self, agent):  # T13
        """The children are exempt via the empty-team skip (NOT grandfathered)
        — modelled on the sketcher precedent, stricter than the house test:
        the child surface carries neither the (d) guidance nor any spawn
        fallback shape."""
        joined = _prompt_surface(agent)
        joined += "\n" + "\n".join(p.read_text(encoding="utf-8") for p in _skill_bodies(agent))
        for snippet in D_REQUIRED_SNIPPETS:
            assert snippet not in joined, f"{agent} carries {snippet!r} (empty-team leaf is exempt)"
        assert "spawn_instance" not in joined
        for pattern in ("spawn a `", "spawn another", "spawn `"):
            assert pattern not in joined, f"{agent} carries spawn-fallback shape {pattern!r}"


# ---------------------------------------------------------------------------
# T14–T18 — prose hygiene (guide §1/§3/§5/§10)
# ---------------------------------------------------------------------------


class TestProseHygiene:
    @pytest.mark.parametrize("agent", FAMILY)
    @pytest.mark.parametrize("token", SYSTEM_INTERNAL_TOKENS)
    def test_no_system_internals_in_prose(self, agent, token):  # T14
        files = [(agent, name, _read(agent, name)) for name in PROMPT_SURFACE]
        for path in _skill_bodies(agent):
            files.append((agent, path.name, _strip_frontmatter(path.read_text(encoding="utf-8"))))
        for _, name, prose in files:
            assert token.lower() not in prose.lower(), (
                f"system-internals token {token!r} found in {agent}/{name}"
            )

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_md_path_tokens_in_prose(self, agent):  # T15 (first closure class)
        files = [(agent, name, _read(agent, name)) for name in PROMPT_SURFACE]
        for path in _skill_bodies(agent):
            files.append((agent, path.name, _strip_frontmatter(path.read_text(encoding="utf-8"))))
        # Enumerated operational-path exemptions (T15's reconciliation list —
        # the lane claims NONE today; a survivor must be added here WITH its
        # operational justification or reworded).
        exemptions: tuple[str, ...] = ()
        for _, name, prose in files:
            hits = [h for h in re.findall(r"\S*\.md\b", prose) if h not in exemptions]
            assert hits == [], f".md path tokens in {agent}/{name}: {hits}"

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_bare_agents_prefix_cross_reference(self, agent):  # T15 (W6 second class)
        """BARE_AGENTS_PREFIX_RE semantics: a bare ``agents/`` prefix is a
        violation ONLY when it functions as a cross-reference to prompt
        sections; operational lowercase paths are exempt (§12.5 #0)."""
        pattern = re.compile(r"\bagents/[\s`*]*(?=[A-Z(])")
        files = [(agent, name, _read(agent, name)) for name in PROMPT_SURFACE]
        for path in _skill_bodies(agent):
            files.append((agent, path.name, _strip_frontmatter(path.read_text(encoding="utf-8"))))
        for _, name, prose in files:
            assert not pattern.search(prose), (
                f"bare agents/-prefix cross-reference in {agent}/{name}"
            )

    @pytest.mark.parametrize("agent", FAMILY)
    def test_rule_md_at_most_seven_cardinals(self, agent):  # T16
        count = _cardinal_count(agent)
        assert 1 <= count <= 7, f"{agent} rule.md has {count} cardinals (cap 7)"

    @pytest.mark.parametrize("agent", FAMILY)
    def test_cardinal_counts_expected_split(self, agent):
        """council-D7 pins: aegis/hacker = 7 content cardinals; cracker/
        sweeper = 6 content cardinals + 1 standing prohibition (7 numbered)."""
        assert _cardinal_count(agent) == 7

    @pytest.mark.parametrize("agent", FAMILY)
    def test_tone_block_in_soul(self, agent):  # T17
        soul = _read(agent, "soul.md")
        for heading in TONE_HEADINGS[agent]:
            assert heading in soul, f"{agent} soul.md missing tone part {heading!r}"

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_adapted_from_provenance(self, agent):  # T18
        files = [(agent, name, _read(agent, name)) for name in PROMPT_SURFACE]
        for path in _skill_bodies(agent):
            files.append((agent, path.name, _strip_frontmatter(path.read_text(encoding="utf-8"))))
        for _, name, prose in files:
            lowered = prose.lower()
            for marker in T18_MARKERS:
                assert marker not in lowered, f"{marker!r} in {agent}/{name}"
            assert not T18_JOB_SHAPE_RE.search(prose), (
                f"'per <agent> pattern, job <id>' shape in {agent}/{name}"
            )


# ---------------------------------------------------------------------------
# T19–T20 — org-chart discipline + the leader one-liner
# ---------------------------------------------------------------------------


class TestOrgChart:
    def test_aegis_skills_name_no_out_of_team_fallback(self):  # T19
        for path in _skill_bodies("aegis"):
            prose = _strip_frontmatter(path.read_text(encoding="utf-8"))
            assert "spawn" not in prose.lower(), (
                f"{path.name} mentions spawn — the strategy skill defines no fallbacks"
            )
            for peer in OUT_OF_TEAM_PEERS:
                if peer == "leader-as-spawn":
                    continue
                assert not re.search(rf"\b{re.escape(peer)}\b", prose), (
                    f"{path.name} names out-of-team peer {peer!r}"
                )

    def test_aegis_workflow_leader_is_caller_reference_only(self):
        """The hand-off names the leader as a CALLER reference (guide §8) and
        never as a spawn target."""
        workflow = _read("aegis", "workflow.md")
        assert "leader" in workflow  # the caller reference exists
        for pattern in ("spawn a `leader", "spawn `leader", "spawn a leader"):
            assert pattern not in workflow.lower()

    def test_leader_team_includes_aegis(self):  # T20
        leader = json.loads((AGENTS_DIR / "leader" / "meta.json").read_text(encoding="utf-8"))
        assert "aegis" in leader["team_members"]


# ---------------------------------------------------------------------------
# T21 / T22 — seeded-skill discipline + version consistency
# ---------------------------------------------------------------------------


class TestSkillDiscipline:
    def test_only_aegis_has_manifest_with_one_entry(self):  # T21
        manifests = list(AGENTS_DIR.glob("*/skill-set.yaml"))
        family_manifests = [p for p in manifests if p.parent.name in FAMILY]
        assert [p.parent.name for p in family_manifests] == ["aegis"]
        entries = yaml.safe_load(family_manifests[0].read_text(encoding="utf-8"))["skills"]
        assert [e["name"] for e in entries] == ["aegis-strategy"]
        assert entries[0]["auto_load"] is True

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_children_have_no_manifest(self, agent):  # T21 (file-invoked mode)
        assert not (_agent_dir(agent) / "skill-set.yaml").exists()

    def test_installer_skills_in_no_manifest(self):  # T21 (the bank gate)
        for manifest in AGENTS_DIR.glob("*/skill-set.yaml"):
            text = manifest.read_text(encoding="utf-8")
            for skill in INSTALLER_SKILLS.values():
                assert skill not in text, (
                    f"{manifest} lists installer skill {skill!r} — file-invoked skills "
                    f"appear in no manifest (AM-7 manifest-gating)"
                )

    def test_aegis_strategy_frontmatter_has_no_include(self):  # T21 (AM-7)
        text = (_agent_dir("aegis") / "skills-template" / "aegis-strategy.md").read_text(encoding="utf-8")
        frontmatter = text.split("---")[1]
        assert "include" not in frontmatter, (
            "aegis-strategy declares include: — the include resolver's cross-agent "
            "by-name fallback would inline another agent's bank row"
        )

    def test_skill_set_entries_have_templates_and_match_versions(self):  # T22
        manifest = yaml.safe_load((_agent_dir("aegis") / "skill-set.yaml").read_text(encoding="utf-8"))
        for entry in manifest["skills"]:
            template = _agent_dir("aegis") / "skills-template" / f"{entry['name']}.md"
            assert template.is_file(), f"missing template for {entry['name']}"
            front = template.read_text(encoding="utf-8").split("---")[1]
            front_version = yaml.safe_load(front)["version"]
            assert str(front_version) == str(entry["version"]), (
                f"version drift: manifest {entry['version']} vs frontmatter {front_version}"
            )


# ---------------------------------------------------------------------------
# T23 — the identical FENCES block (AM-10 definition)
# ---------------------------------------------------------------------------


class TestInstallerFencesBlockIdentical:
    def test_md_fences_bullet_lists_byte_identical(self):  # T23 PRIMARY
        """The three .md FENCES bullet lists are byte-identical MODULO BLANK
        LINES (N6), bullet list + wrapped continuations (council-D4),
        checksum bullet IN, per-tool content OUT (AM-10)."""
        lists = [_fences_bullet_list(_skill_body_path(a)) for a in CHILDREN]
        assert all(len(lst) > 0 for lst in lists), "empty FENCES bullet list"
        assert lists[0] == lists[1] == lists[2], (
            "FENCES bullet lists diverge across the three installer bodies"
        )

    def test_fences_checksum_bullet_present(self):
        """The C3 checksum bullet is inside the byte-identical set."""
        bullets = _fences_bullet_list(_skill_body_path("hacker"))
        assert any("sha256" in b and "before any promotion" in b for b in bullets)

    @pytest.mark.parametrize("agent", CHILDREN)
    @pytest.mark.parametrize("keyword", LIB_FENCES_KEYWORDS)
    def test_lib_fences_header_mentions_keywords(self, agent, keyword):  # T23 SECONDARY
        lib = _lib_path(agent).read_text(encoding="utf-8")
        assert keyword in lib, f"{_lib_path(agent).name} FENCES header missing keyword {keyword!r}"

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_fences_heading_is_neutral(self, agent):
        """AM-8: the .md FENCES heading carries no provenance parenthetical."""
        body = _skill_body_path(agent).read_text(encoding="utf-8")
        assert not re.search(r"^## Fences \(.*\)\s*$", body, re.MULTILINE), (
            f"{agent} FENCES heading carries a provenance parenthetical (AM-8)"
        )


# ---------------------------------------------------------------------------
# T24 — the taxonomy + finding record live ONCE (canonical home)
# ---------------------------------------------------------------------------


class TestSeverityTaxonomyDeclared:
    def test_strategy_declares_levels_and_fields(self):  # T24
        strategy = _strip_frontmatter(
            (_agent_dir("aegis") / "skills-template" / "aegis-strategy.md").read_text(encoding="utf-8")
        )
        for level in SEVERITY_LEVELS:
            assert f"**{level}**" in strategy, f"severity level {level!r} not declared"
        for field in FINDING_RECORD_FIELDS:
            assert re.search(rf"^{field}\s+\S", strategy, re.MULTILINE), (
                f"finding-record field {field!r} not declared in the record block"
            )

    def test_finding_record_block_has_single_home(self):
        """The canonical record block (its distinctive literal) appears in
        the strategy skill and NOWHERE else in the family's surfaces — the
        prompt files link by section name and do not restate it (guide §2)."""
        surfaces = {
            (a, name): _read(a, name) for a in FAMILY for name in PROMPT_SURFACE
        }
        for a in FAMILY:
            for path in _skill_bodies(a):
                surfaces[(a, path.name)] = path.read_text(encoding="utf-8")
        homes = [k for k, v in surfaces.items() if FINDING_RECORD_BLOCK_LITERAL in v]
        assert homes == [("aegis", "aegis-strategy.md")], (
            f"finding-record block found outside its canonical home: {homes}"
        )


# ---------------------------------------------------------------------------
# T25 — worker untouched (mechanical proof of "ZERO changes to worker")
# ---------------------------------------------------------------------------


class TestWorkerUntouched:
    def test_worker_untouched(self):  # T25
        result = subprocess.run(
            ["git", "diff", "--name-only", LANE_BASE_COMMIT, "--", "agents/worker/"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"git diff failed: {result.stderr[-500:]}"
        assert result.stdout.strip() == "", (
            f"agents/worker/ changed vs base: {result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# T26 / T27 — queue + model assignments
# ---------------------------------------------------------------------------


class TestAssignments:
    @pytest.mark.parametrize("agent", FAMILY)
    def test_default_queue_parallel(self, agent):  # T26
        assert _meta(agent)["default_queue"] == "system_parallel_queue"

    def test_model_assignments(self):  # T27
        assert _meta("aegis")["llm_model"] == "agentic"
        for child in CHILDREN:
            assert _meta(child)["llm_model"] == "coding"


# ---------------------------------------------------------------------------
# T28 + the fail-open guard — venv-bound registry legs (subprocess probes;
# BOTH carry the registry_scan name so the bare-python subset excludes them)
# ---------------------------------------------------------------------------

_REGISTRY_PROBE = (
    "import json\n"
    "from pathlib import Path\n"
    "from daemon.registry import ToolFilter, get_registry\n"
    "reg = get_registry()\n"
    "for aid in ('aegis', 'hacker', 'cracker', 'sweeper'):\n"
    "    assert reg.exists(aid), f'{aid} not in registry'\n"
    "    meta = json.loads(Path(f'agents/{aid}/meta.json').read_text())\n"
    "    tf = ToolFilter.model_validate(meta['tools'])\n"
    "    assert set(tf.allow or []) == set(meta['tools']['allow']), aid\n"
    "    assert set(tf.deny or []) == set(meta['tools']['deny']), aid\n"
    "    assert set(tf.deny_spawn or []) == set(meta['tools'].get('deny_spawn', [])), aid\n"
    "print('registry probe OK')\n"
)


class TestRegistryScan:
    def test_registry_scan_discovers_family(self):  # T28
        """Boot-scan proof: the real registry facade resolves all four ids
        (the tier-1 boot-scan analogue of the sketcher precedent)."""
        result = subprocess.run(
            [sys.executable, "-c", _REGISTRY_PROBE],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, (
            f"registry scan probe failed:\n{result.stderr[-2000:]}"
        )

    def test_registry_scan_tools_block_round_trips(self):
        """Fail-open guard (R2/A9): each of the four metas' tools block
        round-trips through the registry's parser — a malformed block parses
        to None = ALL TOOLS ALLOWED, which on a security agent is a privilege
        escalation, not a lint error. The probe asserts the parsed
        allow/deny/deny_spawn equal the authored sets exactly."""
        result = subprocess.run(
            [sys.executable, "-c", _REGISTRY_PROBE],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, (
            f"tools round-trip probe failed:\n{result.stderr[-2000:]}"
        )

# ---------------------------------------------------------------------------
# T29 — the legality-cardinal pin matrix (Blocker 4) — 20 cells, all positive
# ---------------------------------------------------------------------------


class TestLegalityCardinalPinMatrix:
    @pytest.mark.parametrize("agent,family", LEGALITY_CELLS)
    def test_legality_cardinal_pinned_verbatim(self, agent, family):  # T29
        """Each guardrail family is pinned VERBATIM (case-sensitive) in the
        persona's own rule.md, in its per-persona form; deleting or mutating
        the cardinal reds the cell (the approver's acceptance test — deleting
        hacker's 'no external targets, ever' cardinal — is the mutation leg)."""
        rule = _read(agent, "rule.md")
        for anchor in _LEGALITY_MATRIX[(agent, family)]:
            assert anchor in rule, (
                f"{agent} rule.md missing {family!r} anchor {anchor!r} "
                f"(guardrail family not verbatim-pinned in the persona)"
            )

    def test_matrix_is_complete(self):
        """20 asserted cells (5 families x 4 personas), every cell positive
        (R1 leader ruling 2026-11-04)."""
        families = {fam for _, fam in LEGALITY_CELLS}
        agents = {ag for ag, _ in LEGALITY_CELLS}
        assert len(LEGALITY_CELLS) == 20
        assert families == {
            "read-only-on-targets", "sandbox-zero-external", "sample-data-only",
            "findings-private", "white-hat-ethos",
        }
        assert agents == set(FAMILY)

    def test_hacker_external_targets_anchor_deletion_reds(self):
        """Self-proof the matrix bites: the hacker f2 anchor really is inside
        a numbered cardinal (so cardinal deletion removes it).

        The assertion is on the anchor's OWN LINE — the earlier form matched
        the FIRST line of the anchor's *block*, and the hacker's cardinals 1
        and 2 share one block (they are consecutive lines with no blank line
        between them), so the match landed on cardinal 1 and the mutation
        "2. **...**" -> "XX. **...**" left the leg green.

        The anchor is read from the matrix itself, so the self-proof cannot
        drift away from the cell it is proving.
        """
        anchor = _LEGALITY_MATRIX[("hacker", "sandbox-zero-external")][0]
        lines = [
            ln for ln in _read("hacker", "rule.md").splitlines() if anchor in ln
        ]
        assert len(lines) == 1, (
            f"hacker f2 anchor {anchor!r} must occur on exactly ONE line "
            f"(deletion of that line must red the cell), got {len(lines)}"
        )
        assert re.match(r"^\d+\.\s\*\*", lines[0]), (
            "hacker f2 anchor not on a numbered cardinal line — deletion would not red"
        )


# ---------------------------------------------------------------------------
# T30 — the installer-guard pin pack (Blocker 9)
# ---------------------------------------------------------------------------

_NO_INVOCATION_RE = re.compile(r"\b(docker|apt|apt-get|sudo)\b", re.IGNORECASE)


class TestInstallerFencesAndLibraryContract:
    @pytest.mark.parametrize("agent", CHILDREN)
    @pytest.mark.parametrize("bullet", EXPECTED_FENCES_BULLETS)
    def test_fences_bullet_present_in_each_body(self, agent, bullet):  # T30(a)
        """Per-bullet presence pin against a FIXED expected list: deletion or
        mutation of any FENCES bullet REDS in EACH of the three bodies even
        when cross-body byte-identity survives (the B9 uniform-deletion
        false-green class)."""
        body = _skill_body_path(agent).read_text(encoding="utf-8")
        assert bullet in body, (
            f"{agent} skill body lost FENCES bullet {bullet[:60]!r} "
            f"(per-bullet presence pin — uniform deletion across all three bodies reds here)"
        )

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_checksum_compare_is_invoked(self, agent):  # T30(b)
        """The sha256 compare is WIRED into the install flow: the verify
        function is defined, and EVERY promotion of a fetched asset into the
        toolchain bin is preceded by a verify call.

        A generic "some call site exists" check cannot tell a gated promotion
        from an ungated one — a library with two assets and one verify call
        passes it — so the check is PER-PROMOTION: the checksum-compare must
        precede every promotion.

        This leg is STATIC (source order). The EXECUTED behaviour is pinned
        separately by ``test_checksum_compare_refuses_unset_pin`` below, so
        neither leg over-claims the other's evidence."""
        lib = _lib_path(agent).read_text(encoding="utf-8")
        assert re.search(r"^aegis_verify_sha256\(\)", lib, re.MULTILINE), (
            "aegis_verify_sha256 not defined"
        )
        code = [
            (i, ln) for i, ln in enumerate(lib.splitlines())
            if not ln.lstrip().startswith("#")
        ]
        verifies = [
            i for i, ln in code if re.search(r"\baegis_verify_sha256\s+\"", ln)
        ]
        assert verifies, "aegis_verify_sha256 is never called (not executed)"
        promotions = [
            i for i, ln in code
            if re.search(r"\b(cp|chmod)\s+\S+.*AEGIS_TOOLBENCH_ROOT/bin/", ln)
        ]
        assert promotions, (
            f"{agent} library promotes no fetched asset — the promotion shape changed"
        )
        ungated = [i for i in promotions if not any(v < i for v in verifies)]
        assert not ungated, (
            f"{agent} promotes an asset with no preceding aegis_verify_sha256 "
            f"call (line(s) {sorted(u + 1 for u in ungated)}) — the checksum "
            "gate does not cover every promotion"
        )

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_checksum_compare_refuses_unset_pin(self, agent, tmp_path):
        """T30(b) EXECUTED leg: the verify function really runs — sourcing the
        library and calling it with the placeholder pin the library itself
        ships returns rc=1 and emits the HARD_BLOCKER refusal.

        Nothing is downloaded, no install runs, and no release asset is
        involved: the only command executed is the verify function against a
        scratch file. HOME is redirected into the sandbox so the library's
        env-overridable roots resolve there and nothing touches the real
        user tree.
        """
        lib = _lib_path(agent)
        asset = tmp_path / "asset.bin"
        asset.write_bytes(b"not-a-release-asset\n")
        probe = tmp_path / "verify_probe.sh"
        probe.write_text(
            '. "$1"\n'
            'aegis_verify_sha256 "$2" "pin-before-first-install"\n',
            encoding="utf-8",
        )
        scratch_home = tmp_path / "home"
        scratch_home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(scratch_home)
        for _k in [k for k in env if k.startswith("POSTGRES_")]:
            env.pop(_k, None)
        result = subprocess.run(
            ["/bin/sh", str(probe), str(lib), str(asset)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert result.returncode == 1, (
            f"{agent} aegis_verify_sha256 did not refuse the unset pin "
            f"(rc={result.returncode}); stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert "HARD_BLOCKER" in result.stdout and "missing sha256 pin" in result.stdout, (
            f"{agent} unset-pin refusal did not emit the HARD_BLOCKER message: "
            f"stdout={result.stdout!r}"
        )

    def test_corpus_path_guard_is_invoked(self):
        """The corpus-path guard is ENFORCED, not merely defined: the
        library's only sanctioned corpus write calls it. A definition with
        zero call sites is dead code, and the skill body's corpus prose would
        then be claiming a runtime gate that does not exist."""
        lib = _lib_path("cracker").read_text(encoding="utf-8")
        assert re.search(r"^aegis_write_synthetic_corpus\(\)", lib, re.MULTILINE), (
            "aegis_write_synthetic_corpus not defined"
        )
        code_lines = [
            ln for ln in lib.splitlines() if not ln.lstrip().startswith("#")
        ]
        call_sites = [
            ln for ln in code_lines
            if re.search(r"aegis_corpus_path_ok\s+\"\$", ln)
        ]
        assert call_sites, (
            "aegis_corpus_path_ok is defined but never invoked — the corpus "
            "write path is ungated"
        )

    def test_corpus_write_is_gated_by_the_sandbox_root(self, tmp_path):
        """The guarded corpus write really refuses an out-of-sandbox path
        (rc=1, HARD_BLOCKER, nothing created) and really writes an
        in-sandbox path.

        The library is SOURCED in a scratch HOME; the only command executed
        is the lane's own scratch generator — no network, no install, no
        release asset. An out-of-sandbox GENERATOR is refused too: a corpus
        path alone cannot smuggle in material that did not come from the
        lane's own generator.
        """
        lib = _lib_path("cracker")
        sandbox = tmp_path / "sandbox"
        (sandbox / "builds").mkdir(parents=True)
        (sandbox / "corpora").mkdir(parents=True)
        generator = sandbox / "builds" / "gen_corpus.sh"
        generator.write_text(
            "#!/bin/sh\nprintf 'synthetic-hash\\n'\n", encoding="utf-8"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_generator = outside / "gen_corpus.sh"
        outside_generator.write_text(
            "#!/bin/sh\nprintf 'real-world-hash\\n'\n", encoding="utf-8"
        )
        probe = tmp_path / "corpus_probe.sh"
        probe.write_text(
            '. "$1"\n'
            'echo "CASE=inside"\n'
            'if aegis_write_synthetic_corpus "$CORPUS_IN" "$SANDBOX" "$GEN_IN"; '
            'then echo "RC=0"; else echo "RC=$?"; fi\n'
            'echo "CASE=outside"\n'
            'if aegis_write_synthetic_corpus "$CORPUS_OUT" "$SANDBOX" "$GEN_IN"; '
            'then echo "RC=0"; else echo "RC=$?"; fi\n'
            'echo "CASE=badgen"\n'
            'if aegis_write_synthetic_corpus "$CORPUS_BADGEN" "$SANDBOX" "$GEN_OUT"; '
            'then echo "RC=0"; else echo "RC=$?"; fi\n',
            encoding="utf-8",
        )
        scratch_home = tmp_path / "home"
        scratch_home.mkdir()
        corpus_in = sandbox / "corpora" / "synth.hashes"
        corpus_out = outside / "synth.hashes"
        corpus_badgen = sandbox / "corpora" / "badgen.hashes"
        env = dict(os.environ)
        env["HOME"] = str(scratch_home)
        env["SANDBOX"] = str(sandbox)
        env["GEN_IN"] = str(generator)
        env["GEN_OUT"] = str(outside_generator)
        env["CORPUS_IN"] = str(corpus_in)
        env["CORPUS_OUT"] = str(corpus_out)
        env["CORPUS_BADGEN"] = str(corpus_badgen)
        for _k in [k for k in env if k.startswith("POSTGRES_")]:
            env.pop(_k, None)
        result = subprocess.run(
            ["/bin/sh", str(probe), str(lib)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        # split the probe output PER CASE — a refusal's HARD_BLOCKER must be
        # the one that refused THAT case, not some other case's message
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in result.stdout.splitlines():
            if line.startswith("CASE="):
                current = line[len("CASE="):]
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        assert set(sections) == {"inside", "outside", "badgen"}, (
            f"corpus probe produced no per-case output: {result.stdout!r} {result.stderr!r}"
        )
        assert "RC=0" in sections["inside"], (
            f"in-sandbox corpus write was refused: {sections['inside']!r}"
        )
        assert "RC=1" in sections["outside"], (
            f"out-of-sandbox corpus path was NOT refused: {sections['outside']!r}"
        )
        assert "RC=1" in sections["badgen"], (
            f"out-of-sandbox corpus generator was NOT refused: {sections['badgen']!r}"
        )
        assert "HARD_BLOCKER" in "\n".join(sections["outside"]), (
            f"out-of-sandbox corpus refusal did not emit HARD_BLOCKER: "
            f"{sections['outside']!r}"
        )
        assert "HARD_BLOCKER" in "\n".join(sections["badgen"]), (
            f"out-of-sandbox generator refusal did not emit HARD_BLOCKER: "
            f"{sections['badgen']!r}"
        )
        assert corpus_in.is_file(), "in-sandbox corpus was not written"
        assert "synthetic-hash" in corpus_in.read_text(encoding="utf-8")
        assert not corpus_out.exists(), (
            "out-of-sandbox corpus file was created — the gate did not hold"
        )
        assert not corpus_badgen.exists(), (
            "corpus was written by an out-of-sandbox generator — the gate did not hold"
        )

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_no_docker_apt_sudo_invocations(self, agent):  # T30(b)
        """No docker/apt/sudo token in any non-comment line of the library.
        (The FENCES comment header may NAME the bans — that is the contract
        text; executable code may never carry the tokens.)"""
        code_lines = [
            line for line in _lib_path(agent).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        offenders = [ln for ln in code_lines if _NO_INVOCATION_RE.search(ln)]
        assert offenders == [], f"{_lib_path(agent).name} carries forbidden invocations: {offenders[:3]}"

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_lock_release_is_trap_based(self, agent):  # T30(b)
        lib = _lib_path(agent).read_text(encoding="utf-8")
        assert re.search(r"trap\s+'aegis_release_install_lock'", lib), (
            "lock release not trap-based — it must run on EVERY exit path"
        )

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_attempt_cap_present(self, agent):  # T30(b)
        lib = _lib_path(agent).read_text(encoding="utf-8")
        assert "AEGIS_ATTEMPT_CAP=2" in lib
        assert "aegis_attempt_allowed" in lib

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_readiness_probe_three_state(self, agent):
        lib = _lib_path(agent).read_text(encoding="utf-8")
        assert "aegis_readiness_probe" in lib
        assert "return 2" in lib and "return 1" in lib and "return 0" in lib

    @pytest.mark.parametrize("agent", CHILDREN)
    def test_reversal_section_present(self, agent):
        lib = _lib_path(agent).read_text(encoding="utf-8")
        assert "aegis_reversal()" in lib


# ---------------------------------------------------------------------------
# AM-6 doctrine pins (α: credential rule; β: data-never-instructions)
# ---------------------------------------------------------------------------


class TestAegisDoctrinePins:
    def test_alpha_no_credential_tasking(self):
        """(α) No aegis surface instructs a lane to read credentials/env
        values/secrets, and findings name a secret class, never a value."""
        surface = _prompt_surface("aegis")
        surface += "\n" + "\n".join(
            _strip_frontmatter(p.read_text(encoding="utf-8")) for p in _skill_bodies("aegis")
        )
        assert "never task any lane with reading credentials" in surface.lower() or \
               "never instruct any lane to read credentials" in surface.lower() or \
               "never task any lane with reading credentials, environment values, or secrets" in surface.lower(), (
            "AM-6 α pin: the credential-rule sentence is absent from the aegis surface"
        )
        assert "names a secret class, never a value" in surface.lower(), (
            "AM-6 α pin: the 'secret class, never a value' clause is absent"
        )

    def test_beta_fetched_content_is_data(self):
        """(β) Fetched external content is DATA, never instructions (AM-3)."""
        strategy = (_agent_dir("aegis") / "skills-template" / "aegis-strategy.md").read_text(encoding="utf-8")
        assert "DATA, never instructions" in strategy

    def test_am13_honest_limitation_sentences(self):
        """AM-13: the honest-limitation sentence appears in the aegis persona
        AND in the report template's provenance note."""
        rule = _read("aegis", "rule.md")
        assert "discipline, not a boundary" in rule
        strategy = (_agent_dir("aegis") / "skills-template" / "aegis-strategy.md").read_text(encoding="utf-8")
        assert "read-only-on-target is discipline, not a boundary" in strategy

    def test_am4_privacy_honesty_sentence(self):
        """AM-4: the honest sentence appears wherever the storage choice is
        stated (aegis workflow + strategy skill)."""
        assert "privacy of the findings is discipline, not a boundary" in _read("aegis", "workflow.md")
        strategy = (_agent_dir("aegis") / "skills-template" / "aegis-strategy.md").read_text(encoding="utf-8")
        assert "privacy of the findings is discipline, not a boundary" in strategy


# ---------------------------------------------------------------------------
# Baseline arithmetic (D-4/(b) + AM-11): file EXISTS + exactly 177 rows
# ---------------------------------------------------------------------------

EXPECTED_BASELINE_ROWS = 177


class TestBaselineArithmetic:
    def test_baseline_file_exists(self):
        assert BASELINE_PATH.is_file(), (
            "agents/.prompt-artifacts.sha256 missing — the generator's --check exits 0 "
            "even when the file is ABSENT (AM-11), so rc=0 alone proves nothing"
        )

    def test_baseline_row_count_exact(self):
        lines = [ln for ln in BASELINE_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == EXPECTED_BASELINE_ROWS, (
            f"baseline rows = {len(lines)}, expected {EXPECTED_BASELINE_ROWS} "
            f"(161 base + 4 agents x 4 prompt files — a fifth prompt file or a "
            f"missing one moves this number)"
        )

    @pytest.mark.parametrize("agent", FAMILY)
    def test_each_new_agent_contributes_exactly_four_rows(self, agent):
        lines = [ln for ln in BASELINE_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
        agent_rows = [ln for ln in lines if f"agents/{agent}/" in ln]
        assert len(agent_rows) == 4, (
            f"{agent} contributes {len(agent_rows)} baseline rows (expected exactly 4 — "
            f"no memory/growth/tools/skill legacy artifact may ride along, AM-11)"
        )

    @pytest.mark.parametrize("agent", FAMILY)
    def test_no_legacy_artifact_names_on_new_agents(self, agent):
        """AM-11: none of the four legacy artifact names exists on a new agent
        (each would silently move the 177 count)."""
        for name in ("memory.md", "growth.md", "tools.md", "skill.md"):
            assert not (_agent_dir(agent) / name).exists(), f"{agent}/{name} must not exist (AM-11)"
