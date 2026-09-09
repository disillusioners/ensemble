"""Privileged category exclusivity + de-scope (W1-P2, task 2.10e).

The ``system-log`` and ``ens-db`` categories are privileged after
W1-P2 (see ``PRIVILEGED_TOOL_CATEGORIES``). They never default-grant
— agents reach them only via explicit ``tools.allow`` entries naming
the category.

Pins:

1. ``developer`` (base) + ``developer[v2]`` + ``wanderer`` LOSE
   ``system-log`` — their resolved tool sets exclude every
   ``ens_system_log_*`` name.
2. ``worker`` KEEPS ``system-log`` (designated break-glass per
   OPEN B / D14) but NEVER ``ens-db`` (DB write path closed at the
   worker layer — architect §7.2 standing guard).
3. ``watcher`` LOSES ``system-log`` by R-SR16 side-effect — empty
   allow + empty deny strips privileged categories (architect §4.6).
4. Enumerates base AND versioned metas (architect §4.3 — standing
   guard for future ``developer[v3]``-class additions).
5. LEADER ADJUDICATION #1 (2.7a): ``agents/_baby_template/meta.json``
   ``tools.allow`` contains ZERO entries from
   ``PRIVILEGED_TOOL_CATEGORIES = frozenset({...})``.
6. LEADER ADJUDICATION #2 (2.7b): variant-tolerant grep over
   ``agents/developer/``, ``agents/developer[v2]/``,
   ``agents/wanderer/`` prompt files returns ZERO matches for
   ``ens_system_log_*`` / ``system-log`` / "log forensics" USAGE
   INSTRUCTIONS. Worker's prompt files retain their existing
   references (no assertion on worker).
7. LEADER ADJUDICATION #6 (D20 env-agnostic): ``tools.deny`` strip
   fires for ``system_restart`` across mocked dev/demo/live env
   values — ``resolve_tool_filter`` is env-agnostic BY DESIGN;
   call-time live refusal at ``upgrade_tools.py:1458-1465`` is
   defense-in-depth (untestable in CI without a live-env fixture —
   recorded acceptance).

Note on the "USAGE INSTRUCTIONS" pattern (LEADER ADJUDICATION #2):
the spec reword rule explicitly requires the reworded prompt to
include "(with reference to maintenancer's KB-03 \`log-forensics\`
skill)" — which is a literal substring match on the variant-tolerant
grep pattern. The acceptance therefore means ZERO USAGE INSTRUCTION
matches, not ZERO literal substring matches. The test pattern below
distinguishes usage forms (imperative + catalog-listing) from
delegation references.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from daemon.tools._tool_registry import PRIVILEGED_TOOL_CATEGORIES
from daemon.tools.instance import (
    _strip_privileged_category_tools,
    resolve_tool_filter,
)

# Repo root — tests/unit/tools/test_privileged_category_system_log.py
REPO_ROOT = Path(__file__).resolve().parents[3]


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_meta(agent_dir: Path) -> dict:
    """Load an agent's meta.json — tolerant of non-existent dirs."""
    meta_path = agent_dir / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _mk_tool(name: str):
    """Build a minimal tool-like object for the strip helper."""
    def _func():
        pass

    _func.__name__ = name
    obj = type(name, (), {})()
    obj.name = name
    obj.func = _func
    obj._tool_category = (
        "system-log" if name in SYSTEM_LOG_TOOLS
        else "ens-db" if name in ENS_DB_TOOLS
        else "system_upgrade" if name in SYSTEM_UPGRADE_TOOLS
        else "general"
    )
    return obj


# Real tools that fall under each category — used for resolve_tool_filter.
SYSTEM_LOG_TOOLS = {
    "ens_system_log_list",
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
}
ENS_DB_TOOLS = {
    "ens_db_postgres_select",
    "ens_db_inspect",
    "ens_db_repair_execute",
    "ens_db_pool_status",
}
SYSTEM_UPGRADE_TOOLS = {
    "release_info",
    "upgrade_status",
    "system_restart",
    "system_upgrade",
}


def _make_categories() -> dict[str, set[str]]:
    """Build the tool_categories map for resolve_tool_filter.

    Includes every real category so deny/allow resolution has the
    right universe. Mirrors ``daemon.tools.instance.list_tools_by_category``
    shape — only the categories relevant to this test are populated.
    """
    return {
        "bash": {"bash"},
        "filesystem": {"read_file", "write_file", "edit_file"},
        "instance": {"spawn_instance", "send_message", "terminate_instance"},
        "system-log": SYSTEM_LOG_TOOLS,
        "system_upgrade": SYSTEM_UPGRADE_TOOLS,
        "ens-db": ENS_DB_TOOLS,
        "db": {"db_conn_add", "db_conn_delete", "db_conn_list", "db_conn_test", "db_postgres_dml_select"},
        "knowledge": {"explore", "experience"},
    }


