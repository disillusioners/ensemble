"""Dev-fenced PostgreSQL helper for the Phase-3 T7 pack (council C4).

Shared by ``tests/unit/services/test_tool_usage_digest_determinism.py``
and ``tests/unit/repositories/test_tool_usage_seed_lifecycle.py`` so the
fence exists ONCE (a second copy is a copy that will drift).

THE FENCE (dev only, zero live contact):

* coordinates come from the WORKTREE ``.env`` file — repo-relative read,
  never a HOME-derived or live-install path, never the ambient
  ``POSTGRES_*`` / ``DATABASE_URL`` (an offsite shell cannot redirect the
  engine);
* the database name must be EXACTLY ``ensemble_dev`` and the host must be
  loopback — anything else is a HARD ERROR, never a silent skip (a fence
  that degrades quietly into a live contact is not a fence);
* every leg runs inside a THROWAWAY SCHEMA (``<prefix>_<uuid8>``) created
  over the same engine and dropped ``CASCADE`` at teardown, so the shared
  dev database is otherwise untouched;
* an unreachable dev PostgreSQL skips the leg cleanly — the SQLite leg of
  each dual-dialect test always runs.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

#: The only database the PostgreSQL legs may touch (hard fence).
ALLOWED_DB_NAME = "ensemble_dev"
#: Loopback-only — the worktree dev fence lives on 127.0.0.1.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def worktree_root() -> Path:
    """``tests/unit/<helper>.py`` → the worktree root."""
    return Path(__file__).resolve().parents[2]


def read_env_fence() -> dict[str, str]:
    """Read the worktree ``.env`` fence (``KEY=VALUE`` lines only)."""
    env_path = worktree_root() / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def _fenced_base_url() -> str:
    fence = read_env_fence()
    host = fence.get("POSTGRES_HOST")
    port = fence.get("POSTGRES_PORT", "5432")
    dbname = fence.get("POSTGRES_DB")
    user = fence.get("POSTGRES_USER")
    password = fence.get("POSTGRES_PASSWORD")

    if not (host and dbname and user and password):
        pytest.skip(
            "dev-fenced PostgreSQL unavailable: the worktree .env carries no "
            "complete POSTGRES_* fence (the SQLite leg still runs)."
        )

    # ---- HARD FENCE: the dev database only, loopback only ----
    if dbname != ALLOWED_DB_NAME:
        raise RuntimeError(
            f"FENCE VIOLATION: refusing to run against database {dbname!r} "
            f"(allowed: {ALLOWED_DB_NAME!r})."
        )
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError(
            f"FENCE VIOLATION: refusing a non-loopback PostgreSQL host "
            f"{host!r} (allowed: {sorted(LOOPBACK_HOSTS)})."
        )

    return (
        f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{dbname}"
    )


@contextmanager
def pg_throwaway_schema_engine(
    tables: list[Any], prefix: str = "t7probe"
) -> Iterator[Engine]:
    """A dev-fenced PG engine inside a throwaway schema (dropped on exit)."""
    base_url = _fenced_base_url()

    probe = create_engine(base_url, pool_pre_ping=True, future=True)
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — unreachable ⇒ clean skip
        probe.dispose()
        pytest.skip(f"dev-fenced PostgreSQL unreachable: {exc}")

    schema = f"{prefix}_{uuid.uuid4().hex[:8]}"
    try:
        with probe.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.commit()
    finally:
        probe.dispose()

    engine = create_engine(
        base_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"options": f"-c timezone=UTC -c search_path={schema}"},
    )
    try:
        SQLModel.metadata.create_all(engine, tables=tables)
        yield engine
    finally:
        engine.dispose()
        cleanup = create_engine(base_url, future=True)
        try:
            with cleanup.connect() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                conn.commit()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass
        finally:
            cleanup.dispose()


__all__ = [
    "ALLOWED_DB_NAME",
    "LOOPBACK_HOSTS",
    "pg_throwaway_schema_engine",
    "read_env_fence",
    "worktree_root",
]
