"""Unit tests for the ``view_write`` tool (ari-tools Phase 3 — deliverable A).

Covers the phase3-plan §7 suite A matrix:

* T-A1 — a page is written under the reports dir; the return carries
  all eight labeled lines.
* T-A2 — the return shape is ``view_link``-compatible (the first five
  labels byte-match).
* T-A3 — path traversal (``../x``, ``a/../../b``, leading ``/``,
  backslash, control char, null byte) ⇒ a typed ``Error:`` and NO
  file written.
* T-A4 — the extension gate (``.txt`` refused, no extension ⇒
  ``.html`` appended).
* T-A5 — the 32 MiB write-time cap (cap+1 refused naming the limit;
  exactly-at-cap accepted).
* T-A6 — overwrite semantics (an existing path is refused; an
  overwrite replaces the bytes and does NOT bump the generation).
* T-A7 — ``refresh=True`` bumps the generation and issues a new
  passcode.
* T-A8 — the privilege posture, ALL THREE layers: the privileged
  category, the allow-list, and the IN-BODY gate (which fires with
  ZERO side effects — no file, no passcode row).
* T-A9 — registration completeness (the three-step seam).
* T-A10 — the ``reports`` root is daemon-injected when absent, NOT
  overridden when the operator declares it, and the injected path is
  RESOLVED ABSOLUTE even for the relative ``./data`` default (the
  A-30/G1 blocking defect).

MOCK DISCIPLINE
---------------
The passcode service is the REAL
:class:`daemon.services.view_passcode_service.ViewPasscodeService` on
a REAL SQLite database — so "no passcode row was minted" is a REAL
row count, not a mock's own return value. The live-views service is
the REAL :class:`daemon.services.live_views.LiveViewsService` over a
real tmp_path directory.

SAFETY FENCE: every path comes from pytest's ``tmp_path`` — no
HOME-derived or live-shaped path is resolved anywhere in this file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.services.live_views import (
    MAX_SERVED_BYTES,
    RESERVED_ROOT_NAMES,
    LiveViewsService,
)
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher,
    ViewPasscodeService,
)
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
)
from daemon.tools.board_link import create_board_link_tools
from daemon.tools.view_write import create_view_write_tools
from daemon.tools.views_auth import (
    BOARD_LINK_AGENTS,
    VIEW_WRITE_AGENTS,
    _is_views_authorized,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

VIEW_WRITE_CATEGORY = "view-write"
VIEW_WRITE_MODULE = "daemon.tools.view_write"


# ─────────────────────────────────────────────────────────────────
# Fixtures — REAL services over REAL storage
# ─────────────────────────────────────────────────────────────────


class _ManagerDouble:
    """A minimal manager double exposing the two seams the tool reads.

    The tool reaches ``manager.live_views_service`` and the passcode
    service; both properties carry the REAL service objects, so every
    assertion below is made against real behaviour.
    """

    def __init__(self, live_views_service, passcode_service):
        self.live_views_service = live_views_service
        self.view_passcode_service = passcode_service
        # The tool also probes the private attribute first (the
        # ``view_link`` lookup shape) — keep it consistent.
        self._view_passcode_service = passcode_service


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    """The daemon-global reports directory (a real tmp_path)."""
    path = tmp_path / "reports"
    path.mkdir()
    return path


@pytest.fixture
def live_views_service(reports_dir: Path) -> LiveViewsService:
    """A REAL LiveViewsService with the ``reports`` root registered."""
    cfg = LiveViewsConfig()
    cfg.roots["reports"] = LiveViewsRootConfig(
        type="filesystem",
        path=str(reports_dir.resolve()),
        allowed_extensions=["html"],
    )
    return LiveViewsService(config=cfg)


@pytest.fixture
def passcode_engine(tmp_path: Path):
    """A REAL SQLite engine with the passcode tables created."""
    db_path = tmp_path / "passcodes.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def passcode_service(passcode_engine) -> ViewPasscodeService:
    """The REAL passcode service over the REAL database."""
    cipher = ViewPasscodeCipher(
        encryption_key=Fernet.generate_key().decode()
    )
    return ViewPasscodeService(engine=passcode_engine, cipher=cipher)


@pytest.fixture
def manager(live_views_service, passcode_service) -> _ManagerDouble:
    return _ManagerDouble(live_views_service, passcode_service)


def _passcode_row_count(passcode_service: ViewPasscodeService) -> int:
    """Count the REAL ``live_view_passcodes`` rows."""
    from daemon.repositories.view_passcodes.models import LiveViewPasscode

    with Session(passcode_service._engine) as session:
        return len(list(session.query(LiveViewPasscode).all()))


def _call(tool, **kwargs) -> str:
    """Invoke a tool coroutine and return its string result."""
    import asyncio

    return asyncio.run(tool.ainvoke(kwargs))


# ─────────────────────────────────────────────────────────────────
# The REAL tool-resolution harness (synthetic agents under tmp_path)
# ─────────────────────────────────────────────────────────────────


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, allow: list
) -> Path:
    """Stage a synthetic agent dir under tmp_path.

    Clones the real ``agents/watcher/meta.json`` shape with a replaced
    ``tools.allow`` — NO HOME-derived or live-shaped path is resolved.
    """
    import daemon.registry as dr
    from daemon.registry import AgentRegistry

    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = {"allow": allow, "deny": []}
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(
        json.dumps(real_meta), encoding="utf-8"
    )
    registry = AgentRegistry(agents_dir)
    registry.discover()
    dr._registry = registry
    return agents_dir


def _build_instance_tools_for(
    tmp_path: Path, agent_id: str, allow: list
) -> dict:
    """Stage a synthetic agent and build its REAL instance toolset."""
    from unittest.mock import MagicMock

    from daemon.tools.instance import create_instance_tools

    _stage_synthetic_agent(tmp_path, agent_id, allow)
    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    manager._secret_service = None
    manager._instance_repository = None
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


# ─────────────────────────────────────────────────────────────────
# T-A1 / T-A2 — the happy path + the return shape
# ─────────────────────────────────────────────────────────────────


class TestWriteAndShape:
    def test_a1_the_page_is_written_and_the_return_carries_eight_lines(
        self, manager, reports_dir: Path, passcode_service
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        payload = "<html><title>Hi</title><body>yo</body></html>"
        out = _call(
            tool,
            path="2026-10-19/board.html",
            html=payload,
            title="Q3 status board",
        )
        assert not out.startswith("Error:"), out
        target = reports_dir / "2026-10-19" / "board.html"
        assert target.is_file()
        # The served bytes are BYTE-FOR-BYTE the caller's bytes.
        assert target.read_bytes() == payload.encode("utf-8")
        labels = [line.split(":", 1)[0] for line in out.splitlines()]
        for expected in (
            "Public URL",
            "Passcode",
            "Authenticated URL",
            "Generation",
            "Locked",
            "Title",
            "Bytes",
            "Overwrote",
        ):
            assert expected in labels, (expected, out)
        assert "Title: Q3 status board" in out
        assert f"Bytes: {len(payload)}" in out
        assert "Overwrote: no" in out
        # A passcode row was REALLY minted.
        assert _passcode_row_count(passcode_service) == 1
        row = passcode_service.get_row("reports", "2026-10-19/board.html")
        assert row is not None
        assert row.generation == 1

    def test_a2_first_five_lines_are_view_link_compatible(
        self, manager
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(
            tool,
            path="page.html",
            html="<html><title>x</title></html>",
            title="T",
        )
        first_five = out.splitlines()[:5]
        assert [line.split(":", 1)[0] for line in first_five] == [
            "Public URL",
            "Passcode",
            "Authenticated URL",
            "Generation",
            "Locked",
        ]
        # The public URL is the passcode-gated lane.
        assert first_five[0].startswith(
            "Public URL: /views/public/reports/page.html"
        )
        assert first_five[2].startswith(
            "Authenticated URL: /views/reports/page.html"
        )

    def test_a1b_title_is_echoed_but_not_injected_into_the_html(
        self, manager, reports_dir: Path
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        _call(
            tool,
            path="t.html",
            html="<html><title>Original</title></html>",
            title="My Secret Label",
        )
        written = (reports_dir / "t.html").read_text(encoding="utf-8")
        assert "My Secret Label" not in written
        assert "<title>Original</title>" in written

    def test_a1c_a_missing_title_element_produces_a_note(
        self, manager
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool, path="n.html", html="<p>no title</p>", title="T")
        assert "Note:" in out
        assert "<title>" in out


# ─────────────────────────────────────────────────────────────────
# T-A3 / T-A4 — path + extension rules
# ─────────────────────────────────────────────────────────────────


class TestPathRules:
    @pytest.mark.parametrize(
        "bad_path",
        [
            "../x",
            "a/../../b",
            "/absolute.html",
            "back\\slash.html",
            "ctrl\x01char.html",
            "null\x00byte.html",
            "del\x7fchar.html",
            "c1\x9bchar.html",
        ],
    )
    def test_a3_malformed_paths_are_refused_and_write_nothing(
        self, manager, reports_dir: Path, passcode_service, bad_path: str
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool, path=bad_path, html="<p>x</p>", title="T")
        assert out.startswith("Error:"), out
        # ZERO side effects — no file, no passcode row.
        assert list(reports_dir.rglob("*")) == []
        assert _passcode_row_count(passcode_service) == 0

    @pytest.mark.parametrize(
        "encoded_path",
        [
            "%2e%2e%2fx.html",  # the tester's literal repro
            "%2e%2e%2fboard.html",
            "a%20b.html",  # a benign-looking percent escape
            "page%2Ehtml",  # an encoded DOT (no literal extension)
            "100%25.html",  # an encoded PERCENT
        ],
    )
    def test_a3_percent_encoded_paths_are_refused_and_write_nothing(
        self, manager, reports_dir: Path, passcode_service, encoded_path: str
    ) -> None:
        """Tester D1 — an ENCODED path is REFUSED outright (fail-closed).

        The shared validator (``is_well_formed_rel_path``) checks the
        POST-decode form BY DESIGN — the URL framework decodes
        percent-escapes before the token is compared. The WRITE path
        used to hand it the UN-decoded token, so ``%2e%2e%2fx.html``
        was ACCEPTED and landed on disk under its LITERAL name.

        Containment HELD (the file stayed inside the reports root), but
        the page was UNSERVABLE, the passcode row was ORPHANED, and the
        success envelope advertised a public URL that 404s — a LYING
        envelope. This pin proves the refusal is now TOTAL: a typed
        error naming the reason, NO file under the reports root (not
        even under the literal encoded name), and NO passcode row.
        """
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool, path=encoded_path, html="<p>x</p>", title="T")
        assert out.startswith("Error:"), out
        # The reason is NAMED — the agent can act on it.
        assert "percent-encoded" in out
        # ZERO side effects — no file (NOT even the literal encoded
        # name), no passcode row.
        assert list(reports_dir.rglob("*")) == []
        assert _passcode_row_count(passcode_service) == 0

    def test_a3_the_shared_validator_semantics_are_untouched(
        self, manager, reports_dir: Path, passcode_service
    ) -> None:
        """The refusal lives in the TOOL, not in the shared validator.

        ``is_well_formed_rel_path`` is the SERVE path's validator for
        OTHER roots; changing its semantics could break existing
        suites. The tool therefore refuses '%' ITSELF, before the
        shared validator runs — and the shared validator still accepts
        the post-decode form it was designed for.
        """
        from daemon.services.live_views import is_well_formed_rel_path

        # The shared validator's own contract is unchanged: it judges
        # the DECODED form.
        assert is_well_formed_rel_path("..") is False
        assert is_well_formed_rel_path("a/b.html") is True
        # The tool refuses the ENCODED form regardless.
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(
            tool, path="a%2fb.html", html="<p>x</p>", title="T"
        )
        assert out.startswith("Error:"), out
        assert "percent-encoded" in out
        assert list(reports_dir.rglob("*")) == []
        assert _passcode_row_count(passcode_service) == 0

    def test_a4_extension_gate(self, manager, reports_dir: Path) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool, path="page.txt", html="<p>x</p>", title="T")
        assert out.startswith("Error:"), out
        assert "only .html" in out
        assert not (reports_dir / "page.txt").exists()
        # No extension ⇒ '.html' is appended.
        out = _call(tool, path="page", html="<p>x</p>", title="T")
        assert not out.startswith("Error:"), out
        assert (reports_dir / "page.html").is_file()


# ─────────────────────────────────────────────────────────────────
# T-A11 — the ORDERING INVARIANT: the passcode row is minted ONLY
# AFTER a successful write (tester D1 — the leader's ruling).
# ─────────────────────────────────────────────────────────────────


class TestWriteThenMintOrdering:
    def test_a_failed_write_mints_NO_passcode_row(
        self, manager, reports_dir: Path, passcode_service
    ) -> None:
        """A write failure must leave NO passcode row behind.

        The ORDERING INVARIANT: the write runs FIRST (step 7) and the
        mint SECOND (step 8), so the mint is only reachable once the
        write returned successfully. No write-failure mode can orphan
        a row.

        The failure is forced WITHOUT touching a shared module: the
        target's parent is a FILE, so ``parent.mkdir()`` raises (an
        OSError) — root-proof, unlike a chmod-based fixture.
        """
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        # A FILE where the parent directory should be ⇒ mkdir raises.
        (reports_dir / "blocked").write_text("not a directory")
        out = _call(
            tool,
            path="blocked/page.html",
            html="<p>x</p>",
            title="T",
        )
        assert out.startswith("Error:"), out
        assert "could not write" in out
        # NO passcode row — the mint NEVER ran.
        assert _passcode_row_count(passcode_service) == 0
        # And no page was left behind.
        assert not (reports_dir / "blocked" / "page.html").exists()

    def test_a_mint_failure_after_a_successful_write_removes_the_new_page(
        self, manager, reports_dir: Path, passcode_service, monkeypatch
    ) -> None:
        """The RESIDUAL the ordering invariant cannot cover alone: the
        mint fails AFTER the write already succeeded.

        The page and its row are ONE pair: a page with no row can
        never be served, and a row with no page points at a 404. A
        page THIS call CREATED is therefore removed — the pair stays
        atomic from the caller's point of view.
        """
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]

        def _boom(*args, **kwargs):
            raise RuntimeError("mint refused")

        monkeypatch.setattr(passcode_service, "mint", _boom)
        out = _call(tool, path="new.html", html="<p>x</p>", title="T")
        assert out.startswith("Error:"), out
        assert "passcode service rejected" in out
        # The page THIS call created is gone — no orphan page.
        assert not (reports_dir / "new.html").exists()
        # And no row was minted.
        assert _passcode_row_count(passcode_service) == 0

    def test_a_mint_failure_leaves_an_overwritten_pre_existing_page(
        self, manager, reports_dir: Path, passcode_service, monkeypatch
    ) -> None:
        """An OVERWRITE of a PRE-EXISTING page is NOT removed.

        Those bytes were already reachable under the pre-existing row,
        and deleting them would destroy a page this call did not
        author. The pre-existing row remains exactly as it was.
        """
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        # A first, successful publish — one row, one page.
        out = _call(tool, path="old.html", html="<p>first</p>", title="T")
        assert not out.startswith("Error:"), out
        assert _passcode_row_count(passcode_service) == 1

        def _boom(*args, **kwargs):
            raise RuntimeError("mint refused")

        monkeypatch.setattr(passcode_service, "mint", _boom)
        out = _call(
            tool,
            path="old.html",
            html="<p>second</p>",
            title="T",
            overwrite=True,
        )
        assert out.startswith("Error:"), out
        # The pre-existing page is LEFT ALONE.
        assert (reports_dir / "old.html").is_file()
        # The pre-existing row remains — exactly ONE row, unchanged.
        assert _passcode_row_count(passcode_service) == 1


# ─────────────────────────────────────────────────────────────────
# T-A5 — the 32 MiB write-time cap
# ─────────────────────────────────────────────────────────────────


class TestSizeCap:
    def test_a5_over_the_cap_is_refused_and_nothing_is_written(
        self, manager, reports_dir: Path, passcode_service
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        payload = "x" * (MAX_SERVED_BYTES + 1)
        out = _call(tool, path="big.html", html=payload, title="T")
        assert out.startswith("Error:"), out
        assert str(MAX_SERVED_BYTES) in out
        assert "32 MiB" in out
        assert list(reports_dir.rglob("*")) == []
        assert _passcode_row_count(passcode_service) == 0

    def test_a5_exactly_at_the_cap_is_accepted(
        self, manager, reports_dir: Path
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        payload = "x" * MAX_SERVED_BYTES
        out = _call(tool, path="exact.html", html=payload, title="T")
        assert not out.startswith("Error:"), out
        assert (reports_dir / "exact.html").stat().st_size == MAX_SERVED_BYTES


# ─────────────────────────────────────────────────────────────────
# T-A6 / T-A7 — overwrite + refresh semantics
# ─────────────────────────────────────────────────────────────────


class TestOverwriteAndRefresh:
    def test_a6_an_existing_path_is_refused_without_overwrite(
        self, manager, reports_dir: Path
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        _call(tool, path="p.html", html="<p>first</p>", title="T")
        out = _call(tool, path="p.html", html="<p>second</p>", title="T")
        assert out.startswith("Error:"), out
        assert "already exists" in out
        # The original bytes are intact.
        assert (reports_dir / "p.html").read_text() == "<p>first</p>"

    def test_a6_overwrite_replaces_the_bytes_and_keeps_the_url_stable(
        self, manager, reports_dir: Path, passcode_service
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        first = _call(tool, path="p.html", html="<p>first</p>", title="T")
        second = _call(
            tool,
            path="p.html",
            html="<p>second</p>",
            title="T",
            overwrite=True,
        )
        assert "Overwrote: yes" in second
        assert (reports_dir / "p.html").read_text() == "<p>second</p>"
        # The URL is stable and the GENERATION is NOT bumped.
        assert (
            first.splitlines()[0] == second.splitlines()[0]
        ), (first, second)
        assert "Generation: 1" in second
        row = passcode_service.get_row("reports", "p.html")
        assert row is not None and row.generation == 1

    def test_a7_refresh_bumps_the_generation_and_the_passcode(
        self, manager, passcode_service
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        first = _call(tool, path="p.html", html="<p>x</p>", title="T")
        second = _call(
            tool,
            path="p.html",
            html="<p>x</p>",
            title="T",
            overwrite=True,
            refresh=True,
        )
        assert "Generation: 1" in first
        assert "Generation: 2" in second
        first_code = first.splitlines()[1]
        second_code = second.splitlines()[1]
        assert first_code != second_code
        row = passcode_service.get_row("reports", "p.html")
        assert row is not None and row.generation == 2


# ─────────────────────────────────────────────────────────────────
# T-A8 — the privilege posture (THREE layers)
# ─────────────────────────────────────────────────────────────────


class TestPrivilege:
    def test_a8i_the_category_is_privileged(self) -> None:
        assert VIEW_WRITE_CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_a8i_ari_resolves_the_tool_and_others_do_not(
        self, tmp_path: Path
    ) -> None:
        """The REAL ``create_instance_tools`` resolves the tool for an
        agent whose allow list carries the category — and NOT for an
        agent whose allow list does not."""
        by_name = _build_instance_tools_for(
            tmp_path, "syn-vw-allow", ["view-write"]
        )
        assert "view_write" in by_name
        by_name = _build_instance_tools_for(
            tmp_path,
            "syn-vw-empty",
            [],
        )
        # PRIVILEGED DEFAULT-DENY — an empty-allow agent resolves ZERO
        # view-write tools (this is the property that made the
        # category privileged).
        assert "view_write" not in by_name
        by_name = _build_instance_tools_for(
            tmp_path,
            "syn-vw-other",
            ["bash", "filesystem", "time", "help"],
        )
        assert "view_write" not in by_name

    def test_a8ii_the_allow_list_is_ari_only(self) -> None:
        for agent_id, expected in (
            ("ari", True),
            ("leader", False),
            ("designer", False),
            ("worker", False),
            ("tester", False),
            ("watcher", False),
        ):
            meta = json.loads(
                (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(
                    encoding="utf-8"
                )
            )
            allowed = meta["tools"]["allow"]
            assert (VIEW_WRITE_CATEGORY in allowed) is expected, agent_id

    @pytest.mark.parametrize(
        "agent_id", ["", "leader", "worker", "designer", "tester"]
    )
    def test_a8iii_the_body_gate_denies_with_zero_side_effects(
        self, manager, reports_dir: Path, passcode_service, agent_id: str
    ) -> None:
        """The body gate is what actually enforces "ari-only".

        A LITERAL tool-name allow entry bypasses the category gate
        outright (``daemon/tools/instance.py``) — so the body must
        gate too. The assertion is made against a REAL database and a
        REAL filesystem: no file written AND no passcode row minted.
        """
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id=agent_id
        )[0]
        out = _call(tool, path="sneak.html", html="<p>x</p>", title="T")
        assert out.startswith("Error:"), out
        assert "restricted to the 'ari' agent" in out
        assert list(reports_dir.rglob("*")) == []
        assert _passcode_row_count(passcode_service) == 0

    def test_a8iii_ari_is_the_only_allowed_id(
        self, manager, reports_dir: Path, passcode_service
    ) -> None:
        tool = create_view_write_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool, path="ok.html", html="<p>x</p>", title="T")
        assert not out.startswith("Error:"), out
        assert (reports_dir / "ok.html").is_file()
        assert _passcode_row_count(passcode_service) == 1

    def test_a8_the_helper_is_shared_not_copied(self) -> None:
        """ONE shared helper, TWO call sites — a copied second helper
        would be caught by this source scan."""
        view_write_src = (
            REPO_ROOT / "daemon" / "tools" / "view_write.py"
        ).read_text(encoding="utf-8")
        board_link_src = (
            REPO_ROOT / "daemon" / "tools" / "board_link.py"
        ).read_text(encoding="utf-8")
        for source in (view_write_src, board_link_src):
            assert "from .views_auth import" in source
            assert "_is_views_authorized" in source
            # No LOCAL definition of the helper in either module.
            assert "def _is_views_authorized" not in source
        # And the shared module really defines it.
        auth_src = (
            REPO_ROOT / "daemon" / "tools" / "views_auth.py"
        ).read_text(encoding="utf-8")
        assert "def _is_views_authorized" in auth_src

    def test_a8_the_helper_is_fail_closed(self) -> None:
        assert _is_views_authorized("", VIEW_WRITE_AGENTS) is False
        assert _is_views_authorized(None, VIEW_WRITE_AGENTS) is False
        assert _is_views_authorized(123, VIEW_WRITE_AGENTS) is False
        assert _is_views_authorized("ari", VIEW_WRITE_AGENTS) is True
        assert _is_views_authorized("leader", VIEW_WRITE_AGENTS) is False
        assert _is_views_authorized("leader", BOARD_LINK_AGENTS) is True
        assert VIEW_WRITE_AGENTS != BOARD_LINK_AGENTS


# ─────────────────────────────────────────────────────────────────
# T-A9 — registration completeness
# ─────────────────────────────────────────────────────────────────


class TestRegistration:
    def test_a9_the_three_step_seam(self) -> None:
        assert "view_write" in DYNAMIC_TOOL_NAMES
        assert "view_write" in KNOWN_TOOL_NAMES
        assert CATEGORY_MODULES[VIEW_WRITE_CATEGORY] == VIEW_WRITE_MODULE

    def test_a9_the_construction_extend_exists(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        assert "create_view_write_tools(" in source
        assert "agent_id=agent_id," in source

    def test_a9_the_tool_is_produced_by_create_instance_tools(
        self, tmp_path: Path
    ) -> None:
        """The construction extend is the THIRD registration step —
        decorator-only is SILENTLY INVISIBLE."""
        by_name = _build_instance_tools_for(
            tmp_path, "syn-vw-reg", ["view-write", "board-link"]
        )
        assert "view_write" in by_name
        assert "board_link" in by_name


# ─────────────────────────────────────────────────────────────────
# T-A10 — the daemon-injected reports root
# ─────────────────────────────────────────────────────────────────


class TestReportsRootInjection:
    def test_a10_the_root_is_injected_when_absent(self, tmp_path) -> None:
        from daemon.api import inject_reports_live_view_root
        from daemon.config import Config

        cfg = Config()
        assert "reports" not in cfg.live_views.roots
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        assert inject_reports_live_view_root(cfg, data_dir) is True
        entry = cfg.live_views.roots["reports"]
        assert entry.type == "filesystem"
        assert entry.allowed_extensions == ["html"]
        assert Path(entry.path) == (data_dir / "reports").resolve()

    def test_a10_an_operator_declared_root_is_not_overridden(
        self, tmp_path
    ) -> None:
        from daemon.api import inject_reports_live_view_root
        from daemon.config import Config, LiveViewsRootConfig

        cfg = Config()
        operator_path = str((tmp_path / "operator").resolve())
        cfg.live_views.roots["reports"] = LiveViewsRootConfig(
            type="filesystem", path=operator_path
        )
        assert (
            inject_reports_live_view_root(cfg, tmp_path / "data") is False
        )
        assert cfg.live_views.roots["reports"].path == operator_path

    def test_a10_a_relative_data_dir_yields_an_absolute_resolved_path(
        self, tmp_path, monkeypatch
    ) -> None:
        """A-30/G1 — the blocking defect.

        ``data_dir``'s default chain ends at the RELATIVE ``./data``.
        An unresolved injected path makes the filesystem resolver
        REFUSE the root ⇒ every published page 404s behind a
        misleading "not configured" error.
        """
        from daemon.api import inject_reports_live_view_root
        from daemon.config import Config

        monkeypatch.chdir(tmp_path)
        cfg = Config()
        assert inject_reports_live_view_root(cfg, Path("./data")) is True
        entry = cfg.live_views.roots["reports"]
        assert Path(entry.path).is_absolute()
        # And the REAL resolver accepts it — the root resolves.
        service = LiveViewsService(config=cfg.live_views)
        assert service.root_path("reports") == Path(
            entry.path
        )

    def test_a10_a_missing_data_dir_is_fail_soft(self) -> None:
        from daemon.api import inject_reports_live_view_root
        from daemon.config import Config

        cfg = Config()
        assert inject_reports_live_view_root(cfg, None) is False
        assert "reports" not in cfg.live_views.roots

    def test_a10_board_is_a_reserved_root_name(self) -> None:
        assert "board" in RESERVED_ROOT_NAMES
        assert "public" in RESERVED_ROOT_NAMES

    def test_a10_the_lifespan_calls_the_injection_helper(self) -> None:
        """The wiring pin — the injection happens in the LIFESPAN,
        immediately BEFORE the ``LiveViewsService`` construction (the
        service reads its roots at construction time)."""
        source = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
        assert "inject_reports_live_view_root(" in source
        call = source.index("inject_reports_live_view_root(\n        config,")
        construction = source.index(
            "live_views_service = LiveViewsService("
        )
        assert call < construction, (call, construction)
        # Exactly ONE construction site — the injection cannot precede
        # a different one.
        assert (
            source.count("live_views_service = LiveViewsService(") == 1
        )


# ─────────────────────────────────────────────────────────────────
# The board_link tool's gate set is DIFFERENT (cross-module discipline)
# ─────────────────────────────────────────────────────────────────


def test_the_two_gate_sets_are_different() -> None:
    assert VIEW_WRITE_AGENTS == frozenset({"ari"})
    assert BOARD_LINK_AGENTS == frozenset({"ari", "leader"})
    assert VIEW_WRITE_AGENTS != BOARD_LINK_AGENTS


def test_the_board_link_factory_is_keyword_only_after_manager() -> None:
    import inspect

    sig = inspect.signature(create_board_link_tools)
    params = list(sig.parameters.values())
    assert params[0].name == "manager"
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for param in params[1:]:
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_view_write_factory_is_keyword_only_after_manager() -> None:
    import inspect

    sig = inspect.signature(create_view_write_tools)
    params = list(sig.parameters.values())
    assert params[0].name == "manager"
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for param in params[1:]:
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_tool_never_raises_on_a_hostile_path() -> None:
    """The house rule: tools NEVER raise — every failure is a typed
    ``Error: ...`` string."""
    manager = _ManagerDouble(None, None)
    tool = create_view_write_tools(
        manager, current_instance_id="i", agent_id="ari"
    )[0]
    out = _call(tool, path="x.html", html="<p>x</p>", title="T")
    assert out.startswith("Error:"), out


def test_the_error_envelope_prefix_is_error_not_error_uppercase() -> None:
    """Phase 3 uses the ``Error:`` convention (the ``view_link``
    family), NOT phase 1's ``ERROR:``."""
    manager = _ManagerDouble(None, None)
    tool = create_view_write_tools(
        manager, current_instance_id="i", agent_id="ari"
    )[0]
    out = _call(tool, path="x.html", html="<p>x</p>", title="T")
    assert out.startswith("Error: ")
    assert not out.startswith("ERROR")


def test_the_source_has_no_str_format_on_the_css_template() -> None:
    """A-32 — the board template must never be str.format-ed."""
    source = (
        REPO_ROOT / "daemon" / "services" / "board_view_pages.py"
    ).read_text(encoding="utf-8")
    assert ".format(" not in source
    assert not re.search(r"\{\s*[\w.]+\s*\}", source.split("import re")[0])