# ── Test 1: developer base + developer[v2] + wanderer LOSE system-log ──────


class TestDeveloperWandererLoseSystemLog:
    @pytest.mark.parametrize(
        "agent_id,meta_path",
        [
            ("developer", "agents/developer/meta.json"),
            ("developer", "agents/developer[v2]/meta.json"),
            ("wanderer", "agents/wanderer/meta.json"),
        ],
        ids=["developer-base", "developer-v2", "wanderer"],
    )
    def test_resolved_tools_exclude_ens_system_log(
        self, agent_id: str, meta_path: str
    ) -> None:
        meta = json.loads((REPO_ROOT / meta_path).read_text(encoding="utf-8"))
        allow = meta.get("tools", {}).get("allow", [])
        deny = meta.get("tools", {}).get("deny", [])
        resolved = resolve_tool_filter(
            allow=allow,
            deny=deny,
            tool_categories=_make_categories(),
        )
        assert resolved is not None, (
            f"{agent_id} ({meta_path}): resolved is None means ALL tools "
            f"allowed — verify allow list"
        )
        leak = resolved & SYSTEM_LOG_TOOLS
        assert not leak, (
            f"{agent_id} ({meta_path}): resolved tool set leaks system-log "
            f"tools {leak}; allow={allow} deny={deny}"
        )


# ── Test 2: worker KEEPS system-log only, NEVER ens-db ─────────────────────


class TestWorkerKeepsSystemLogNotEnsDb:
    def test_worker_keeps_system_log(self) -> None:
        meta = json.loads(
            (REPO_ROOT / "agents/worker/meta.json").read_text(encoding="utf-8")
        )
        allow = meta.get("tools", {}).get("allow", [])
        deny = meta.get("tools", {}).get("deny", [])
        resolved = resolve_tool_filter(
            allow=allow, deny=deny, tool_categories=_make_categories()
        )
        assert resolved is not None
        keep = resolved & SYSTEM_LOG_TOOLS
        assert keep == SYSTEM_LOG_TOOLS, (
            f"worker must keep ALL system-log tools (break-glass); "
            f"got {keep}; resolved sample={sorted(resolved)[:10]}"
        )

    def test_worker_never_holds_ens_db(self) -> None:
        """Workers NEVER get ens-db — DB write path closed at worker layer."""
        meta = json.loads(
            (REPO_ROOT / "agents/worker/meta.json").read_text(encoding="utf-8")
        )
        allow = meta.get("tools", {}).get("allow", [])
        deny = meta.get("tools", {}).get("deny", [])
        resolved = resolve_tool_filter(
            allow=allow, deny=deny, tool_categories=_make_categories()
        )
        assert resolved is not None
        leak = resolved & ENS_DB_TOOLS
        assert not leak, (
            f"worker must NEVER hold ens-db tools (architect §7.2 standing "
            f"guard); got {leak}"
        )


# ── Test 3: watcher LOSES system-log by R-SR16 side-effect ─────────────────


class TestWatcherLosesSystemLog:
    def test_watcher_empty_allow_strips_system_log(self) -> None:
        """watcher has empty allow → default-grant path applies (architect §4.6
        audit re-verified clean). Privileged categories must NOT join the
        default universe (R-SR16).

        The strip happens in ``_apply_tool_filter`` at
        ``instance.py:4602`` (``resolve_tool_filter`` returns ``None``
        for empty-allow+empty-deny, then the strip runs). We assert
        the strip helper directly with a synthetic tools list."""
        meta = json.loads(
            (REPO_ROOT / "agents/watcher/meta.json").read_text(encoding="utf-8")
        )
        allow = meta.get("tools", {}).get("allow", [])
        deny = meta.get("tools", {}).get("deny", [])
        assert allow == [], (
            f"watcher allow is expected to be []; got {allow} (test fixture drift)"
        )
        # resolve_tool_filter with empty allow + empty/None deny returns
        # None — the "all tools allowed" sentinel. The downstream
        # ``_strip_privileged_category_tools`` then strips privileged.
        sentinel = resolve_tool_filter(
            allow=allow, deny=deny, tool_categories=_make_categories()
        )
        assert sentinel is None, (
            "resolve_tool_filter should return None for empty allow+deny "
            "(the 'all tools allowed' sentinel); the strip helper runs "
            "downstream. If this fails, the resolve path changed."
        )

        # Now exercise the strip with synthetic tools covering every
        # category we care about — the privileged ones must be removed.
        synthetic_tools = [
            _mk_tool("bash"),
            _mk_tool("read_file"),
            _mk_tool("spawn_instance"),
            # Privileged — must be stripped:
            *[_mk_tool(n) for n in SYSTEM_LOG_TOOLS],
            *[_mk_tool(n) for n in ENS_DB_TOOLS],
            *[_mk_tool(n) for n in SYSTEM_UPGRADE_TOOLS],
        ]
        stripped = _strip_privileged_category_tools(synthetic_tools)
        kept_names = {getattr(t, "name", None) or t.func.__name__ for t in stripped}
        leak = kept_names & (SYSTEM_LOG_TOOLS | ENS_DB_TOOLS | SYSTEM_UPGRADE_TOOLS)
        assert not leak, (
            f"_strip_privileged_category_tools leaked privileged tools: {leak}"
        )


