#!/usr/bin/env python3
"""Sonic Team (ENSEMBLE-31, COMBINED v1.1+v1.2) — agent-definition contract checker.

Companion checker for pack ``sonic_agent_defs_unit_test`` (see the .sh
sibling for the invocation contract).  Implements the phase-2 spec
(``phase2-test-pack-spec.md``) assertions A1-A11 PLUS the combined-rework
pins A12-A14, each derived from the FROZEN blocks of the combined spec
(``phase1-implementation-spec-v1.1.md``) — the combined spec wins on any
mismatch (spec §7-4 VERIFY-AT-IMPL doctrine).

Combined shape (Emperor ruling 2026-11-13: v1.2 merged into v1.1): FIVE
sonic agents — the new ``sonic-developer`` layer plans/splits/dispatches/
merges and authors HANDOFF-v2; ``sonic-coder`` is a one-shot MODULE
executor (source exploration expected, RAG excluded, commits its module
branch, returns a compact module report); ``sonic-reviewer`` and
``sonic-tester`` operate at INTEGRATION level (HANDOFF-v2 + base..merged).

Three layers:

  --static      stdlib-only contract checks A1-A14 against the real tree.
                NO daemon import (mirrors the
                ``scripts/protection/generate_prompt_artifact_baseline.py``
                doctrine: a bare host python3 must not drag the langchain
                stack).  Daemon CONSTANTS that the assertions need
                (``TOOL_REQUIRED_AGENTS``, ``_ALLOWED_MODELS_DEFAULT``) are
                AST-extracted from source, never imported.

  --self-test   the mutation matrix: every assertion is (a) proven GREEN on
                a good in-memory fixture and (b) proven RED on that fixture
                with ONE planted counter-mutation (spec §3 counter-mutation
                column; POK ``mutation-01-guards-need-counter-mutation``).
                A leg that stays green under its own counter-mutation is a
                FAIL of the self-test, never a skip.  NO repo bytes are
                touched (in-memory fixtures only — precedent: the header of
                ``tests/unit/test_ari_pm_terminology_pins.py``).

  --real        the REAL resolver layer: ``get_registry()`` +
                ``_check_team_membership`` (imports daemon — run under
                ``uv run``).  A2-real/A3-real drive the same expectations
                through the production authorization gate.  If the venv or
                an import fails this layer FAILS LOUDLY — skipping a
                resolver leg is the aegis blind-spot class.

Frozen strings are restated from phase1-implementation-spec-v1.1.md §3/§4
and must stay character-for-character identical (POK
``plan-body-ledger-agreement``).

Implementation notes (disclosed):
- A11/A12/A13 anchor matching whitespace-normalizes both haystack and
  needle before containment: the landed prompts wrap sentences across
  markdown line breaks, so raw substring matching would false-RED on
  verbatim-spec text.  Normalized matching is insensitive to re-wraps.
- A10 matches by containment (grep -F semantics): the landed escalation
  line sits inside Markdown backticks.
- A9 (steady team untouched) judges the UNION of the committed delta
  (``git diff --name-only <v1-base>..HEAD -- agents/``) and the
  working-tree status (``git status --porcelain -- agents/``): pre-commit
  lanes carry their delta in the working tree; post-merge lanes carry it
  in commits.  The pack stays valid in both states by construction
  (ancestry drift pin, not a branch-name pin).
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# FROZEN constants — single source: phase1-implementation-spec-v1.1.md §1-§4.
# The combined spec wins on any mismatch (phase2 spec §7-4 VERIFY-AT-IMPL).
# ---------------------------------------------------------------------------

SONIC_AGENTS = (
    "sonic-leader",
    "sonic-developer",
    "sonic-coder",
    "sonic-reviewer",
    "sonic-tester",
)

# ---- FROZEN [sonic-tools-v2] (combined spec §1.2/§1.3/§2.1 + Emperor ruling
# 2026-11-13: knowledge granted to sonic-developer + sonic-leader ONLY;
# coder/reviewer/tester ZERO-KNOWLEDGE — the coder grant is REVERSED; coder
# keeps SOURCE exploration with file tools, excluded ONLY from the RAG call)
# ----
FROZEN_TEAMS = {
    "sonic-leader": ["sonic-developer", "sonic-coder", "sonic-reviewer",
                     "sonic-tester"],
    "sonic-developer": ["sonic-coder"],
    "sonic-coder": [],
    "sonic-reviewer": [],
    "sonic-tester": [],
}

FROZEN_TOOLS = {
    "sonic-leader": {
        "allow": ["instance", "bash", "filesystem", "time", "self", "help",
                  "context", "shared_meta_kv", "midflight", "knowledge", "pok",
                  "nest-report"],
        "deny": ["edit_file", "write_file"],
    },
    "sonic-developer": {
        "allow": ["instance", "knowledge", "bash", "proc", "filesystem", "todo",
                  "shared_meta_kv", "time", "self", "help", "context", "pok",
                  "nest-report"],
        "deny": ["edit_file", "write_file"],
    },
    "sonic-coder": {
        "allow": ["bash", "proc", "filesystem", "time", "self", "help",
                  "context", "shared_meta_kv", "pok", "nest-report"],
        "deny": [],
    },
    "sonic-reviewer": {
        "allow": ["bash", "filesystem", "time", "self", "help", "context",
                  "pok", "nest-report"],
        "deny": ["proc", "instance", "service", "midflight", "shared_meta_kv",
                 "infra", "mcp", "secrets", "edit_file", "write_file"],
    },
    "sonic-tester": {
        "allow": ["bash", "proc", "filesystem", "time", "self", "help",
                  "context", "pok", "nest-report"],
        "deny": ["instance", "service", "midflight", "shared_meta_kv",
                 "infra", "mcp", "secrets"],
    },
}
# ---- END FROZEN [sonic-tools-v2] ----

# Effective spawn sets (combined spec §9): leader = its 4 sonic team members
# PLUS the two knowledge-lane support agents (TOOL_REQUIRED_AGENTS maps
# "knowledge" -> ["explorer", "kb-writer"]); developer = sonic-coder PLUS the
# same two implicit members (its ONE explore at plan-open); coder/reviewer/
# tester = EMPTY (no team, no knowledge-implied members — the v1.1 coder
# grant is reversed).
EXPECTED_EFFECTIVE = {
    "sonic-leader": {"sonic-developer", "sonic-coder", "sonic-reviewer",
                     "sonic-tester", "explorer", "kb-writer"},
    "sonic-developer": {"sonic-coder", "explorer", "kb-writer"},
    "sonic-coder": set(),
    "sonic-reviewer": set(),
    "sonic-tester": set(),
}

# ---- FROZEN [sonic-blast-radius-v1] (combined spec §3.5) — 14 globs, exact
# order; the block lives in THREE workflow files (leader, coder, developer)
# and must be byte-identical across all three ----
FROZEN_GLOBS = [
    "daemon/graph.py",
    "daemon/manager.py",
    "daemon/config.py",
    "daemon/api.py",
    "daemon/services/**",
    "daemon/repositories/**",
    "daemon/tools/**",
    "daemon/migrations/versions/**",
    "daemon/sources/**",
    "daemon/mcp/**",
    "daemon/routers/**",
    "scripts/**",
    "agents/**/*.md",
    "agents/.prompt-artifacts.sha256",
]
BLAST_MARKER_OPEN = "# ---- FROZEN [sonic-blast-radius-v1] ----"
BLAST_MARKER_CLOSE = "# ---- END FROZEN [sonic-blast-radius-v1] ----"
BLAST_WORKFLOW_FILES = (
    "agents/sonic-leader/workflow.md",
    "agents/sonic-developer/workflow.md",
    "agents/sonic-coder/workflow.md",
)

# ---- FROZEN [sonic-handoff-v2] (combined spec §4.1) — Title-Case
# authoritative; authorship = sonic-developer (integration-level author) ----
HANDOFF_HEADERS = [
    "## Files touched across modules",
    "## What changed",
    "## What is UNTESTED",
    "## Known risks",
    "## How to verify",
    "## Bash-raises used",
    "## Blast-radius self-assessment vs predicate",
]
HANDOFF_PATH_PREFIX = "HANDOFF_PATH = HANDOFF.md"
HANDOFF_FILES = (
    "agents/sonic-developer/workflow.md",
    "agents/sonic-reviewer/workflow.md",
    "agents/sonic-tester/workflow.md",
)

# ---- FROZEN [sonic-module-report-v2] (combined spec §3.4) — the coder's
# compact module report shape (a MESSAGE, never a file) ----
MODULE_REPORT_PREFIX = "MODULE-REPORT: <module-slug>"
MODULE_REPORT_KEYS = (
    "branch: <branch-name> @ <tip-sha>",
    "files:",
    "changed:",
    "self-test:",
    "untested:",
    "risks:",
    "raises:",
    "blast:",
)

# ---- FROZEN [sonic-coder-discipline-v2] (combined spec §3.3) — the two
# pinned coder sentences; BOTH shapes in rule.md AND workflow.md ----
CODER_SOURCE_EXPLORE_SENTENCE = (
    "Source exploration is EXPECTED: it is a core part of module work, "
    "not ceremony, and it is unbounded within your module scope."
)
CODER_RAG_EXCLUSION_SENTENCE = (
    "The ONE exclusion is the RAG tool: no `explore()` call — the "
    "developer's plan carries the stored experience; explore the SOURCE "
    "freely with your file tools."
)
CODER_NO_KNOWLEDGE_MARKER = "NO `knowledge`"
CODER_TOOLS_NOTE = "agents/sonic-coder/tools_note.md"
CODER_DISCIPLINE_FILES = (
    "agents/sonic-coder/rule.md",
    "agents/sonic-coder/workflow.md",
)

# ---- FROZEN developer never-experience boundary (combined spec §2.3 +
# D-G/D-B rationale) — one pinned sentence per artifact ----
DEV_RULE_NEVER_EXPERIENCE = (
    "You NEVER call `experience()` — the leader owns the single knowledge "
    "write at commission end."
)
DEV_WORKFLOW_EXPLORE_BOUND = (
    "The bound is hard: exactly ONE `explore()` per commission, at "
    "plan-open — no other knowledge call, and NEVER `experience()`."
)
DEV_TOOLSNOTE_NEVER_EXPERIENCE = (
    "`experience()` is NOT yours — the leader owns the single knowledge "
    "write at commission end"
)
DEV_RULE = "agents/sonic-developer/rule.md"
DEV_WORKFLOW = "agents/sonic-developer/workflow.md"
DEV_TOOLS_NOTE = "agents/sonic-developer/tools_note.md"

# ---- FROZEN [sonic-fast-path-v1] (combined spec §7 / D-E) — the gate line
# pinned in the leader's rule.md AND workflow.md ----
FAST_PATH_GATE_LINE = (
    "FAST-PATH GATE: single module, single coder, no new agent dir, no "
    "cross-module risk — anything larger goes through sonic-developer."
)

# ---- FROZEN [sonic-escalation-v1] (phase1 §4.0) ----
ESCALATION_LINE = (
    "ESCALATION: <path> matches <glob> — returning commission to Ari "
    "(steady-team routing)."
)

# Ari note (phase1 §5.1/§5.2).
ARI_RULE = "agents/ari/rule.md"
ARI_HEADING = "### Sonic Lane (Opt-In)"
ARI_ANCHORS = [
    "Delegation defaults stay with the steady team (`leader`).",
    "opt-in by POSITIVE selection only",
    "escalates the commission back to me with its reason",
]
_BACKING_NOTE_TOKEN = "backed by Plane"

# Baseline / seal anchors.
BASELINE_FILE = "agents/.prompt-artifacts.sha256"
SEAL_FILE = "tests/test_chart_image_delivery_e2e.py"
SEAL_DICT_NAME = "SEALED_SHA_BASELINES"
EXPECTED_BASELINE_ROWS = 201  # 197 at the v1.1 tip + 4 sonic-developer artifacts
EXPECTED_SEAL_ROWS = 35
SONIC_ARTIFACTS = ("soul.md", "rule.md", "workflow.md", "tools_note.md")
SONIC_BASELINE_PATHS = tuple(
    f"agents/{agent}/{artifact}"
    for agent in SONIC_AGENTS
    for artifact in SONIC_ARTIFACTS
)  # 20 rows

# ---- FROZEN [sonic-llm-pins-v3] (combined spec §1/§2.2 + D-B: developer
# joins the judgment class at "agentic"; coder/tester stay "coding";
# leader/reviewer stay "agentic") ----
EXPECTED_LLM_MODEL = {
    "sonic-leader": "agentic",
    "sonic-developer": "agentic",
    "sonic-coder": "coding",
    "sonic-reviewer": "agentic",
    "sonic-tester": "coding",
}
EXPECTED_MODELS_IN_DEFAULT_SET = ("agentic", "coding")
# ---- END FROZEN [sonic-llm-pins-v3] ----

# Config single source (AST-extracted, never imported in the static layer).
ALLOWED_MODELS_CONST = "_ALLOWED_MODELS_DEFAULT"  # daemon/config.py
CONFIG_FILE = "daemon/config.py"
AUTH_FILE = "daemon/tools/_auth.py"
TOOL_REQUIRED_CONST = "TOOL_REQUIRED_AGENTS"

# Fallback copy of TOOL_REQUIRED_AGENTS used ONLY by the in-memory self-test
# fixtures (the static layer AST-extracts the live dict; if that extraction
# fails the static layer FAILS LOUDLY — never silently falls back).
_TOOL_REQUIRED_AGENTS_FROZEN = {
    "knowledge": ["explorer", "kb-writer"],
    "chart": ["charter"],
    "image": ["image-reader"],
    "council": ["governor"],
    "design": ["image-comparator"],
}


class CheckFailure(AssertionError):
    """One failed assertion, shaped ``FAIL <id> <message>`` per spec §3."""

    def __init__(self, ac_id: str, message: str) -> None:
        self.ac_id = ac_id
        self.message = message
        super().__init__(f"FAIL {ac_id} {message}")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm(text: str) -> str:
    """Collapse all whitespace runs to single spaces (markdown re-wrap safe)."""
    return " ".join(text.split())


def _count_token(text: str, token: str) -> int:
    return len(re.findall(re.escape(token), text, flags=re.IGNORECASE))


# ---------------------------------------------------------------------------
# AST extraction of daemon constants (stdlib-only; loud on failure).
# ---------------------------------------------------------------------------

def _extract_const_from_source(path: Path, const_name: str):
    """literal_eval a module-level ``<name> = <literal>`` assignment."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == const_name for t in node.targets):
                return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == const_name:
                if node.value is not None:
                    return ast.literal_eval(node.value)
    raise CheckFailure(
        "static",
        f"const {const_name!r} not found in {path} — daemon surface drifted; "
        "re-derive the pack constant (spec §7-4 VERIFY-AT-IMPL)",
    )


