#!/usr/bin/env python3
"""Sonic Team (ENSEMBLE-31) — agent-definition contract checker.

Companion checker for pack ``sonic_agent_defs_unit_test`` (see the .sh
sibling for the invocation contract).  Implements the phase-2 spec
(``phase2-test-pack-spec.md``) assertions PLUS the sonic-routing
amendment pins (Emperor ruling 2026-11-14, decisions.md "Sonic routing
amendment") — each derived from the frozen blocks of the combined spec
(``phase1-implementation-spec-v1.1.md``, amend block 2026-11-14) — the
spec wins on any mismatch (spec §7-4 VERIFY-AT-IMPL doctrine).

Combined shape (Emperor ruling 2026-11-13: v1.2 merged into v1.1): FIVE
sonic agents — the new ``sonic-developer`` layer plans/splits/dispatches/
merges and authors HANDOFF-v2; ``sonic-coder`` is a one-shot MODULE
executor (source exploration expected, RAG excluded, commits its module
branch, returns a compact module report); ``sonic-reviewer`` and
``sonic-tester`` operate at INTEGRATION level (HANDOFF-v2 + base..merged).

v0.30.2 amendment (Emperor ruling 2026-11-13, decisions.md R16-R19):
giter re-added to sonic-leader + added to sonic-developer (escape hatch),
devops on the leader, and the image read-trio granted to leader/developer/
coder/reviewer via allow "image" + deny "image_save" (critic pattern).

v0.30.5 sonic-routing amendment (Emperor ruling 2026-11-14, four rulings):
  A — the FROZEN blast-radius predicate (the frozen 14-glob block) is REMOVED; routing
      is by COMPLEXITY, not paths.  The developer assesses complexity after
      explore+plan and PAUSES on high/extremely-high with a handover
      suggestion to the leader; the leader adjudicates ACCEPT (HANDOVER
      REPORT to Ari) or REJECT (narrower split).
  B — cross-team interop: HANDOFF-v2 moves into
      ``.agents/shared/planning/sonic-team/<task>/``; the tester registers
      packs in ``.agents/tester/PACKS.md`` (one line, tagged sonic); the
      leader/developer prompts read the shared planning dir + PACKS; the
      POK ``DEFAULT_AUDIENCE_SETS`` code gains sonic-leader/developer.
  C — the handover is a RECOMMENDATION: user/Ari/leader overrides resolve
      it; nothing may silently downgrade the recorded verdict.
  D — "steady team" vocabulary becomes "expert team".
The former A7 (blast globs) and A10 (blast escalation line) legs are gone;
their replacements pin the complexity-handover protocol + interop.

Three layers:

  --static      stdlib-only contract checks against the real tree.
                NO daemon import (mirrors the
                ``scripts/protection/generate_prompt_artifact_baseline.py``
                doctrine: a bare host python3 must not drag the langchain
                stack).  Daemon CONSTANTS the assertions need
                (``TOOL_REQUIRED_AGENTS``, ``_ALLOWED_MODELS_DEFAULT``,
                ``DEFAULT_AUDIENCE_SETS``) are AST-extracted from source,
                never imported.

  --self-test   the mutation matrix: every assertion is (a) proven GREEN on
                a good in-memory fixture and (b) proven RED on that fixture
                with ONE planted counter-mutation (POK
                ``mutation-01-guards-need-counter-mutation``).  A leg that
                stays green under its own counter-mutation is a FAIL of the
                self-test, never a skip.  NO repo bytes are touched.

  --real        the REAL resolver layer: ``get_registry()`` +
                ``_check_team_membership`` (imports daemon — run under
                ``uv run``).  A2-real/A3-real drive the same expectations
                through the production authorization gate.  If the venv or
                an import fails this layer FAILS LOUDLY — skipping a
                resolver leg is the aegis blind-spot class.

Frozen strings are restated from phase1-implementation-spec-v1.1.md §3/§4
and the amend block, and must stay character-for-character identical (POK
``plan-body-ledger-agreement``).

Implementation notes (disclosed):
- Containment assertions whitespace-normalize both haystack and needle
  before matching: the landed prompts wrap sentences across markdown line
  breaks, so raw substring matching would false-RED on verbatim text.
- A9 (steady-team fence) judges the UNION of the committed delta
  (``git diff --name-only 3f45cd6dd..HEAD -- agents/``) and the working-tree
  status (``git status --porcelain -- agents/``); its fence base is the
  v0.30.4 release commit (this lane's base).
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
# FROZEN constants — single source: phase1-implementation-spec-v1.1.md §1-§4
# (+ the 2026-11-14 amend block).  The combined spec wins on any mismatch.
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
# ---- v0.30.2 roster & support-parity amendment (Emperor ruling 2026-11-13,
# decisions.md R16-R19): giter re-added to sonic-leader AND added to
# sonic-developer (escape hatch — workflow/worktree expertise); devops on the
# leader (rarely invoked by design — infra/deploy escape hatch); the image
# read-trio granted to leader/developer/coder/reviewer via allow "image" +
# deny "image_save" (critic pattern); sonic-tester UNCHANGED.
# ---- guideline-read-tool amendment (2026-11-13): "guidelines" (privileged
# category #22 — the READ-ONLY shipped-guideline window) tail-appended to
# sonic-leader's allow ONLY; sonic-developer/coder/reviewer/tester UNCHANGED.
# ----
FROZEN_TEAMS = {
    "sonic-leader": ["sonic-developer", "sonic-coder", "sonic-reviewer",
                     "sonic-tester", "giter", "devops"],
    "sonic-developer": ["sonic-coder", "giter"],
    "sonic-coder": [],
    "sonic-reviewer": [],
    "sonic-tester": [],
}

FROZEN_TOOLS = {
    "sonic-leader": {
        "allow": ["instance", "bash", "filesystem", "time", "self", "help",
                  "context", "shared_meta_kv", "midflight", "knowledge", "image",
                  "pok", "nest-report", "guidelines"],
        "deny": ["edit_file", "write_file", "image_save"],
    },
    "sonic-developer": {
        "allow": ["instance", "knowledge", "bash", "proc", "filesystem", "todo",
                  "shared_meta_kv", "time", "self", "help", "context", "image",
                  "pok", "nest-report"],
        "deny": ["edit_file", "write_file", "image_save"],
    },
    "sonic-coder": {
        "allow": ["bash", "proc", "filesystem", "time", "self", "help",
                  "context", "shared_meta_kv", "image", "pok", "nest-report"],
        "deny": ["image_save"],
    },
    "sonic-reviewer": {
        "allow": ["bash", "filesystem", "time", "self", "help", "context",
                  "image", "pok", "nest-report"],
        "deny": ["proc", "instance", "service", "midflight", "shared_meta_kv",
                 "infra", "mcp", "secrets", "edit_file", "write_file",
                 "image_save"],
    },
    "sonic-tester": {
        "allow": ["bash", "proc", "filesystem", "time", "self", "help",
                  "context", "pok", "nest-report"],
        "deny": ["instance", "service", "midflight", "shared_meta_kv",
                 "infra", "mcp", "secrets"],
    },
}
# ---- END FROZEN [sonic-tools-v2] ----

# Effective spawn sets (combined spec §9 + v0.30.2 amendment): leader = its six
# team members (4 sonic + giter + devops) PLUS the two knowledge-lane support
# agents (TOOL_REQUIRED_AGENTS maps "knowledge" -> ["explorer", "kb-writer"])
# PLUS image-reader (TOOL_REQUIRED_AGENTS maps "image" -> ["image-reader"]);
# developer = sonic-coder + giter PLUS the same three implicit members; coder/
# reviewer = image-reader ONLY; tester = EMPTY.
EXPECTED_EFFECTIVE = {
    "sonic-leader": {"sonic-developer", "sonic-coder", "sonic-reviewer",
                     "sonic-tester", "giter", "devops", "explorer", "kb-writer",
                     "image-reader"},
    "sonic-developer": {"sonic-coder", "giter", "explorer", "kb-writer",
                        "image-reader"},
    "sonic-coder": {"image-reader"},
    "sonic-reviewer": {"image-reader"},
    "sonic-tester": set(),
}

# ---- FROZEN [sonic-handoff-v2] (combined spec §4.1; section 7 renamed per
# Ruling A: the blast predicate is gone, the handoff carries a complexity
# self-assessment instead).  Authorship = sonic-developer.  v0.30.5 Ruling B:
# the artifact lives in the shared planning dir. ----
HANDOFF_HEADERS = [
    "## Files touched across modules",
    "## What changed",
    "## What is UNTESTED",
    "## Known risks",
    "## How to verify",
    "## Bash-raises used",
    "## Complexity self-assessment",
]
HANDOFF_PATH_PREFIX = (
    "HANDOFF_PATH = .agents/shared/planning/sonic-team/<task>/HANDOFF.md"
)
PLANNING_HANDOFF_DIR_TOKEN = ".agents/shared/planning/sonic-team/<task>/HANDOFF.md"
HANDOFF_FILES = (
    "agents/sonic-developer/workflow.md",
    "agents/sonic-reviewer/workflow.md",
    "agents/sonic-tester/workflow.md",
)

# ---- FROZEN [sonic-module-report-v2] (combined spec §3.4) — the coder's
# compact module report shape (a MESSAGE, never a file).  v0.30.5: the former
# ``blast:`` field is replaced by ``complexity:`` (the predicate is gone; a
# module that turns out heavier than briefed flags its own band). ----
MODULE_REPORT_PREFIX = "MODULE-REPORT: <module-slug>"
MODULE_REPORT_KEYS = (
    "branch: <branch-name> @ <tip-sha>",
    "files:",
    "changed:",
    "self-test:",
    "untested:",
    "risks:",
    "raises:",
    "complexity:",
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
LEADER_RULE = "agents/sonic-leader/rule.md"
LEADER_WORKFLOW = "agents/sonic-leader/workflow.md"
TESTER_RULE = "agents/sonic-tester/rule.md"
TESTER_WORKFLOW = "agents/sonic-tester/workflow.md"

# ---- FROZEN [sonic-fast-path-v1] (combined spec §7 / D-E) — the gate line
# pinned in the leader's rule.md AND workflow.md ----
FAST_PATH_GATE_LINE = (
    "FAST-PATH GATE: single module, single coder, no new agent dir, no "
    "cross-module risk — anything larger goes through sonic-developer."
)

# ---- Ruling A: the complexity-handover protocol (deterministic shape) ----
COMPLEXITY_BANDS = "low | medium | high | extremely-high"
DEV_PAUSE_SENTENCE = (
    "HIGH / EXTREMELY-HIGH → PAUSE the pipeline: do NOT dispatch any coder"
)
DEV_HANDOVER_LINE = (
    "handover suggestion: <complexity> because <reasons from the plan>"
)
DEV_ORDER_SENTENCE = "explore → plan → complexity verdict → THEN dispatch"
LEADER_ACCEPT_SENTENCE = (
    "ACCEPT → I end the commission as a HANDOVER REPORT to Ari"
)
LEADER_REJECT_SENTENCE = "REJECT → I continue"
HANDOVER_REPORT_FIELDS = (
    "HANDOVER-REPORT:",
    "complexity:",
    "reasons:",
    "leader-assessment:",
    "work-state:",
    "override:",
    "recommendation: reroute to the expert team",
)
# ---- Ruling C: override paths + no-silent-downgrade ----
OVERRIDE_TOKENS = ("USER OVERRIDE", "ARI OVERRIDE", "LEADER OVERRIDE")
NO_DOWNGRADE_SENTENCE = "NOTHING MAY SILENTLY DOWNGRADE THE RECORDED VERDICT"
HANDOVER_VERDICT_TOKEN = "handover-verdict:"
# ---- Ruling D: terminology ----
EXPERT_TEAM_TOKEN = "expert team"

# ---- Ruling B: interop contract ----
PACKS_REGISTRY = ".agents/tester/PACKS.md"
PACKS_GATE_TOKEN = "gate-verifiable = registered"
PACKS_PACK_ID = "sonic_agent_defs_unit_test"
PLANNING_DIR_READ_TOKEN = ".agents/shared/planning/"

# ---- Ruling B.4: POK audience defaults (CODE, AST-extracted) ----
POK_INJECTION_FILE = "daemon/services/pok_injection.py"
POK_AUDIENCE_CONST = "DEFAULT_AUDIENCE_SETS"
EXPECTED_POK_AUDIENCE = {
    "operation": {"ari", "leader", "devops", "sonic-leader", "sonic-developer"},
    "implementation": {"leader", "developer", "sonic-developer"},
}

# Ari note (Ruling A/D rework of the sonic triage section).
ARI_RULE = "agents/ari/rule.md"
ARI_HEADING = "### Sonic Team (Default) — Expert Team for Genuinely Heavy Work"
ARI_ANCHORS = [
    "Routing is by COMPLEXITY, not paths",
    "roughly 80–90% of tasks",
    "never silently downgrade a recorded complexity verdict",
]
_BACKING_NOTE_TOKEN = "backed by Plane"

# Baseline / seal anchors.
BASELINE_FILE = "agents/.prompt-artifacts.sha256"
SEAL_FILE = "tests/test_chart_image_delivery_e2e.py"
SEAL_DICT_NAME = "SEALED_SHA_BASELINES"
# Regenerated at v0.30.5 (was 201 at the v0.30.2 tag; agent dirs added across
# v0.30.3/v0.30.4 grew the tree to 217).  Row COUNT is fixed by the tree, not
# by this lane (no artifact files added/removed); edit only after a regen.
EXPECTED_BASELINE_ROWS = 217
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

# This lane's base — the A9 steady-team fence is judged relative to it.
AGENTS_FENCE_BASE = "3f45cd6dd"

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


def _contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


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


def _eval_audience_node(node) -> set:
    """Evaluate a set-ish AST node: ``{...}`` / ``set([...])`` / ``frozenset({...})``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("set", "frozenset", "list", "tuple"):
        return set(_eval_audience_node(node.args[0]))
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return {ast.literal_eval(elt) for elt in node.elts}
    raise CheckFailure(
        "A23",
        f"unsupported DEFAULT_AUDIENCE_SETS value node: {type(node).__name__}",
    )


