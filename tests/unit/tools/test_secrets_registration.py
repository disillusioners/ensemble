"""Registration + opt-in tests for the ``secrets`` tool category
(agent-secrets P1, plan f11965039, 2026-10-17).

Mirrors the 4-step discipline documented in
``daemon/tools/upgrade_tools.py:110-143`` (the P2.2
REGISTRATION CHECKLIST) and adapted for the ``secrets``
category:

1. **Decorator order** — ``@register_tool_category("secrets")``
   MUST sit ABOVE ``@tool`` so the category attr is set on
   the raw function before langchain wraps it. Verified by
   (a) reading the live ``_tool_category`` and
   ``_tool_category_first_party`` attrs on the StructuredTool
   and (b) source-greping the decorator order.
2. **CATEGORY_MODULES entry** — ``"secrets": "daemon.tools.secrets_tools"``
   in ``daemon/tools/_tool_registry.py``.
3. **DYNAMIC_TOOL_NAMES** — the 6 factory-created names
   (decorator-only registration is SILENTLY INVISIBLE — this
   is the gotcha the §8 checklist exists to catch).
4. **KNOWN_TOOL_NAMES** — the regen-pasted frozen-binary
   fallback also carries the 6 names (drift equality is
   owned by ``test_frozen_tool_name_discovery``).

Plus the privileged-category pin (septet → octet → nonet → decet):

* ``PRIVILEGED_TOOL_CATEGORIES`` is exactly twelve entries
  (``system_upgrade``, ``system-log``, ``ens-db``, ``ens-env``,
  ``view-views``, ``cu``, ``secrets``, ``settings``, ``board``,
  ``self-alarm``) — NEVER default-granted
  (R-SR16: opt-in-only via per-agent ``tools.allow``).
* The default-deny seam applies: an EMPTY-allow agent
  (watcher-like) gets ZERO ``secrets`` tools (R-SR16).

Plus the day-one allow-list (Q2 ruling):

* ``worker``, ``tester``, ``leader``, ``ari`` carry
  ``"secrets"`` in their ``tools.allow`` — the only agents
  with the category in v1.
* ``designer``, ``sketcher``, ``critic``, ``charter`` are
  NOT in the day-one list — they get ZERO ``secrets`` tools
  through the standard ``_apply_tool_filter`` /
  ``_strip_privileged_category_tools`` seams.

Plus the live resolution pin:

* A worker agent's REAL ``meta.json`` resolves the 6
  ``secret_*`` tools through the REAL
  ``create_instance_tools()`` path.
* A non-allow agent (e.g. ``charter``) does NOT resolve any
  ``secret_*`` tool.

Synthetic agents are staged under ``tmp_path`` (a copy of
the real ``agents/watcher/meta.json`` with modified tools
config) with the registry boot path redirected — same
pattern as ``test_upgrade_registration.py`` /
``test_attestation_registration.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import daemon.registry as dr
from daemon.registry import AgentRegistry
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)


# Repo root: tests/unit/tools/test_secrets_registration.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

SECRETS_CATEGORY = "secrets"
SECRETS_MODULE = "daemon.tools.secrets_tools"
SECRETS_TOOL_NAMES: frozenset[str] = frozenset({
    "secret_generate_ssh_keypair",
    "secret_generate_api_token",
    "secret_store",
    "secret_list",
    "secret_read_public",
    "secret_retire",
})


# ── Step 1-4: static registration points (greppable checklist) ──


class TestStaticRegistrationChecklist:
    """The §8 / P2.2 checklist adapted for the ``secrets``
    category (Phase 1 of the agent-secrets commission,
    2026-10-17)."""

    def test_step1_ast_discovery_finds_all_six(self) -> None:
        """Step 1 + AST walker: the category module's
        factory-created ``@tool`` functions are
        discoverable from source. ``discover_source_only_tool_names``
        walks the entire AST tree, so the module-level
        functions are caught whether the factory closure or
        module-level registration is used.
        """
        discovered = discover_source_only_tool_names()
        missing = SECRETS_TOOL_NAMES - discovered
        assert missing == set(), (
            f"AST discovery missed: {missing}"
        )

    def test_step2_category_modules_entry(self) -> None:
        """Step 2: ``CATEGORY_MODULES['secrets']`` points at
        ``daemon.tools.secrets_tools``."""
        assert CATEGORY_MODULES.get(SECRETS_CATEGORY) == SECRETS_MODULE

    def test_step3_dynamic_tool_names_contains_all_six(self) -> None:
        """Step 3: the 6 factory-created names live in
        ``DYNAMIC_TOOL_NAMES`` so startup validation knows
        them before any instance is built (decorator-only =
        SILENTLY INVISIBLE if missing)."""
        missing = SECRETS_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), (
            f"DYNAMIC_TOOL_NAMES missing: {missing}"
        )

    def test_step3b_known_tool_names_contains_all_six(self) -> None:
        """Step 3b: the frozen-binary fallback universe
        (``KNOWN_TOOL_NAMES``) carries the 6 names too.
        Drift equality is owned by
        ``test_frozen_tool_name_discovery``; this test
        stays focused on the explicit ``secrets`` category
        pin."""
        missing = SECRETS_TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), (
            f"KNOWN_TOOL_NAMES missing: {missing}; "
            f"regen via: uv run python -c \"from daemon.tools._tool_registry "
            f"import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))\""
        )

    def test_step4_create_instance_tools_list_append_present_in_source(self) -> None:
        """Step 4: the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``: ``create_secret_tools(...)``
        extended into the tools list. Decorator-only
        registration is SILENTLY INVISIBLE — the §8
        checklist's central gotcha."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .secrets_tools import create_secret_tools" in source
        assert "tools.extend(secret_tool_list)" in source
        assert "create_secret_tools(" in source

    def test_decorator_order_register_above_tool_in_source(self) -> None:
        """Decorator order: ``@register_tool_category("secrets")``
        MUST sit above ``@tool`` so the category attr is set
        on the raw function before langchain wraps it
        (langchain ``@tool`` preserves the attr via
        functools.wraps, but only if the register decorator
        ran first). Verified by source-grepping the
        decorator order in the ``secrets_tools`` module."""
        source = (REPO_ROOT / "daemon" / "tools" / "secrets_tools.py").read_text(
            encoding="utf-8"
        )
        # All 6 tools should have the decorator above @tool.
        register_count = source.count('@register_tool_category("secrets")')
        tool_count = source.count("@tool")
        # 6 tools + 1 in the docstring could be 6 or 7 depending
        # on how the decorator is written; pin ≥ 6 to be safe
        # (the count is a structural check; the regex check
        # below pins the order).
        assert register_count >= 6, (
            f"expected ≥ 6 @register_tool_category decorators; "
            f"got {register_count}"
        )
        assert tool_count >= 6, (
            f"expected ≥ 6 @tool decorators; got {tool_count}"
        )
        # Order check: the first ``@register_tool_category``
        # appears BEFORE the first ``@tool``.
        register_idx = source.find('@register_tool_category("secrets")')
        tool_idx = source.find("@tool")
        assert register_idx != -1, "@register_tool_category decorator missing"
        assert tool_idx != -1, "@tool decorator missing"
        assert register_idx < tool_idx, (
            f"decorator order wrong: register@{register_idx}, "
            f"tool@{tool_idx}"
        )

    def test_secrets_in_privileged_categories(self) -> None:
        """The ``secrets`` category is entry #7 in
        ``PRIVILEGED_TOOL_CATEGORIES`` (sextet → septet → octet →
        nonet; the sextet→septet step per the Q2 ruling 2026-10-17
        promoted ``secrets`` to #7, the septet→octet step
        per the settings-worker-slots P2 commission 2026-10-18
        promoted ``settings`` to #8, and the octet→nonet step per
        the ari-tools Phase 1 commission 2026-10-20 promoted
        ``board`` to #9, and the nonet→decet step per the ari-tools
        Phase 2 commission 2026-10-20 promoted ``self-alarm`` to #10).
        It is NEVER
        default-granted (R-SR16: opt-in-only via
        per-agent ``tools.allow``).

        The full set is exactly twelve entries:
        ``system_upgrade``, ``system-log``, ``ens-db``,
        ``ens-env``, ``view-views``, ``cu``,
        ``secrets``, ``settings`` (settings-worker-slots
        P2, 2026-10-18), ``board`` (ari-tools Phase 1,
        2026-10-20) and ``self-alarm`` (ari-tools Phase 2,
        2026-10-20). Adding another category without
        bumping this pin is a deliberate trust
        decision (the same-PR pin update rule per
        D18/A14).
        """
        assert SECRETS_CATEGORY in PRIVILEGED_TOOL_CATEGORIES
        # The full set is exactly twelve entries — the
        # same-PR pin (the SEVEN-file rule; the fifth
        # exact-set pin was added by the iteration-002
        # council B2 correction, the sixth + seventh by
        # ari-tools phases 1 + 2). The 3+1
        # integration sanity pin lives at
        # ``tests/integration/test_service_tool_flag_off_byte_identical.py``.
        assert PRIVILEGED_TOOL_CATEGORIES == frozenset({
            "system_upgrade",
            "system-log",
            "ens-db",
            "ens-env",
            "view-views",
            "cu",
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
            # nest-ops (2026-10-21): the two maintenancer fleet categories
            # (read #13 / act #14) — opt-in-only, never default-granted.
            "instance_read",
            "instance_ops",
            "board-link",
        })