def extract_tool_required_agents(root: Path = ROOT) -> dict:
    value = _extract_const_from_source(root / AUTH_FILE, TOOL_REQUIRED_CONST)
    if not isinstance(value, dict):
        raise CheckFailure("static", f"{TOOL_REQUIRED_CONST} is not a dict")
    return value


def extract_allowed_models_default(root: Path = ROOT) -> list:
    value = _extract_const_from_source(root / CONFIG_FILE, ALLOWED_MODELS_CONST)
    return list(value)


# ---------------------------------------------------------------------------
# Fixture: the single data surface both the real tree and the self-test
# in-memory fixtures feed to the assertion functions.
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Fixture:
    metas: dict  # agent -> RAW meta.json text (a1 parses; mutation legs can corrupt)
    texts: dict  # relpath -> file text
    baseline: dict  # baseline path -> sha row
    seal: dict  # SEALED_SHA_BASELINES rows
    agents_changed: list  # A9 input: committed-delta UNION working-tree paths
    tool_required_agents: dict
    allowed_models_default: list

    def bytes(self, relpath: str) -> bytes:
        return self.texts[relpath].encode("utf-8")

    def sha(self, relpath: str) -> str:
        return _sha256_bytes(self.bytes(relpath))


def _git_changed_agents(root: Path) -> list:
    """A9 input: committed delta UNION working-tree status, under agents/."""
    changed: set = set()
    committed = subprocess.run(
        ["git", "diff", "--name-only", "ac9849bf7..HEAD", "--", "agents/"],
        capture_output=True, text=True, cwd=str(root), check=False,
    )
    if committed.returncode != 0:
        raise CheckFailure("A9", f"git diff failed: {committed.stderr.strip()}")
    changed.update(ln.strip() for ln in committed.stdout.splitlines() if ln.strip())
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "agents/"],
        capture_output=True, text=True, cwd=str(root), check=False,
    )
    if status.returncode != 0:
        raise CheckFailure("A9", f"git status failed: {status.stderr.strip()}")
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:  # rename rows: keep the NEW path
            path = path.split(" -> ", 1)[1]
        path = path.strip('"').rstrip("/")
        if path:
            changed.add(path)
    return sorted(changed)


