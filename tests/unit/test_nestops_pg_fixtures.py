"""The shared nest-ops PG fixtures — helper contract + teardown hygiene.

WHAT THIS FILE OWNS
--------------------
* the ONE tool-lookup helper the four lane files share
  (:func:`by_name` / :func:`call` — the tidier fold replaced four
  drifting local copies);
* the teardown is LOUD: the disposable database/role teardown must
  never swallow a failure in silence, because a swallowed DROP leaves
  a scratch database (and a scratch role) behind on the PG server,
  where nobody is looking for it.

PLANNED MUTATION (one behaviour per run):
  * make ``by_name`` return ``None`` for a missing tool → the
    fail-closed pin RED;
  * restore a bare ``pass`` in the teardown's except blocks → the
    teardown-is-loud pin RED.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.helpers.nestops_pg_fixtures import (
    REAL_HOME,
    by_name,
    call,
    install_fake_home,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_PATH = REPO_ROOT / "tests" / "helpers" / "nestops_pg_fixtures.py"


class _Tool:
    def __init__(self, name):
        self.name = name

    async def ainvoke(self, kwargs):
        return '{"ok": true}'


class TestToolLookupHelpers:
    """The shared lookup helper's contract."""

    def test_by_name_returns_the_named_tool(self):
        tools = [_Tool("alpha"), _Tool("beta")]
        assert by_name(tools, "beta").name == "beta"

    def test_by_name_fails_closed_on_a_missing_tool(self):
        """A missing tool is an ASSERTION, never a silent ``None``.

        One of the four former local copies returned a bare
        ``StopIteration`` (no assertion at all) — the failure mode was
        invisible in the test output.
        """
        tools = [_Tool("alpha")]
        with pytest.raises(AssertionError) as excinfo:
            by_name(tools, "beta")
        message = str(excinfo.value)
        assert "beta" in message
        # the REGISTERED names ride along — the whole point of the
        # shared helper.
        assert "alpha" in message

    async def test_call_invokes_and_json_decodes(self):
        payload = await call([_Tool("alpha")], "alpha", x=1)
        assert payload == {"ok": True}


class TestHomeFence:
    """The S6 mechanical HOME fence.

    The lane's test files DECLARED the fence in prose; this makes it a
    MECHANISM: HOME is redirected at a throwaway directory for every
    test, and the redirect is verified in-process — so a HOME-derived
    path resolution can never reach the live install, and a broken
    fence fails loudly at the fence.

    PLANNED MUTATION:
      * point the fake at the REAL home → the escape guard RED;
      * shape the fake like a live install → the escape guard RED;
      * remove the fixture from one lane file → the "armed in every
        lane file" pin RED.
    """

    def test_the_redirect_takes_effect(self, monkeypatch, tmp_path):
        fake = install_fake_home(monkeypatch, tmp_path)
        assert Path.home().resolve() == fake
        assert str(fake) == __import__("os").environ["HOME"]

    def test_the_fake_is_not_the_real_home(self, monkeypatch, tmp_path):
        fake = install_fake_home(monkeypatch, tmp_path)
        assert fake != Path(REAL_HOME).resolve()
        assert fake.is_dir()

    def test_a_live_shaped_fake_is_refused(self, monkeypatch, tmp_path):
        """The escape guard: the fake must never look like an install."""
        base = tmp_path / "agents-ensemble"
        base.mkdir()
        with pytest.raises(AssertionError):
            install_fake_home(monkeypatch, base)

    def test_the_fence_is_armed_in_every_lane_file(self):
        """Every lane test file installs the fence.

        A file that drops the fixture loses the fence silently — this
        pin is what makes the omission visible.
        """
        lane_files = (
            "tests/unit/test_instance_ops_behaviour.py",
            "tests/unit/test_instance_read_shakedown_defects.py",
            "tests/unit/test_instance_ops_delegation_chain.py",
            "tests/integration/test_nestops_ops_real_pg.py",
            "tests/integration/test_nestops_husk_fleet_real_pg.py",
        )
        for rel in lane_files:
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "install_fake_home(monkeypatch, tmp_path)" in source, (
                f"{rel} must install the mechanical HOME fence"
            )


class TestTeardownIsLoud:
    """The disposable teardown must never swallow a failure in silence."""

    def test_the_teardown_has_no_bare_pass_in_an_except_block(self):
        source = FIXTURES_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        bare = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [n for n in node.body if not isinstance(n, ast.Expr)]
            for stmt in body:
                if isinstance(stmt, ast.Pass):
                    bare.append(node.lineno)
        assert not bare, (
            "the disposable teardown must LOG its failures — a swallowed "
            f"DROP leaves a scratch database/role behind: lines {bare}"
        )

    def test_the_module_configures_a_logger(self):
        source = FIXTURES_PATH.read_text(encoding="utf-8")
        assert "logger = logging.getLogger(__name__)" in source