# ── Live tool behavior: decorator order survives langchain wrap ─


class TestLiveToolBehavior:
    """Verify the live StructuredTool carries the category
    metadata that ``@register_tool_category`` set on the raw
    function — i.e. that decorator order preserved the attr
    through the langchain ``@tool`` wrap."""

    def test_secrets_tools_carry_category_attrs(self) -> None:
        from daemon.tools.secrets_tools import create_secret_tools

        manager = MagicMock(name="InstanceManager")
        # Tolerate a manager with no service wired (the
        # tools dereference ``manager._secret_service`` at
        # call time, NOT at construction time — the
        # factory call is safe with a None-stub manager).
        manager._secret_service = None
        manager._instance_repository = None
        tools = create_secret_tools(manager, "test-inst-id")
        assert len(tools) == 6
        for tool_obj in tools:
            assert tool_obj._tool_category == SECRETS_CATEGORY, (
                f"{tool_obj.name!r} missing _tool_category='secrets'"
            )
            assert tool_obj._tool_category_first_party is True
            assert hasattr(tool_obj, "_full_doc_")
            # The 6 names are exactly the documented
            # P1 tool surface.
            assert tool_obj.name in SECRETS_TOOL_NAMES


# ── Synthetic-agent staging (tmp tree + registry redirect) ───


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict | None
) -> Path:
    """Stage a synthetic agent dir under tmp_path by cloning
    watcher's real meta.json shape with a replaced tools
    config. Returns the tmp agents dir."""
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)  # irrelevant for tool filtering
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = tools_cfg
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(
        json.dumps(real_meta), encoding="utf-8"
    )
    return agents_dir


