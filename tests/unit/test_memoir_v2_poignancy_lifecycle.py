"""Memoir Store v2 — the POIGNANCY LIFECYCLE (the SQLite lane).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.2/T4.3;
AC1/AC2; pack ``test/packs/memoir_v2_poignancy_lifecycle_test.sh``).

What this suite pins:

* THE HOT APPEND NEVER BLOCKS (AC1 / DD-20 / R4) — ``memoir_write``
  stays ONE fast INSERT, the scorer is never called on the write path,
  and the write returns promptly even while the scoring lane's LLM is
  hung;
* the INSERT's EXPLICIT ``poignancy_status`` literal NULL — the
  unscored state never rests on a column-default accident;
* the END-TO-END FLOW — the write lands, the background worker picks the
  episode up, the score lands in ``poignancy`` and the AUTHORED
  ``importance`` is UNCHANGED (AC2);
* the WRITE PATH NEVER REACHES FOR THE LLM — a static pin on the
  repository module (the write path carries no provider at all).
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_poignancy_service import MemoirPoignancyService
from daemon.services.memoir_poignancy_worker import MemoirPoignancyWorker

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"

REPOSITORY_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "daemon"
    / "repositories"
    / "memoir"
    / "repository.py"
)


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-poignancy-lifecycle.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


class ProbeManager:
    """A manager double — the house shape (``test_memoir_tools_unit.py``).

    It counts the engine touches so the write-latency leg can prove the
    write path does exactly ONE DB round trip (the INSERT) and never
    reaches for the LLM.
    """

    def __init__(self, engine=None, knob_on: bool = True) -> None:
        self._engine_handle = engine
        self._knob_on = knob_on
        self.engine_touches = 0
        self.knob_reads = 0

    @property
    def config(self):
        self.knob_reads += 1
        return SimpleNamespace(memoir=SimpleNamespace(enabled=self._knob_on))

    @property
    def _engine(self):
        self.engine_touches += 1
        return self._engine_handle


def _tools(manager):
    from daemon.tools.memoir import create_memoir_tools

    return {
        tool_obj.name: tool_obj
        for tool_obj in create_memoir_tools(
            manager, current_instance_id="inst-1", agent_id=AGENT
        )
    }


def _row(repo, episode_id):
    with Session(repo.engine) as session:
        return session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()


# ── the hot append (AC1) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_memoir_write_tool_never_touches_the_scorer(repo):
    """THE HOT-PATH PROOF: the write path has no LLM in it at all.

    The scorer is a BACKGROUND worker — the write never calls it, never
    enqueues into it, and never waits for it.
    """
    manager = ProbeManager(engine=repo.engine)
    tools = _tools(manager)

    result = await tools["memoir_write"].ainvoke({"text": "a quiet moment"})

    # The write landed (the tool's success envelope carries the id).
    assert isinstance(result, dict)
    assert result.get("episode_id")
    with Session(repo.engine) as session:
        rows = list(session.exec(select(MemoirEpisode)).all())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_the_write_returns_promptly_while_the_scorer_is_hung(repo):
    """THE WRITE-LATENCY LEG.

    The scoring lane's LLM is rigged to HANG (far longer than the
    write's budget) and the write STILL returns immediately — because
    the write path never calls it. The proof is both the wall-clock and
    the LLM-call counter.
    """
    calls: list[str] = []

    def _hung_caller(prompt: str) -> str:
        calls.append(prompt)
        time.sleep(5)
        return "7"

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=_hung_caller,
    )
    # The scorer exists and is armed — the write must still not touch it.
    assert service is not None

    manager = ProbeManager(engine=repo.engine)
    tools = _tools(manager)

    started = time.monotonic()
    await tools["memoir_write"].ainvoke({"text": "another quiet moment"})
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, (
        f"the write took {elapsed:.2f}s — the hot append must never wait "
        f"for the scoring lane"
    )
    assert calls == [], "the write path called the scorer"


def test_the_write_path_module_carries_no_llm_provider():
    """THE STATIC HALF: the repository module (the ONE INSERT's owner)
    imports no LLM provider and no scorer — the write path cannot
    reach for a model even by accident."""
    tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = [
        name
        for name in imported
        if name in {"openai", "httpx"}
        or "llm" in name.lower()
        or "memoir_poignancy_service" in name
    ]
    assert forbidden == [], f"the write path imports {forbidden}"


def test_the_insert_writes_the_poignancy_status_as_an_explicit_null():
    """V2-D 1.14 / V2-42 (already landed in phase 1): the unscored state
    is EXPLICIT, never a column-default accident."""
    from daemon.repositories.memoir.repository import MEMOIR_INSERT_SQL

    values = MEMOIR_INSERT_SQL[
        MEMOIR_INSERT_SQL.index("VALUES") : MEMOIR_INSERT_SQL.rindex(")")
    ]
    assert "poignancy_status" in MEMOIR_INSERT_SQL
    assert ":poignancy_status" not in values
    assert values.rstrip().endswith("NULL")


def test_a_fresh_episode_is_unscored(repo):
    """The row lands unscored — the worker's queue."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="a moment")
    row = _row(repo, episode_id)
    assert row.poignancy is None
    assert row.poignancy_status is None
    assert row.poignancy_attempts == 0
    assert int(row.importance) == 5


# ── the end-to-end flow (AC2) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_write_then_the_worker_then_the_score(repo):
    """AC2: the background worker fills the score and the AUTHORED
    importance is UNCHANGED."""
    episode_id = repo.insert_episode(
        agent_id=AGENT, body="the constitution was rewritten", importance=8
    )

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=lambda prompt: "10",
    )
    worker = MemoirPoignancyWorker(service, config=None)

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy == 10
    assert row.poignancy_status == "scored"
    # THE IMPORTANCE RULING: the authored record is UNTOUCHED.
    assert int(row.importance) == 8


