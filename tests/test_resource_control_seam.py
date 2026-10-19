"""P6 control-seam pack — dormancy contract + scope fence (phase6-plan).

Covers (phase6-plan §5):

  * AC1 — T3 ``resource_pid_map`` dormant columns (``action`` /
    ``action_by`` / ``acted_at``) exist on BOTH dialect boot paths
    (the SQLite migration-runner path AND the SQLModel-metadata
    create_all path), default NULL, and no ``daemon/`` code names
    them outside the model definition (mechanical write-site audit:
    word-boundary grep + string-token scan + the flusher INSERT
    column-list AST pin). A future accidental write turns the suite
    red (D2).
  * AC2 — T4 ``resource_limit_policy`` exists EMPTY post-boot on
    both paths; ``user_gated`` NOT NULL default true;
    ``enabled`` default false; zero seed rows; no CRUD endpoints
    (the router's only non-GET route is the plan-sanctioned
    ``POST /resources/profile`` seam).
  * AC3 — ``GET /api/resources/control-capabilities`` returns the
    exact §2.8 static shape (``available:false`` per action) with a
    LIVE ``policy_rows`` repo-read; missing manager/engine/table
    degrades to ``policy_rows: 0``, never 503 (D11).
  * AC4 — the models module docstring documents the future
    enforcement port (consumer = the ``child_kind='chart_render'``
    spawn seam; the O2 leader ruling named).
  * AC5 — the enforcement FENCE: a tokenizer-level audit of
    ``daemon/services/resource_monitor/**`` + ``daemon/routers/
    resources.py`` proving the words ``throttle|kill|enforce`` appear
    ONLY in docstrings/comments or in the explicitly adjudicated
    allowlist below. Any future enforcement code fails the suite
    until the fence is consciously lifted (extend the allowlist WITH
    a provenance comment — that friction is the point, D1/D6).
  * AC6 — zero DDL in P6: the resource migration set is still
    exactly the 4 P1 files.
  * AC7 — ``POST /api/resources/profile`` is SEAM-ONLY: 403 when
    ``profiling_enabled=false`` (fail-closed when the config is
    absent), 409 while a reservation is open, ``seconds`` clamped to
    ``profile_timebox_s``, capability-probed; zero profiler backend
    (import grep), zero row writes (DB-count proof), the in-RAM
    ``profile_seam_invocations`` counter is the only trace.

FENCE DISCIPLINE (2026-10-12 convention): file-SQLite at pytest
``tmp_path``; FAKE_HOME mktemp escape guard; synthetic fixtures
only — the real ``/proc``, the live install dir and live-shaped
HOME-derived paths are NEVER resolved.
"""

from __future__ import annotations

import ast
import io
import os
import re
import tempfile
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.config import ResourceMonitorConfig
from daemon.models.resource_monitor import (
    ResourceLimitPolicy,
    ResourcePidMap,
)
from daemon.routers.resources import (
    _profile_seam_state,
    router as resources_router,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "daemon" / "migrations" / "versions"

# The EXACT P1 migration set — AC6 asserts this never grows in P6.
_P1_RESOURCE_MIGRATIONS = [
    "20261018_090001_create_resource_daemon_bucket.sql",
    "20261018_090002_create_resource_instance_sample.sql",
    "20261018_090003_create_resource_pid_map.sql",
    "20261018_090004_create_resource_limit_policy.sql",
]

_DORMANT_COLUMNS = ("action", "action_by", "acted_at")


# ──────────────────────────────────────────────────────────────────────────
# Fence — never resolve live-shaped paths (incident class 2026-10-12)
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}",
            returncode=1,
        )
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


# ──────────────────────────────────────────────────────────────────────────
# Dialect-boot fixtures (the two P1 storage-pack paths)
# ──────────────────────────────────────────────────────────────────────────


def _split_sql_up(sql_text: str) -> list[str]:
    """Apply the migration runner's splitter (full-line ``--`` strip
    then ``;``) — the P1 storage-pack helper, mirrored verbatim."""
    if "-- DOWN" in sql_text:
        sql_text = sql_text.split("-- DOWN")[0]
    code = "\n".join(
        ln for ln in sql_text.splitlines() if not ln.lstrip().startswith("--")
    )
    return [s.strip() for s in code.split(";") if s.strip()]