# ── Test 4: enumerate base + versioned metas (architect §4.3) ──────────────


class TestEnumerateBaseAndVersionedMetas:
    """Standing guard for future ``developer[v3]``-class additions:
    any future versioned meta MUST also lose system-log."""

    @pytest.fixture(scope="class")
    def all_developer_meta_paths(self) -> list[Path]:
        """All meta.json files under agents/developer*/ — base + versioned."""
        pattern = "agents/developer*/meta.json"
        return sorted(REPO_ROOT.glob(pattern))

    def test_developer_metas_discoverable(self, all_developer_meta_paths: list[Path]) -> None:
        # The fixture itself enumerates — verify >= 2 (base + [v2]).
        assert len(all_developer_meta_paths) >= 2, (
            f"Expected ≥ 2 developer meta files (base + versioned); "
            f"got {all_developer_meta_paths}"
        )

    def test_every_developer_meta_excludes_system_log(
        self, all_developer_meta_paths: list[Path]
    ) -> None:
        for meta_path in all_developer_meta_paths:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", [])
            deny = meta.get("tools", {}).get("deny", [])
            resolved = resolve_tool_filter(
                allow=allow, deny=deny, tool_categories=_make_categories()
            )
            assert resolved is not None, f"{meta_path}: resolved is None"
            leak = resolved & SYSTEM_LOG_TOOLS
            assert not leak, (
                f"{meta_path}: developer meta still leaks system-log tools "
                f"{leak}; allow={allow}"
            )


# ── Test 5: _baby_template has ZERO PRIVILEGED entries (2.7a) ──────────────


class TestBabyTemplateZeroPrivileged:
    """The baby template's illustrative allow-list must NOT contain
    any PRIVILEGED_TOOL_CATEGORIES member — centralization leak fix."""

    def test_baby_template_allow_has_no_privileged_entry(self) -> None:
        meta = json.loads(
            (REPO_ROOT / "agents/_baby_template/meta.json").read_text(
                encoding="utf-8"
            )
        )
        allow = meta.get("tools", {}).get("allow", [])
        leak = set(allow) & set(PRIVILEGED_TOOL_CATEGORIES)
        assert not leak, (
            f"agents/_baby_template/meta.json allow-list contains privileged "
            f"entries {leak}; centralization leak fix (LEADER ADJUDICATION #1, "
            f"task 2.7a). Allow: {allow}"
        )


# ── Test 6: variant-tolerant grep zero-usage-instructions (2.7b) ───────────


# Usage-instruction patterns (imperative + catalog listings).
# Delegation references like "the \`system-log\` tool category is held
# by maintenancer" are EXPLICITLY allowed by the reword rule (the spec
# mandates "reference to maintenancer's KB-03 \`log-forensics\` skill").
_USAGE_INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ``use the system-log tools`` / ``use system-log`` / ``use the
    # system-log category``.
    re.compile(r"\buse\s+(the\s+)?system[-\s]log\b", re.IGNORECASE),
    # ``ens_system_log_<verb>`` — concrete tool-name invocation.
    re.compile(r"\bens_system_log_[a-z_]+\s*\(", re.IGNORECASE),
    # ``Available tools (category: \`system-log\`)`` — catalog header.
    re.compile(r"available\s+tools\s*\(\s*category\s*:\s*`?system[-\s]log", re.IGNORECASE),
    # ``self-healing workflow: ... ens_system_log_<verb>`` — workflow
    # prose calling out a concrete tool.
    re.compile(r"self[-\s]healing.*ens_system_log_", re.IGNORECASE),
    # ``Read daemon logs to understand ...`` — capability claim.
    re.compile(r"\bRead\s+daemon\s+logs\s+to\b", re.IGNORECASE),
    # ``I can inspect daemon logs read-only via ...`` — capability
    # claim about own tool access.
    re.compile(r"\bI\s+can\s+inspect\s+daemon\s+logs\b", re.IGNORECASE),
    re.compile(
        r"\bI\s+have\s+read[-\s]only\s+access\s+to\s+daemon\s+logs\b",
        re.IGNORECASE,
    ),
)