def build_real_fixture(root: Path = ROOT) -> Fixture:
    metas = {}
    texts = {}
    for agent in SONIC_AGENTS:
        metas[agent] = (root / "agents" / agent / "meta.json").read_text(encoding="utf-8")
    needed = [
        "agents/sonic-leader/workflow.md",
        "agents/sonic-leader/rule.md",
        "agents/sonic-developer/workflow.md",
        "agents/sonic-developer/rule.md",
        "agents/sonic-developer/tools_note.md",
        "agents/sonic-coder/workflow.md",
        "agents/sonic-coder/rule.md",
        "agents/sonic-coder/tools_note.md",
        "agents/sonic-reviewer/workflow.md",
        "agents/sonic-tester/workflow.md",
        ARI_RULE,
        "agents/ari/workflow.md",
        "agents/ari/soul.md",
    ] + list(SONIC_BASELINE_PATHS)  # A8(c) re-hashes every sonic artifact
    for rel in needed:
        texts[rel] = (root / rel).read_text(encoding="utf-8")
    baseline = {}
    for line in (root / BASELINE_FILE).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([0-9a-f]{64})  (agents/.+)$", line)
        if m:
            baseline[m.group(2)] = m.group(1)
    seal = extract_seal_dict(root / SEAL_FILE)
    return Fixture(
        metas=metas,
        texts=texts,
        baseline=baseline,
        seal=seal,
        agents_changed=_git_changed_agents(root),
        tool_required_agents=extract_tool_required_agents(root),
        allowed_models_default=extract_allowed_models_default(root),
    )