def _docstring_spans(src: str) -> list[tuple[int, int]]:
    """Char-offset spans of every module/class/function docstring in
    ``src`` — shared by the tokenizer audits (docstrings are prose,
    not code)."""
    tree = ast.parse(src)
    line_offsets = [0]
    for ln in src.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(ln))

    def off(row: int, col: int) -> int:
        return line_offsets[row - 1] + col

    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if not node.body:
                continue
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                spans.append(
                    (
                        off(first.lineno, first.col_offset),
                        off(first.end_lineno, first.end_col_offset),
                    )
                )
    return spans


def _string_token_contents(src: str, skip_docstrings: bool = False):
    """Yield ``(lineno, content)`` for every STRING literal in
    ``src`` (f-string middles included as raw text)."""
    spans = _docstring_spans(src) if skip_docstrings else []
    line_offsets = [0]
    for ln in src.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(ln))

    def off(row: int, col: int) -> int:
        return line_offsets[row - 1] + col

    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            start, end = off(*tok.start), off(*tok.end)
            if any(s <= start and end <= e for s, e in spans):
                continue
            try:
                content = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                content = tok.string
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            yield tok.start[0], content
        elif fstring_middle is not None and tok.type == fstring_middle:
            yield tok.start[0], tok.string


@pytest.fixture
def runner_engine(tmp_path):
    """SQLite engine with the 4 P1 ``.sql`` migrations applied — the
    SQLite (migration-runner) boot path."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'seam_runner.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for fn in _P1_RESOURCE_MIGRATIONS:
        sql = (_MIGRATIONS_DIR / fn).read_text()
        with engine.begin() as conn:
            for stmt in _split_sql_up(sql):
                conn.execute(text(stmt))
    return engine


@pytest.fixture
def metadata_engine(tmp_path):
    """SQLite engine created via ``SQLModel.metadata.create_all`` —
    the PG boot path (the metadata ``create_all`` is the
    dialect-neutral creation routine; the PG story, proven on
    SQLite per the P1 storage-pack precedent)."""
    import daemon.models.resource_monitor  # noqa: F401 — metadata registration

    engine = create_engine(f"sqlite:///{tmp_path / 'seam_meta.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _make_app(engine) -> FastAPI:
    """Minimal app mounting the resources router with a synthetic
    manager exposing ``engine`` (the snapshots/resources DI shape)."""

    class _FakeManager:
        pass

    app = FastAPI()
    app.include_router(resources_router, prefix="/api")
    app.state.manager = _FakeManager()
    app.state.manager.engine = engine
    return app


# ──────────────────────────────────────────────────────────────────────────
# AC1 — T3 dormant columns: exist, NULL-only, on both dialect paths
# ──────────────────────────────────────────────────────────────────────────


class TestT3DormantColumnsBoot:
    @pytest.mark.parametrize("engine_fixture", ["runner_engine", "metadata_engine"])
    def test_dormant_columns_exist(self, engine_fixture, request):
        engine = request.getfixturevalue(engine_fixture)
        cols = {c["name"] for c in inspect(engine).get_columns("resource_pid_map")}
        assert set(_DORMANT_COLUMNS).issubset(cols)

    @pytest.mark.parametrize("engine_fixture", ["runner_engine", "metadata_engine"])
    def test_dormant_columns_default_null_on_plain_insert(
        self, engine_fixture, request
    ):
        """A plain INSERT that omits the dormant columns yields NULL
        for all three — the D2 dormancy contract, at the DB level."""
        engine = request.getfixturevalue(engine_fixture)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_pid_map (pid, spawned_at) "
                    "VALUES (:pid, :ts)"
                ),
                {"pid": 4242, "ts": "2026-10-19 00:00:00"},
            )
            row = conn.execute(
                text(
                    "SELECT action, action_by, acted_at "
                    "FROM resource_pid_map WHERE pid = 4242"
                )
            ).one()
        assert tuple(row) == (None, None, None)

    def test_model_columns_carry_no_server_default(self):
        """The model definitions themselves are NULL-default (no
        server_default sneaks a value into fresh rows)."""
        for name in _DORMANT_COLUMNS:
            col = ResourcePidMap.__table__.columns[name]
            assert col.nullable is True
            assert col.server_default is None


# ──────────────────────────────────────────────────────────────────────────
# AC1 — dormancy write-site audits (the mechanical half of D2)
# ──────────────────────────────────────────────────────────────────────────


class TestDormancyWriteSiteAudit:
    """No ``daemon/`` code may NAME the dormant columns outside the
    model definition. Any future write site must consciously edit
    these audits — that friction is the dormancy contract (D2)."""

    _MODEL_REL = "daemon/models/resource_monitor.py"
    _NAME_RE = re.compile(r"\b(action_by|acted_at)\b")

    def test_no_daemon_file_names_action_by_acted_at(self):
        """Word-boundary grep over every ``daemon/**/*.py``: the
        distinctive dormant names appear ONLY in the model module.
        (``\\b`` keeps ``compacted_at``-style neighbours inert.)"""
        violations: list[str] = []
        for py in sorted((_REPO_ROOT / "daemon").rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(_REPO_ROOT).as_posix()
            if rel == self._MODEL_REL:
                continue
            for lineno, line in enumerate(
                py.read_text(encoding="utf-8").splitlines(), 1
            ):
                if self._NAME_RE.search(line):
                    violations.append(f"{rel}:{lineno}")
        assert violations == [], (
            "dormant-column write-site drift (only "
            f"{self._MODEL_REL} may name action_by/acted_at): {violations}"
        )

    def test_no_resource_pid_map_string_names_action(self):
        """No non-docstring string literal anywhere in ``daemon/`` may
        combine the T3 table with the word ``action`` — that
        combination is how a raw-SQL write (INSERT column list /
        UPDATE SET) names the dormant column. Docstrings are prose
        (the model docstring legitimately documents the seam) and are
        skipped; comments never reach the tokenizer string pass."""
        word_re = re.compile(r"\baction\b", re.IGNORECASE)
        violations: list[str] = []
        for py in sorted((_REPO_ROOT / "daemon").rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(_REPO_ROOT).as_posix()
            src = py.read_text(encoding="utf-8")
            for lineno, content in _string_token_contents(
                src, skip_docstrings=True
            ):
                if (
                    isinstance(content, str)
                    and "resource_pid_map" in content
                    and word_re.search(content)
                ):
                    violations.append(f"{rel}:{lineno}: {content[:60]!r}")
        assert violations == [], f"T3 action-column write drift: {violations}"

    def test_flusher_pid_map_insert_excludes_dormant_columns(self):
        """AST pin on the ONLY T3 write seam — the flusher's
        ``_dialect_insert('resource_pid_map', [columns…])`` call:
        its column list must stay disjoint from the dormant set.
        The ``action_by``/``acted_at`` grep above already covers the
        distinctive names; this pin closes the bare-``action`` gap
        in the enumerated INSERT."""
        import daemon.services.resource_monitor.flusher as flusher_mod  # noqa: F401 — import proves the module loads

        src = (_REPO_ROOT / "daemon/services/resource_monitor/flusher.py").read_text()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fname = getattr(func, "attr", None) or getattr(func, "id", None)
            if fname != "_dialect_insert" or not node.args:
                continue
            table_arg = node.args[0]
            if not (
                isinstance(table_arg, ast.Constant)
                and table_arg.value == "resource_pid_map"
            ):
                continue
            found = True
            cols_arg = node.args[1]
            assert isinstance(cols_arg, ast.List), (
                "_dialect_insert column list changed shape — re-adjudicate "
                "this pin against the new write shape"
            )
            names = {
                elt.value
                for elt in cols_arg.elts
                if isinstance(elt, ast.Constant)
            }
            overlap = names & set(_DORMANT_COLUMNS)
            assert not overlap, (
                f"flusher pid_map INSERT names dormant columns: {overlap}"
            )
        assert found, "the flusher _write_pid_map INSERT seam vanished"


# ──────────────────────────────────────────────────────────────────────────
# AC2 — T4 exists EMPTY post-boot with the D3 defaults
# ──────────────────────────────────────────────────────────────────────────


class TestT4EmptyPostBoot:
    @pytest.mark.parametrize("engine_fixture", ["runner_engine", "metadata_engine"])
    def test_t4_exists_and_is_empty(self, engine_fixture, request):
        engine = request.getfixturevalue(engine_fixture)
        assert "resource_limit_policy" in inspect(engine).get_table_names()
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM resource_limit_policy")
            ).scalar()
        assert count == 0, "T4 must stay EMPTY in v1 — zero seed rows"

    def test_t4_server_defaults_on_runner_path(self, runner_engine):
        """The .sql DDL carries NOT NULL + defaults server-side:
        ``INSERT DEFAULT VALUES`` produces user_gated=1, enabled=0."""
        info = {
            c["name"]: c
            for c in inspect(runner_engine).get_columns("resource_limit_policy")
        }
        assert info["user_gated"]["nullable"] is False
        assert info["enabled"]["nullable"] is False
        assert str(info["user_gated"]["default"]) == "1"
        assert str(info["enabled"]["default"]) == "0"
        with runner_engine.begin() as conn:
            conn.execute(text("INSERT INTO resource_limit_policy DEFAULT VALUES"))
            row = conn.execute(
                text("SELECT user_gated, enabled FROM resource_limit_policy")
            ).one()
        assert tuple(row) == (1, 0)

    def test_t4_model_defaults_on_metadata_path(self, metadata_engine):
        """The metadata path has NO server defaults (house pattern) —
        the SQLModel-side defaults (user_gated=True, enabled=False)
        are the contract. NOT NULL is enforced by the column type."""
        info = {
            c["name"]: c
            for c in inspect(metadata_engine).get_columns("resource_limit_policy")
        }
        assert info["user_gated"]["nullable"] is False
        assert info["enabled"]["nullable"] is False
        assert ResourceLimitPolicy.model_fields["user_gated"].default is True
        assert ResourceLimitPolicy.model_fields["enabled"].default is False
        row = ResourceLimitPolicy()
        assert row.user_gated is True
        assert row.enabled is False

    def test_router_mutation_surface_is_seam_only(self):
        """D3: no CRUD endpoints. The router's ONLY non-GET route is
        the plan-sanctioned ``POST /resources/profile`` seam — any
        policy CRUD route added later fails here."""
        mutations = []
        for route in resources_router.routes:
            methods = set(getattr(route, "methods", None) or ())
            non_get = methods - {"GET", "HEAD", "OPTIONS"}
            if non_get:
                mutations.append((route.path, tuple(sorted(non_get))))
        assert mutations == [("/resources/profile", ("POST",))]


# ──────────────────────────────────────────────────────────────────────────
# AC6 — zero DDL in P6
# ──────────────────────────────────────────────────────────────────────────


class TestZeroDDL:
    def test_resource_migration_set_is_exactly_p1(self):
        """``ls daemon/migrations/versions/ | grep -c resource`` == 4,
        and the 4 are byte-for-byte the P1 filenames — P6 adds zero
        DDL (phase6-plan D4; a missing column discovered by the boot
        tests above is a P1 acceptance failure, not P6 work)."""
        actual = sorted(
            p.name
            for p in _MIGRATIONS_DIR.glob("*.sql")
            if "resource" in p.name
        )
        assert actual == sorted(_P1_RESOURCE_MIGRATIONS)
        assert len(actual) == 4


# ──────────────────────────────────────────────────────────────────────────
# AC3 — GET /api/resources/control-capabilities
# ──────────────────────────────────────────────────────────────────────────


class TestControlCapabilities:
    def test_exact_shape(self, runner_engine):
        """§2.8 exact shape — every action ``available:false``; the
        live T4 read proves the by-design emptiness."""
        client = TestClient(_make_app(runner_engine))
        r = client.get("/api/resources/control-capabilities")
        assert r.status_code == 200
        assert r.json() == {
            "enforcement": "observe-only",
            "actions": [
                {"name": "alert", "user_gated": True, "available": False},
                {"name": "throttle", "user_gated": True, "available": False},
                {"name": "kill", "user_gated": True, "available": False},
            ],
            "policy_rows": 0,
        }

    def test_policy_rows_reads_reality(self, runner_engine):
        """The count is a LIVE repo-read, not a hardcoded 0: seed two
        rows and the payload reports them."""
        with runner_engine.begin() as conn:
            for i in range(2):
                conn.execute(
                    text(
                        "INSERT INTO resource_limit_policy "
                        "(scope, target, metric) VALUES (:s, :t, :m)"
                    ),
                    {"s": "child_kind", "t": "chart_render", "m": f"m{i}"},
                )
        client = TestClient(_make_app(runner_engine))
        r = client.get("/api/resources/control-capabilities")
        assert r.status_code == 200
        assert r.json()["policy_rows"] == 2

    def test_degrade_manager_without_engine_never_503(self):
        """Manager present but engine-less (degraded boot) ⇒ 200 +
        the full shape with ``policy_rows: 0`` — never 503 (D11).
        (The repo conftest fabricates a MagicMock manager for ABSENT
        ones, so absence itself is simulated by its observable
        effect: no engine reachable → the count read is skipped.)"""
        app = FastAPI()
        app.include_router(resources_router, prefix="/api")
        app.state.manager = SimpleNamespace()  # no .engine attribute
        client = TestClient(app)
        r = client.get("/api/resources/control-capabilities")
        assert r.status_code == 200
        body = r.json()
        assert body["enforcement"] == "observe-only"
        assert body["policy_rows"] == 0
        assert all(a["available"] is False for a in body["actions"])

    def test_degrade_count_read_failure_never_503(self):
        """A count read that RAISES ⇒ WARN + ``policy_rows: 0`` at
        200 — never 503 (D11 degrade, same envelope family)."""

        class _ExplodingEngine:
            def connect(self):
                raise RuntimeError("engine unavailable (synthetic)")

        app = _make_app(_ExplodingEngine())
        client = TestClient(app)
        r = client.get("/api/resources/control-capabilities")
        assert r.status_code == 200
        body = r.json()
        assert body["policy_rows"] == 0
        assert body["enforcement"] == "observe-only"

    def test_degrade_missing_table_never_503(self, tmp_path):
        """Engine whose DB predates the resource family (no T4 table)
        ⇒ missing-relation tolerance ⇒ 200 + ``policy_rows: 0``."""
        engine = create_engine(f"sqlite:///{tmp_path / 'bare.db'}")
        client = TestClient(_make_app(engine))
        r = client.get("/api/resources/control-capabilities")
        assert r.status_code == 200
        assert r.json()["policy_rows"] == 0


# ──────────────────────────────────────────────────────────────────────────
# AC7 — POST /api/resources/profile: the seam-only profile gate
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def seam_app(runner_engine):
    app = _make_app(runner_engine)
    _profile_seam_state.reset()
    yield app
    _profile_seam_state.reset()


class TestProfileSeamGating:
    def test_disabled_refuses_403(self, seam_app):
        """Default config: ``profiling_enabled=false`` ⇒ 403 with the
        typed envelope; the seam NEVER reaches a capability probe."""
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig()  # profiling_enabled=False
        )
        client = TestClient(seam_app)
        r = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 10},
        )
        assert r.status_code == 403
        body = r.json()["detail"]
        assert body["code"] == "profiling_disabled"
        assert "error" in body

    def test_absent_config_fails_closed_403(self, seam_app):
        """No ``ensemble_config`` on app.state (degraded boot) ⇒ the
        seam cannot prove enablement ⇒ refuse (fail-closed)."""
        client = TestClient(seam_app)
        r = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 10},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "profiling_disabled"

    def test_busy_returns_409_until_reset(self, seam_app):
        """Enabled config: first POST accepts and OPENS the in-RAM
        reservation; a second POST inside the window ⇒ 409
        ``profile_active``; after ``reset()`` the seam accepts again
        (the expiry/reset seam a future profiler commission owns)."""
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig(profiling_enabled=True)
        )
        client = TestClient(seam_app)
        first = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 10},
        )
        assert first.status_code == 200
        assert first.json()["accepted"] is True
        assert first.json()["active_until"] is not None

        second = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 10},
        )
        assert second.status_code == 409
        body = second.json()["detail"]
        assert body["code"] == "profile_active"
        assert body["active_scope"] == "daemon"

        _profile_seam_state.reset()
        third = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 10},
        )
        assert third.status_code == 200

    def test_over_cap_clamps_to_profile_timebox(self, seam_app):
        """``seconds`` beyond ``profile_timebox_s`` is CLAMPED (not
        rejected): the envelope echoes requested vs effective."""
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig(
                profiling_enabled=True, profile_timebox_s=30
            )
        )
        client = TestClient(seam_app)
        r = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 9999},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] is True
        assert body["requested_seconds"] == 9999
        assert body["seconds"] == 30
        assert body["clamped"] is True

    def test_capability_probed_and_counter_counts(self, seam_app):
        """The accept envelope carries the capability probe (no
        profiler provisioned) and the invocation counter counts
        EVERY call — gate outcomes included."""
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig(profiling_enabled=True)
        )
        client = TestClient(seam_app)
        before = _profile_seam_state.invocations
        r = client.post(
            "/api/resources/profile",
            json={"scope": "daemon", "seconds": 10, "requested_by": "worker-1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["capability"] == {
            "available": False,
            "profiler": None,
            "note": (
                "observe-only seam; the profiler backend is a separate "
                "commission behind this gate"
            ),
        }
        assert body["requested_by"] == "worker-1"
        assert body["profile_seam_invocations"] == before + 1
        assert _profile_seam_state.invocations == before + 1

        # a refused call ALSO counts (it is a seam invocation)
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig()  # disabled
        )
        r2 = client.post(
            "/api/resources/profile", json={"scope": "daemon", "seconds": 10}
        )
        assert r2.status_code == 403
        assert _profile_seam_state.invocations == before + 2

    def test_seam_never_writes_rows(self, seam_app, runner_engine):
        """D2b row audit: an accepted reservation must not create a
        single row — T4 stays empty, T3 count is unchanged, and no
        ``*profile*`` table exists at all."""
        with runner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_pid_map (pid, spawned_at) "
                    "VALUES (777, '2026-10-19 00:00:00')"
                )
            )
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig(profiling_enabled=True)
        )
        client = TestClient(seam_app)
        assert client.post(
            "/api/resources/profile", json={"scope": "daemon", "seconds": 10}
        ).status_code == 200
        with runner_engine.connect() as conn:
            assert conn.execute(
                text("SELECT COUNT(*) FROM resource_limit_policy")
            ).scalar() == 0
            assert conn.execute(
                text("SELECT COUNT(*) FROM resource_pid_map")
            ).scalar() == 1
        tables = inspect(runner_engine).get_table_names()
        assert not [t for t in tables if "profile" in t]

    def test_invalid_scope_400(self, seam_app):
        seam_app.state.ensemble_config = SimpleNamespace(
            resource_monitor=ResourceMonitorConfig(profiling_enabled=True)
        )
        client = TestClient(seam_app)
        r = client.post(
            "/api/resources/profile",
            json={"scope": "galaxy:north", "seconds": 10},
        )
        assert r.status_code == 400
        assert "error" in r.json()["detail"]


class TestProfileSeamAudits:
    """The grep/count audits behind AC7's 'zero profiler backend
    invoked' gate (R5)."""

    _ROUTER = _REPO_ROOT / "daemon" / "routers" / "resources.py"

    def test_router_imports_no_profiler_backend(self):
        """No profiler backend vocabulary in the router: no
        tracemalloc/cProfile/py-spy/memray/yappi/pyinstrument/austin
        import or reference, no ``import profile``/``from profile``,
        and no process-control (SIGKILL/SIGTERM/``.kill(``/
        ``.terminate(``) — the seam gates, it never acts."""
        src = self._ROUTER.read_text(encoding="utf-8")
        backend_re = re.compile(
            r"tracemalloc|cProfile|py_spy|pyspy|memray|yappi|"
            r"pyinstrument|austin",
            re.IGNORECASE,
        )
        assert not backend_re.search(src), "profiler-backend token in router"
        assert not re.search(
            r"^\s*(import|from)\s+profile\b", src, re.MULTILINE
        ), "'import profile' in router"
        assert not re.search(r"SIGKILL|SIGTERM", src), "signal control in router"
        assert not re.search(r"\.kill\(|\.terminate\(", src), (
            "process-kill call in router"
        )

    def test_router_writes_no_rows(self):
        """String-token scan: the router contains NO INSERT/UPDATE
        SQL at all — its only DB touch is the capabilities
        row-COUNT read."""
        violations = []
        write_re = re.compile(r"\b(INSERT\s+INTO|UPDATE)\b", re.IGNORECASE)
        for tok in tokenize.generate_tokens(
            io.StringIO(self._ROUTER.read_text(encoding="utf-8")).readline
        ):
            if tok.type != tokenize.STRING:
                continue
            try:
                content = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                continue
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            if isinstance(content, str) and write_re.search(content):
                violations.append(f"{tok.start[0]}: {content[:60]!r}")
        assert violations == [], f"router writes rows: {violations}"

    def test_invocation_counter_is_the_only_trace(self):
        """The ``profile_seam_invocations`` counter exists in the
        router (the ONE durable trace D2b allows) — and it is
        in-RAM (no session/exec of any counter table)."""
        src = self._ROUTER.read_text(encoding="utf-8")
        assert "profile_seam_invocations" in src
        assert "exec(" not in src