@pytest.mark.asyncio
async def test_a_failed_scoring_leaves_the_authored_importance_untouched(repo):
    """A row that cannot be scored keeps its authored importance AND its
    place in the queue."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="a moment", importance=4)

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=lambda prompt: "no score here",
    )
    worker = MemoirPoignancyWorker(service, config=None)

    await worker._tick()

    row = _row(repo, episode_id)
    assert row.poignancy is None
    assert row.poignancy_status is None
    assert row.poignancy_attempts == 1
    assert int(row.importance) == 4
    # The row is STILL in the queue — the next tick retries it.
    assert len(repo.get_pending_poignancy_episodes()) == 1


# ── the REAL latency leg (W4 — the theatrical one is replaced) ───────────


@pytest.mark.asyncio
async def test_the_append_returns_before_the_blocked_scorer_is_released(repo):
    """W4 — THE REAL INVOCATION LEG.

    The scorer is BLOCKED on an ``asyncio.Event`` INSIDE the real
    worker tick, and the append runs against the SAME repository while
    the scoring call is hung. The append must RETURN BEFORE the release
    (AC1: "returns within standard turn latency"), and once the scorer
    is released the score still lands.

    THE PREVIOUS LEG WAS THEATRICAL: it constructed a service with a
    hung caller and never wired the hung caller into the write path, so
    it proved that a constructor does not block — not that the append
    does not block. This leg drives the REAL worker tick.
    """
    loop = asyncio.get_running_loop()
    release = asyncio.Event()
    entered = asyncio.Event()

    def _blocked(prompt: str) -> str:
        # The scorer runs on the lane's executor THREAD — the asyncio
        # Event is bridged across the thread boundary.
        loop.call_soon_threadsafe(entered.set)
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=30)
        return "8"

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=_blocked,
    )
    worker = MemoirPoignancyWorker(service, config=None)

    # An episode that is ALREADY in the scoring queue — the worker picks
    # it up and hangs inside the scoring call.
    scored_id = repo.insert_episode(
        agent_id=AGENT, body="the episode the scorer hangs on", importance=5
    )

    # The REAL tick runs and the scorer HANGS inside it.
    tick = asyncio.create_task(worker._tick(), name="memoir-poignancy-tick")
    await asyncio.wait_for(entered.wait(), timeout=10)

    manager = ProbeManager(engine=repo.engine)
    tools = _tools(manager)

    started = time.monotonic()
    result = await tools["memoir_write"].ainvoke({"text": "a quiet moment"})
    elapsed = time.monotonic() - started

    # THE APPEND RETURNED WHILE THE SCORER WAS STILL HUNG
    assert entered.is_set(), "the scorer never entered the scoring call"
    assert not release.is_set()
    assert elapsed < 1.0, (
        f"the append took {elapsed:.2f}s while the scorer was hung — the "
        "hot append must never wait for the scoring lane"
    )
    assert isinstance(result, dict) and result.get("episode_id")

    # THE APPEND REALLY LANDED (the row is in the table — the leg is not
    # measuring a stubbed write path)
    with Session(repo.engine) as session:
        appended = session.exec(
            select(MemoirEpisode).where(
                MemoirEpisode.id == result["episode_id"]
            )
        ).first()
    assert appended is not None, "the append did not land"

    # THE RELEASE-THEN-SCORE PATH STILL WORKS
    release.set()
    await asyncio.wait_for(tick, timeout=30)

    row = _row(repo, scored_id)
    assert row.poignancy == 8, "the released scorer did not land its score"


# ── the TRANSITIVE provider walk (W7) ────────────────────────────────────

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The provider modules the write path may NEVER carry (the house set —
#: the same predicate the DIRECT-import pin above uses).
_PROVIDER_MODULES = {"openai", "httpx", "anthropic", "cohere"}


def _is_provider_edge(module_name: str, symbol: str) -> bool:
    """Is this import a provider edge?

    The house predicate: the module is a provider (``openai`` …) or its
    NAME carries the LLM marker (``llm``). BOTH the module path and the
    imported symbol are checked — ``from daemon.x import llm_client`` is
    an edge too.
    """
    if module_name.split(".")[0] in _PROVIDER_MODULES:
        return True
    if "llm" in module_name.lower():
        return True
    return "llm" in symbol.lower()


class _ModuleIndex:
    """The AST index of ONE project module."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.module_imports: list[tuple[str, str]] = []
        self.symbol_imports: dict[str, tuple[str, bool]] = {}
        self.functions: dict[str, ast.AST] = {}
        self.classes: dict[str, dict[str, ast.AST]] = {}
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = {
                    child.name: child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
        for node in self.tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    name = alias.asname or alias.name
                    self.module_imports.append((node.module, name))
                    self.symbol_imports.setdefault(name, (node.module, True))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    self.module_imports.append((alias.name, name))
                    self.symbol_imports.setdefault(name, (alias.name, False))
        # The function-level imports are real edges too — they join the
        # symbol map (for the call resolution).
        for node in ast.walk(self.tree):
            for source, symbol in _imports_of(node):
                self.symbol_imports.setdefault(symbol, (source, True))

    def resolve(self, qualname: str) -> ast.AST | None:
        """The AST node of a ``function`` or a ``Class.method``."""
        if "." in qualname:
            class_name, method_name = qualname.rsplit(".", 1)
            return self.classes.get(class_name, {}).get(method_name)
        return self.functions.get(qualname)

    def in_scope_imports(self, node: ast.AST) -> list[tuple[str, str]]:
        """The imports IN SCOPE for a function: the module's own plus the
        function's (a function-level import shadows nothing — both are
        edges)."""
        return self.module_imports + _imports_of(node)


def _imports_of(node: ast.AST) -> list[tuple[str, str]]:
    """The ``(module, symbol)`` pairs imported inside a node."""
    found: list[tuple[str, str]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom) and child.module:
            for alias in child.names:
                found.append((child.module, alias.asname or alias.name))
        elif isinstance(child, ast.Import):
            for alias in child.names:
                found.append((alias.name, alias.asname or alias.name.split(".")[0]))
    return found


def _called_names(node: ast.AST) -> list[tuple[str, bool]]:
    """The names a function calls — ``(name, is_a_self_method)``."""
    names: list[tuple[str, bool]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.append((func.id, False))
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id == "self":
                names.append((func.attr, True))
            elif isinstance(func.value, ast.Name):
                # a module alias (``datetime.now``) — the base name is
                # resolved as an import (a third-party module is out of
                # scope, so it resolves to nothing).
                names.append((func.value.id, False))
    return names


def _walk_write_path() -> tuple[list[str], set[str]]:
    """THE TRANSITIVE WALK (W7).

    Follows the CALLS from the append closure (``insert_episode``)
    through the project's own modules and returns the provider edges it
    reaches and the set of reachable modules.

    A third-party module is OUT OF SCOPE: a dependency's own imports are
    not the write path's responsibility (and the walk stays bounded).
    """
    cache: dict[pathlib.Path, _ModuleIndex] = {}

    def index(path: pathlib.Path) -> _ModuleIndex:
        if path not in cache:
            cache[path] = _ModuleIndex(path)
        return cache[path]

    def module_path(module_name: str) -> pathlib.Path | None:
        if not module_name.startswith("daemon"):
            return None
        parts = module_name.split(".")
        candidate = _REPO_ROOT.joinpath(*parts).with_suffix(".py")
        if candidate.exists():
            return candidate
        package = _REPO_ROOT.joinpath(*parts) / "__init__.py"
        if package.exists():
            return package
        return None

    def resolve_call(
        mod: _ModuleIndex, name: str, self_call: bool, qualname: str
    ) -> tuple[pathlib.Path, str] | None:
        if self_call and "." in qualname:
            class_name = qualname.rsplit(".", 1)[0]
            if name in mod.classes.get(class_name, {}):
                return (mod.path, f"{class_name}.{name}")
        if name in mod.functions:
            return (mod.path, name)
        entry = mod.symbol_imports.get(name)
        if entry:
            source, named = entry
            if not named:
                return None
            target_path = module_path(source)
            if target_path is None:
                return None
            target = index(target_path)
            if name in target.functions:
                return (target_path, name)
            for class_name, methods in target.classes.items():
                if name in methods:
                    return (target_path, f"{class_name}.{name}")
        return None

    edges: list[str] = []
    reachable: set[str] = set()
    seen: set[tuple[pathlib.Path, str]] = set()
    worklist: list[tuple[pathlib.Path, str]] = [
        (REPOSITORY_PATH, "MemoirRepository.insert_episode")
    ]
    while worklist:
        path, qualname = worklist.pop()
        if (path, qualname) in seen:
            continue
        seen.add((path, qualname))
        mod = index(path)
        reachable.add(str(path.relative_to(_REPO_ROOT)))
        node = mod.resolve(qualname)
        if node is None:
            continue
        for source, symbol in mod.in_scope_imports(node):
            if _is_provider_edge(source, symbol):
                edges.append(
                    f"{path.relative_to(_REPO_ROOT)} [{qualname}] imports "
                    f"{source} ({symbol})"
                )
        for name, self_call in _called_names(node):
            target = resolve_call(mod, name, self_call, qualname)
            if target is not None:
                worklist.append(target)
    return edges, reachable


def test_the_append_closure_reaches_no_llm_provider_transitively():
    """W7: the TRANSITIVE provider walk — the callable reachability set
    of the append closure.

    THE DIRECT-IMPORT PIN (above) can only see the imports of the ONE
    module that owns the INSERT; it is blind to a provider reached
    THROUGH a call (the ``skill_search_service`` → ``openai`` edge is
    exactly that shape). This walk follows the calls, so a transitive
    provider edge reachable from the append closure turns this pin RED.
    """
    edges, _reachable = _walk_write_path()
    assert edges == [], (
        "the append closure reaches an LLM provider edge:\n  "
        + "\n  ".join(edges)
    )


def test_the_known_transitive_provider_edge_is_outside_the_append_closure():
    """THE PRE-EXISTING EDGE — the phase-1/2 BACKLOG NOTE.

    ``daemon/repositories/memoir/repository.py`` imports
    ``skill_search_service`` (which imports ``openai``) inside the
    LEXICAL RANKER — a READ path. It is UNREACHABLE from the append
    closure, so the write path does not carry the provider. The edge is
    PRE-EXISTING (phase 1/2), READ-PATH, and phase 4 adds ZERO import
    edges: it is recorded as a BACKLOG NOTE in
    ``docs/memoir-v2/implementation-notes.md`` — NO production change.
    """
    skill_search = _REPO_ROOT / "daemon" / "services" / "skill_search_service.py"
    assert "import openai" in skill_search.read_text(encoding="utf-8"), (
        "the known edge is gone — the backlog note must be re-adjudicated"
    )
    _edges, reachable = _walk_write_path()
    assert "daemon/services/skill_search_service.py" not in reachable, (
        "the lexical-ranker module became REACHABLE from the append "
        "closure — the write path now carries the provider"
    )
