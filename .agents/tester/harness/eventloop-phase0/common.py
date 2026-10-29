"""Shared fences + scratch-PostgreSQL machinery for the eventloop-phase0 harness.

NOT SHIPPED — measurement scaffolding under ``.agents/tester/harness/``.
Zero production code; the daemon is imported read-only.

FENCES (binding, mirror of the soak-gate pack + dbpool-phase0 rules)
---------------------------------------------------------------------
* SCRATCH DB ONLY: a throwaway LOGIN role + owned database created by
  this harness and DROPPED at teardown.  The admin DSN comes from the
  explicit ``EVENTLOOP_PG_ADMIN_*`` variables (defaulting to the local
  test cluster's ``ensemble`` role) — the harness NEVER reads ambient
  ``POSTGRES_*`` (on this host the ambient values point at a REMOTE
  ``ensemble_prod``).
* PER-VARIABLE ENV SCRUB: the runner (``run_all.sh``) unsets every
  ``POSTGRES_*`` variable BY NAME before exec'ing the python process;
  the child env is then constructed EXPLICITLY below.  ``env -u
  POSTGRES_*`` is a silent no-op (bash does not glob env names) and is
  never used.
* PORT: a free TCP port picked by bind() — never 9797 / 8079 / 7979,
  asserted before boot.
* NO LIVE CONTACT: no live install path, no promote/rollback scripts,
  no live journal.  ``ENSEMBLE_DATA_DIR`` points at a harness mktemp
  dir so the wedge-capture dir and every HOME-derived default resolve
  under the harness scratch tree (a FAKE_HOME is layered on top for
  defense in depth, the soak-gate precedent).
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

FORBIDDEN_PORTS = {9797, 8079, 7979}  # live / dev / demo daemons

#: The five POSTGRES_* variables the daemon reads — pinned PER VARIABLE
#: (never the glob idiom) when the child env is built.
POSTGRES_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)

#: Every ambient variable the runner must unset BY NAME before the
#: python process starts (the harness double-checks and refuses to run
#: if any survived — the anti-poison fence).
SCRUB_VARS = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_URL",
    "DAEMON_PORT",
    "PORT",
    "ENSEMBLE_UPGRADE_LIVE",
    "ENSEMBLE_SELF_ENV",
)


class FenceError(RuntimeError):
    """A fence refused the run — never worked around, always reported."""


def assert_clean_env() -> None:
    """Refuse to run if any scrubbed ambient variable survived.

    The runner unsets these by name; if one is present anyway the
    process is mis-launched and MUST NOT proceed (a stray ambient
    ``POSTGRES_DB=ensemble_prod`` would aim the daemon at production).
    """
    survivors = {v: os.environ[v] for v in SCRUB_VARS if os.environ.get(v)}
    if survivors:
        raise FenceError(
            f"ambient variables survived the scrub fence: {survivors} — "
            "launch via run_all.sh, which unsets them per-variable"
        )


@dataclass
class ScratchPG:
    """The throwaway role + database pair (created/dropped by this harness)."""

    host: str
    port: int
    db: str
    role: str
    password: str
    admin_dsn: str  # postgres://admin:pw@host:port/postgres

    created_at: float = field(default_factory=time.monotonic)

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.role}:{self.password}@{self.host}:{self.port}/{self.db}"
        )

    def env(self) -> dict[str, str]:
        """The five per-variable POSTGRES_* pins for the child."""
        return {
            "POSTGRES_HOST": self.host,
            "POSTGRES_PORT": str(self.port),
            "POSTGRES_DB": self.db,
            "POSTGRES_USER": self.role,
            "POSTGRES_PASSWORD": self.password,
        }


def admin_conn_kwargs() -> dict[str, str | int]:
    """The local test-cluster admin connection (the soak-gate precedent).

    Defaults mirror ``tests/helpers/soak_gate_pack_common.sh``: the
    local cluster's ``ensemble`` role.  Override with
    ``EVENTLOOP_PG_ADMIN_{HOST,PORT,USER,PASSWORD}``.
    """
    return {
        "host": os.environ.get("EVENTLOOP_PG_ADMIN_HOST", "127.0.0.1"),
        "port": int(os.environ.get("EVENTLOOP_PG_ADMIN_PORT", "5432")),
        "user": os.environ.get("EVENTLOOP_PG_ADMIN_USER", "ensemble"),
        "password": os.environ.get("EVENTLOOP_PG_ADMIN_PASSWORD", "ensemble_dev"),
    }


def create_scratch_pg() -> ScratchPG:
    """Create the throwaway LOGIN role + owned database (soak-gate shape)."""
    import psycopg

    token = f"{int(time.time())}{os.getpid()}"
    role = f"evloop_p0_{token}"
    db = f"evloop_scratch_test_{token}"
    password = f"EvLoop{token}"
    kw = admin_conn_kwargs()
    admin_dsn = (
        f"postgresql://{kw['user']}:{kw['password']}@{kw['host']}:{kw['port']}/postgres"
    )
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\''
        )
        conn.execute(f'CREATE DATABASE "{db}" OWNER "{role}"')
    pg = ScratchPG(
        host=str(kw["host"]),
        port=int(kw["port"]),
        db=db,
        role=role,
        password=password,
        admin_dsn=admin_dsn,
    )
    _assert_scratch_shaped(pg.db)
    return pg


def _assert_scratch_shaped(db: str) -> None:
    """The pre-boot refusal guard (the degrade-alone guard, kept verbatim)."""
    if db in {"ensemble_prod", "ensemble_dev", "ensemble_sandbox", ""}:
        raise FenceError(f"refusing to boot against a live/dev database: {db!r}")
    if "test" not in db.lower() and "scratch" not in db.lower():
        raise FenceError(f"refusing to boot against a non-scratch database: {db!r}")


def drop_scratch_pg(pg: ScratchPG) -> dict[str, object]:
    """DROP the database + role (terminate backends first). Idempotent."""
    import psycopg

    proof: dict[str, object] = {"db": pg.db, "role": pg.role, "dropped": False}
    try:
        with psycopg.connect(pg.admin_dsn, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (pg.db,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{pg.db}" WITH (FORCE)')
            conn.execute(f'DROP ROLE IF EXISTS "{pg.role}"')
            # Proof the objects are gone (N5 fence on the teardown itself).
            row = conn.execute(
                "SELECT count(*) FROM pg_database WHERE datname = %s",
                (pg.db,),
            ).fetchone()
            proof["db_rows_after"] = row[0] if row else None
            row = conn.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname = %s",
                (pg.role,),
            ).fetchone()
            proof["role_rows_after"] = row[0] if row else None
            proof["dropped"] = proof["db_rows_after"] == 0 and proof["role_rows_after"] == 0
    except Exception as exc:  # noqa: BLE001 — teardown errors are REPORTED, never raised
        proof["error"] = f"{type(exc).__name__}: {exc}"
    return proof


def pick_free_port() -> int:
    """Bind-verified free port; the live/dev/demo ports are refused outright."""
    for _ in range(60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port in FORBIDDEN_PORTS:
            continue
        # Re-bind to prove it is still free, then hand it over.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return int(port)
    raise FenceError("cannot find a free port")


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (ceil, the LagHistogram precedent)."""
    import math

    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


def stats_ms(values: list[float]) -> dict[str, object]:
    """The p50/p95/max/count summary in MILLISECONDS."""
    if not values:
        return {"samples": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "samples": len(values),
        "p50_ms": round(percentile(values, 50) * 1000, 2),
        "p95_ms": round(percentile(values, 95) * 1000, 2),
        "max_ms": round(max(values) * 1000, 2),
    }


def write_json(path: Path, data: object) -> None:
    """Atomic JSON write (tmp + os.replace, the report-file precedent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