# ──────────────────────────────────────────────────────────────────────────
# AC4 — the enforcement-port docstring (D3)
# ──────────────────────────────────────────────────────────────────────────


class TestEnforcementPortDocstring:
    def test_models_docstring_documents_future_port(self):
        import daemon.models.resource_monitor as models_mod

        doc = models_mod.__doc__ or ""
        assert "enforcement port" in doc.lower()
        assert "chart_render" in doc, (
            "the child_kind='chart_render' consumer must be named"
        )
        assert "spawn seam" in doc.lower()
        assert "cap=2" in doc, "the O2 leader ruling (default cap=2) named"
        assert "ceremony-gated" in doc, "the O2 ceremony gate named"

    def test_docstring_names_the_dormant_columns(self):
        import daemon.models.resource_monitor as models_mod

        doc = models_mod.__doc__ or ""
        for col in _DORMANT_COLUMNS:
            assert col in doc


# ──────────────────────────────────────────────────────────────────────────
# AC5 — the enforcement FENCE (tokenizer-level code audit)
# ──────────────────────────────────────────────────────────────────────────


_FENCE_ROOTS = [
    _REPO_ROOT / "daemon" / "services" / "resource_monitor",
    _REPO_ROOT / "daemon" / "routers" / "resources.py",
]
_FENCE_RE = re.compile(r"throttle|kill|enforce", re.IGNORECASE)