def extract_seal_dict(path: Path) -> dict:
    """ast.literal_eval the SEALED_SHA_BASELINES dict out of the e2e test."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(SEAL_DICT_NAME) and "= {" in line:
            start = i
            break
    if start is None:
        raise CheckFailure("A8", f"{SEAL_DICT_NAME} not found in {path}")
    end = None
    for i in range(start, len(lines)):
        if lines[i] == "}":
            end = i
            break
    if end is None:
        raise CheckFailure("A8", f"{SEAL_DICT_NAME} closing brace not found")
    block = "\n".join(lines[start:end + 1])
    try:
        return ast.literal_eval(block.split("=", 1)[1].strip())
    except (ValueError, SyntaxError) as exc:
        raise CheckFailure("A8", f"{SEAL_DICT_NAME} literal_eval failed: {exc}")


# ---------------------------------------------------------------------------
# The assertion functions A1-A14.  Each raises CheckFailure on drift and
# returns normally on pass.  Pure functions of the Fixture.
# ---------------------------------------------------------------------------

def a1_meta_parse(fx: Fixture) -> None:
    """A1: meta.json parses; required keys present; id == directory name."""
    required = ("id", "name", "tools", "team_members", "innate_skills", "llm_model")
    for agent in SONIC_AGENTS:
        rel = f"agents/{agent}/meta.json"
        raw = fx.metas[agent]
        try:
            meta = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise CheckFailure("A1", f"{rel}: JSON error: {exc}") from exc
        if not isinstance(meta, dict):
            raise CheckFailure("A1", f"{rel}: top level is not an object")
        missing = [k for k in required if k not in meta]
        if missing:
            raise CheckFailure("A1", f"{rel}: missing key(s) {missing}")
        if meta["id"] != agent:
            raise CheckFailure("A1", f"{rel}: id={meta['id']!r} != dirname {agent!r}")


def _team(fx: Fixture, agent: str) -> list:
    """Absent -> [] normalization (phase1 §3.5; naive ==[] KeyErrors)."""
    meta = json.loads(fx.metas[agent])
    team = meta.get("team_members")
    return list(team) if team else []


def _tools(fx: Fixture, agent: str) -> tuple:
    meta = json.loads(fx.metas[agent])
    tools = meta.get("tools") or {}
    allow = list(tools.get("allow") or [])
    deny = list(tools.get("deny") or [])
    return allow, deny


def a2_team_members(fx: Fixture) -> None:
    """A2: exact team sets after absent->[] normalization (D-H lockstep)."""
    for agent in SONIC_AGENTS:
        team = _team(fx, agent)
        expected = FROZEN_TEAMS[agent]
        if team != expected:
            raise CheckFailure("A2", f"team_members: {agent} actual={team} expected={expected}")


def _effective_spawn_set(fx: Fixture, agent: str) -> set:
    team = set(_team(fx, agent))
    allow, _deny = _tools(fx, agent)
    implicit: set = set()
    for category in allow:
        implicit.update(fx.tool_required_agents.get(category, []))
    return team | implicit


def a3_allow_deny(fx: Fixture) -> None:
    """A3: frozen allow/deny arrays verbatim + effective spawn sets
    (v1.2: knowledge on developer+leader ONLY; coder WITHOUT knowledge)."""
    for agent in SONIC_AGENTS:
        allow, deny = _tools(fx, agent)
        if allow != FROZEN_TOOLS[agent]["allow"]:
            raise CheckFailure(
                "A3", f"allow/deny drift: {agent} allow actual={allow} "
                f"expected={FROZEN_TOOLS[agent]['allow']}")
        if deny != FROZEN_TOOLS[agent]["deny"]:
            raise CheckFailure(
                "A3", f"allow/deny drift: {agent} deny actual={deny} "
                f"expected={FROZEN_TOOLS[agent]['deny']}")
    for agent in SONIC_AGENTS:
        effective = _effective_spawn_set(fx, agent)
        if effective != EXPECTED_EFFECTIVE[agent]:
            raise CheckFailure(
                "A3", f"effective spawn set: {agent} expected "
                f"{sorted(EXPECTED_EFFECTIVE[agent])} got {sorted(effective)}")


def a4_llm_model(fx: Fixture) -> None:
    """A4: per-agent llm_model pin map (developer/leader/reviewer="agentic",
    coder/tester="coding"); BOTH pin values in the in-tree default set."""
    for agent in SONIC_AGENTS:
        meta = json.loads(fx.metas[agent])
        model = meta.get("llm_model")
        expected = EXPECTED_LLM_MODEL[agent]
        if model != expected:
            raise CheckFailure("A4", f"{agent}.llm_model={model} expected={expected}")
    for model in EXPECTED_MODELS_IN_DEFAULT_SET:
        if model not in fx.allowed_models_default:
            raise CheckFailure(
                "A4", f"{model} missing from in-tree {ALLOWED_MODELS_CONST} "
                f"={fx.allowed_models_default}")


def a5_recursion(fx: Fixture) -> None:
    """A5: multiplier 7 on coder ONLY; key ABSENT on the other FOUR."""
    values = {}
    for agent in SONIC_AGENTS:
        meta = json.loads(fx.metas[agent])
        values[agent] = meta.get("recursion_limit_multiplier")
    if values["sonic-coder"] != 7:
        raise CheckFailure("A5", f"multiplier: coder={values['sonic-coder']} others=...")
    others = {a: v for a, v in values.items() if a != "sonic-coder"}
    not_absent = {a: v for a, v in others.items() if v is not None}
    if not_absent:
        raise CheckFailure("A5", f"multiplier: coder=7 others={not_absent} (must be ABSENT)")


def a6_integration_surfaces(fx: Fixture) -> None:
    """A6: HANDOFF-v2's 7 headers in the DEVELOPER workflow (authorship moved
    up) + HANDOFF_PATH three-way (developer/reviewer/tester) + the FROZEN
    [sonic-module-report-v2] template in the coder workflow (the coder's
    contract replacement — it writes NO handoff)."""
    dev = fx.texts[DEV_WORKFLOW]
    for header in HANDOFF_HEADERS:
        if header not in dev:
            raise CheckFailure("A6", f'missing handoff-v2 header "{header}"')
    lines_by_file = {}
    for rel in HANDOFF_FILES:
        hits = [ln for ln in fx.texts[rel].splitlines()
                if ln.startswith("HANDOFF_PATH = ")]
        if len(hits) != 1:
            raise CheckFailure("A6", f"{rel}: expected exactly 1 HANDOFF_PATH line, got {len(hits)}")
        if not hits[0].startswith(HANDOFF_PATH_PREFIX):
            raise CheckFailure("A6", f"{rel}: HANDOFF literal drift: {hits[0]!r}")
        lines_by_file[rel] = hits[0]
    distinct = set(lines_by_file.values())
    if len(distinct) != 1:
        raise CheckFailure("A6", f"HANDOFF path drift: {sorted(distinct)}")
    coder = fx.texts["agents/sonic-coder/workflow.md"]
    if not any(ln.startswith(MODULE_REPORT_PREFIX) for ln in coder.splitlines()):
        raise CheckFailure("A6", "coder workflow missing the MODULE-REPORT template line")
    for key in MODULE_REPORT_KEYS:
        if key not in coder:
            raise CheckFailure("A6", f"coder workflow missing module-report key {key!r}")


def _extract_blast_block(text: str, rel: str) -> list:
    try:
        _, after_open = text.split(BLAST_MARKER_OPEN, 1)
        block, _rest = after_open.split(BLAST_MARKER_CLOSE, 1)
    except ValueError:
        raise CheckFailure("A7", f"blast block drift: {rel} (markers missing)") from None
    return [ln for ln in block.splitlines() if ln.strip()]


def a7_blast_globs(fx: Fixture) -> None:
    """A7: ALL 14 globs, exact order, byte-identical across the THREE
    workflows that carry the block (leader, developer, coder)."""
    blocks = {}
    for rel in BLAST_WORKFLOW_FILES:
        blocks[rel] = _extract_blast_block(fx.texts[rel], rel)
        block = blocks[rel]
        if len(block) != len(FROZEN_GLOBS) or block != FROZEN_GLOBS:
            first_diff = ""
            for i, (want, got) in enumerate(zip(FROZEN_GLOBS, block)):
                if want != got:
                    first_diff = f"{want} vs {got}"
                    break
            else:
                first_diff = f"length {len(block)} vs {len(FROZEN_GLOBS)}"
            raise CheckFailure(
                "A7", f"blast block drift: {rel} ({len(block)}/{len(FROZEN_GLOBS)}, "
                f"first diff {first_diff})")
    first = blocks[BLAST_WORKFLOW_FILES[0]]
    if any(blocks[rel] != first for rel in BLAST_WORKFLOW_FILES[1:]):
        raise CheckFailure("A7", "blast block drift: the three workflow copies differ")


def a8_ari_pins(fx: Fixture, run_generator_check: bool = False) -> None:
    """A8: Ari rule.md exactly-1-Plane + backing note; seal consistency;
    baseline 20 sonic rows + total 201 (+ generator --check in real mode)."""
    rule = fx.texts[ARI_RULE]
    plane_count = _count_token(rule, "plane")
    if plane_count != 1:
        raise CheckFailure("A8", f"rule.md plane count={plane_count}")
    if _count_token(rule, _BACKING_NOTE_TOKEN) < 1:
        raise CheckFailure("A8", f"rule.md plane count={plane_count} but backing note token missing")
    for rel in ("agents/ari/workflow.md", "agents/ari/soul.md"):
        if _count_token(fx.texts[rel], "plane") != 0:
            raise CheckFailure("A8", f"{rel} plane count must be 0")
    # (b) seal consistency — compute, never hardcode.
    if len(fx.seal) != EXPECTED_SEAL_ROWS:
        raise CheckFailure("A8", f"seal map rows={len(fx.seal)} expected {EXPECTED_SEAL_ROWS}")
    want = fx.seal.get(ARI_RULE)
    actual = fx.sha(ARI_RULE)
    if want is None:
        raise CheckFailure("A8", f"seal map missing row for {ARI_RULE}")
    if want != actual:
        raise CheckFailure("A8", f"seal stale for {ARI_RULE}: pinned {want} != actual {actual}")
    # (c) baseline: 20 sonic rows with LIVE values + total 201.
    for rel in SONIC_BASELINE_PATHS:
        row = fx.baseline.get(rel)
        if row is None:
            raise CheckFailure("A8", f"baseline missing sonic row: {rel}")
        if row != fx.sha(rel):
            raise CheckFailure("A8", f"baseline row stale for {rel}: {row} != actual {fx.sha(rel)}")
    total = len(fx.baseline)
    if total != EXPECTED_BASELINE_ROWS:
        raise CheckFailure("A8", f"baseline rows={total} expected {EXPECTED_BASELINE_ROWS}")
    if run_generator_check:
        proc = subprocess.run(
            [sys.executable, "scripts/protection/generate_prompt_artifact_baseline.py",
             "--check", "--tree", "."],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        if proc.returncode != 0:
            raise CheckFailure(
                "A8", f"generator --check rc={proc.returncode}: "
                f"{(proc.stdout + proc.stderr).strip()[:400]}")


def a9_steady_team(fx: Fixture) -> None:
    """A9: changed agents/ set (committed UNION worktree) = sonic-* +
    baseline + exactly ari/rule.md; positive shape: sonic dirs present."""
    residual = [p for p in fx.agents_changed
                if not p.startswith("agents/sonic-")
                and p != BASELINE_FILE]
    if residual != [ARI_RULE]:
        extra = [p for p in residual if p != ARI_RULE]
        raise CheckFailure("A9", f"unexpected agents/ edit: {extra or residual}")
    if ARI_RULE not in fx.agents_changed:
        raise CheckFailure("A9", f"expected {ARI_RULE} in the changed set (positive shape)")
    if not any(p.startswith("agents/sonic-") for p in fx.agents_changed):
        raise CheckFailure("A9", "sonic dirs absent from the changed set (positive shape)")


def a10_escalation(fx: Fixture) -> None:
    """A10: the complete frozen escalation sentence in leader rule.md."""
    if ESCALATION_LINE not in fx.texts["agents/sonic-leader/rule.md"]:
        raise CheckFailure("A10", "escalation line drift")


def a11_ari_note(fx: Fixture) -> None:
    """A11: Ari note heading + 3 frozen anchors; note region Plane-free."""
    text = fx.texts[ARI_RULE]
    lines = text.splitlines()
    heading_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == ARI_HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        raise CheckFailure("A11", "Ari note heading/anchor missing (heading)")
    region_lines = []
    for ln in lines[heading_idx + 1:]:
        if re.match(r"^#{1,3} ", ln):
            break
        region_lines.append(ln)
    region = "\n".join(region_lines)
    region_norm = _norm(region)
    for anchor in ARI_ANCHORS:
        if _norm(anchor) not in region_norm:
            raise CheckFailure("A11", f"Ari note heading/anchor missing: {anchor!r}")
    if _count_token(region, "plane"):
        raise CheckFailure("A11", "plane in Ari note")
    if re.search(r"^## Cardinal", region, flags=re.MULTILINE):
        raise CheckFailure("A11", "## Cardinal inside Ari note")


def a12_developer_never_experience(fx: Fixture) -> None:
    """A12 (combined spec §2.3/D-G): the sonic-developer's THREE artifacts
    each carry the pinned never-experience/explore-bound boundary sentence
    (positive shape, normalized containment)."""
    checks = (
        (DEV_RULE, DEV_RULE_NEVER_EXPERIENCE),
        (DEV_WORKFLOW, DEV_WORKFLOW_EXPLORE_BOUND),
        (DEV_TOOLS_NOTE, DEV_TOOLSNOTE_NEVER_EXPERIENCE),
    )
    for rel, needle in checks:
        if _norm(needle) not in _norm(fx.texts[rel]):
            raise CheckFailure("A12", f"developer never-experience boundary drift in {rel}")


def a13_coder_discipline(fx: Fixture) -> None:
    """A13 (combined spec §3.3): the coder artifacts assert BOTH discipline
    shapes — source-exploration-expected AND RAG-excluded — in rule.md AND
    workflow.md (normalized), plus the tools_note no-knowledge marker with
    NO held-knowledge row (the v1.1 grant is reversed)."""
    for rel in CODER_DISCIPLINE_FILES:
        text = _norm(fx.texts[rel])
        if _norm(CODER_SOURCE_EXPLORE_SENTENCE) not in text:
            raise CheckFailure("A13", f"coder source-exploration shape missing in {rel}")
        if _norm(CODER_RAG_EXCLUSION_SENTENCE) not in text:
            raise CheckFailure("A13", f"coder RAG-exclusion shape missing in {rel}")
    note = fx.texts[CODER_TOOLS_NOTE]
    if CODER_NO_KNOWLEDGE_MARKER not in note:
        raise CheckFailure("A13", "coder tools_note missing the no-knowledge marker")
    if re.search(r"^- `knowledge` —", note, flags=re.MULTILINE):
        raise CheckFailure("A13", "coder tools_note still carries a HELD knowledge row")


def a14_leader_fast_path(fx: Fixture) -> None:
    """A14 (combined spec §7/D-E): the FROZEN fast-path gate line in the
    leader's rule.md AND workflow.md."""
    for rel in ("agents/sonic-leader/rule.md", "agents/sonic-leader/workflow.md"):
        if FAST_PATH_GATE_LINE not in fx.texts[rel]:
            raise CheckFailure("A14", f"fast-path gate line drift in {rel}")


