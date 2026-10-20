"""Maintenancer tool resolution (W1-P2, task 2.10f — CRITICAL).

Asserts the resolved tool set for the maintenancer agent matches the
spec's allow/deny contract. The REAL ``agents/maintenancer/meta.json``
(authored in P1, merge 8eeee457) is loaded directly — the P2-era
inline stub worked around P1 being unmerged and is retired (W1
integration review FIX-NOW item 4).

Pin contract:

* maintenancer gets ``ens-db`` + ``system-log`` + ``system_upgrade``
  (excluding ``system_restart`` — D20 deny strip) + ``knowledge``
  + ``db`` (D19 — external user-registered connections only; NOT
  ``ensemble_prod``).
* ``system_restart`` is EXCLUDED via the ``tools.deny`` strip at
  ``resolve_tool_filter`` time (D20 enforcement — the deny-list
  is the only resolution-level mechanism that survives category-
  grant expansions of ``system_upgrade``, which includes
  ``system_restart`` at ``upgrade_tools.py:1435``).
* ``developer`` + ``developer[v2]`` get NEITHER ``system-log`` NOR
  ``ens-db``.
* ``worker`` gets ``system-log`` only (NOT ``ens-db`` — DB write
  path closed at the worker layer per architect §7.2 standing
  guard).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    PRIVILEGED_TOOL_CATEGORIES,
)
from daemon.tools.instance import resolve_tool_filter

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Helpers ────────────────────────────────────────────────────────────────


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
DB_TOOLS = {
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
}
KNOWLEDGE_TOOLS = {"explore", "experience"}


def _make_categories() -> dict[str, set[str]]:
    """Mirror the real tool_categories for resolve_tool_filter."""
    return {
        "bash": {"bash"},
        "filesystem": {"read_file", "write_file", "edit_file"},
        "instance": {"spawn_instance", "send_message", "terminate_instance"},
        "system-log": SYSTEM_LOG_TOOLS,
        "system_upgrade": SYSTEM_UPGRADE_TOOLS,
        "ens-db": ENS_DB_TOOLS,
        "db": DB_TOOLS,
        "knowledge": KNOWLEDGE_TOOLS,
    }


# ── Maintenancer meta (REAL — loaded from agents/maintenancer/meta.json) ───


def _maintenancer_allow_deny() -> tuple[list[str], list[str]]:
    """Load the REAL maintenancer meta's ``tools.allow`` / ``tools.deny``.

    The P2 PR stubbed this meta inline because P1 (which authors
    ``agents/maintenancer/meta.json``) was unmerged at the time. P1
    has merged — the real file is the contract under test now. A
    missing file fails LOUDLY (at collection) rather than silently
    testing a stub.
    """
    meta_path = REPO_ROOT / "agents" / "maintenancer" / "meta.json"
    assert meta_path.exists(), (
        "agents/maintenancer/meta.json must exist (P1 merged, 8eeee457); "
        "this test pins the REAL meta, not an inline stub"
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    tools = meta.get("tools", {})
    return tools.get("allow", []), tools.get("deny", [])


# Per spec task 1.2 (D5 + D19 + D20) — now loaded from the real meta:
#   tools.allow:  ["system-log", "ens-db", "knowledge", "system_upgrade", "db"]
#   tools.deny:   ["git_commit", "edit_file", "write_file", "system_restart"]
MAINTAINER_META_ALLOW, MAINTAINER_META_DENY = _maintenancer_allow_deny()


class TestRealMaintenancerMetaShape:
    """Pin the real meta's grant/deny SHAPE (not just resolution
    outcomes) — a meta.json edit that changes what maintenancer is
    granted or denied must fail here first."""

    def test_real_meta_allow_is_exact_category_set(self) -> None:
        """tools.allow = exactly the 5 categories: the privileged pair
        (system-log, ens-db), system_upgrade, knowledge, and db
        (D19 — external user-registered connections only)."""
        assert MAINTAINER_META_ALLOW == [
            "system-log",
            "ens-db",
            "knowledge",
            "system_upgrade",
            "db",
        ]

    def test_real_meta_denies_system_restart_per_d20(self) -> None:
        """tools.deny strips system_restart (D20) — the deny-list is
        the only resolution-level mechanism that survives
        system_upgrade category expansion (upgrade_tools.py:1435)."""
        assert MAINTAINER_META_DENY == [
            "git_commit",
            "edit_file",
            "write_file",
            "system_restart",
        ]


# ── Tests ──────────────────────────────────────────────────────────────────


class TestMaintenancerResolves:
    def test_maintenancer_gets_ens_db(self) -> None:
        resolved = resolve_tool_filter(
            allow=MAINTAINER_META_ALLOW,
            deny=MAINTAINER_META_DENY,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        leak = resolved & ENS_DB_TOOLS
        assert leak == ENS_DB_TOOLS, (
            f"maintenancer must resolve ALL ens_db_* tools; "
            f"got {resolved & ENS_DB_TOOLS}; missing {ENS_DB_TOOLS - leak}"
        )

    def test_maintenancer_gets_system_log(self) -> None:
        resolved = resolve_tool_filter(
            allow=MAINTAINER_META_ALLOW,
            deny=MAINTAINER_META_DENY,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        leak = resolved & SYSTEM_LOG_TOOLS
        assert leak == SYSTEM_LOG_TOOLS

    def test_maintenancer_gets_knowledge(self) -> None:
        resolved = resolve_tool_filter(
            allow=MAINTAINER_META_ALLOW,
            deny=MAINTAINER_META_DENY,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        leak = resolved & KNOWLEDGE_TOOLS
        assert leak == KNOWLEDGE_TOOLS, (
            f"maintenancer must resolve all knowledge tools (for first-turn "
            f"RAG mirror duty); got {resolved & KNOWLEDGE_TOOLS}"
        )

    def test_maintenancer_gets_db_category(self) -> None:
        """D19 — ``db`` category resolves to user-registered external
        connections. The category expansion populates the external
        tools (db_conn_* + db_postgres_dml_select). The
        ``ensemble_prod`` connection is NEVER registered in the
        ``db`` category — R13 standing guard — so the resolved set
        contains only the external-connection tools, not any
        synthetic ``ensemble_prod`` alias."""
        resolved = resolve_tool_filter(
            allow=MAINTAINER_META_ALLOW,
            deny=MAINTAINER_META_DENY,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        leak = resolved & DB_TOOLS
        assert leak == DB_TOOLS, (
            f"maintenancer must resolve all db category tools; got {leak}"
        )
        # R13 standing guard: no synthetic ``ensemble_prod`` alias
        # is in the resolved set (such an alias would never be in the
        # db category's tool set, but we assert it explicitly).
        assert "ensemble_prod" not in resolved

    def test_maintenancer_gets_system_upgrade_excluding_system_restart(self) -> None:
        """D20: ``system_upgrade`` category expansion includes
        ``system_restart``, but the deny-list strips it at the
        resolution level. The rest of the category is reachable."""
        resolved = resolve_tool_filter(
            allow=MAINTAINER_META_ALLOW,
            deny=MAINTAINER_META_DENY,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        # system_restart MUST be denied — the resolution-level strip.
        assert "system_restart" not in resolved, (
            f"system_restart leaked through resolve_tool_filter despite "
            f"tools.deny entry (D20 enforcement violated)"
        )
        # The rest of the system_upgrade category is reachable.
        for tool_name in SYSTEM_UPGRADE_TOOLS - {"system_restart"}:
            assert tool_name in resolved, (
                f"system_upgrade tool {tool_name!r} not in resolved set"
            )


class TestDeveloperFamilyHasNoSystemLogNoEnsDb:
    """developer + developer[v2] get NEITHER system-log NOR ens-db."""

    @pytest.mark.parametrize(
        "meta_path",
        ["agents/developer/meta.json", "agents/developer[v2]/meta.json"],
        ids=["developer-base", "developer-v2"],
    )
    def test_no_system_log_no_ens_db(self, meta_path: str) -> None:
        meta = json.loads((REPO_ROOT / meta_path).read_text(encoding="utf-8"))
        allow = meta.get("tools", {}).get("allow", [])
        deny = meta.get("tools", {}).get("deny", [])
        resolved = resolve_tool_filter(
            allow=allow, deny=deny, tool_categories=_make_categories()
        )
        assert resolved is not None
        leak = resolved & (SYSTEM_LOG_TOOLS | ENS_DB_TOOLS)
        assert not leak, (
            f"{meta_path}: developer family must NOT resolve system-log or "
            f"ens-db tools; leaked {leak}"
        )


class TestWorkerResolvesSystemLogOnly:
    """worker gets system-log only (NOT ens-db — DB write path closed)."""

    def test_worker_resolves_system_log(self) -> None:
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
        assert keep == SYSTEM_LOG_TOOLS

    def test_worker_does_not_resolve_ens_db(self) -> None:
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
            f"worker must NEVER hold ens_db_* (architect §7.2 standing "
            f"guard — DB write path closed at the worker layer); "
            f"leaked {leak}"
        )


class TestPrivilegedCategoryRegistryShape:
    """PRIVILEGED_TOOL_CATEGORIES must be exactly twelve categories
    (the daemon-internal authority trio ``system_upgrade``,
    ``system-log``, ``ens-db`` plus ``ens-env`` per the W4 leader
    decision 2026-10-02, ``view-views`` per the REWORK 2026-10-07
    M1 user refinement, ``cu`` per the cu-mcp task 2026-10-16,
    ``secrets`` per the agent-secrets P1 commission 2026-10-17,
    ``settings`` per the settings-worker-slots P2 commission
    2026-10-18, ``board`` per the ari-tools Phase 1 commission
    2026-10-20, ``self-alarm`` per the ari-tools Phase 2
    commission 2026-10-20, and ``view-write`` + ``board-link`` per
    the ari-tools Phase 3 commission 2026-10-20). The ``service``
    category was REMOVED from this set by user override 2026-09-16
    (D4 reversed; see
    ``.agents/shared/planning/service-tool/decisions.md`` §D4
    override note) — R-SR16 silent additions are visible by pin;
    this is pin file 3 of 7 per the A14/D18 same-PR pin rule
    (the fifth exact-set pin added by the iteration-002 council B2
    correction)."""

    def test_privileged_categories_contains_twelve_entries(self) -> None:
        # W4 (leader decision 2026-10-02): the set was a quartet —
        # ``ens-env`` joined (key-returning tool, explicit opt-in
        # only). REWORK 2026-10-07 (M1) added ``view-views`` (the
        # read-only ``view_link`` URL minter; user decision:
        # RESTRICTED first-release visibility to ari + leader +
        # designer only). cu-mcp (2026-10-16) added ``cu`` (Computer
        # Use MCP built-in's prefix-overridden tools; full
        # local-control surface, explicit ``tools.allow: ["cu"]``
        # on ari + leader meta.json — empty-allow universe must
        # NOT auto-grant it). agent-secrets P1 (2026-10-17, plan
        # f11965039) added ``secrets`` (the encrypted-secret-store
        # category; opt-in-only via
        # ``tools.allow: ["secrets"]`` on the day-one allow-list
        # ``worker`` / ``tester`` / ``leader`` / ``ari``; the
        # trust ruling has no per-secret grants — per-agent
        # ``tools.allow`` is the ONLY access boundary).
        # settings-worker-slots P2 (2026-10-18) added
        # ``settings`` (the daemon-runtime-settings category;
        # opt-in-only; ari + leader allow entries landed
        # 2026-10-18, P3). ari-tools Phase 1 (2026-10-20) added
        # ``board`` (the global work-board category; opt-in-only;
        # the board is ONE GLOBAL namespace, so the empty-allow
        # inherit universe must never auto-grant write authority
        # over it — ari + leader allow entries are the only access
        # path). Method
        # name kept for grep-stability; the count moved
        # 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 with the same-PR pin
        # updates (D18/A14) — 4 = ens-env (2026-10-02), 5 =
        # view-views (2026-10-07), 6 = cu (2026-10-16), 7 =
        # secrets (2026-10-17), 8 = settings (2026-10-18), 9 =
        # board (2026-10-20), 10 = self-alarm (2026-10-20). Every
        # step landed its pin update in the SAME COMMIT as the
        # category addition EXCEPT 7 = secrets: that registration
        # (152a9c93b) carried no pin update, and the pins were
        # closed in the immediately-following same-PR commit
        # 72fbd6126 ("update SAME-PR triple-pin tests").
        assert PRIVILEGED_TOOL_CATEGORIES == frozenset({
            "system_upgrade",
            "system-log",
            "ens-db",
            "ens-env",
            "view-views",
            "cu",
            # agent-secrets P1 (2026-10-17, plan f11965039):
            # the encrypted-secret-store category. opt-in-only;
            # worker / tester / leader / ari day-one allow-list.
            "secrets",
            # settings-worker-slots P2 (2026-10-18): the
            # daemon-runtime-settings category (settings_get /
            # settings_set / settings_clear). opt-in-only;
            # mutates daemon-global runtime state — NEVER
            # default-granted (R-SR16).
            "settings",
            # ari-tools Phase 1 (2026-10-20): the global work-board
            # category (board_create / board_update / board_list /
            # board_get). opt-in-only; the board is ONE GLOBAL
            # namespace, so the empty-allow inherit universe must
            # never auto-grant write authority over it. ari + leader
            # allow entries are the only access path.
            "board",
            # ari-tools Phase 2 (2026-10-20): the self-alarm wrapper
            # category (self_alarm). opt-in-only; arming an alarm makes
            # a FRESH Ari instance execute the message text VERBATIM, so
            # the empty-allow inherit universe must never auto-grant it.
            # The ari tools.allow entry is the only access path.
            "self-alarm",
            # ari-tools Phase 3 (2026-10-20): the report-page writer
            # category (view_write). opt-in-only; the ``reports`` live-
            # view root is shared and customer-facing, so the empty-allow
            # inherit universe must never auto-grant write authority over
            # it. The ari-only tools.allow entry is the only access path;
            # the tool body ALSO carries the runtime agent_id gate.
            "view-write",
            # ari-tools Phase 3 (2026-10-20): the live board's link-
            # minter category (board_link). opt-in-only; minting the
            # board's passcode authorises reading the user's whole work
            # board. The ari + leader tools.allow entries are the only
            # access path; the tool body ALSO carries the runtime
            # agent_id gate.
            "board-link",
        })

    def test_ens_db_registered_in_category_modules(self) -> None:
        """CATEGORY_MODULES entry exists for the new category (binding
        step 2 of the 10-step checklist, ``upgrade_tools.py:110-142``)."""
        assert CATEGORY_MODULES.get("ens-db") == "daemon.tools.ens_db_tools"