class TestZeroUsageInstructionsInDeScopedAgents:
    """LEADER ADJUDICATION #2 — zero USAGE INSTRUCTION matches.

    Variant-tolerant: enumerates BOTH spaced (``system log``) and
    unspaced (``system-log``) forms. The agent's prompt files may
    still MENTION ``system-log`` in a delegation reference, but
    they MUST NOT contain imperative / catalog / capability-claim
    prose about the agent's own use of the tools.
    """

    @pytest.fixture(scope="class")
    def all_target_files(self) -> list[Path]:
        """The 12 reworded prompt files across developer + developer[v2]
        + wanderer (4 files per agent: soul/rule/workflow/tools_note)."""
        all_files: list[Path] = []
        for agent_dir_name in ("developer", "developer[v2]", "wanderer"):
            agent_dir = REPO_ROOT / "agents" / agent_dir_name
            for prompt_name in ("soul.md", "rule.md", "workflow.md", "tools_note.md"):
                p = agent_dir / prompt_name
                if p.exists():
                    all_files.append(p)
        return all_files

    def test_files_discoverable(self, all_target_files: list[Path]) -> None:
        # Spec says 12 (4 agents × 3). developer + developer[v2] +
        # wanderer = 3 agents; 4 files each = 12 total.
        assert len(all_target_files) == 12, (
            f"Expected 12 reworded prompt files; got {len(all_target_files)}: "
            f"{[str(p.relative_to(REPO_ROOT)) for p in all_target_files]}"
        )

    def test_zero_usage_instructions(
        self, all_target_files: list[Path]
    ) -> None:
        failures: list[tuple[Path, str]] = []
        for path in all_target_files:
            text = path.read_text(encoding="utf-8")
            for pattern in _USAGE_INSTRUCTION_PATTERNS:
                for match in pattern.finditer(text):
                    line_no = text[: match.start()].count("\n") + 1
                    snippet = match.group(0)
                    failures.append((path, f"L{line_no}: {snippet!r}"))
        assert not failures, (
            "Usage-instruction matches found in de-scoped agent prompts:\n"
            + "\n".join(f"  {p.relative_to(REPO_ROOT)}: {info}" for p, info in failures)
        )


# ── Test 7: D20 env-agnostic deny-strip for system_restart ─────────────────


class TestDenyStripIsEnvAgnostic:
    """D20 (LEADER ADJUDICATION #6): ``tools.deny`` strip fires for
    ``system_restart`` across dev/demo/live env values.

    ``resolve_tool_filter`` is env-agnostic BY DESIGN — the
    resolution-level enforcement must work regardless of
    ``ENSEMBLE_SELF_ENV``. The call-time refusal at
    ``upgrade_tools.py:1458-1465`` (``if self_env == "live"``) is
    defense-in-depth and is NOT exercised by this test (no live-env
    CI fixture exists; recorded acceptance).
    """

    @pytest.mark.parametrize("self_env", ["dev", "demo", "live", "sandbox", None])
    def test_system_restart_stripped_for_every_env(self, self_env: str) -> None:
        """A maintenance agent meta.json with ``tools.deny: ['system_restart']``
        and ``tools.allow: ['system_upgrade']`` must resolve WITHOUT
        ``system_restart`` regardless of env. ``system_upgrade`` is
        reachable (it doesn't carry the deny)."""
        # The maintenance agent meta is authored by P1 (not yet
        # merged). For this test we synthesize the meta config
        # inline — same as the spec's "mock/stub the maintenancer
        # meta" directive.
        synthetic_allow = ["system_upgrade", "ens-db", "system-log", "knowledge", "db"]
        synthetic_deny = ["git_commit", "edit_file", "write_file", "system_restart"]

        resolved = resolve_tool_filter(
            allow=synthetic_allow,
            deny=synthetic_deny,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        assert "system_restart" not in resolved, (
            f"system_restart leaked through resolve_tool_filter at env={self_env!r}; "
            f"D20 env-agnostic deny-strip violated"
        )
        # system_upgrade (the rest of the category) IS resolved.
        assert "system_upgrade" in resolved