# Adjudicated allowlist — each entry is an EXISTING, reviewed piece of
# observability vocabulary. A fence hit outside this set fails the suite;
# lifting the fence means ADDING here with a provenance comment (D6:
# conscious, reviewable, never silent).
_FENCE_ALLOWED_IDENTIFIERS = {
    # Sampler EMA self-cost protection (§2.8 self-cost discipline):
    # the sampler backs off its OWN cadence when ITS tick cost
    # exceeds its budget — self-observation hygiene, NOT
    # control-plane enforcement.
    "self_throttled",
    # cgroup cpu.stat kernel vocabulary (§2.1 sources table): pure
    # READINGS of kernel-side cgroup CPU throttling, passed through
    # to /live. Observation of throttling ≠ doing throttling.
    "cg_throttled_usec",
    "cg_nr_throttled",
    "throttled_usec",
    "nr_throttled",
}
_FENCE_ALLOWED_STRINGS = {
    # T4 ``op`` vocabulary advertised as available:false by
    # /control-capabilities — seam advertisement, not implementation.
    "alert",
    "throttle",
    "kill",
    # The §2.8 payload key advertising the observe-only posture.
    "enforcement",
    # cgroup cpu.stat kernel vocabulary — cgroup kernel names that
    # ride through as dict keys (the cgroup controller calls its
    # counters "throttled" — these are kernel READINGS passed
    # through /live, NOT throttling the process).
    "throttled_usec",
    "nr_throttled",
    "cg_throttled_usec",
    "cg_nr_throttled",
}
_FENCE_ALLOWED_STRING_PHRASES = (
    # House kill-switch vocabulary (facade boot log + P3/P4 prose).
    "kill-switch",
    # Sampler self-throttle log message (self-cost protection).
    "self-throttle",
)