ASSERTIONS = (
    ("A1", a1_meta_parse, "meta.json parse + id==dirname x5"),
    ("A2", a2_team_members, "team_members exact sets (D-H: leader=4 sonic, developer=[coder])"),
    ("A3", a3_allow_deny, "frozen allow/deny arrays (v1.2: knowledge on developer+leader ONLY; coder zero-knowledge) + effective spawn sets"),
    ("A4", a4_llm_model, "llm_model pin map (developer/leader/reviewer=agentic, coder/tester=coding) + both in default set"),
    ("A5", a5_recursion, "multiplier 7 coder-only"),
    ("A6", a6_integration_surfaces, "HANDOFF-v2 headers in developer workflow + HANDOFF_PATH three-way + coder module-report template"),
    ("A7", a7_blast_globs, "14 blast globs zero-diff across leader/developer/coder workflows"),
    ("A8", a8_ari_pins, "Ari plane==1 + seal + baseline 201/20"),
    ("A9", a9_steady_team, "steady team untouched (union predicate)"),
    ("A10", a10_escalation, "escalation full frozen line"),
    ("A11", a11_ari_note, "Ari note heading + anchors + region hygiene"),
    ("A12", a12_developer_never_experience, "developer never-experience boundary pinned in 3 artifacts"),
    ("A13", a13_coder_discipline, "coder source-exploration + RAG-exclusion shapes + tools_note no-knowledge marker"),
    ("A14", a14_leader_fast_path, "leader fast-path gate line in rule.md + workflow.md"),
)


# ---------------------------------------------------------------------------
# Self-test: in-memory GOOD fixture + one counter-mutation leg per assertion.
# ---------------------------------------------------------------------------

def _meta_text(meta: dict) -> str:
    return json.dumps(meta, indent=2, ensure_ascii=False) + "\n"


def _good_rule(agent: str) -> str:
    return (
        f"# {agent} rules\n\n"
        "1. One-shot mandate. Explore, code, self-test, report.\n"
    )


def _good_workflow(role: str, with_blast: bool, with_handoff: bool,
                   with_module_report: bool = False) -> str:
    parts = [f"# {role} workflow\n\n", "## 1. Intake\n\nRead the commission.\n\n"]
    if with_handoff:
        parts.append(
            "## Handoff\n\n"
            "HANDOFF_PATH = HANDOFF.md   # worktree-root-relative; the ABSOLUTE path is passed in every dispatch\n\n"
        )
        for header in HANDOFF_HEADERS:
            parts.append(f"{header}\n\nnone\n\n")
    if with_module_report:
        parts.append(
            "## Module report\n\n"
            "MODULE-REPORT: <module-slug>\n"
            "branch: <branch-name> @ <tip-sha>\n"
            "files: <exact paths touched, comma-separated>\n"
            "changed: <1-3 lines>\n"
            "self-test: <what ran>\n"
            "untested: <list, or none>\n"
            "risks: <list, or none>\n"
            "raises: <bash-raises, or none>\n"
            "blast: <no predicate match | ESCALATED>\n\n"
        )
    if with_blast:
        parts.append(
            "## Blast-Radius Predicate\n\n"
            f"{BLAST_MARKER_OPEN}\n"
            + "\n".join(FROZEN_GLOBS)
            + f"\n{BLAST_MARKER_CLOSE}\n\n"
        )
    return "".join(parts)