def extract_pok_audience(root: Path = ROOT) -> dict:
    """AST-extract ``DEFAULT_AUDIENCE_SETS`` (dict[str, frozenset]) from source."""
    path = root / POK_INJECTION_FILE
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        target_node = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == POK_AUDIENCE_CONST for t in node.targets
        ):
            target_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == POK_AUDIENCE_CONST and node.value is not None:
            target_node = node.value
        if target_node is not None:
            if not isinstance(target_node, ast.Dict):
                raise CheckFailure("A23", f"{POK_AUDIENCE_CONST} is not a dict literal")
            out = {}
            for k, v in zip(target_node.keys, target_node.values):
                out[ast.literal_eval(k)] = _eval_audience_node(v)
            return out
    raise CheckFailure(
        "static", f"const {POK_AUDIENCE_CONST!r} not found in {path}"
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
    metas: dict  # agent -> RAW meta.json text
    texts: dict  # relpath -> file text
    baseline: dict  # baseline path -> sha row
    seal: dict  # SEALED_SHA_BASELINES rows
    agents_changed: list  # A9 input: committed-delta UNION working-tree paths
    tool_required_agents: dict
    allowed_models_default: list
    pok_audience: dict

    def bytes(self, relpath: str) -> bytes:
        return self.texts[relpath].encode("utf-8")

    def sha(self, relpath: str) -> str:
        return _sha256_bytes(self.bytes(relpath))


def _git_changed_agents(root: Path) -> list:
    """A9 input: committed delta UNION working-tree status, under agents/."""
    changed: set = set()
    committed = subprocess.run(
        ["git", "diff", "--name-only", f"{AGENTS_FENCE_BASE}..HEAD", "--", "agents/"],
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
        LEADER_WORKFLOW,
        LEADER_RULE,
        DEV_WORKFLOW,
        DEV_RULE,
        DEV_TOOLS_NOTE,
        "agents/sonic-coder/workflow.md",
        "agents/sonic-coder/rule.md",
        "agents/sonic-coder/tools_note.md",
        "agents/sonic-reviewer/workflow.md",
        "agents/sonic-reviewer/rule.md",
        TESTER_WORKFLOW,
        TESTER_RULE,
        PACKS_REGISTRY,
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
        pok_audience=extract_pok_audience(root),
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
# The assertion functions.  Each raises CheckFailure on drift.
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
    """A3: frozen allow/deny arrays verbatim + effective spawn sets."""
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
    """A4: per-agent llm_model pin map; BOTH pin values in the in-tree set."""
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
    """A6: HANDOFF-v2's 7 headers in the DEVELOPER workflow + HANDOFF_PATH
    three-way (developer/reviewer/tester, now the shared-planning-dir literal)
    + the FROZEN [sonic-module-report-v2] template in the coder workflow."""
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


def a8_ari_pins(fx: Fixture, run_generator_check: bool = False) -> None:
    """A8: Ari rule.md exactly-1-Plane + backing note; seal consistency;
    baseline 20 sonic rows + total EXPECTED_BASELINE_ROWS (+ generator --check)."""
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
    # (c) baseline: 20 sonic rows with LIVE values + total EXPECTED_BASELINE_ROWS.
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
    """A9: changed agents/ set (committed UNION worktree) = sonic-* + baseline
    + exactly ari/rule.md; positive shape: sonic dirs present.

    v0.30.5: the fence base is this lane's base commit (3f45cd6dd), so the
    only non-sonic, non-baseline agent change this lane may make is the Ari
    note.  The expert-team agent dirs stay UNTOUCHED (the dispatcher fence).
    """
    residual = [p for p in fx.agents_changed
                if not p.startswith("agents/sonic-")
                and p != BASELINE_FILE]
    if sorted(residual) != [ARI_RULE]:
        raise CheckFailure("A9", f"unexpected agents/ edit: {residual or '(none)'}")
    if ARI_RULE not in fx.agents_changed:
        raise CheckFailure("A9", f"expected {ARI_RULE} in the changed set (positive shape)")
    if not any(p.startswith("agents/sonic-") for p in fx.agents_changed):
        raise CheckFailure("A9", "sonic dirs absent from the changed set (positive shape)")


def a10_dev_pause_handover(fx: Fixture) -> None:
    """A10 (Ruling A-i/ii): the developer pauses on high/extremely-high — the
    PAUSE sentence and the handover-suggestion line live in BOTH rule.md AND
    workflow.md (positive shape, normalized containment)."""
    for rel in (DEV_RULE, DEV_WORKFLOW):
        text = fx.texts[rel]
        if not _contains(text, DEV_PAUSE_SENTENCE):
            raise CheckFailure("A10", f"developer pause sentence missing in {rel}")
        if not _contains(text, DEV_HANDOVER_LINE):
            raise CheckFailure("A10", f"developer handover-suggestion line missing in {rel}")
        if not _contains(text, COMPLEXITY_BANDS):
            raise CheckFailure("A10", f"developer complexity bands missing in {rel}")


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
    for anchor in ARI_ANCHORS:
        if not _contains(region, anchor):
            raise CheckFailure("A11", f"Ari note heading/anchor missing: {anchor!r}")
    if _count_token(region, "plane"):
        raise CheckFailure("A11", "plane in Ari note")
    if re.search(r"^## Cardinal", region, flags=re.MULTILINE):
        raise CheckFailure("A11", "## Cardinal inside Ari note")


def a12_developer_never_experience(fx: Fixture) -> None:
    """A12 (combined spec §2.3/D-G): the sonic-developer's THREE artifacts
    each carry the pinned never-experience/explore-bound boundary sentence."""
    checks = (
        (DEV_RULE, DEV_RULE_NEVER_EXPERIENCE),
        (DEV_WORKFLOW, DEV_WORKFLOW_EXPLORE_BOUND),
        (DEV_TOOLS_NOTE, DEV_TOOLSNOTE_NEVER_EXPERIENCE),
    )
    for rel, needle in checks:
        if not _contains(fx.texts[rel], needle):
            raise CheckFailure("A12", f"developer never-experience boundary drift in {rel}")


def a13_coder_discipline(fx: Fixture) -> None:
    """A13 (combined spec §3.3): the coder artifacts assert BOTH discipline
    shapes — source-exploration-expected AND RAG-excluded — in rule.md AND
    workflow.md, plus the tools_note no-knowledge marker."""
    for rel in CODER_DISCIPLINE_FILES:
        text = fx.texts[rel]
        if not _contains(text, CODER_SOURCE_EXPLORE_SENTENCE):
            raise CheckFailure("A13", f"coder source-exploration shape missing in {rel}")
        if not _contains(text, CODER_RAG_EXCLUSION_SENTENCE):
            raise CheckFailure("A13", f"coder RAG-exclusion shape missing in {rel}")
    note = fx.texts[CODER_TOOLS_NOTE]
    if CODER_NO_KNOWLEDGE_MARKER not in note:
        raise CheckFailure("A13", "coder tools_note missing the no-knowledge marker")
    if re.search(r"^- `knowledge` —", note, flags=re.MULTILINE):
        raise CheckFailure("A13", "coder tools_note still carries a HELD knowledge row")


def a14_leader_fast_path(fx: Fixture) -> None:
    """A14 (combined spec §7/D-E): the FROZEN fast-path gate line in the
    leader's rule.md AND workflow.md."""
    for rel in (LEADER_RULE, LEADER_WORKFLOW):
        if FAST_PATH_GATE_LINE not in fx.texts[rel]:
            raise CheckFailure("A14", f"fast-path gate line drift in {rel}")


def a15_handover_shape(fx: Fixture) -> None:
    """A15 (Ruling A-ii): the HANDOVER-REPORT shape — first line + six fields —
    present in the leader's workflow (the shape block lives there; the rule
    names the HANDOVER REPORT it produces)."""
    workflow = fx.texts[LEADER_WORKFLOW]
    if not _contains(workflow, "HANDOVER-REPORT:"):
        raise CheckFailure("A15", "HANDOVER-REPORT shape missing in workflow")
    for field in HANDOVER_REPORT_FIELDS:
        if not _contains(workflow, field):
            raise CheckFailure("A15", f"handover-report field missing in workflow: {field!r}")


def a16_leader_adjudication(fx: Fixture) -> None:
    """A16 (Ruling A-iii): ACCEPT ends as a HANDOVER REPORT to Ari; REJECT
    continues — both pinned in the leader's rule.md AND workflow.md."""
    for rel in (LEADER_RULE, LEADER_WORKFLOW):
        text = fx.texts[rel]
        if not _contains(text, LEADER_ACCEPT_SENTENCE):
            raise CheckFailure("A16", f"leader ACCEPT adjudication missing in {rel}")
        if not _contains(text, LEADER_REJECT_SENTENCE):
            raise CheckFailure("A16", f"leader REJECT adjudication missing in {rel}")


def a17_dev_ordering(fx: Fixture) -> None:
    """A17 (Ruling A-iv): the developer's ORDERING — explore → plan →
    complexity verdict → THEN dispatch — pinned in the rule AND workflow."""
    for rel in (DEV_RULE, DEV_WORKFLOW):
        if not _contains(fx.texts[rel], DEV_ORDER_SENTENCE):
            raise CheckFailure("A17", f"developer verdict-ordering sentence missing in {rel}")


def a18_overrides(fx: Fixture) -> None:
    """A18 (Ruling C-a/b/c): the three override paths present in the leader's
    rule (the binding record) — USER / ARI / LEADER OVERRIDE."""
    rule = fx.texts[LEADER_RULE]
    for token in OVERRIDE_TOKENS:
        if token not in rule:
            raise CheckFailure("A18", f"override path missing in leader rule: {token!r}")
    # The override must be a RECOMMENDATION, not a hard stop, and the original
    # verdict must stay visible — the leader rule carries that binding sentence.
    if not _contains(rule, "RECOMMENDATION"):
        raise CheckFailure("A18", "leader rule does not state the handover is a RECOMMENDATION")


def a19_no_silent_downgrade(fx: Fixture) -> None:
    """A19 (Ruling C-vi): nothing silently downgrades the recorded verdict —
    the binding sentence + the handover-verdict attribution token."""
    if not _contains(fx.texts[LEADER_RULE], NO_DOWNGRADE_SENTENCE):
        raise CheckFailure("A19", "leader rule missing the no-silent-downgrade sentence")
    if not _contains(fx.texts[LEADER_WORKFLOW], NO_DOWNGRADE_SENTENCE):
        raise CheckFailure("A19", "leader workflow missing the no-silent-downgrade sentence")
    if HANDOVER_VERDICT_TOKEN not in fx.texts[LEADER_WORKFLOW]:
        raise CheckFailure("A19", "leader workflow missing the handover-verdict attribution token")


def a20_interop_handoff_path(fx: Fixture) -> None:
    """A20 (Ruling B.1/B.5): HANDOFF-v2 lives in the shared planning dir — the
    literal in all three consuming workflows + the developer's authorship path."""
    for rel in HANDOFF_FILES:
        if not _contains(fx.texts[rel], PLANNING_HANDOFF_DIR_TOKEN):
            raise CheckFailure("A20", f"HANDOFF planning-dir token missing in {rel}")
    if PLANNING_HANDOFF_DIR_TOKEN not in fx.texts[DEV_WORKFLOW]:
        raise CheckFailure("A20", "developer workflow missing the shared-planning-dir handoff path")


def a21_packs_registration(fx: Fixture) -> None:
    """A21 (Ruling B.2/B.5): the sonic pack is registered in the shared PACKS
    registry (one line), and the tester prompt carries the registration
    contract (standard test tree + one-line PACKS entry)."""
    if PACKS_PACK_ID not in fx.texts[PACKS_REGISTRY]:
        raise CheckFailure("A21", f"pack {PACKS_PACK_ID} not registered in {PACKS_REGISTRY}")
    tester = fx.texts[TESTER_RULE] + "\n" + fx.texts[TESTER_WORKFLOW]
    if PACKS_REGISTRY not in tester:
        raise CheckFailure("A21", "tester prompt does not reference the PACKS registry")
    if not _contains(tester, PACKS_GATE_TOKEN):
        raise CheckFailure("A21", "tester prompt missing the gate-verifiable = registered token")
    if not _contains(tester, "standard test tree"):
        raise CheckFailure("A21", "tester prompt missing the standard-test-tree write instruction")


def a22_prompt_protocol_lines(fx: Fixture) -> None:
    """A22 (Ruling B.3/B.5): both-team prompt protocol — the developer reads
    the shared planning dir regardless of authoring team, and leader/developer/
    tester each point at the shared PACKS registry."""
    if PLANNING_DIR_READ_TOKEN not in fx.texts[DEV_RULE]:
        raise CheckFailure("A22", "developer rule missing the shared-planning-dir read line")
    for rel in (LEADER_RULE, DEV_RULE, TESTER_RULE):
        if PACKS_REGISTRY not in fx.texts[rel]:
            raise CheckFailure("A22", f"{rel} missing the shared PACKS-registry line")


def a23_pok_audience(fx: Fixture) -> None:
    """A23 (Ruling B.4/B.5): the CODE defaults put sonic-leader/developer in
    `operation` and sonic-developer in `implementation` (release content)."""
    actual = {k: set(v) for k, v in fx.pok_audience.items()}
    expected = {k: set(v) for k, v in EXPECTED_POK_AUDIENCE.items()}
    for audience, members in expected.items():
        got = actual.get(audience)
        if got is None:
            raise CheckFailure("A23", f"DEFAULT_AUDIENCE_SETS missing audience {audience!r}")
        if got != members:
            raise CheckFailure(
                "A23", f"DEFAULT_AUDIENCE_SETS[{audience!r}] actual={sorted(got)} "
                f"expected={sorted(members)}")


ASSERTIONS = (
    ("A1", a1_meta_parse, "meta.json parse + id==dirname x5"),
    ("A2", a2_team_members, "team_members exact sets (leader=4 sonic + giter + devops; developer=[coder, giter])"),
    ("A3", a3_allow_deny, "frozen allow/deny arrays + effective spawn sets"),
    ("A4", a4_llm_model, "llm_model pin map + both in default set"),
    ("A5", a5_recursion, "multiplier 7 coder-only"),
    ("A6", a6_integration_surfaces, "HANDOFF-v2 headers in developer workflow + shared-planning HANDOFF_PATH three-way + coder module-report template"),
    ("A8", a8_ari_pins, "Ari plane==1 + seal + baseline 217/20"),
    ("A9", a9_steady_team, "steady/expert-team fence (union predicate vs 3f45cd6dd)"),
    ("A10", a10_dev_pause_handover, "developer pause on high/extremely-high + handover-suggestion line"),
    ("A11", a11_ari_note, "Ari sonic-team note heading + anchors + region hygiene"),
    ("A12", a12_developer_never_experience, "developer never-experience boundary pinned in 3 artifacts"),
    ("A13", a13_coder_discipline, "coder source-exploration + RAG-exclusion shapes + tools_note no-knowledge marker"),
    ("A14", a14_leader_fast_path, "leader fast-path gate line in rule.md + workflow.md"),
    ("A15", a15_handover_shape, "HANDOVER-REPORT first line + six fields"),
    ("A16", a16_leader_adjudication, "leader ACCEPT (handover report) / REJECT (continue) adjudication"),
    ("A17", a17_dev_ordering, "developer explore→plan→verdict→dispatch ordering"),
    ("A18", a18_overrides, "override paths USER/ARI/LEADER + recommendation-not-hard-stop"),
    ("A19", a19_no_silent_downgrade, "no-silent-downgrade sentence + handover-verdict attribution"),
    ("A20", a20_interop_handoff_path, "HANDOFF-v2 shared-planning-dir path (3 consumers + developer)"),
    ("A21", a21_packs_registration, "PACKS one-line registration + tester registration contract"),
    ("A22", a22_prompt_protocol_lines, "developer planning-dir read + leader/developer/tester PACKS lines"),
    ("A23", a23_pok_audience, "POK DEFAULT_AUDIENCE_SETS includes sonic-leader/developer"),
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


def _good_workflow(role: str, with_handoff: bool, with_module_report: bool = False) -> str:
    parts = [f"# {role} workflow\n\n", "## 1. Intake\n\nRead the commission.\n\n"]
    if with_handoff:
        parts.append(
            "## Handoff\n\n"
            f"{HANDOFF_PATH_PREFIX}   # repo-root-relative; the ABSOLUTE path is passed in every dispatch\n\n"
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
            "complexity: <low | medium | high | extremely-high>\n\n"
        )
    return "".join(parts)


def _good_leader_rule() -> str:
    return (
        "# sonic-leader rules\n\n"
        f"`handover suggestion: <complexity> because <reasons>`\n\n"
        f"1. {LEADER_ACCEPT_SENTENCE} (developer's reasoning + my assessment + work state). "
        f"{LEADER_REJECT_SENTENCE} with a narrower split and recorded reasoning.\n\n"
        "2. Overrides: (a) USER OVERRIDE — proceed, original verdict visible; "
        "(b) ARI OVERRIDE — Ari proceeds over my recommendation; "
        "(c) LEADER OVERRIDE — my reject-and-continue stands. The handover is a "
        f"RECOMMENDATION, never a hard stop. {NO_DOWNGRADE_SENTENCE}: an override only "
        "fills `override:`.\n\n"
        f"{FAST_PATH_GATE_LINE}\n\n"
        f"{PACKS_REGISTRY} is the shared pack registry — gate-verifiable = registered.\n\n"
    )


def _good_leader_workflow() -> str:
    return (
        "# sonic-leader workflow\n\n"
        f"{FAST_PATH_GATE_LINE}\n\n"
        "## Complexity handover\n\n"
        f"{LEADER_ACCEPT_SENTENCE}; {LEADER_REJECT_SENTENCE}.\n\n"
        "HANDOVER-REPORT: <task-slug>\n"
        "complexity: <high | extremely-high>   # original verdict, never softened\n"
        "reasons: <developer reasons>\n"
        "leader-assessment: <mine>\n"
        "work-state: <branch/worktree/sha>\n"
        "override: <none | user:... | ari:...>\n"
        "recommendation: reroute to the expert team\n\n"
        f"{NO_DOWNGRADE_SENTENCE}. A proceed-under-override report carries "
        "`handover-verdict: <original complexity> — overridden by <user|ari>`.\n\n"
        f"{PACKS_REGISTRY} is the shared pack registry — gate-verifiable = registered.\n\n"
        + _good_workflow("sonic-leader", with_handoff=False)
    )


def _good_dev_rule() -> str:
    return (
        "# Sonic Developer Rules\n\n"
        f"{DEV_RULE_NEVER_EXPERIENCE}\n\n"
        "## Complexity assessment\n\n"
        f"Assess {COMPLEXITY_BANDS}. {DEV_PAUSE_SENTENCE}, do NOT do half-work. "
        f"Report: {DEV_HANDOVER_LINE}. {DEV_ORDER_SENTENCE}.\n\n"
        "## Shared surfaces\n\n"
        f"Read relevant {PLANNING_DIR_READ_TOKEN} entries regardless of authoring team. "
        f"{PACKS_REGISTRY} is the shared pack registry — gate-verifiable = registered.\n\n"
    )


def _good_dev_workflow() -> str:
    return (
        "# Sonic Developer Workflow\n\n"
        f"{DEV_WORKFLOW_EXPLORE_BOUND}\n\n"
        "## Complexity verdict\n\n"
        f"Assess {COMPLEXITY_BANDS}. {DEV_PAUSE_SENTENCE} and report "
        f"{DEV_HANDOVER_LINE}. {DEV_ORDER_SENTENCE}.\n\n"
        f"Handoff lives at {PLANNING_HANDOFF_DIR_TOKEN}.\n\n"
        + _good_workflow("sonic-developer", with_handoff=True)
    )


def _good_tester_rule() -> str:
    return (
        "# Sonic Tester Rules\n\n"
        "## Standard test tree + ONE line\n\n"
        "Write packs into the standard test tree and register each with one line in "
        f"{PACKS_REGISTRY} (tagged sonic) — {PACKS_GATE_TOKEN}.\n\n"
    )


def build_good_fixture() -> Fixture:
    metas = {
        "sonic-leader": {
            "id": "sonic-leader", "name": "Sonic Leader", "version": "1.1.1",
            "llm_model": "agentic", "innate_skills": ["todo"],
            "skill_injection": False, "default_queue": "system_parallel_queue",
            "tools": {"allow": list(FROZEN_TOOLS["sonic-leader"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-leader"]["deny"])},
            "team_members": list(FROZEN_TEAMS["sonic-leader"]),
        },
        "sonic-developer": {
            "id": "sonic-developer", "name": "Sonic Developer", "version": "1.0.1",
            "llm_model": "agentic", "innate_skills": ["todo"],
            "skill_injection": False, "default_queue": "system_parallel_queue",
            "tools": {"allow": list(FROZEN_TOOLS["sonic-developer"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-developer"]["deny"])},
            "team_members": list(FROZEN_TEAMS["sonic-developer"]),
        },
        "sonic-coder": {
            "id": "sonic-coder", "name": "Sonic Coder", "version": "1.1.1",
            "llm_model": "coding", "innate_skills": ["todo"],
            "skill_injection": False, "recursion_limit_multiplier": 7,
            "tools": {"allow": list(FROZEN_TOOLS["sonic-coder"]["allow"]),
                      "deny": list(FROZEN_TOOLS["sonic-coder"]["deny"])},
            "team_members": [],
        },
        "sonic-reviewer": {
            "id": "sonic-reviewer", "name": "Sonic Reviewer", "version": "1.1.1",
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
        LEADER_WORKFLOW: _good_leader_workflow(),
        LEADER_RULE: _good_leader_rule(),
        DEV_WORKFLOW: _good_dev_workflow(),
        DEV_RULE: _good_dev_rule(),
        DEV_TOOLS_NOTE: (
            "# Sonic Developer Tools\n\n"
            f"- `knowledge` — ONE explore at plan-open. {DEV_TOOLSNOTE_NEVER_EXPERIENCE}.\n"
        ),
        "agents/sonic-coder/workflow.md": (
            "# Sonic Coder Workflow\n\n"
            f"{CODER_SOURCE_EXPLORE_SENTENCE}\n\n"
            f"{CODER_RAG_EXCLUSION_SENTENCE}\n\n"
            + _good_workflow("sonic-coder", with_handoff=False, with_module_report=True)
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
        "agents/sonic-reviewer/workflow.md": _good_workflow(
            "sonic-reviewer", with_handoff=True),
        TESTER_WORKFLOW: (
            _good_workflow("sonic-tester", with_handoff=True)
            + f"\nWrite packs into the standard test tree; register in {PACKS_REGISTRY} — "
            f"{PACKS_GATE_TOKEN}.\n"
        ),
        TESTER_RULE: _good_tester_rule(),
        PACKS_REGISTRY: (
            "# PACKS registry (fixture)\n\n"
            f"| `{PACKS_PACK_ID}` | wrapper | sonic team defs | 2026-11-14 | PASS |\n"
        ),
        ARI_RULE: (
            "### CRITICAL: TASK TRIAGE\n\n"
            "- \"Help me set up project X\" — could be quick or huge; **ask** if unclear\n\n"
            f"{ARI_HEADING}\n\n"
            "Routing is by COMPLEXITY, not paths. Most project work defaults to the\n"
            "sonic team — roughly 80–90% of tasks. The expert team takes genuinely\n"
            "heavy, high-complexity work. A sonic handover re-routes to the expert\n"
            "team; I never silently downgrade a recorded complexity verdict.\n\n"
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
        pok_audience={k: set(v) for k, v in EXPECTED_POK_AUDIENCE.items()},
    )
    # Fill the remaining sonic artifact texts (baseline hashing needs all 20).
    for rel in SONIC_BASELINE_PATHS:
        if rel not in fixture.texts:
            fixture.texts[rel] = f"# {rel} (fixture)\n\nfixture body.\n"
    # Seal: 35 rows, ari/rule.md pinned to the sha of the fixture bytes.
    seal = {ARI_RULE: fixture.sha(ARI_RULE)}
    for i in range(EXPECTED_SEAL_ROWS - 1):
        seal[f"agents/filler-{i}/rule.md"] = f"{i:064x}"
    fixture.seal = seal
    # Baseline: exactly EXPECTED_BASELINE_ROWS rows, sonic rows matching bytes.
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
        fixture.tool_required_agents.pop("knowledge")

    def a3_reviewer_drop_bash(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["allow"].remove("bash"))

    def a3_reviewer_deny_drop_editfile(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["deny"].remove("edit_file"))

    def a3_leader_drop_giter(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["team_members"].remove("giter"))

    def a3_leader_drop_devops(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["team_members"].remove("devops"))

    def a3_developer_drop_giter(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m["team_members"].remove("giter"))

    def a3_leader_drop_image(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["tools"]["allow"].remove("image"))

    def a3_developer_drop_image(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m["tools"]["allow"].remove("image"))

    def a3_coder_drop_image(fixture):
        _mutate_meta(fixture, "sonic-coder",
                     lambda m: m["tools"]["allow"].remove("image"))

    def a3_reviewer_drop_image(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["allow"].remove("image"))

    def a3_leader_deny_drop_image_save(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["tools"]["deny"].remove("image_save"))

    def a3_developer_deny_drop_image_save(fixture):
        _mutate_meta(fixture, "sonic-developer",
                     lambda m: m["tools"]["deny"].remove("image_save"))

    def a3_coder_deny_drop_image_save(fixture):
        _mutate_meta(fixture, "sonic-coder",
                     lambda m: m["tools"]["deny"].remove("image_save"))

    def a3_reviewer_deny_drop_image_save(fixture):
        _mutate_meta(fixture, "sonic-reviewer",
                     lambda m: m["tools"]["deny"].remove("image_save"))

    def a3_tester_add_image(fixture):
        _mutate_meta(fixture, "sonic-tester",
                     lambda m: m["tools"]["allow"].append("image"))

    def a3_effective_drop_image_row(fixture):
        fixture.tool_required_agents.pop("image")

    def a3_leader_add_design(fixture):
        _mutate_meta(fixture, "sonic-leader",
                     lambda m: m["tools"]["allow"].append("design"))

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
        fixture.texts[TESTER_WORKFLOW] = fixture.texts[TESTER_WORKFLOW].replace(
            HANDOFF_PATH_PREFIX, "HANDOFF_PATH = handoff.md", 1)

    def a6_coder_module_report_drop(fixture):
        fixture.texts["agents/sonic-coder/workflow.md"] = fixture.texts[
            "agents/sonic-coder/workflow.md"].replace(
            "MODULE-REPORT: <module-slug>\n", "", 1)

    def a8_ari_plane_append(fixture):
        fixture.texts[ARI_RULE] += "\n<!-- Plane -->\n"

    def a8_seal_revert(fixture):
        fixture.seal[ARI_RULE] = "fc0a7542157d79d16011a48d7e79d31d33fe1376ca5fc5c694d47e670bcfdf10"

    def a8_baseline_delete_row(fixture):
        fixture.baseline.pop(SONIC_BASELINE_PATHS[0])

    def a9_extra_steady_path(fixture):
        fixture.agents_changed = fixture.agents_changed + ["agents/leader/rule.md"]

    def a10_pause_corrupt(fixture):
        fixture.texts[DEV_RULE] = fixture.texts[DEV_RULE].replace(
            DEV_PAUSE_SENTENCE, "assess the complexity but proceed regardless", 1)

    def a10_handover_line_corrupt(fixture):
        fixture.texts[DEV_WORKFLOW] = fixture.texts[DEV_WORKFLOW].replace(
            DEV_HANDOVER_LINE, "report the complexity", 1)

    def a11_heading_rename(fixture):
        fixture.texts[ARI_RULE] = fixture.texts[ARI_RULE].replace(
            ARI_HEADING, "### Sonic Lane (Opt-In)", 1)

    def a11_anchor_drop(fixture):
        fixture.texts[ARI_RULE] = fixture.texts[ARI_RULE].replace(
            "never silently downgrade a recorded complexity verdict",
            "never overrule", 1)

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
        fixture.texts[LEADER_WORKFLOW] = fixture.texts[LEADER_WORKFLOW].replace(
            FAST_PATH_GATE_LINE,
            "FAST-PATH GATE: small work may skip the developer layer.", 1)

    def a15_field_drop(fixture):
        fixture.texts[LEADER_WORKFLOW] = fixture.texts[LEADER_WORKFLOW].replace(
            "leader-assessment: <mine>\n", "", 1)

    def a16_accept_corrupt(fixture):
        fixture.texts[LEADER_RULE] = fixture.texts[LEADER_RULE].replace(
            LEADER_ACCEPT_SENTENCE, "ACCEPT → I quietly absorb the work", 1)

    def a17_ordering_corrupt(fixture):
        fixture.texts[DEV_RULE] = fixture.texts[DEV_RULE].replace(
            DEV_ORDER_SENTENCE, "dispatch first, then think about complexity", 1)

    def a18_override_corrupt(fixture):
        fixture.texts[LEADER_RULE] = fixture.texts[LEADER_RULE].replace(
            "USER OVERRIDE", "soft hint", 1)

    def a19_downgrade_corrupt(fixture):
        fixture.texts[LEADER_WORKFLOW] = fixture.texts[LEADER_WORKFLOW].replace(
            NO_DOWNGRADE_SENTENCE, "an override may quietly lower the verdict", 1)

    def a20_handoff_path_corrupt(fixture):
        fixture.texts[DEV_WORKFLOW] = fixture.texts[DEV_WORKFLOW].replace(
            PLANNING_HANDOFF_DIR_TOKEN, "HANDOFF.md")

    def a21_packs_row_drop(fixture):
        fixture.texts[PACKS_REGISTRY] = fixture.texts[PACKS_REGISTRY].replace(
            PACKS_PACK_ID, "some_other_pack", 1)

    def a22_protocol_line_drop(fixture):
        fixture.texts[LEADER_RULE] = fixture.texts[LEADER_RULE].replace(
            PACKS_REGISTRY, "a pack list", 1)

    def a23_audience_drop(fixture):
        fixture.pok_audience["operation"].discard("sonic-developer")

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
        ("A3-leader-drop-giter", a3_leader_drop_giter),
        ("A3-leader-drop-devops", a3_leader_drop_devops),
        ("A3-developer-drop-giter", a3_developer_drop_giter),
        ("A3-leader-drop-image", a3_leader_drop_image),
        ("A3-developer-drop-image", a3_developer_drop_image),
        ("A3-coder-drop-image", a3_coder_drop_image),
        ("A3-reviewer-drop-image", a3_reviewer_drop_image),
        ("A3-leader-deny-drop-image_save", a3_leader_deny_drop_image_save),
        ("A3-developer-deny-drop-image_save", a3_developer_deny_drop_image_save),
        ("A3-coder-deny-drop-image_save", a3_coder_deny_drop_image_save),
        ("A3-reviewer-deny-drop-image_save", a3_reviewer_deny_drop_image_save),
        ("A3-tester-add-image", a3_tester_add_image),
        ("A3-effective-drop-image-row", a3_effective_drop_image_row),
        ("A3-leader-add-design", a3_leader_add_design),
        ("A4-developer-model-coding", a4_developer_model_coding),
        ("A4-coder-model-agentic", a4_coder_model_agentic),
        ("A4-reviewer-model-coding", a4_reviewer_model_coding),
        ("A5-coder-drop-multiplier", a5_coder_drop_multiplier),
        ("A5-developer-add-multiplier", a5_developer_add_multiplier),
        ("A5-reviewer-add-multiplier", a5_reviewer_add_multiplier),
        ("A6-developer-drop-header", a6_developer_drop_header),
        ("A6-tester-handoff-literal", a6_tester_handoff_literal),
        ("A6-coder-module-report-drop", a6_coder_module_report_drop),
        ("A8-ari-plane-append", a8_ari_plane_append),
        ("A8-seal-revert", a8_seal_revert),
        ("A8-baseline-delete-row", a8_baseline_delete_row),
        ("A9-extra-steady-path", a9_extra_steady_path),
        ("A10-pause-corrupt", a10_pause_corrupt),
        ("A10-handover-line-corrupt", a10_handover_line_corrupt),
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
        ("A15-field-drop", a15_field_drop),
        ("A16-accept-corrupt", a16_accept_corrupt),
        ("A17-ordering-corrupt", a17_ordering_corrupt),
        ("A18-override-corrupt", a18_override_corrupt),
        ("A19-downgrade-corrupt", a19_downgrade_corrupt),
        ("A20-handoff-path-corrupt", a20_handoff_path_corrupt),
        ("A21-packs-row-drop", a21_packs_row_drop),
        ("A22-protocol-line-drop", a22_protocol_line_drop),
        ("A23-audience-drop", a23_audience_drop),
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
            tool_required_agents=dict(good.tool_required_agents),
            allowed_models_default=list(good.allowed_models_default),
            pok_audience={k: set(v) for k, v in good.pok_audience.items()},
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
    negatives = {
        "sonic-leader": ["leader", "developer", "planner", "worker"],
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
                        help="stdlib-only contract checks against the tree")
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
