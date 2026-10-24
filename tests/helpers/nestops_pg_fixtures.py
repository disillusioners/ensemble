"""Disposable-PostgreSQL fixtures for the nest-ops shakedown Phase 4 e2e pins.

Commission: nest-ops shakedown defect patch — Phase 4 (T4.1 + T4.2).

WHAT THIS MODULE OWNS
---------------------
* **T4.1 — the disposable-DB lifecycle.** A throwaway LOGIN role + an
  owned database on the LOCAL PostgreSQL (``PG_TEST_HOST/PORT``, default
  ``localhost:5432``), created per session and DROPPED at teardown with
  ``WITH (FORCE)`` + a ``pg_terminate_backend`` fallback (the canonical
  ``tests/helpers/checkpoint_prune_pg.py`` pattern, ported to the
  role+database pair D-13 prescribes).
* **The environment fence.** The ambient ``POSTGRES_*`` values are NEVER
  trusted: the ambient values are captured at import, the fixtures pin
  all five variables PER-VARIABLE to the disposable database, and a
  dedicated fence test asserts the resolved engine never points at the
  ambient database. ``env -u POSTGRES_*`` is a silent NO-OP (the shell
  does not glob env names — house critical note) so the scrub here is
  the explicit per-variable form.
* **T4.2 — the TWO-TIER >window fixture (D-21, arch §11.2).**
  *Tier 1* — the two proof husks born through the FULL REAL spawn order
  (``manager.spawn_instance_with_mcp`` → ``spawn_instance`` →
  ``_spawn_instance_db_sync``) under the EXACT production UUIDs, plus
  ONE synthetic touch: a single ``created_at``/``updated_at`` backdate
  UPDATE (12 / 16 days). *Tier 2* — ≥1,001 filler rows built with the
  SAME production ORM constructor ``_spawn_instance_db_sync`` uses
  (``daemon/services/instance_lifecycle.py``, the ``Instance(...)`` call
  of the unified M8 transaction), inserted in ONE session
  (``add_all`` + ``commit``). D-13 reading (stated, per arch §11.2): the
  "no synthetic rows" foreclosure applies to the PROOF rows only — the
  filler's only job is pushing the husks past the
  ``FLEET_SCAN_LIMIT`` (1000-row) window boundary.
* **The TEXT-ISO discipline (GT-9).** ``created_at`` is TEXT compared
  LEXICOGRAPHICALLY by ``ORDER BY created_at DESC`` — the backdate AND
  every filler row MUST use the production format
  ``datetime.now(timezone.utc).isoformat()`` or the husks may not land
  at the tail and the F3×F4 coupling pin goes vacuous.

SAFETY FENCES (binding)
-----------------------
* The disposable database NAME is scratch-shaped ("test" in the name)
  and checked against a forbidden-name denylist — ``ensemble_prod``,
  ``ensemble_dev``, and every other live-shaped name is REFUSED.
* The disposable database is created on ``PG_TEST_HOST/PORT`` ONLY —
  the local test server. Nothing here ever contacts ``:9797`` (the live
  daemon) and no ``$HOME``-derived path is ever resolved.
* The husk UUIDs are FIXTURES ONLY: they may be terminate TARGETS at
  most — they are NEVER dispatched to (no message, no task, no job).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session

logger = logging.getLogger(__name__)


#: The REAL home, captured at MODULE IMPORT — before any fixture
#: redirects it. The fence's own assertion compares against it.
REAL_HOME = os.path.expanduser("~")


def install_fake_home(monkeypatch: Any, base: Any) -> Path:
    """Point ``HOME`` at a throwaway fake directory (the S6 fence).

    WHY A MECHANICAL FENCE
    -----------------------
    The house critical note is explicit: a test must NEVER resolve a
    live-shaped, HOME-derived path — two live installs were deleted
    this way before the rule existed. Every lane file DECLARED the
    fence in prose ("no HOME-derived or live-shaped path is ever
    resolved"), but a declaration is not a mechanism: the next edit
    could resolve one and nothing would go red.

    This function is the mechanism. ``HOME`` is redirected at a
    throwaway directory, so a HOME-derived resolution — ``Path.home()``,
    ``os.path.expanduser("~")``, ``$HOME/...`` — lands in the fake and
    NEVER in the live install. The redirect is VERIFIED in-process
    (the caller's ``Path.home()`` must now resolve inside the fake),
    so a broken redirect fails loudly instead of silently leaking.

    Args:
        monkeypatch: pytest's ``MonkeyPatch`` (the env write is undone
            at teardown).
        base: the throwaway base directory (usually ``tmp_path``).

    Returns:
        The resolved fake home directory.
    """
    fake = Path(base) / "fake-home"
    fake.mkdir(parents=True, exist_ok=True)
    resolved = fake.resolve()

    # The escape guard: the fake must NEVER be the real home, and it
    # must never be shaped like a live install directory.
    assert resolved != Path(REAL_HOME).resolve(), (
        "the fake HOME must not be the REAL home — the fence would be "
        "a no-op"
    )
    assert "agents-ensemble" not in resolved.parts, (
        "the fake HOME must not be shaped like a live install directory "
        f"({resolved})"
    )
    assert resolved != Path("/").resolve(), "the fake HOME must not be /"

    monkeypatch.setenv("HOME", str(resolved))

    # The mechanical proof: a HOME-derived resolution NOW lands in the
    # fake. If this ever stops holding, the fence is broken and the
    # test fails HERE rather than at some later, unrelated moment.
    assert Path.home().resolve() == resolved, (
        "the HOME fence did not take effect — Path.home() still resolves "
        f"to {Path.home()}"
    )
    return resolved


# ── the environment fence ──────────────────────────────────────────────────

#: The five variables the house per-variable scrub names. ``POSTGRES_URL``
#: is scrubbed too (``create_postgres_engine`` prefers it over the
#: discrete variables).
SCRUBBED_POSTGRES_VARS = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_URL",
)

#: The AMBIENT values, captured at MODULE IMPORT — before any fixture
#: pins anything. The fence test reads this snapshot to prove the ambient
#: database (e.g. a shell-exported ``POSTGRES_DB=ensemble_prod``) can
#: never leak into the resolved engine.
AMBIENT_POSTGRES: dict[str, str | None] = {
    name: os.environ.get(name) for name in SCRUBBED_POSTGRES_VARS
}

#: Local test server defaults (mirrors ``tests/postgres/conftest.py``).
PG_TEST_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_TEST_PORT = os.environ.get("PG_TEST_PORT", "5432")
PG_TEST_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_TEST_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

_ADMIN_URL = (
    f"postgresql+psycopg://{PG_TEST_USER}:{PG_TEST_PASSWORD}"
    f"@{PG_TEST_HOST}:{PG_TEST_PORT}/postgres"
)

#: Databases this module must NEVER touch (the DF-4 denylist, verbatim).
FORBIDDEN_DATABASES = frozenset(
    {
        "ensemble",
        "ensemble_dev",
        "ensemble_demo",
        "ensemble_dev_prod",
        "ensemble_prod",
        "ensemble_sandbox",
        "postgres",
        "template0",
        "template1",
    }
)

#: The POSITIVE scratch-shape rule (house convention): the disposable
#: name must be visibly scratch. The denylist is necessary, not
#: sufficient.
SCRATCH_NAME_MARKERS = ("test", "scratch")


def assert_scratch_db(db_name: str, *, where: str) -> None:
    """Refuse a live/dev database OR a name that is not scratch-shaped."""
    assert db_name not in FORBIDDEN_DATABASES, (
        f"refusing to run against a live/dev database ({where}): {db_name!r}"
    )
    lowered = db_name.lower()
    assert any(marker in lowered for marker in SCRATCH_NAME_MARKERS), (
        f"refusing to run against a non scratch-shaped database ({where}): "
        f"{db_name!r} — the name must contain 'test' or 'scratch'"
    )


def probe_admin_engine() -> Engine | None:
    """Return a connected ADMIN engine, or ``None`` when PG is unreachable.

    The caller skips loudly (``pytest.skip``) on ``None`` — CI portability
    (the canonical pattern from ``tests/helpers/checkpoint_prune_pg.py``).
    """
    try:
        engine = create_engine(_ADMIN_URL, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:  # noqa: BLE001 — any failure means "skip loudly"
        return None


# ── the shared tool-lookup helpers (the tidier fold) ──────────────────────


def by_name(tools, name):
    """The tool named ``name`` — with the REGISTERED names on failure.

    ONE helper for the four lane files that each carried their own
    copy (the copies had already drifted: two asserted, one did not,
    and the assertion messages differed).

    Fail-closed: a missing tool is a hard assertion, never a silent
    ``None`` and never a bare ``StopIteration``.
    """
    tool = next((t for t in tools if t.name == name), None)
    assert tool is not None, (
        f"tool {name!r} is not registered "
        f"(registered: {sorted(t.name for t in tools)})"
    )
    return tool


async def call(tools, name, **kwargs):
    """Invoke one tool and JSON-decode its payload."""
    out = await by_name(tools, name).ainvoke(kwargs)
    return json.loads(out)


# ── T4.1 — the disposable role + database lifecycle ───────────────────────


class DisposableDB:
    """A throwaway LOGIN role + an owned database on the LOCAL PG server.

    Attributes carry everything a test needs to build engines: ``dsn``
    (psycopg-style), ``url`` (SQLAlchemy-style), ``db_name``, ``role``
    and ``password``. The object is inert until :meth:`create` runs;
    :meth:`drop` is idempotent and best-effort-fenced (terminate stray
    backends before dropping, so a leaked pool connection cannot block
    the DROP).
    """

    def __init__(self) -> None:
        token = uuid.uuid4().hex[:10]
        self.db_name = f"ens_shakedown_test_{token}"
        self.role = f"ens_shakedown_{token}"
        self.password = secrets.token_urlsafe(18)
        assert_scratch_db(self.db_name, where="disposable db name")
        self._created = False

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg://{self.role}:{self.password}"
            f"@{PG_TEST_HOST}:{PG_TEST_PORT}/{self.db_name}"
        )

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.role}:{self.password}"
            f"@{PG_TEST_HOST}:{PG_TEST_PORT}/{self.db_name}"
        )

    def create(self, admin: Engine) -> None:
        """CREATE ROLE ... LOGIN + CREATE DATABASE ... OWNER (one call).

        CREATE ROLE is a PG utility statement — bind parameters are not
        supported for it. The password is generated HERE by
        :mod:`secrets` (``token_urlsafe``: ``[A-Za-z0-9_-]`` only), is
        additionally guarded against quote/backslash characters, and is
        inlined with the standard doubling escape.
        """
        pw = self.password
        if any(c in pw for c in ("'", "\\", ";", " ")):  # defensive; unreachable
            raise ValueError("generated password contains an unsafe character")
        with admin.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(
                text(
                    f"CREATE ROLE \"{self.role}\" LOGIN "
                    f"PASSWORD '{pw.replace(chr(39), chr(39) * 2)}'"
                )
            )
            conn.execute(
                text(f'CREATE DATABASE "{self.db_name}" OWNER "{self.role}"')
            )
        self._created = True

    def drop(self, admin: Engine) -> None:
        """Terminate stray backends, DROP DATABASE (FORCE fallback), DROP ROLE.

        Idempotent: safe to call twice; a partially-created fixture
        (role without database) is cleaned up too.
        """
        if not self._created:
            return
        with admin.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            try:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM "
                        "pg_stat_activity WHERE datname = :d "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"d": self.db_name},
                )
                try:
                    conn.execute(
                        text(f'DROP DATABASE IF EXISTS "{self.db_name}" WITH (FORCE)')
                    )
                except Exception as exc:  # noqa: BLE001 — older PG without FORCE
                    # LOUD, not silent: the fallback path runs only on a
                    # PG without WITH (FORCE), and a swallowed failure
                    # here leaves the disposable DATABASE BEHIND on the
                    # server with nobody the wiser.
                    logger.warning(
                        "DROP DATABASE ... WITH (FORCE) failed for %s "
                        "(this PG version may not support it) — retrying "
                        "without FORCE: %s",
                        self.db_name,
                        exc,
                    )
                    conn.execute(
                        text(f'DROP DATABASE IF EXISTS "{self.db_name}"')
                    )
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "the disposable database %s could not be "
                    "terminated/dropped — it may still exist on the PG "
                    "server: %s",
                    self.db_name,
                    exc,
                )
            try:
                conn.execute(text(f'DROP ROLE IF EXISTS "{self.role}"'))
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "the disposable role %s could not be dropped — it may "
                    "still exist on the PG server: %s",
                    self.role,
                    exc,
                )
        self._created = False


@contextmanager
def disposable_db(admin: Engine | None) -> Iterator[DisposableDB]:
    """Context manager: create the disposable pair, yield, drop at exit.

    ``admin=None`` yields a sentinel that skips (the caller turns that
    into a loud ``pytest.skip``).
    """
    db = DisposableDB()
    if admin is None:  # pragma: no cover — the caller skips first
        raise RuntimeError("disposable_db requires a connected admin engine")
    db.create(admin)
    try:
        yield db
    finally:
        db.drop(admin)


def pin_scratch_postgres_env(
    monkeypatch: pytest.MonkeyPatch, db: DisposableDB
) -> None:
    """Pin ALL five ``POSTGRES_*`` variables + scrub ``POSTGRES_URL``.

    The EXPLICIT per-variable form (the glob idiom ``env -u POSTGRES_*``
    is a silent no-op). ``create_postgres_engine`` reads ``POSTGRES_*``
    FIRST, so this pins the resolved engine to the disposable database —
    never the ambient one (the fence test proves the negative).
    """
    monkeypatch.setenv("POSTGRES_HOST", PG_TEST_HOST)
    monkeypatch.setenv("POSTGRES_PORT", str(PG_TEST_PORT))
    monkeypatch.setenv("POSTGRES_DB", db.db_name)
    monkeypatch.setenv("POSTGRES_USER", db.role)
    monkeypatch.setenv("POSTGRES_PASSWORD", db.password)
    monkeypatch.delenv("POSTGRES_URL", raising=False)


# ── the REAL manager (T4.2 tier 1 + T4.3/T4.5) ────────────────────────────


def build_real_manager(db: DisposableDB, tmp_path: Any):
    """A REAL ``InstanceManager`` over the disposable PostgreSQL database.

    Mirrors the DF-4 closure pin's ``real_manager`` fixture: pinned
    per-variable ``POSTGRES_*`` env, ``mcp_pool.enabled = False`` (no
    external MCP discovery), tmp_path-scoped SQLite/checkpoint paths
    (never a live or HOME-derived install path). The RESOLVED engine
    database is asserted scratch-shaped AND equal to the disposable
    name before the manager is handed out.
    """
    from daemon.config import Config
    from daemon.ensemble_config import EnsembleConfig, PostgresConfig
    from daemon.manager import InstanceManager

    from pathlib import Path

    config = Config()
    config.persistence.db_path = str(Path(tmp_path) / "instances.db")
    config.mcp_pool.enabled = False
    ensemble_config = EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            host=PG_TEST_HOST,
            port=int(PG_TEST_PORT),
            db=db.db_name,
            user=db.role,
            password=db.password,
        ),
    )
    manager = InstanceManager(config, ensemble_config=ensemble_config)
    resolved = manager.engine.url.database
    assert_scratch_db(resolved, where="the RESOLVED manager engine database")
    assert resolved == db.db_name, (
        "the manager's engine must resolve to the disposable database "
        f"({db.db_name!r} != {resolved!r})"
    )
    # The production startup hook (api.py lifespan →
    # ensure_system_default_project) — spawn_instance normalises
    # project_id=None to the system default and then VALIDATES the row,
    # so the row must exist before any real spawn.
    manager._project_repository.ensure_system_default_project()
    return manager


# ── T4.2 — the two-tier >window fixture ───────────────────────────────────

#: The EXACT production UUIDs (read-side §9.2 — the two live targets the
#: shakedown found). FIXTURES ONLY: terminate TARGETS at most, NEVER
#: dispatched to.
HUSK_A_ID = "5ba21b2d-43b7-4378-be53-cca781f9c124"
HUSK_B_ID = "7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f"

#: Backdates: 12 and 16 days (1 036 800 s … 1 382 400 s — 12×–16× the
#: 86 400 s husk threshold; read-side §3.5 leg 3).
HUSK_A_AGE_DAYS = 12
HUSK_B_AGE_DAYS = 16

#: Tier-2 filler size: ≥1,001 so both backdated husks land OUTSIDE the
#: first ``FLEET_SCAN_LIMIT`` (1000-row) window of the
#: ``created_at DESC`` ordering.
FILLER_ROWS = 1_001

#: A deliberately NON-KB filler agent id (the census excludes KB agents;
#: the filler must be census-visible).
FILLER_AGENT_ID = "worker"


def production_iso(dt: datetime) -> str:
    """The production timestamp format (GT-9).

    ``created_at`` is TEXT compared LEXICOGRAPHICALLY — every timestamp
    this module writes MUST be the exact production format
    ``datetime.now(timezone.utc).isoformat()`` so the lexicographic
    ordering matches the chronological one.
    """
    return dt.isoformat()


async def spawn_real_husk(
    manager: Any, instance_id: str, agent_id: str = "worker"
) -> str:
    """TIER 1 — the FULL REAL spawn order, exact UUID.

    ``spawn_instance_with_mcp`` (manager, awaited — it is async) →
    ``spawn_instance`` (lifecycle) → ``_spawn_instance_db_sync`` (the
    unified M8 transaction). The row is born exactly as production
    births it: ``status='idle'``, ``version=1``, ``last_activity_at``
    NULL. NO message, NO task, NO job is ever dispatched to the row —
    the point of the fixture.
    """
    spawned = await manager.spawn_instance_with_mcp(
        instance_id=instance_id, agent_id=agent_id
    )
    assert spawned == instance_id
    return spawned


def backdate_husk(
    engine: Engine, instance_id: str, *, days: int
) -> tuple[str, str]:
    """The ONE synthetic touch: backdate ``created_at``/``updated_at``.

    A single direct UPDATE in its own transaction. The timestamps use
    the production ISO format (TEXT-ISO discipline). Returns the two
    written values for the caller's assertion.
    """
    created = production_iso(datetime.now(timezone.utc) - timedelta(days=days))
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE instances SET created_at = :c, updated_at = :c "
                "WHERE instance_id = :iid"
            ),
            {"c": created, "iid": instance_id},
        )
        assert result.rowcount == 1, (
            f"backdate touched {result.rowcount} rows for {instance_id!r}"
        )
    return created, created


def insert_filler_fleet(engine: Engine, *, count: int = FILLER_ROWS) -> list[str]:
    """TIER 2 — ≥1,001 filler rows via the production ORM constructor.

    The EXACT constructor ``_spawn_instance_db_sync`` uses
    (``Instance(instance_id=…, project_id=…, agent_id=…, agent_tag=…,
    agent_dir=…, agent_name=…, parent_id=…, status=IDLE,
    instance_metadata=…, version=1, created_at=…, updated_at=…)``),
    inserted in ONE session (``add_all`` + ``commit``) — production row
    shape + production INSERT path, minus graph/MCP (D-21/arch §11.2).

    ``agent_id`` is NON-KB so the census (which excludes KB agents)
    still counts the filler. Timestamps use the production ISO format.
    """
    from daemon.repositories.instance.models import Instance, InstanceStatus

    now = datetime.now(timezone.utc)
    ids: list[str] = []
    with Session(engine) as session:
        rows = []
        for i in range(count):
            iid = str(uuid.uuid4())
            ids.append(iid)
            ts = production_iso(now + timedelta(microseconds=i))
            rows.append(
                Instance(
                    instance_id=iid,
                    agent_id=FILLER_AGENT_ID,
                    agent_dir=f"agents/{FILLER_AGENT_ID}",
                    agent_name=f"shakedown-filler-{i}",
                    parent_id=None,
                    status=InstanceStatus.IDLE.value,
                    instance_metadata={},
                    version=1,
                    created_at=ts,
                    updated_at=ts,
                )
            )
        session.add_all(rows)
        session.commit()
    return ids