def build_good_fixture() -> Fixture:
    metas = {
        "sonic-leader": {
            "id": "sonic-leader", "name": "Sonic Leader", "version": "1.1.0",
            "llm_model": "agentic", "innate_skills": ["todo"],
            "skill_injection": False, "default_queue": "system_parallel_queue",
            "tools": {"allow": list(FROZEN_TOOLS["sonic-leader"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-leader"]["deny"])},
            "team_members": list(FROZEN_TEAMS["sonic-leader"]),
        },
        "sonic-developer": {
            "id": "sonic-developer", "name": "Sonic Developer", "version": "1.0.0",
            "llm_model": "agentic", "innate_skills": ["todo"],
            "skill_injection": False, "default_queue": "system_parallel_queue",
            "tools": {"allow": list(FROZEN_TOOLS["sonic-developer"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-developer"]["deny"])},
            "team_members": list(FROZEN_TEAMS["sonic-developer"]),
        },
        "sonic-coder": {
            "id": "sonic-coder", "name": "Sonic Coder", "version": "1.1.0",
            "llm_model": "coding", "innate_skills": ["todo"],
            "skill_injection": False, "recursion_limit_multiplier": 7,
            "tools": {"allow": list(FROZEN_TOOLS["sonic-coder"]["allow"])},
            "team_members": [],
        },
        "sonic-reviewer": {
            "id": "sonic-reviewer", "name": "Sonic Reviewer", "version": "1.1.0",
            "llm_model": "agentic", "innate_skills": [], "skill_injection": False,
            "tools": {"allow": list(FROZEN_TOOLS["sonic-reviewer"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-reviewer"]["deny"])},
            "team_members": [],
        },
        "sonic-tester": {
            "id": "sonic-tester", "name": "Sonic Tester", "version": "1.1.0",
            "llm_model": "coding", "innate_skills": [], "skill_injection": False,
            "tools": {"allow": list(FROZEN_TOOLS["sonic-tester"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-tester"]["deny"])},
            "team_members": [],
        },
    }
    texts = {
        "agents/sonic-leader/workflow.md": (
            "# sonic-leader workflow\n\n"
            f"{FAST_PATH_GATE_LINE}\n\n"
            + _good_workflow("sonic-leader", with_blast=True, with_handoff=False)
        ),
        "agents/sonic-leader/rule.md": (
            "# sonic-leader rules\n\n"
            f"`{ESCALATION_LINE}`\n\n"
            f"{FAST_PATH_GATE_LINE}\n\n"
            + _good_rule("sonic-leader")
        ),
        "agents/sonic-developer/workflow.md": (
            "# Sonic Developer Workflow\n\n"
            f"{DEV_WORKFLOW_EXPLORE_BOUND}\n\n"
            + _good_workflow("sonic-developer", with_blast=True, with_handoff=True)
        ),
        "agents/sonic-developer/rule.md": (
            "# sonic-developer rules\n\n"
            f"{DEV_RULE_NEVER_EXPERIENCE}\n\n"
            + _good_rule("sonic-developer")
        ),
        "agents/sonic-developer/tools_note.md": (
            "# Sonic Developer Tools\n\n"
            f"- `knowledge` — ONE explore at plan-open. {DEV_TOOLSNOTE_NEVER_EXPERIENCE}.\n"
        ),
        "agents/sonic-coder/workflow.md": (
            "# Sonic Coder Workflow\n\n"
            f"{CODER_SOURCE_EXPLORE_SENTENCE}\n\n"
            f"{CODER_RAG_EXCLUSION_SENTENCE}\n\n"
            + _good_workflow("sonic-coder", with_blast=True, with_handoff=False, with_module_report=True)
        ),
        "agents/sonic-coder/rule.md": (
            "# Sonic Coder Rules\n\n"
            f"{CODER_SOURCE_EXPLORE_SENTENCE}\n\n"
            f"{CODER_RAG_EXCLUSION_SENTENCE}\n\n"
            + _good_rule("sonic-coder")
        ),
        "agents/sonic-coder/tools_note.md": (
            "# Sonic Coder Tools\n\n"
            f"- {CODER_NO_KNOWLEDGE_MARKER} — you make NO RAG/knowledge call.\n"
        ),
        "agents/sonic-reviewer/workflow.md": _good_workflow("sonic-reviewer", with_blast=False, with_handoff=True),
        "agents/sonic-tester/workflow.md": _good_workflow("sonic-tester", with_blast=False, with_handoff=True),
        ARI_RULE: (
            "### CRITICAL: TASK TRIAGE\n\n"
            "- \"Help me set up project X\" — could be quick or huge; **ask** if unclear\n\n"
            f"{ARI_HEADING}\n\n"
            "Delegation defaults stay with the steady team (`leader`). The sonic lane\n"
            "(`sonic-leader`) is opt-in by POSITIVE selection only — dispatch to it when\n"
            "the user asks for it by name, or for small-to-medium, low-coupling work where\n"
            "speed is the point. Everything else defaults to `leader`. If the work turns\n"
            "out to sit on load-bearing paths, sonic-leader escalates the commission back\n"
            "to me with its reason, and I re-route it to `leader`.\n\n"
            "---\n\n"
            "### Always Watch Jobs You Create\n\n"
            "Note: the PM interface is currently backed by Plane — an implementation\n"
            "detail that may change.\n"
        ),
        "agents/ari/workflow.md": "# ari workflow\n",
        "agents/ari/soul.md": "# ari soul\n",
    }
    fixture = Fixture(
        metas={a: _meta_text(m) for a, m in metas.items()},
        texts=texts,
        baseline={},
        seal={},
        agents_changed=[ARI_RULE, BASELINE_FILE] + list(SONIC_BASELINE_PATHS),
        tool_required_agents=dict(_TOOL_REQUIRED_AGENTS_FROZEN),
        allowed_models_default=["agentic", "coding", "coding2", "vision", "img-gen", "decision"],
    )
    # Fill the remaining sonic artifact texts (baseline hashing needs all 20);
    # workflow.md texts set above are kept.
    for rel in SONIC_BASELINE_PATHS:
        if rel not in fixture.texts:
            fixture.texts[rel] = f"# {rel} (fixture)\n\nfixture body.\n"
    # Seal: 35 rows, ari/rule.md pinned to the sha of the fixture bytes.
    seal = {ARI_RULE: fixture.sha(ARI_RULE)}
    for i in range(EXPECTED_SEAL_ROWS - 1):
        seal[f"agents/filler-{i}/rule.md"] = f"{i:064x}"
    fixture.seal = seal
    # Baseline: exactly 201 rows, sonic rows matching fixture bytes.
    for rel in SONIC_BASELINE_PATHS:
        fixture.baseline[rel] = fixture.sha(rel)
    fixture.baseline[ARI_RULE] = fixture.sha(ARI_RULE)
    for i in range(EXPECTED_BASELINE_ROWS - len(fixture.baseline)):
        fixture.baseline[f"agents/filler-{i}/rule.md"] = f"{i:064x}"
    return fixture


def _mutate_meta(fixture: Fixture, agent: str, mutate) -> None:
    meta = json.loads(fixture.metas[agent])
    mutate(meta)
    fixture.metas[agent] = _meta_text(meta)


def _mutation_legs():
    """(leg_id, mutator) — one leg per counter-mutation, minimum.  Every
    assertion is reddened by AT LEAST one leg (POK mutation-01)."""

    def a1_drop_id(fixture):
        _mutate_meta(fixture, "sonic-leader", lambda m: m.pop("id"))

    def a1_trailing_comma(fixture):
        fixture.metas["sonic-leader"] = fixture.metas["sonic-leader"].replace(
            "}", ",}", 1)

    def a2_leader_add_planner(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["team_members"].append("sonic-planner"))

    def a2_coder_add_worker(fixture):
        _mutate_meta(fixture, "sonic-coder",
                     lambda m: m.__setitem__("team_members", ["worker"]))

    def a2_developer_add_reviewer(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m["team_members"].append("sonic-reviewer"))

    def a3_leader_drop_knowledge(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["tools"]["allow"].remove("knowledge"))

    def a3_coder_add_knowledge(fixture):
        # v1.2 counter-mutation: the coder grant is REVERSED — re-adding
        # knowledge to the coder's allow must RED.
        _mutate_meta(fixture, "sonic-coder",
                     lambda m: m["tools"]["allow"].insert(-2, "knowledge"))

    def a3_developer_drop_knowledge(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m["tools"]["allow"].remove("knowledge"))

    def a3_developer_deny_drop_editfile(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m["tools"]["deny"].remove("edit_file"))

    def a3_reviewer_add_knowledge(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["allow"].append("knowledge"))

    def a3_effective_drop_required_row(fixture):
        # Positive shape: the effective-spawn-set pin depends on the
        # TOOL_REQUIRED_AGENTS row — removing the mapping must RED.
        fixture.tool_required_agents.pop("knowledge")

    def a3_reviewer_drop_bash(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["allow"].remove("bash"))

    def a3_reviewer_deny_drop_editfile(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["deny"].remove("edit_file"))

    def a4_developer_model_coding(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m.__setitem__("llm_model", "coding"))

    def a4_coder_model_agentic(fixture):
        _mutate_meta(fixture, "sonic-coder",
                     lambda m: m.__setitem__("llm_model", "agentic"))

    def a4_reviewer_model_coding(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m.__setitem__("llm_model", "coding"))

    def a5_coder_drop_multiplier(fixture):
        _mutate_meta(fixture, "sonic-coder",
                     lambda m: m.pop("recursion_limit_multiplier"))

    def a5_developer_add_multiplier(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m.__setitem__("recursion_limit_multiplier", 7))

    def a5_reviewer_add_multiplier(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m.__setitem__("recursion_limit_multiplier", 7))

    def a6_developer_drop_header(fixture):
        fixture.texts[DEV_WORKFLOW] = fixture.texts[DEV_WORKFLOW].replace(
            "## What is UNTESTED\n\nnone\n\n", "", 1)

    def a6_tester_handoff_literal(fixture):
        fixture.texts["agents/sonic-tester/workflow.md"] = fixture.texts[
            "agents/sonic-tester/workflow.md"].replace(
            "HANDOFF_PATH = HANDOFF.md", "HANDOFF_PATH = handoff.md", 1)

    def a6_coder_module_report_drop(fixture):
        fixture.texts["agents/sonic-coder/workflow.md"] = fixture.texts[
            "agents/sonic-coder/workflow.md"].replace(
            "MODULE-REPORT: <module-slug>\n", "", 1)

    def a7_glob_delete(fixture):
        fixture.texts["agents/sonic-coder/workflow.md"] = fixture.texts[
            "agents/sonic-coder/workflow.md"].replace("daemon/routers/**\n", "", 1)

    def a7_developer_glob_delete(fixture):
        fixture.texts[DEV_WORKFLOW] = fixture.texts[DEV_WORKFLOW].replace(
            "daemon/routers/**\n", "", 1)

    def a7_glob_reorder(fixture):
        text = fixture.texts["agents/sonic-leader/workflow.md"]
        text = text.replace("daemon/api.py\ndaemon/services/**",
                            "daemon/services/**\ndaemon/api.py", 1)
        fixture.texts["agents/sonic-leader/workflow.md"] = text

    def a8_ari_plane_append(fixture):
        fixture.texts[ARI_RULE] += "\n<!-- Plane -->\n"

    def a8_seal_revert(fixture):
        fixture.seal[ARI_RULE] = "fc0a7542157d79d16011a48d7e79d31d33fe1376ca5fc5c694d47e670bcfdf10"

    def a8_baseline_delete_row(fixture):
        fixture.baseline.pop(SONIC_BASELINE_PATHS[0])

    def a9_extra_steady_path(fixture):
        fixture.agents_changed = fixture.agents_changed + ["agents/leader/rule.md"]

    def a10_escalation_corrupt(fixture):
        fixture.texts["agents/sonic-leader/rule.md"] = fixture.texts[
            "agents/sonic-leader/rule.md"].replace(
            "returning commission to Ari (steady-team routing).",
            "returning commission to the agent.", 1)

    def a11_heading_rename(fixture):
        fixture.texts[ARI_RULE] = fixture.texts[ARI_RULE].replace(
            ARI_HEADING, "### Sonic Lane (opt-in)", 1)

    def a11_anchor_drop(fixture):
        fixture.texts[ARI_RULE] = fixture.texts[ARI_RULE].replace(
            "escalates the commission back\n"
            "to me with its reason,", "escalates it back,", 1)

    def a11_note_plane_inject(fixture):
        fixture.texts[ARI_RULE] = fixture.texts[ARI_RULE].replace(
            ARI_HEADING, f"{ARI_HEADING}\n\nrouted through the Plane board.", 1)

    def a12_rule_corrupt(fixture):
        fixture.texts[DEV_RULE] = fixture.texts[DEV_RULE].replace(
            DEV_RULE_NEVER_EXPERIENCE,
            "You may call experience() when a module teaches something.", 1)

    def a12_workflow_corrupt(fixture):
        fixture.texts[DEV_WORKFLOW] = fixture.texts[DEV_WORKFLOW].replace(
            DEV_WORKFLOW_EXPLORE_BOUND,
            "The bound is soft: explore as often as the plan needs.", 1)

    def a12_toolsnote_corrupt(fixture):
        fixture.texts[DEV_TOOLS_NOTE] = fixture.texts[DEV_TOOLS_NOTE].replace(
            DEV_TOOLSNOTE_NEVER_EXPERIENCE,
            "experience() is yours when the merge teaches something", 1)

    def a13_workflow_drop_s1(fixture):
        fixture.texts["agents/sonic-coder/workflow.md"] = fixture.texts[
            "agents/sonic-coder/workflow.md"].replace(
            CODER_SOURCE_EXPLORE_SENTENCE,
            "Source exploration is optional if the brief is detailed enough.", 1)

    def a13_rule_drop_s2(fixture):
        fixture.texts["agents/sonic-coder/rule.md"] = fixture.texts[
            "agents/sonic-coder/rule.md"].replace(
            CODER_RAG_EXCLUSION_SENTENCE,
            "One bounded explore() at open is allowed when the brief is thin.", 1)

    def a13_toolsnote_drop_marker(fixture):
        fixture.texts[CODER_TOOLS_NOTE] = fixture.texts[CODER_TOOLS_NOTE].replace(
            CODER_NO_KNOWLEDGE_MARKER + " — you make NO RAG/knowledge call.",
            "Knowledge access follows the team default.", 1)

    def a13_toolsnote_add_knowledge_row(fixture):
        fixture.texts[CODER_TOOLS_NOTE] += (
            "- `knowledge` — read-once: exactly ONE bounded explore() at open.\n")

    def a14_leader_gate_corrupt(fixture):
        fixture.texts["agents/sonic-leader/workflow.md"] = fixture.texts[
            "agents/sonic-leader/workflow.md"].replace(
            FAST_PATH_GATE_LINE,
            "FAST-PATH GATE: small work may skip the developer layer.", 1)

    return [
        ("A1-drop-id", a1_drop_id),
        ("A1-trailing-comma", a1_trailing_comma),
        ("A2-leader-add-planner", a2_leader_add_planner),
        ("A2-coder-add-worker", a2_coder_add_worker),
        ("A2-developer-add-reviewer", a2_developer_add_reviewer),
        ("A3-leader-drop-knowledge", a3_leader_drop_knowledge),
        ("A3-coder-add-knowledge", a3_coder_add_knowledge),
        ("A3-developer-drop-knowledge", a3_developer_drop_knowledge),
        ("A3-developer-deny-drop-editfile", a3_developer_deny_drop_editfile),
        ("A3-reviewer-add-knowledge", a3_reviewer_add_knowledge),
        ("A3-effective-drop-required-row", a3_effective_drop_required_row),
        ("A3-reviewer-drop-bash", a3_reviewer_drop_bash),
        ("A3-reviewer-deny-drop-editfile", a3_reviewer_deny_drop_editfile),
        ("A4-developer-model-coding", a4_developer_model_coding),
        ("A4-coder-model-agentic", a4_coder_model_agentic),
        ("A4-reviewer-model-coding", a4_reviewer_model_coding),
        ("A5-coder-drop-multiplier", a5_coder_drop_multiplier),
        ("A5-developer-add-multiplier", a5_developer_add_multiplier),
        ("A5-reviewer-add-multiplier", a5_reviewer_add_multiplier),
        ("A6-developer-drop-header", a6_developer_drop_header),
        ("A6-tester-handoff-literal", a6_tester_handoff_literal),
        ("A6-coder-module-report-drop", a6_coder_module_report_drop),
        ("A7-glob-delete", a7_glob_delete),
        ("A7-developer-glob-delete", a7_developer_glob_delete),
        ("A7-glob-reorder", a7_glob_reorder),
        ("A8-ari-plane-append", a8_ari_plane_append),
        ("A8-seal-revert", a8_seal_revert),
        ("A8-baseline-delete-row", a8_baseline_delete_row),
        ("A9-extra-steady-path", a9_extra_steady_path),
        ("A10-escalation-corrupt", a10_escalation_corrupt),
        ("A11-heading-rename", a11_heading_rename),
        ("A11-anchor-drop", a11_anchor_drop),
        ("A11-note-plane-inject", a11_note_plane_inject),
        ("A12-rule-corrupt", a12_rule_corrupt),
        ("A12-workflow-corrupt", a12_workflow_corrupt),
        ("A12-toolsnote-corrupt", a12_toolsnote_corrupt),
        ("A13-workflow-drop-s1", a13_workflow_drop_s1),
        ("A13-rule-drop-s2", a13_rule_drop_s2),
        ("A13-toolsnote-drop-marker", a13_toolsnote_drop_marker),
        ("A13-toolsnote-add-knowledge-row", a13_toolsnote_add_knowledge_row),
        ("A14-leader-gate-corrupt", a14_leader_gate_corrupt),
    ]


def run_self_test() -> bool:
    good = build_good_fixture()
    ok = True
    print("--- self-test: good fixture must PASS every assertion ---")
    for ac_id, fn, desc in ASSERTIONS:
        try:
            fn(good)
            print(f"  GREEN {ac_id} ({desc})")
        except AssertionError as exc:
            ok = False
            print(f"  VACUOUS-FIXTURE {ac_id}: good fixture failed: {exc}")
    print("--- self-test: counter-mutation legs must RED ---")
    for leg_id, mutator in _mutation_legs():
        fixture = dataclasses.replace(
            good,
            metas=dict(good.metas),
            texts=dict(good.texts),
            baseline=dict(good.baseline),
            seal=dict(good.seal),
            agents_changed=list(good.agents_changed),
        )
        mutator(fixture)
        target = leg_id.split("-", 1)[0]
        fn = dict((a, f) for a, f, _ in ASSERTIONS)[target]
        try:
            fn(fixture)
            ok = False
            print(f"  VACUOUS {leg_id}: stayed GREEN under its counter-mutation")
        except AssertionError as exc:
            print(f"  RED    {leg_id}: {str(exc).splitlines()[0][:100]}")
    print(f"SELF-TEST: {'PASS' if ok else 'FAIL'}")
    return ok


def run_static() -> bool:
    fixture = build_real_fixture()
    ok = True
    for ac_id, fn, desc in ASSERTIONS:
        try:
            if ac_id == "A8":
                fn(fixture, run_generator_check=True)
            else:
                fn(fixture)
            print(f"PASS {ac_id} ({desc})")
        except CheckFailure as exc:
            ok = False
            print(f"{exc}", file=sys.stderr)
    return ok


def run_real() -> bool:
    """Real-resolver layer — daemon imports; run under `uv run`."""
    sys.path.insert(0, str(ROOT))
    try:
        from daemon.registry import get_registry  # noqa: E402
        from daemon.tools._auth import _check_team_membership  # noqa: E402
    except Exception as exc:  # pragma: no cover - import failure path
        print(f"FAIL REAL-RESOLVER import: {exc}", file=sys.stderr)
        return False
    ok = True
    registry = get_registry()
    # A2-real: all five discovered as AgentMetadata; leader spawns exactly its
    # 4 sonic members; developer spawns exactly sonic-coder.
    for agent in SONIC_AGENTS:
        meta = registry.get_resolved(agent)
        if meta is None:
            print(f"FAIL A2-real {agent} not discovered by get_registry()", file=sys.stderr)
            ok = False
            continue
        if meta.id != agent:
            print(f"FAIL A2-real registry id {meta.id!r} != {agent!r}", file=sys.stderr)
            ok = False
    if ok:
        for target in FROZEN_TEAMS["sonic-leader"]:
            err = _check_team_membership("sonic-leader", target)
            if err is not None:
                print(f"FAIL A2-real sonic-leader cannot spawn {target}: {err}", file=sys.stderr)
                ok = False
        for target in FROZEN_TEAMS["sonic-developer"]:
            err = _check_team_membership("sonic-developer", target)
            if err is not None:
                print(f"FAIL A2-real sonic-developer cannot spawn {target}: {err}", file=sys.stderr)
                ok = False
        err = _check_team_membership("sonic-coder", "worker")
        if err is None:
            print("FAIL A2-real sonic-coder spawned worker (deny-by-default broken)", file=sys.stderr)
            ok = False
    if ok:
        print("PASS A2-real (discovery + leader spawns its 4 + developer spawns coder + coder deny-by-default)")
    # A3-real: high-risk negatives through the REAL gate.  sonic-leader must
    # NOT spawn giter (R10: giter removed from the sonic team).
    negatives = {
        "sonic-leader": ["leader", "developer", "planner", "worker", "giter"],
        "sonic-developer": ["worker", "sonic-reviewer", "sonic-leader"],
        "sonic-coder": ["worker"],
        "sonic-reviewer": ["worker", "sonic-coder"],
        "sonic-tester": ["worker"],
    }
    a3_ok = True
    for caller, targets in negatives.items():
        for target in targets:
            err = _check_team_membership(caller, target)
            if err is None:
                print(f"FAIL A3-real {caller} must NOT spawn {target}", file=sys.stderr)
                a3_ok = False
    if a3_ok:
        print("PASS A3-real (high-risk spawn negatives all denied)")
    else:
        ok = False
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--static", action="store_true",
                        help="stdlib-only contract checks A1-A14 against the tree")
    parser.add_argument("--self-test", action="store_true",
                        help="in-memory counter-mutation matrix")
    parser.add_argument("--real", action="store_true",
                        help="real-resolver layer (daemon import — needs the venv)")
    args = parser.parse_args(argv)
    if not (args.static or args.self_test or args.real):
        parser.print_help()
        return 2
    ok = True
    if args.static:
        ok = run_static() and ok
    if args.self_test:
        ok = run_self_test() and ok
    if args.real:
        ok = run_real() and ok
    print(f"CHECKS: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
