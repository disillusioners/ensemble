"""Phase-3 T7 pack (b) — ``tool_usage_digest`` registration + call surface.

Covers plan ``phase3-plan.md`` §4 T7 ``tool-registration (T4)`` and §3.6
(the THREE-STEP registration seam), plus the T3 callable/status-string
contract. Decorator-only registration is SILENTLY INVISIBLE — this file
pins each of the three steps independently so a dropped step reds loudly:

  (a) the ``@register_tool_category("scheduling")`` + ``@tool`` decorators
      in ``daemon/tools/tool_usage.py`` (with the ORDER pinned: the
      category decorator ABOVE ``@tool``, else the attribute is lost);
  (b) the ``CATEGORY_MODULES["scheduling"]`` entry carries the module
      path as a ``daemon.tools.*`` string (a ``daemon.services.*`` value
      is never discovered by the AST scanner);
  (c) the construction hunk in ``daemon/tools/instance.py`` —
      ``create_tool_usage_tools_if_available(...)`` + ``tools.extend(...)``
      — and its PLACEMENT before ``create_help_tool`` /
      ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` (a later
      extend is invisible to the help tool or BYPASSES the filter).

Council C1 context: ``tool_usage_digest`` is missing from the
regenerated ``KNOWN_TOOL_NAMES`` before commit ``8b8df3ead``; the frozen
fallback universe membership is therefore pinned here as a regression pin
for that fix. The static universe and the AST-scanned source stay
bidirectional (zero drift) — the same invariant the house drift pack
enforces for every lane.

REVIEWER RULINGS OBSERVED: the ``scheduling`` category is deliberately
REUSED (no new category value ⇒ no ``VOCABULARY_VERSION`` bump), and the
category is deliberately NOT privileged (D-8 — ari/leader/jober inherit).

SAFETY FENCE: every read is a repo-relative path; the one engine used is
a per-test tmp SQLite file.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from daemon.repositories.tool_usage.models import ToolUsageDaily
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.tool_usage import (
    CATEGORY,
    create_tool_usage_tools,
    create_tool_usage_tools_if_available,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = "daemon.tools.tool_usage"
TOOL_NAME = "tool_usage_digest"


def _module_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "tool_usage.py").read_text(encoding="utf-8")


def _instance_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(encoding="utf-8")


class _StubManager:
    """The minimum surface the factory reads: ``_engine``."""

    def __init__(self, engine=None) -> None:
        self._engine = engine


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'digest_tool.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(eng, tables=[ToolUsageDaily.__table__])
    try:
        yield eng
    finally:
        eng.dispose()


# ══════════════════════════════════════════════════════════════════════════
# (a) step one — the decorators, and their ORDER
# ══════════════════════════════════════════════════════════════════════════


class TestDecoratorStep:
    def test_the_category_is_scheduling(self):
        """The reused category (D-8) — no new category value, no vocab bump."""
        assert CATEGORY == "scheduling"

    def test_the_decorator_order_puts_the_category_above_the_tool(self):
        """``register_tool_category`` ABOVE ``@tool``: reversed, the helper
        returns a new object and the ``_tool_category`` attribute is lost
        (the category strip / filter then cannot see it)."""
        source = _module_source()
        idx = source.index("async def tool_usage_digest(")
        head = source[:idx]
        category_idx = head.rindex("@register_tool_category(CATEGORY)")
        tool_idx = head.rindex("@tool\n")
        assert category_idx < tool_idx, (
            "register_tool_category must sit ABOVE @tool — the decorator "
            "order is load-bearing"
        )

    def test_the_source_declares_exactly_one_tool_decorated_function(self):
        tree = ast.parse(_module_source())
        found = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and any(
                (isinstance(d, ast.Name) and d.id == "tool")
                or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "tool")
                for d in node.decorator_list
            )
        }
        assert found == {TOOL_NAME}


# ══════════════════════════════════════════════════════════════════════════
# (b) step two — the registry entry + both name universes
# ══════════════════════════════════════════════════════════════════════════


class TestRegistryEntryStep:
    def test_category_modules_scheduling_carries_the_module(self):
        entry = CATEGORY_MODULES[CATEGORY]
        assert isinstance(entry, (list, tuple)), (
            "the scheduling entry must be a list — the scanner accepts a "
            "list, and the tool_usage module is a SECOND member"
        )
        assert MODULE_PATH in entry
        # The pre-existing scheduling module stays (an accidental replace
        # would silently drop the task_schedule_* surface).
        assert "daemon.tools.scheduling" in entry

    def test_every_scheduling_entry_is_a_daemon_tools_path(self):
        """A ``daemon.services.*`` value is never discovered by the AST
        scanner and the exact-equality drift pin fails at boot."""
        for value in CATEGORY_MODULES[CATEGORY]:
            assert value.startswith("daemon.tools."), value

    def test_the_ast_scanner_discovers_the_tool_name(self):
        """Decorator-only registration would be invisible — this is the
        discovery half of the seam."""
        assert TOOL_NAME in discover_source_only_tool_names()

    def test_known_tool_names_carries_the_name(self):
        """Council C1 regression pin: the frozen-binary fallback universe
        must list the tool, or a frozen install false-warns on every
        agent allow-list that names it."""
        assert TOOL_NAME in KNOWN_TOOL_NAMES

    def test_the_static_universe_and_the_source_have_zero_drift(self):
        """Bidirectional equality — a name added to one side only reds."""
        source_names = discover_source_only_tool_names()
        assert set(KNOWN_TOOL_NAMES) - source_names == set()
        assert source_names - set(KNOWN_TOOL_NAMES) == set()

    def test_the_category_is_not_privileged(self):
        """D-8: reusing ``scheduling`` grants the tool to its existing
        holders (ari/leader/jober) — no default-deny strip applies."""
        assert CATEGORY not in PRIVILEGED_TOOL_CATEGORIES

    def test_the_tool_name_is_discoverable_from_a_name_universe(self):
        """Every factory-created or decorator-registered tool must land in
        at least one name universe, or agent allow-list validation
        false-warns on boot.

        This tool is decorator-registered inside a factory, so it is
        discovered from SOURCE (``discover_source_only_tool_names``) rather
        than listed in ``DYNAMIC_TOOL_NAMES`` — which is exactly why the
        ``KNOWN_TOOL_NAMES`` membership above is the frozen-binary
        fallback that matters. Pinned as a disjunction so a future edit
        cannot leave BOTH sides empty."""
        source_names = discover_source_only_tool_names()
        assert TOOL_NAME in source_names or TOOL_NAME in DYNAMIC_TOOL_NAMES


# ══════════════════════════════════════════════════════════════════════════
# (c) step three — the construction hunk and its placement
# ══════════════════════════════════════════════════════════════════════════


class TestConstructionStep:
    def test_instance_py_calls_the_factory_and_extends(self):
        source = _instance_source()
        call_idx = source.index("tool_usage_tool_list = create_tool_usage_tools_if_available(")
        extend_idx = source.index("tools.extend(tool_usage_tool_list)")
        assert call_idx < extend_idx
        # The factory call threads the caller identity + version tag.
        call_end = source.index(")", call_idx)
        window = source[call_idx:call_end]
        assert "manager," in window
        assert "current_instance_id," in window
        assert "agent_id," in window
        assert "agent_tag=version_tag" in window

    def test_the_extend_precedes_the_help_scan_and_filter(self):
        """PLACEMENT: a later extend is invisible to ``create_help_tool``
        and BYPASSES ``_apply_tool_filter``."""
        source = _instance_source()
        extend_idx = source.index("tools.extend(tool_usage_tool_list)")
        for later in (
            "help_tool = create_help_tool(",
            "scan_tools_for_full_docs(tools)",
            "tools = _apply_tool_filter(",
        ):
            assert extend_idx < source.index(later), (
                f"the tool_usage extend must precede {later!r}"
            )

    def test_the_import_is_guarded(self):
        """The import sits in a try/except with a no-op fallback, so a
        broken optional dependency can never abort instance construction."""
        source = _instance_source()
        idx = source.index("from .tool_usage import create_tool_usage_tools_if_available")
        window = source[max(0, idx - 40) : idx + 300]
        assert "try:" in window
        assert "except Exception" in window
        assert "return []" in window


# ══════════════════════════════════════════════════════════════════════════
# the constructed tool object
# ══════════════════════════════════════════════════════════════════════════


class TestConstructedTool:
    def test_construction_yields_exactly_one_categorised_tool(self, engine):
        tools = create_tool_usage_tools(
            _StubManager(engine),
            current_instance_id="inst-1",
            agent_id="ari",
            agent_tag=None,
        )
        assert len(tools) == 1
        tool = tools[0]
        assert getattr(tool, "name", None) == TOOL_NAME
        assert getattr(tool, "_tool_category", None) == CATEGORY
        assert getattr(tool, "_tool_category_first_party", None) is True

    def test_if_available_returns_empty_without_an_engine(self):
        """Degraded boot: no engine ⇒ the tool is ABSENT (not a broken
        stub), so no agent inherits a tool that can only error."""
        assert create_tool_usage_tools_if_available(_StubManager(None)) == []

    def test_if_available_returns_the_tool_with_an_engine(self, engine):
        assert len(create_tool_usage_tools_if_available(_StubManager(engine))) == 1

    def test_the_tool_takes_a_run_date_argument_and_nothing_else(self, engine):
        tool = create_tool_usage_tools(_StubManager(engine), agent_id="ari")[0]
        schema = getattr(tool, "args_schema", None)
        assert schema is not None
        fields = set(getattr(schema, "model_fields", {}) or {})
        assert fields == {"run_date"}


# ══════════════════════════════════════════════════════════════════════════
# the call surface (T3): one file per date, a short status string
# ══════════════════════════════════════════════════════════════════════════


class TestCallSurface:
    async def test_the_tool_writes_exactly_one_dated_file(
        self, engine, tmp_path, monkeypatch
    ):
        from daemon.services import tool_usage_digest as tud

        root = tmp_path / "digests"
        monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(root))
        with Session(engine) as session:
            session.add(
                ToolUsageDaily(
                    day="2026-11-20",
                    project_id="proj-1",
                    tool_name="bash",
                    agent_id="ari",
                    call_count=2,
                    updated_at="2026-11-20T00:00:00+00:00",
                )
            )
            session.commit()

        tool = create_tool_usage_tools(_StubManager(engine), agent_id="ari")[0]
        result = await tool.ainvoke({"run_date": "2026-11-21"})

        assert result.startswith("Digest written:")
        assert "2026-11-21.md" in result
        assert sorted(p.name for p in root.iterdir()) == ["2026-11-21.md"]
        body = (root / "2026-11-21.md").read_text(encoding="utf-8")
        assert body.startswith("# Tool Usage Digest — 2026-11-21")
        assert "| bash |" in body

    async def test_a_same_day_second_call_overwrites_rather_than_appends(
        self, engine, tmp_path, monkeypatch
    ):
        from daemon.services import tool_usage_digest as tud

        root = tmp_path / "digests"
        monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(root))
        tool = create_tool_usage_tools(_StubManager(engine), agent_id="ari")[0]

        await tool.ainvoke({"run_date": "2026-11-21"})
        await tool.ainvoke({"run_date": "2026-11-21"})

        files = sorted(p.name for p in root.iterdir())
        assert files == ["2026-11-21.md"]
        body = (root / "2026-11-21.md").read_text(encoding="utf-8")
        assert body.count("# Tool Usage Digest") == 1

    async def test_the_tool_reports_a_typed_error_without_an_engine(self):
        tool = create_tool_usage_tools(_StubManager(None), agent_id="ari")[0]
        result = await tool.ainvoke({"run_date": "2026-11-21"})
        assert result == "ERROR: no database engine available"

    async def test_the_tool_never_raises_on_a_bad_run_date(
        self, engine, tmp_path, monkeypatch
    ):
        """A malformed date falls back to the UTC today rather than
        exploding inside the scheduled fire."""
        from daemon.services import tool_usage_digest as tud

        monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(tmp_path / "digests"))
        tool = create_tool_usage_tools(_StubManager(engine), agent_id="ari")[0]
        result = await tool.ainvoke({"run_date": "not-a-date"})
        assert result.startswith("Digest written:")


# ══════════════════════════════════════════════════════════════════════════
# artifact delivery pins (D4 / F6) — §3.9.5 node 56, §3.9.8 node 57
# ══════════════════════════════════════════════════════════════════════════


#: The hash-verified tree roots (§5.4). The digest output must sit under
#: NONE of them — a file dropped there must not move any tree hash.
HASH_VERIFIED_ROOTS = frozenset(
    {"agents", "frontend", "plugins", "guidelines", "scripts", "daemon_slice"}
)


def test_the_written_digest_lands_outside_every_hash_verified_tree(
    engine, tmp_path, monkeypatch
):
    """Node 56 (§3.9.5): D4 pinning assertion (i) at unit level.

    The DEFAULT frozen resolution is what matters: the env override wins
    BEFORE any resolver call, so an override-path test proves nothing about
    where a real frozen install writes. The digest dir is DERIVED from that
    resolution (and asserted), never injected.
    """
    from daemon.services import tool_usage_digest as tud

    install = tmp_path / "install"
    binary = install / "releases" / "v0.0.0" / "ensemble-prod"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)
    monkeypatch.delenv(tud.ARTIFACT_DIR_ENV, raising=False)

    expected_dir = (
        install
        / ".agents"
        / "shared"
        / "planning"
        / "tool-usage-metrics-p1"
        / "digests"
    )
    digest_dir = tud.digest_root()  # derived from the default resolution
    assert digest_dir == expected_dir
    assert "releases" not in digest_dir.parts

    with Session(engine) as session:
        session.add(
            ToolUsageDaily(
                day="2026-11-20",
                project_id="proj-1",
                tool_name="bash",
                agent_id="ari",
                call_count=2,
                updated_at="2026-11-20T00:00:00+00:00",
            )
        )
        session.commit()

    _md, path = tud.write_digest(engine, "2026-11-21")
    assert path.is_file()
    assert path.parent == digest_dir
    assert HASH_VERIFIED_ROOTS.isdisjoint(p.name for p in path.parents)

    payload_dir = tud._artifact_dir()
    assert payload_dir == install / "releases" / "v0.0.0" / "tool-usage-artifacts"
    assert digest_dir != payload_dir
    assert payload_dir not in digest_dir.parents


def _git_output(repo_root: Path, *args: str) -> str | None:
    """Git stdout, or None when history is unavailable (shallow/no git)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()