@pytest.fixture
def registry_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Factory fixture: stage a synthetic agent, install a
    registry that discovers it, and return a fresh
    tool-builder bound to that registry."""

    def _install(agents_dir: Path) -> AgentRegistry:
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _build_instance_tools(agent_id: str) -> dict[str, object]:
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    # Tolerate a None-stub SecretService (the factory
    # dereferences it at call time only).
    manager._secret_service = None
    manager._instance_repository = None
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


def _secret_names(by_name: dict) -> set:
    return set(by_name) & SECRETS_TOOL_NAMES


# ── Step 4 functional: allow resolves all 6; default-deny
# resolves none ─────────────────────────────────────────────────


class TestFunctionalRegistration:
    """The functional side: a synthetic agent with
    ``secrets`` in ``tools.allow`` resolves all 6 tools
    through the REAL ``create_instance_tools()`` path;
    without it, the 6 tools are NOT resolved."""

    def test_allow_entry_resolves_all_six(
        self, tmp_path: Path, registry_for
    ) -> None:
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-allow", {"allow": [SECRETS_CATEGORY]}
        )
        registry = registry_for(agents_dir)
        assert registry.exists("syn-allow")
        by_name = _build_instance_tools("syn-allow")
        got = _secret_names(by_name)
        assert got == SECRETS_TOOL_NAMES, (
            f"tools.allow=[secrets] must resolve all 6, got {got}"
        )

    def test_allow_single_tool_name_grants_only_that_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """An individual tool name in allow passes through
        WITHOUT expanding the whole category
        (``resolve_tool_filter`` semantics: category names
        expand, individual names don't)."""
        _stage_synthetic_agent(
            tmp_path,
            "syn-one-tool",
            {"allow": ["bash", "secret_list"]},
        )
        agents_dir = tmp_path / "agents"
        registry = registry_for(agents_dir)
        assert registry.exists("syn-one-tool")
        by_name = _build_instance_tools("syn-one-tool")
        # Only ``secret_list`` resolves — the other 5 are
        # stripped because the category key
        # (``secrets``) is not in the allow list.
        assert _secret_names(by_name) == {"secret_list"}

    def test_without_allow_entry_resolves_none(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path,
            "syn-none",
            {
                "allow": [
                    "bash",
                    "filesystem",
                    "time",
                    "help",
                ]
            },
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-none")
        assert _secret_names(by_name) == set(), (
            "agent without a secrets allow entry must not "
            "see the tools"
        )

    def test_empty_allow_default_deny_rsr16(
        self, tmp_path: Path, registry_for
    ) -> None:
        """R-SR16: an EMPTY allow list means
        "every non-privileged category" — the privileged
        ``secrets`` category is NEVER default-granted
        (watcher-like agent; the review-minor-#3
        functional gap)."""
        _stage_synthetic_agent(
            tmp_path, "syn-empty-allow", {"allow": []}
        )
        registry = registry_for(tmp_path / "agents")
        assert registry.exists("syn-empty-allow")
        by_name = _build_instance_tools("syn-empty-allow")
        assert _secret_names(by_name) == set(), (
            "empty-allow agent default-granted the privileged "
            "secrets category — R-SR16 regression"
        )
        # …but the agent still gets the ordinary
        # categories (the exclusion is scoped to
        # privileged categories only, not a blanket
        # deny).
        assert "bash" in by_name or "time" in by_name

    def test_no_tools_config_at_all_default_deny_rsr16(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The other default-allow path: tools config
        entirely ABSENT — the strip must apply there too
        (defense-in-depth)."""
        _stage_synthetic_agent(tmp_path, "syn-no-tools", None)
        # tools: null in meta
        meta_path = (
            tmp_path / "agents" / "syn-no-tools" / "meta.json"
        )
        meta = json.loads(meta_path.read_text())
        meta["tools"] = None
        meta_path.write_text(json.dumps(meta))
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-no-tools")
        assert _secret_names(by_name) == set()

    def test_deny_wins_over_allow(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path,
            "syn-deny",
            {
                "allow": [SECRETS_CATEGORY],
                "deny": [SECRETS_CATEGORY],
            },
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-deny")
        assert _secret_names(by_name) == set(), (
            "deny must strip the category"
        )


# ── Real-meta resolution (worker + non-allow agents) ─────


class TestRealAgentResolution:
    """The REAL registry: the day-one allow-list (Q2
    ruling) is ``worker``, ``tester``, ``leader``,
    ``ari`` — only those four carry ``"secrets"`` in
    ``tools.allow``. Every other agent is denied the
    6 tools through the real ``create_instance_tools()``
    path."""

    @pytest.fixture
    def real_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> AgentRegistry:
        registry = AgentRegistry(REPO_ROOT / "agents")
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    @pytest.mark.parametrize(
        "agent_id", ["worker", "tester", "leader", "ari"]
    )
    def test_day_one_allow_list_resolves_all_six(
        self, real_registry: AgentRegistry, agent_id: str
    ) -> None:
        """The Q2 day-one allow-list (worker / tester /
        leader / ari) resolves all 6 ``secrets`` tools
        through the REAL ``create_instance_tools()`` path.
        """
        meta = (
            real_registry.get_version(agent_id, None)
            or real_registry.get_resolved(agent_id)
        )
        assert meta is not None, f"{agent_id} meta must exist"
        assert meta.tools is not None
        assert SECRETS_CATEGORY in meta.tools.allow, (
            f"{agent_id} meta.json tools.allow missing "
            f"{SECRETS_CATEGORY!r}; got {meta.tools.allow}"
        )
        by_name = _build_instance_tools(agent_id)
        assert _secret_names(by_name) == SECRETS_TOOL_NAMES, (
            f"{agent_id} instance must resolve all 6 "
            f"secrets tools via the create_instance_tools "
            f"path; got {sorted(_secret_names(by_name))}"
        )

    @pytest.mark.parametrize(
        "agent_id", ["designer", "sketcher", "critic", "charter"]
    )
    def test_non_allow_agents_resolve_none(
        self, real_registry: AgentRegistry, agent_id: str
    ) -> None:
        """The OD-plugin / charter agents are NOT in the
        Q2 day-one allow-list — they get ZERO
        ``secrets`` tools through both the
        ``_strip_privileged_category_tools`` default-deny
        strip AND the ``resolve_tool_filter`` empty-allow
        branch.
        """
        meta = (
            real_registry.get_version(agent_id, None)
            or real_registry.get_resolved(agent_id)
        )
        assert meta is not None, f"{agent_id} meta must exist"
        # The agent may have a tools.allow list, but it
        # MUST NOT contain ``secrets`` (Q2 ruling: day-one
        # allow-list is worker / tester / leader / ari
        # only).
        if meta.tools is not None and meta.tools.allow:
            assert SECRETS_CATEGORY not in meta.tools.allow, (
                f"{agent_id} is NOT in the Q2 day-one "
                f"allow-list; meta.json tools.allow must "
                f"not contain {SECRETS_CATEGORY!r}; got "
                f"{meta.tools.allow}"
            )
        by_name = _build_instance_tools(agent_id)
        assert _secret_names(by_name) == set(), (
            f"{agent_id} must NOT resolve any secrets "
            f"tools (not in Q2 day-one allow-list); "
            f"leaked {_secret_names(by_name)}"
        )


# ─────────────────────────────────────────────────────────────────────
# PB-F1 envelope pin — M2 governor pre-promote fix (2026-10-17)
# ─────────────────────────────────────────────────────────────────────


class TestPB1Envelope:
    """Pin the M2 PB-F1 error envelope contract.

    Every wrapper at the ``secrets`` tool boundary MUST
    return a class-name-only JSON envelope on failure —
    ``str(exc)``, ``traceback``, and ``repr(exc)`` are
    NEVER carried into the response payload (CWE-209; the
    PB-F1 leak class is proven real in this project —
    2026-10-03 occ-2 incident).

    Expected typed errors from the ``SecretServiceError``
    hierarchy MAY carry their safe message (they are
    authored strings). Anything else — SQLAlchemy errors,
    OS errors, library errors that carry DB URLs /
    credentials in their message — is class-name-only.

    The pin's leak scenario: a canary string
    (``"CANARY_PASSWORD"``) embedded in an unexpected
    exception's ``str(exc)``. The wrapper MUST NOT carry
    the canary into the response payload. The
    mutation-verification test confirms the pin actually
    fails when the guarded regression (``str(exc)`` in
    the wrapper) is in place.
    """

    CANARY: str = "CANARY_PASSWORD"
    CANARY_URL: str = (
        "postgresql://user:CANARY_PASSWORD@host:5432/db"
    )

    def _build_tool(self, tool_name: str, manager: Any):
        """Build a single secret_* tool by name."""
        from daemon.tools.secrets_tools import create_secret_tools

        tools = create_secret_tools(manager, "test-inst-id")
        for tool_obj in tools:
            if tool_obj.name == tool_name:
                return tool_obj
        raise AssertionError(f"tool {tool_name!r} not found")

    def _invoke(self, tool_obj, **kwargs):
        """Invoke a langchain ``StructuredTool`` with kwargs."""
        # LangChain ``StructuredTool`` exposes ``invoke`` for
        # ad-hoc runs. Pass kwargs as a dict.
        return tool_obj.invoke(kwargs)

    def _build_service_with_iter(self, exc_to_raise: Exception):
        """Build a manager whose ``_secret_service`` raises ``exc_to_raise``
        on every call."""
        from daemon.services.secret_service import SecretService

        manager = MagicMock(name="InstanceManager")
        svc = MagicMock(spec=SecretService)

        def _raise(*args, **kwargs):
            raise exc_to_raise

        # Wire every public method to raise the same exception.
        svc.generate_ssh_keypair.side_effect = _raise
        svc.generate_api_token.side_effect = _raise
        svc.store.side_effect = _raise
        svc.list.side_effect = _raise
        svc.read_public.side_effect = _raise
        svc.retire.side_effect = _raise
        manager._secret_service = svc
        manager._instance_repository = None
        return manager

    def test_unexpected_exception_str_not_in_envelope(self) -> None:
        """Pin #1: an unexpected exception with the canary
        string in ``str(exc)`` MUST NOT carry the canary
        into the tool envelope."""
        from daemon.services.secret_service import (
            SecretService,
        )

        class FakeSQLError(Exception):
            """Mimic a SQLAlchemy-style exception that leaks
            the DB URL in its message (the real PB-F1 leak
            class from the 2026-10-03 occ-2 incident)."""

            def __init__(self, msg: str) -> None:
                super().__init__(msg)

        exc = FakeSQLError(
            f"(psycopg2.OperationalError) could not connect "
            f"to {self.CANARY_URL}"
        )
        manager = self._build_service_with_iter(exc)
        tool_obj = self._build_tool(
            "secret_generate_ssh_keypair", manager
        )
        response_json = self._invoke(tool_obj, name="pin-1")
        response = json.loads(response_json)
        # The canary MUST NOT be anywhere in the response.
        assert self.CANARY not in response_json, (
            f"PB-F1 leak: canary string {self.CANARY!r} "
            f"found in tool envelope: {response_json!r}"
        )
        # Class name IS present.
        assert response.get("error") == "FakeSQLError"
        # No ``message`` field for unexpected exceptions.
        assert "message" not in response, (
            f"PB-F1 leak: unexpected exception's str(exc) "
            f"leaked via 'message' field: {response!r}"
        )

    def test_all_six_wrappers_block_canary_leak(self) -> None:
        """Pin #2: every wrapper — not just one — must
        block the canary. Tests all 6 tool names."""
        from daemon.services.secret_service import SecretService

        class FakeSQLError(Exception):
            def __init__(self, msg: str) -> None:
                super().__init__(msg)

        exc = FakeSQLError(
            f"connection refused: {self.CANARY_URL}"
        )
        manager = self._build_service_with_iter(exc)

        # Six wrappers — one per tool name. Each takes
        # the canary differently (e.g. ``generate_api_token``
        # needs ``length``, ``read_public`` needs ``id``,
        # ``store`` needs ``kind`` / ``value``).
        tool_call_signatures = {
            "secret_generate_ssh_keypair": {"name": "x"},
            "secret_generate_api_token": {
                "name": "x",
                "length": 32,
            },
            "secret_store": {
                "name": "x",
                "kind": "generic",
                "value": "v",
            },
            "secret_list": {},
            "secret_read_public": {"id": "abc"},
            "secret_retire": {"id": "abc"},
        }
        for tool_name, kwargs in tool_call_signatures.items():
            tool_obj = self._build_tool(tool_name, manager)
            response_json = self._invoke(tool_obj, **kwargs)
            assert self.CANARY not in response_json, (
                f"PB-F1 leak in {tool_name!r}: canary in "
                f"tool envelope: {response_json!r}"
            )
            response = json.loads(response_json)
            assert response.get("error") == "FakeSQLError"
            assert "message" not in response, (
                f"PB-F1 leak in {tool_name!r}: 'message' "
                f"field present for unexpected exception: "
                f"{response!r}"
            )

    def test_typed_error_message_preserved(self) -> None:
        """Pin #3 (positive case): expected typed errors
        from the ``SecretServiceError`` hierarchy MAY
        carry their safe message (the messages are
        authored strings)."""
        from daemon.services.secret_service import (
            SecretNotFoundError,
            SecretNameConflictError,
        )
        manager = self._build_service_with_iter(
            SecretNotFoundError(
                f"secret id 'abc123' not found"
            )
        )
        tool_obj = self._build_tool(
            "secret_read_public", manager
        )
        response_json = self._invoke(tool_obj, id="abc123")
        response = json.loads(response_json)
        # Class name IS present.
        assert response.get("error") == "SecretNotFoundError"
        # Message IS present (the authored string is safe).
        assert response.get("message") == (
            "secret id 'abc123' not found"
        )

        # Conflict case — same positive contract.
        manager2 = self._build_service_with_iter(
            SecretNameConflictError(
                "secret name 'x' already exists"
            )
        )
        tool_obj2 = self._build_tool(
            "secret_generate_ssh_keypair", manager2
        )
        response_json2 = self._invoke(tool_obj2, name="x")
        response2 = json.loads(response_json2)
        assert response2.get("error") == "SecretNameConflictError"
        assert response2.get("message") == (
            "secret name 'x' already exists"
        )

    def test_mutation_verification_str_exc_breaks_pin(self) -> None:
        """Mutation verification — the PB-F1 pin actually
        fails when the guarded regression (``str(exc)`` in
        the wrapper) is in place.

        Procedure (repo convention):
          1. Apply the guarded regression: replace
             ``_format_error_envelope`` with a
             ``str(exc)``-leaking variant.
          2. Run the canary-leak pin.
          3. Assert the pin FAILS (canary present).
          4. Restore the class-name-only implementation.

        The pin's value is the regression it detects; this
        test documents that value.
        """
        import json as _json
        from daemon.tools import secrets_tools as tools_mod

        original = tools_mod._format_error_envelope

        def leaking_envelope(exc):
            """Guarded regression: pre-M2 ``str(exc)``
            shape (the leak class the M2 fix closes)."""
            return _json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

        class FakeSQLError(Exception):
            def __init__(self, msg: str) -> None:
                super().__init__(msg)

        try:
            tools_mod._format_error_envelope = leaking_envelope

            exc = FakeSQLError(
                f"could not connect to {self.CANARY_URL}"
            )
            manager = self._build_service_with_iter(exc)
            tool_obj = self._build_tool(
                "secret_generate_ssh_keypair", manager
            )
            response_json = self._invoke(tool_obj, name="pin")

            # The PB-F1 pin asserts the canary is NOT in
            # the response. With the ``str(exc)``-leaking
            # regression in place, the canary IS present.
            assert self.CANARY in response_json, (
                f"mutation-verification failed: the canary "
                f"should be present under the ``str(exc)`` "
                f"regression but is missing — the pin is "
                f"NOT detecting the regression"
            )
        finally:
            # Restore the class-name-only implementation.
            tools_mod._format_error_envelope = original
