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

from tests.helpers.nestops_pg_fixtures import by_name, call

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