def _string_allowed(content: str) -> bool:
    stripped = content.strip()
    if stripped in _FENCE_ALLOWED_STRINGS:
        return True
    return any(phrase in content for phrase in _FENCE_ALLOWED_STRING_PHRASES)


def _fence_violations(path: Path) -> list[str]:
    """Tokenizer-level audit of ONE file: docstrings/comments are
    stripped by construction; every surviving NAME token (identifier)
    and STRING token (incl. f-string middles) matching the fence RE
    must be explicitly adjudicated. Substring matching is the SAFE
    bias — a benign future word (e.g. ``skill``) trips the fence and
    gets consciously allowlisted rather than silently exempted."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))

    # Docstring char spans (module/class/function first-stmt strings).
    line_offsets = [0]
    for ln in src.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(ln))

    def off(row: int, col: int) -> int:
        return line_offsets[row - 1] + col

    doc_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if not node.body:
                continue
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                doc_spans.append(
                    (off(first.lineno, first.col_offset),
                     off(first.end_lineno, first.end_col_offset))
                )

    violations: list[str] = []
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        start, end = off(*tok.start), off(*tok.end)
        if tok.type == tokenize.STRING:
            if any(s <= start and end <= e for s, e in doc_spans):
                continue  # docstring — prose, allowed
            try:
                content = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                content = tok.string
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            if (
                isinstance(content, str)
                and _FENCE_RE.search(content)
                and not _string_allowed(content)
            ):
                violations.append(f"{tok.start[0]}: string {content[:60]!r}")
        elif fstring_middle is not None and tok.type == fstring_middle:
            if _FENCE_RE.search(tok.string) and not _string_allowed(tok.string):
                violations.append(f"{tok.start[0]}: fstring {tok.string[:60]!r}")
        elif tok.type == tokenize.NAME:
            if (
                _FENCE_RE.search(tok.string)
                and tok.string not in _FENCE_ALLOWED_IDENTIFIERS
            ):
                violations.append(f"{tok.start[0]}: identifier {tok.string!r}")
    return violations


class TestEnforcementFence:
    def test_fence_code_is_clean_across_scope(self):
        """AC5 — no throttle/kill/enforce CODE outside docstrings,
        comments, and the adjudicated allowlist, across the monitor
        package + the resources router. A future enforcement change
        fails HERE until the fence is consciously lifted."""
        violations: list[str] = []
        for root in _FENCE_ROOTS:
            files = (
                sorted(root.rglob("*.py")) if root.is_dir() else [root]
            )
            for py in files:
                if "__pycache__" in py.parts:
                    continue
                for v in _fence_violations(py):
                    violations.append(f"{py.relative_to(_REPO_ROOT)}:{v}")
        assert violations == [], (
            "ENFORCEMENT FENCE TRIPPED — either remove the code, or "
            "consciously extend _FENCE_ALLOWED_* with a provenance "
            f"comment (phase6-plan D1/D6):\n  " + "\n  ".join(violations)
        )