def _verify_artifact(repo_root: Path, target: Path) -> subprocess.CompletedProcess[str]:
    """Run the schema-2 producer's separated --verify, never its writer."""
    return subprocess.run(
        [
            sys.executable,  # the lane worktree's own .venv python under pytest
            str(repo_root / "scripts" / "gen_tool_usage_allowlists.py"),
            "--agents-dir",
            str(repo_root / "agents"),
            "--verify",
            "--out",
            str(target),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_committed_bundle_equals_generator_output(tmp_path):
    """Node 57 (§3.9.8): the F6 freshness gate + APPROVER-B1 vacuity guard.

    --verify compares the SEMANTIC PAYLOAD (byte-equal), the unresolved
    ledger (as a set), and the provenance CONTENT hashes; tree_commit is
    informational and excluded. It NEVER rewrites the committed artifact.
    The same node proves the gate can go RED on a payload-only mutation.
    """
    repo_root = REPO_ROOT
    artifact = (
        repo_root
        / ".agents"
        / "shared"
        / "planning"
        / "tool-usage-metrics-p1"
        / "allowlists.json"
    )
    assert artifact.is_file()

    if _git_output(repo_root, "rev-parse", "--is-inside-work-tree") is None:
        pytest.skip("git history unavailable: not inside a git work tree")
    if _git_output(repo_root, "rev-parse", "--is-shallow-repository") == "true":
        pytest.skip("shallow clone: provenance inputs have no git history")
    for input_path in ("agents", "daemon/tools"):
        if _git_output(repo_root, "log", "-1", "--format=%H", "--", input_path) is None:
            pytest.skip(f"git history unavailable for {input_path}")

    original = artifact.read_text(encoding="utf-8")
    clean = _verify_artifact(repo_root, artifact)
    assert clean.returncode == 0, (
        f"--verify drift: rc={clean.returncode} stdout={clean.stdout!r} stderr={clean.stderr!r}"
    )
    assert artifact.read_text(encoding="utf-8") == original, (
        "--verify must compare, never author the committed artifact"
    )

    # VACUITY GUARD: mutate an allow entry in the PAYLOAD (never provenance).
    data = json.loads(original)
    victims = sorted(
        agent_id
        for agent_id, info in data["agents"].items()
        if isinstance(info, dict) and info.get("allow")
    )
    assert victims, "vacuity guard: no agent with an allow entry to mutate"
    victim = victims[0]
    data["agents"][victim]["allow"] = sorted(
        ["__verify_vacuity_probe__", *data["agents"][victim]["allow"][1:]]
    )
    mutated = tmp_path / "mutated-allowlists.json"
    mutated.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert mutated.read_text(encoding="utf-8") != original

    drifted = _verify_artifact(repo_root, mutated)
    assert drifted.returncode != 0, (
        "--verify compared nothing: a payload-only mutation must go RED "
        f"(rc={drifted.returncode} stdout={drifted.stdout!r} stderr={drifted.stderr!r})"
    )
    assert artifact.read_text(encoding="utf-8") == original
